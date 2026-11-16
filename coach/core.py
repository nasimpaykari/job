"""Persistent settings, canonical vacancies, migration and auditable memory."""
import contextlib, copy, csv, hashlib, io, json, os, re, shutil, sqlite3, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

STATUSES = ['New','Shortlist','Drafting','Ready to Submit','Submitted','Interviewing','Offer','Rejected','Withdrawn','Archived','Ignored']
CATEGORIES = ['CV','Resume','Cover','Research','Teaching','Diversity','Mentor']
SOURCES = {
 'CRA': 'https://careercenter.cra.org/jobs',
 'IEEE': 'https://jobs.ieee.org/jobs/results/keyword/{query}',
 'AcademicJobsOnline': 'https://academicjobsonline.org/ajo/Computer%20Science',
 'HigherEdJobs': 'https://www.higheredjobs.com/faculty/search.cfm?JobCat=101',
 'Computeroxy': 'https://computeroxy.com/',
 'InsideHigherEd': 'https://jobs.insidehighered.com/jobs/computer-science/',
 'AcademicKeys': 'https://www.academickeys.com/all/choose_discipline.php',
 'AcademicCareers': 'https://academiccareers.com/jobs?q={query}',
 'LinkedIn': 'https://www.linkedin.com/jobs/'
}
DEFAULT = dict(network_enabled=False, ai_enabled=False, provider='gemini', model='',
 queries=[], excluded_keywords=[],
 excluded_employers=[], countries=[], regions=[], minimum_salary=0, currency='USD',
 exclude_unknown_salary=False, exclude_unknown_location=False, required_keywords=[],
 preferred_keywords=[], preferred_job_types=[],
 browser_render=False, show_browser=False, max_pages=8, search_result_limit=8, custom_urls=[],
  sources={n:{'enabled':n!='LinkedIn','limit':8,'url':u} for n,u in SOURCES.items()},
 profile={'full_name':'','verified':False,'facts':''},
 document_categories=list(CATEGORIES), ollama_url='http://127.0.0.1:11434', discovery_mode='auto',
 pdf_engine='auto', pdf_browser_fallback=False, pdf_remote_consent=False, trained_run='', learning_previous={},
 deadline_warning_days=14,deadline_alerts=True,saved_search_enabled=False,search_targets=[],
 auto_university_discovery=True, include_tab_universities=True, include_tab_boards=True, include_tab_social=True,
 documents_root='', career_evidence_mode='profile',
 storage={'mode':'local','destination':'','auto_sync':False},
 rules={c:{'dos':'Use verified profile facts. Tailor to the actual posting.', 'donts':'Do not invent achievements, affiliations, courses or probabilities.', 'prompt':''} for c in CATEGORIES}, templates={})

def now(): return datetime.now(timezone.utc).isoformat(timespec='microseconds')
def uid(): return uuid.uuid4().hex

def canonical_url(url):
    p=urlsplit((url or '').strip())
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password:
        raise ValueError('Enter an http(s) URL without credentials.')
    host=p.hostname.lower().removeprefix('www.')
    path=re.sub('/+','/',p.path).rstrip('/')
    if host=='academicjobsonline.org':
        m=re.match(r'/ajo/jobs/(\d+)(?:/|$)',path)
        if m:return 'https://academicjobsonline.org/ajo/jobs/'+m[1]
    if host=='linkedin.com':
        m=re.search(r'(?:-|/)(\d{7,})(?:/|$)',path)
        if m:return 'https://linkedin.com/jobs/view/'+m[1]
    query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid','trk','trackingid')]
    netloc=host+(f':{p.port}' if p.port and p.port not in (80,443) else '')
    return urlunsplit(('https',netloc,path,urlencode(sorted(query)),''))

def is_linkedin_url(url):
    try:host=(urlsplit(str(url).strip()).hostname or '').lower().removeprefix('www.')
    except ValueError:return False
    return host=='linkedin.com' or host.endswith('.linkedin.com')

def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s).lower()).strip()

def identity_urls(data):
    primary=canonical_url(data['url']);urls={primary}
    if data.get('apply_url'):
        au=canonical_url(data['apply_url'])
        if au==primary or re.search(r'/ajo/jobs/\d+$|/jobs/view/\d+$|/jobs/\d+(?:/|$)|/job/\d+(?:/|$)|apply\.interfolio\.com/\d+$|[?&]JobCode=\d+',au,re.I):urls.add(au)
    return urls

class Store:
    def __init__(self,root):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'coach.db'; self.lock=threading.RLock()
        with self.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,data TEXT NOT NULL,status TEXT NOT NULL,created TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS aliases(url TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,job_id TEXT,kind TEXT,data TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS memory(id TEXT PRIMARY KEY,category TEXT,text TEXT,approved INTEGER,created TEXT);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,job_id TEXT,mode TEXT,question TEXT,answer TEXT,feedback TEXT,score REAL,created TEXT);
            CREATE TABLE IF NOT EXISTS universities(id TEXT PRIMARY KEY,data TEXT NOT NULL,updated TEXT);
            CREATE TABLE IF NOT EXISTS document_folders(job_id TEXT PRIMARY KEY,name TEXT UNIQUE COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,job_id TEXT,category TEXT,content TEXT,prompt TEXT,approved INTEGER,created TEXT);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,state TEXT,data TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS legacy(source TEXT,row_key TEXT,payload TEXT,job_id TEXT,PRIMARY KEY(source,row_key));
            ''')
            c.execute("INSERT OR IGNORE INTO kv VALUES('settings',?)",(json.dumps(DEFAULT),))
            c.execute("UPDATE tasks SET state='interrupted' WHERE state='running'")
        from .career import initialize
        initialize(self)
        from .freshness import initialize as initialize_freshness
        initialize_freshness(self)
        from .packages import initialize as initialize_packages
        initialize_packages(self)
        from .workbench import initialize as initialize_workbench
        initialize_workbench(self)
        from .journey import initialize as initialize_journey
        initialize_journey(self)
        from .scheduler import initialize as initialize_scheduler
        initialize_scheduler(self)
        from .universities import ensure_university
        with self.db() as c:
            for row in c.execute('SELECT data FROM jobs').fetchall():
                from .universities import institution_key
                job=json.loads(row[0]);known=c.execute('SELECT 1 FROM universities WHERE id=?',(institution_key(job['institution']),)).fetchone()
                key=ensure_university(c,job)
                if key and not known:
                    d=json.loads(c.execute('SELECT data FROM universities WHERE id=?',(key,)).fetchone()[0]);d['discovery_status']='needs_input'
                    c.execute('UPDATE universities SET data=? WHERE id=?',(json.dumps(d),key))
            for row in c.execute('SELECT id,data FROM universities').fetchall():
                d=json.loads(row['data'])
                if d.get('discovery_status')=='queued':
                    d['discovery_status']='pending';c.execute('UPDATE universities SET data=? WHERE id=?',(json.dumps(d),row['id']))
        # Move legacy UUID-named job folders once, preserving every version.
        if (self.root/'documents').exists():
            with self.db() as c:ids=[r[0] for r in c.execute('SELECT id FROM jobs')]
            for jid in ids:
                if (self.root/'documents'/jid).is_dir():self.document_folder(jid)
        with self.db() as c:versions=[r[0] for r in c.execute('SELECT id FROM documents')]
        for did in versions:self.document_version(did)
    @contextlib.contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL')
        try:
            with c:yield c
        finally:c.close()
    def document_folder(self,jid):
        """Stable readable job folder for approved PDF and LaTeX exports."""
        with self.lock:
            job=self.job(jid);parent=self.documents_root();parent.mkdir(parents=True,exist_ok=True)
            def clean(value):
                text=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value))
                return re.sub(r'\s+',' ',text).strip(' .') or 'Untitled'
            # Keep room for UUID version folders and Windows path limits.
            budget=max(30,min(100,220-len(str(parent))-34))
            part=max(10,(budget-2)//2)
            base=clean(job['institution'])[:part].rstrip(' .')+'__'+clean(job['title'])[:part].rstrip(' .')
            with self.db() as c:
                row=c.execute('SELECT name FROM document_folders WHERE job_id=?',(jid,)).fetchone()
                name=row[0] if row else base
                if not row:
                    if (parent/name).exists() or c.execute('SELECT 1 FROM document_folders WHERE name=?',(name,)).fetchone():name=base[:max(10,budget-34)]+'__'+jid
                    c.execute('INSERT INTO document_folders VALUES(?,?)',(jid,name))
            target=parent/name;legacy=parent/jid
            if legacy.is_dir() and legacy!=target:
                if not target.exists():legacy.rename(target)
                else:
                    for item in legacy.iterdir():
                        destination=target/item.name
                        if destination.exists():raise ValueError('Conflicting legacy document folders; originals retained for manual review.')
                        shutil.move(str(item),str(destination))
                    legacy.rmdir()
            target.mkdir(exist_ok=True)
            return target

    def document_version(self,did):
        """Private version history, separate from the readable approved exports.

        Only registered document directories are migrated. Unrelated user files
        are untouched. If an old directory was manually removed, recover its
        source from SQLite and its PDF from the matching approved export.
        """
        if not re.fullmatch(r'[A-Za-z0-9_-]+',did):raise ValueError('Invalid document ID')
        with self.lock:
            target=self.root/'document_versions'/did
            target.parent.mkdir(parents=True,exist_ok=True)
            with self.db() as c:row=c.execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone()
            if not row:return target
            public=self.document_folder(row['job_id']);legacy=public/did
            if legacy.is_dir() and not legacy.is_symlink():
                if not target.exists():shutil.move(str(legacy),str(target))
                else:
                    # A partially copied migration must never overwrite a conflict.
                    import filecmp
                    files=[p for p in legacy.rglob('*') if p.is_file()]
                    if any(p.is_symlink() or ((target/p.relative_to(legacy)).exists() and not filecmp.cmp(p,target/p.relative_to(legacy),shallow=False)) for p in files):
                        return legacy
                    shutil.copytree(legacy,target,dirs_exist_ok=True)
                    shutil.rmtree(legacy)
            target.mkdir(parents=True,exist_ok=True)
            if not (target/'document.tex').exists():(target/'document.tex').write_text(row['content'],encoding='utf-8')
            if not (target/'document.pdf').exists():
                matches=list(public.glob('*_'+did[:8]+'.pdf'))
                if len(matches)==1:shutil.copy2(matches[0],target/'document.pdf')
            return target

    def documents_root(self):
        return Path(self.settings().get('documents_root') or self.root/'documents')

    def change_documents_root(self, value):
        """Copy before switching; keep originals as a recovery copy, never overwrite."""
        value=str(value).strip()
        target=Path(value).expanduser() if value else self.root/'documents'
        if not target.is_absolute():raise ValueError('Choose a full local or desktop-synced folder path, not a web URL or rclone remote.')
        target=target.resolve()
        with self.lock:
            source=self.documents_root().resolve()
            if target==source:return str(target)
            if target in source.parents or source in target.parents:raise ValueError('Choose a separate folder, not a parent or child of the current documents folder.')
            target.mkdir(parents=True,exist_ok=True)
            if any(target.iterdir()):raise ValueError('Choose an empty dedicated folder to avoid mixing or overwriting documents.')
            probe=target/'.write-test';probe.write_text('test');probe.unlink()
            if source.exists():shutil.copytree(source,target,dirs_exist_ok=True)
            self.save_settings({'documents_root':str(target) if value else ''})
        return str(target)

    def settings(self):
        with self.db() as c: saved=json.loads(c.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])
        defaults=copy.deepcopy(DEFAULT);defaults.update(saved);return defaults
    def save_settings(self,d,connection=None):
        if not isinstance(d,dict):raise ValueError('Settings must be an object.')
        s=self.settings(); unknown=set(d)-set(DEFAULT)
        if unknown:raise ValueError('Unknown settings: '+', '.join(unknown))
        s.update(d)
        if s['career_evidence_mode'] not in ('profile','verified_facts'):raise ValueError('Invalid career evidence mode')
        for key in ['deadline_alerts','saved_search_enabled','auto_university_discovery','include_tab_universities','include_tab_boards','include_tab_social','network_enabled','ai_enabled','browser_render','show_browser','exclude_unknown_salary','exclude_unknown_location','pdf_remote_consent','pdf_browser_fallback']:
            if type(s[key]) is not bool:raise ValueError(key+' must be true or false')
        for key in ['queries','excluded_keywords','excluded_employers','countries','regions','required_keywords','preferred_keywords','preferred_job_types','custom_urls']:
            if not isinstance(s[key],list) or not all(isinstance(v,str) for v in s[key]):raise ValueError(key+' must be a list of text values')
        if s['provider'] not in ['gemini','openai','claude','deepseek','ollama','local_trained']:raise ValueError('Unknown provider')
        if s['pdf_engine'] not in ('auto','tectonic','pdflatex','online','browser'):raise ValueError('Unknown PDF engine')
        if not isinstance(s['model'],str):raise ValueError('Model must be text')
        if type(s['deadline_warning_days']) is not int or not 1<=s['deadline_warning_days']<=365:raise ValueError('Deadline warning must be 1–365 days')
        from .search_plan import validate_targets
        validate_targets(s['search_targets'],s['sources'])
        for key,ceiling in [('max_pages',500),('search_result_limit',1000)]:
            if type(s[key]) is not int or not 1<=s[key]<=ceiling:raise ValueError(key+' must be between 1 and '+str(ceiling))
        s['minimum_salary']=max(0,float(s['minimum_salary']))
        for n,v in s['sources'].items():
            if n not in SOURCES:raise ValueError('Unknown source: '+n)
            if type(v.get('enabled')) is not bool:raise ValueError('Source enabled must be boolean')
            v['limit']=max(1,min(100,int(v.get('limit',8)))); canonical_url(v['url'])
        if not isinstance(s['profile'],dict):raise ValueError('Profile must be an object')
        if not isinstance(s['document_categories'],list) or not s['document_categories'] or len(s['document_categories'])>40:raise ValueError('Choose 1–40 document categories')
        for cat in s['document_categories']:
            if not isinstance(cat,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9 _-]{0,59}',cat):raise ValueError('Document type names must use letters, numbers, spaces, underscores or dashes')
            s['rules'].setdefault(cat,{'dos':'Use verified profile facts','donts':'Do not invent qualifications','prompt':''})
        if s['discovery_mode'] not in ['auto','api','web']:raise ValueError('Invalid discovery mode')
        if s['storage'].get('mode') not in ['local','folder','google_drive','onedrive']:raise ValueError('Invalid storage mode')
        if type(s['storage'].get('auto_sync')) is not bool:raise ValueError('auto_sync must be boolean')
        for cat in s['document_categories']:
            rule=s['rules'].get(cat)
            if not isinstance(rule,dict) or not all(isinstance(rule.get(k,''),str) for k in ['dos','donts','prompt']):raise ValueError('Invalid category rules')
        if connection is not None:connection.execute("UPDATE kv SET value=? WHERE key='settings'",(json.dumps(s),))
        else:
            with self.db() as c:c.execute("UPDATE kv SET value=? WHERE key='settings'",(json.dumps(s),))
        return s
    def key(self,provider):
        p=self.root/'credentials.json'
        return json.loads(p.read_text()).get(provider,'') if p.exists() else ''
    def save_key(self,provider,key):
        if provider not in ['gemini','openai','claude','deepseek','ollama','local_trained','college_scorecard']:raise ValueError('Unknown provider')
        with self.lock:
            p=self.root/'credentials.json'; d=json.loads(p.read_text()) if p.exists() else {}
            d[provider]=key.strip(); tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(d)); os.chmod(tmp,0o600); tmp.replace(p)
    def jobs(self):
        with self.db() as c:return [dict(json.loads(r['data']),id=r['id'],status=r['status'],created=r['created'],updated=r['updated']) for r in c.execute('SELECT * FROM jobs WHERE id NOT IN (SELECT job_id FROM deleted_jobs) ORDER BY updated DESC')]
    def job(self,jid):
        with self.db() as c:
            r=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not r:raise ValueError('Job not found')
            data=dict(json.loads(r['data']),id=jid,status=r['status'],created=r['created'],updated=r['updated'])
            data['source_urls']=[x[0] for x in c.execute('SELECT url FROM aliases WHERE job_id=?',(jid,))]
            return data
    def event(self,c,jid,kind,data):c.execute('INSERT INTO events VALUES(?,?,?,?,?)',(uid(),jid,kind,json.dumps(data),now()))
    def add_job(self,data,status='New',migration=False,preserve_existing=False):
        data=copy.deepcopy(data)
        from .workbench import validate_job
        validate_job(data)
        from .universities import ensure_university
        if status not in STATUSES:status='New'
        if not data.get('title','').strip() or not data.get('institution','').strip():raise ValueError('Institution and title are required')
        data.setdefault('url','https://manual-entry.local/'+uid())
        urls=identity_urls(data)
        with self.lock,self.db() as c:
            matches=c.execute('SELECT DISTINCT job_id FROM aliases WHERE url IN ('+','.join('?' for _ in urls)+')',tuple(urls)).fetchall()
            if len(matches)>1:raise ValueError('Source URLs connect multiple existing jobs; review and merge them first')
            existing=matches[0] if matches else None
            if existing:
                jid=existing[0]; old=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
                if c.execute('SELECT 1 FROM deleted_jobs WHERE job_id=?',(jid,)).fetchone():return jid,'ignored'
                if preserve_existing:return jid,'ignored' if old['status']=='Ignored' else 'duplicate'
                merged=json.loads(old['data'])
                for k,v in data.items():
                    if v and not merged.get(k):merged[k]=v
                if not migration:
                    for k in ['deadline','salary','salary_min','currency','description','location','country','region','job_type','required_documents']:
                        if data.get(k) not in (None,'',[]) and k not in merged.get('manual_overrides',[]):merged[k]=data[k]
                sources=set(merged.get('sources',[])); sources.update(data.get('sources',[])); merged['sources']=sorted(sources)
                merged['last_seen']=now()
                # An ignore survives refresh/import. Preserve all legacy statuses in event history.
                newstatus='Ignored' if old['status']=='Ignored' or status=='Ignored' else old['status']
                if migration and newstatus!='Ignored':
                    rank={v:i for i,v in enumerate(STATUSES)}
                    newstatus=max((old['status'],status),key=lambda x:rank.get(x,0))
                c.execute('UPDATE jobs SET data=?,status=?,updated=? WHERE id=?',(json.dumps(merged),newstatus,now(),jid))
                for u in urls:c.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)',(u,jid))
                ensure_university(c,merged)
                self.event(c,jid,'source_seen',data)
                return jid,'ignored' if newstatus=='Ignored' else 'duplicate'
            jid=uid(); data.setdefault('sources',['Manual']); data['last_seen']=now()
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',(jid,json.dumps(data),status,now(),now()))
            for u in urls:c.execute('INSERT INTO aliases VALUES(?,?)',(u,jid))
            ensure_university(c,data)
            self.event(c,jid,'created',data)
            return jid,'added'
    def edit_job(self,jid,patch):
        from .packages import active_job
        active_job(self,jid)
        from .workbench import validate_job
        validate_job(patch)
        old=self.job(jid); status=patch.pop('status',old['status'])
        if status not in STATUSES:raise ValueError('Invalid status')
        allowed={'title','institution','department','location','country','region','salary','salary_min','currency','deadline','description','url','apply_url','notes','next_action','contacts','job_type','required_documents','offer','role_type','tenure_status','application_portal','career_track','work_mode','seniority','salary_period','next_action_due','priority'}
        from .universities import ensure_university,ROLES,TENURES,PORTALS
        for field,choices in [('role_type',ROLES),('tenure_status',TENURES),('application_portal',PORTALS)]:
            if field in patch and patch[field] and patch[field] not in choices:raise ValueError('Invalid '+field)
        if set(patch)-allowed:raise ValueError('Invalid job fields')
        if not isinstance(patch.get('offer',{}),dict):raise ValueError('Offer must be an object')
        with self.lock,self.db() as c:
            if patch.get('url'):
                u=canonical_url(patch['url']); row=c.execute('SELECT job_id FROM aliases WHERE url=?',(u,)).fetchone()
                if row and row[0]!=jid:raise ValueError('URL belongs to another job; review and merge first')
                c.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)',(u,jid))
            data={k:v for k,v in old.items() if k not in ['id','status','created','updated','source_urls']};data.update(patch)
            data['manual_overrides']=sorted(set(data.get('manual_overrides',[]))|set(patch))
            if not data.get('title','').strip() or not data.get('institution','').strip():raise ValueError('Institution and title are required')
            for u in identity_urls(data):
                existing=c.execute('SELECT job_id FROM aliases WHERE url=?',(u,)).fetchone()
                if existing and existing[0]!=jid:raise ValueError('A posting/application URL belongs to another job; review and merge first')
                c.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)',(u,jid))
            ensure_university(c,data)
            self.event(c,jid,'edited',{'before':old,'patch':patch,'status':status})
            c.execute('UPDATE jobs SET data=?,status=?,updated=? WHERE id=?',(json.dumps(data),status,now(),jid))
        return self.job(jid)
    def candidates(self):
        from difflib import SequenceMatcher
        jobs=self.jobs(); out=[]
        for i,a in enumerate(jobs):
            for b in jobs[i+1:]:
                if SequenceMatcher(None,norm(a['institution']),norm(b['institution'])).ratio()>=.9 and SequenceMatcher(None,norm(a['title']),norm(b['title'])).ratio()>=.88:
                    out.append({'a':a['id'],'b':b['id'],'institution':a['institution'],'title_a':a['title'],'title_b':b['title'],'reason':'Similar employer/title; may be distinct requisitions. Review source URLs.'})
        return out
    def merge(self,keep,other):
        if keep==other:raise ValueError('Choose two different jobs')
        from .packages import active_job
        a,b=active_job(self,keep),active_job(self,other)
        old_folder=self.document_folder(other);new_folder=self.document_folder(keep)
        moved=[]
        try:
            with self.lock,self.db() as c:
                d=json.loads(c.execute('SELECT data FROM jobs WHERE id=?',(keep,)).fetchone()[0])
                for k,v in b.items():
                    if k not in ['id','status','source_urls','created','updated'] and not d.get(k):d[k]=v
                d['sources']=sorted(set(a.get('sources',[])+b.get('sources',[])))
                if b.get('notes') and b.get('notes')!=a.get('notes'):d['notes']=a.get('notes','')+'\nMerged notes: '+b['notes']
                status='Ignored' if 'Ignored' in (a['status'],b['status']) else a['status']
                self.event(c,keep,'merged',{'kept':a,'other':b})
                if old_folder.exists():
                    if any((new_folder/item.name).exists() for item in old_folder.iterdir()):raise ValueError('Conflicting document filenames; merge cancelled with originals retained.')
                    for item in old_folder.iterdir():
                        destination=new_folder/item.name;shutil.move(str(item),str(destination));moved.append((item,destination))
                    old_folder.rmdir()
                c.execute('UPDATE application_packages SET job_id=? WHERE job_id=?',(keep,other))
                c.execute('INSERT OR IGNORE INTO hidden_job_types SELECT ?,category,created FROM hidden_job_types WHERE job_id=?',(keep,other))
                c.execute('DELETE FROM hidden_job_types WHERE job_id=?',(other,))
                c.execute('DELETE FROM document_folders WHERE job_id=?',(other,))
                for table in ('application_requirements','career_feedback','role_criteria','journey_events'):c.execute('UPDATE '+table+' SET job_id=? WHERE job_id=?',(keep,other))
                c.execute('INSERT OR IGNORE INTO journey_links SELECT ?,contact_id FROM journey_links WHERE job_id=?',(keep,other))
                c.execute('DELETE FROM journey_links WHERE job_id=?',(other,))
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='mail_reviews'").fetchone():c.execute('UPDATE mail_reviews SET job_id=? WHERE job_id=?',(keep,other))
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='suite_items'").fetchone():
                    for row in c.execute('SELECT id,data FROM suite_items WHERE deleted=0').fetchall():
                        value=json.loads(row['data'])
                        if value.get('job_id')==other:
                            value['job_id']=keep;c.execute('UPDATE suite_items SET data=?,revision=revision+1,updated=? WHERE id=?',(json.dumps(value),now(),row['id']))
                c.execute('UPDATE posting_checks SET job_id=? WHERE job_id=?',(keep,other))
                c.execute('DELETE FROM posting_watch WHERE job_id IN (?,?)',(keep,other))
                c.execute('DELETE FROM checklist_reviews WHERE job_id IN (?,?)',(keep,other))
                c.execute('UPDATE aliases SET job_id=? WHERE job_id=?',(keep,other))
                for table in ['documents','sessions','events','legacy']:c.execute(f'UPDATE {table} SET job_id=? WHERE job_id=?',(keep,other))
                c.execute('UPDATE jobs SET data=?,status=?,updated=? WHERE id=?',(json.dumps(d),status,now(),keep));c.execute('DELETE FROM jobs WHERE id=?',(other,))
        except Exception:
            if moved:
                old_folder.mkdir(parents=True,exist_ok=True)
                for original,destination in reversed(moved):
                    if destination.exists():shutil.move(str(destination),str(original))
            raise
        return self.job(keep)
    def backup(self):
        folder=self.root/'backups';folder.mkdir(exist_ok=True);p=folder/('coach-'+uid()+'.db')
        with self.db() as c:
            dest=sqlite3.connect(p);c.backup(dest);dest.close()
        return p
    def migrate(self,path):
        path=Path(path).resolve()
        if path==self.path:raise ValueError('Cannot import the active database')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        src=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);src.row_factory=sqlite3.Row
        try:
            rows=[dict(r) for r in src.execute('SELECT * FROM jobs')]
            tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            black={r[0] for r in src.execute('SELECT url_hash FROM blacklist')} if 'blacklist' in tables else set()
        finally:src.close()
        with self.db() as c: imported=c.execute('SELECT count(*) FROM legacy WHERE source=?',(digest,)).fetchone()[0]
        if imported<len(rows):self.backup()
        report={'read':len(rows),'added':0,'duplicate':0,'ignored':0,'already_imported':0,'errors':[]}
        for i,r in enumerate(rows):
            key=str(r.get('job_id',r.get('id',i)))
            with self.db() as c:
                if c.execute('SELECT 1 FROM legacy WHERE source=? AND row_key=?',(digest,key)).fetchone():report['already_imported']+=1;continue
            try:
                d={'institution':r.get('institution') or r.get('university'),'title':r.get('position_title'), 'url':r.get('job_description_url') or r.get('url') or r.get('direct_apply_url') or ('https://manual-entry.local/legacy-'+digest[:12]+'-'+key), 'apply_url':r.get('direct_apply_url',''), 'description':r.get('raw_description') or r.get('description',''), 'sources':[r.get('source_board') or r.get('portal','Legacy')], 'location':r.get('location',''), 'department':r.get('department',''), 'salary':r.get('salary_range',''), 'deadline':r.get('deadline',''), 'notes':r.get('notes',''), 'country':r.get('country',''), 'region':r.get('state_region',''), 'required_documents':r.get('required_documents',''), 'job_type':r.get('position_category',''), 'legacy_record':r}
                status='Ignored' if r.get('url_hash') in black else r.get('application_status',r.get('status','New'))
                jid,result=self.add_job(d,status,migration=True);report[result]+=1
                with self.db() as c:c.execute('INSERT INTO legacy VALUES(?,?,?,?)',(digest,key,json.dumps(r),jid))
            except (ValueError,sqlite3.Error) as e:report['errors'].append({'row':key,'error':str(e)})
        return report
    def memory(self,approved_only=False):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT * FROM memory '+('WHERE approved=1 ' if approved_only else '')+'ORDER BY created DESC')]
    def add_memory(self,category,text,approved=False):
        if not text.strip():raise ValueError('Memory text is empty')
        mid=uid()
        with self.db() as c:c.execute('INSERT INTO memory VALUES(?,?,?,?,?)',(mid,category,text.strip(),int(approved),now()))
        return mid
    def export_csv(self):
        out=io.StringIO();w=csv.writer(out);fields=['id','status','institution','title','location','deadline','salary','url','notes','next_action']
        w.writerow(fields)
        for j in self.jobs():
            vals=[str(j.get(k,'') or '') for k in fields]
            w.writerow(["'"+v if v.startswith(('=','+','-','@')) else v for v in vals])
        return '\ufeff'+out.getvalue()
