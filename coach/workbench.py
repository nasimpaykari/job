"""Reviewed role targeting, interview evidence and local workspace diagnostics."""
import json,re,math
from datetime import date
from .core import uid,now,canonical_url
from .career import Career,plain,digest,text_field
from .packages import active_job

TRACKS=['Academic','Industry','Government / nonprofit','Unknown']
WORK_MODES=['Remote','Hybrid','On-site','Unknown']
LEVELS=['Internship','Entry level','Mid level','Senior','Lead / principal','Manager / director','Unknown']
PERIODS=['Annual','Monthly','Weekly','Hourly','Unknown']
ORGANIZATIONS=['University / college','Company','Government / nonprofit','Unknown']


def job_track(job):
    if job.get('career_track') in TRACKS:return job['career_track']
    name=str(job.get('institution',''));title=str(job.get('title',''))
    if re.search(r'\b(university|college|polytechnic)\b',name,re.I) or re.search(r'\b(professor|postdoc|postdoctoral|lecturer)\b',title,re.I):return 'Academic'
    if re.search(r'\b(inc\.?|llc|ltd\.?|corporation|corp\.?)\b',name,re.I):return 'Industry'
    return 'Unknown'


def organization_kind(job):
    return {'Academic':'University / college','Industry':'Company','Government / nonprofit':'Government / nonprofit'}.get(job_track(job),'Unknown')


def validate_job(data):
    for field,choices in [('career_track',TRACKS),('work_mode',WORK_MODES),('seniority',LEVELS),('salary_period',PERIODS),('priority',['High','Normal','Low'])]:
        if field in data and data[field] not in choices+['']:raise ValueError('Choose a valid '+field.replace('_',' ')+'.')
    for key in ('url','apply_url'):
        if data.get(key):canonical_url(data[key])
    if data.get('next_action_due'):
        try:
            value=data['next_action_due'];assert re.fullmatch(r'\d{4}-\d{2}-\d{2}',value);date.fromisoformat(value)
        except (ValueError,TypeError,AssertionError):raise ValueError('Next action date must be YYYY-MM-DD.') from None
    if data.get('salary_min') is not None:
        value=data['salary_min']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('Salary floor must be a finite, nonnegative number.')
    for key,limit in [('title',500),('institution',500),('next_action',1000),('next_action_due',10),('notes',30000),('description',200000)]:
        if key in data and (not isinstance(data[key],str) or len(data[key])>limit):raise ValueError('Invalid '+key.replace('_',' ')+'.')


def initialize(store):
    with store.db() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='role_criteria'").fetchone()
        populated=c.execute('SELECT 1 FROM jobs LIMIT 1').fetchone()
    if not exists and populated:store.backup()
    with store.db() as c:c.executescript('''
    CREATE TABLE IF NOT EXISTS role_criteria(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS interview_stories(id TEXT PRIMARY KEY,data TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
    ''')


class Workbench:
    def __init__(self,store):self.store=store
    def facts(self,ids):
        if not isinstance(ids,list) or len(ids)>20 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise ValueError('Select up to 20 unique verified facts.')
        facts={f['id']:f for f in Career(self.store).evidence()}
        if any(i not in facts for i in ids):raise ValueError('Selected facts must still be verified in this workspace.')
        return {i:digest(facts[i]['text']) for i in ids}
    def is_stale(self,data,facts):return any(i not in facts or digest(facts[i]['text'])!=h for i,h in data.get('fact_hashes',{}).items())
    def criteria(self,jid):
        active_job(self.store,jid);career=Career(self.store);posting=career.posting_hash(jid);facts={f['id']:f for f in career.evidence()}
        with self.store.db() as c:rows=c.execute('SELECT * FROM role_criteria WHERE job_id=? ORDER BY created',(jid,)).fetchall()
        result=[]
        for row in rows:
            d=dict(json.loads(row['data']),id=row['id']);d['stale']=d['posting_hash']!=posting or self.is_stale(d,facts)
            d['facts']=[{'id':i,'text':facts[i]['text']} for i in d['fact_hashes'] if i in facts];result.append(d)
        return {'criteria':result,'facts':list(facts.values()),'posting_hash':posting,'note':'This is your evidence review, not an ATS score, eligibility decision or hiring probability.'}
    def save_criterion(self,jid,data,cid=''):
        with self.store.lock:
            job=active_job(self.store,jid);label=text_field(data,'label',500);quote=text_field(data,'quote',4000,False)
            if quote and quote not in plain(job.get('description','')):raise ValueError('The posting excerpt must be copied exactly from the saved description.')
            priority=data.get('importance','Required');status=data.get('status','Unknown')
            if priority not in ('Required','Preferred') or status not in ('Supported','Partial','Gap','Unknown'):raise ValueError('Invalid criterion importance or evidence status.')
            hashes=self.facts(data.get('fact_ids',[]))
            if status in ('Supported','Partial') and not hashes:raise ValueError('Link verified facts for a Supported or Partial assessment.')
            value={'label':label,'quote':quote,'importance':priority,'status':status,'notes':text_field(data,'notes',4000,False),'reviewed':data.get('reviewed') is True,'fact_hashes':hashes,'posting_hash':Career(self.store).posting_hash(jid)}
            with self.store.db() as c:
                if cid and not c.execute('SELECT 1 FROM role_criteria WHERE id=? AND job_id=?',(cid,jid)).fetchone():raise ValueError('Criterion not found for this job.')
                if cid:c.execute('UPDATE role_criteria SET data=?,updated=? WHERE id=?',(json.dumps(value),now(),cid))
                else:
                    if c.execute('SELECT count(*) FROM role_criteria WHERE job_id=?',(jid,)).fetchone()[0]>=100:raise ValueError('Keep up to 100 criteria per job.')
                    cid=uid();c.execute('INSERT INTO role_criteria VALUES(?,?,?,?,?)',(cid,jid,json.dumps(value),now(),now()))
                self.store.event(c,jid,'role_criterion_saved',{'id':cid,'reviewed':value['reviewed']})
            return {'id':cid}
    def delete_criterion(self,jid,cid):
        active_job(self.store,jid)
        with self.store.lock,self.store.db() as c:
            row=c.execute('SELECT * FROM role_criteria WHERE id=? AND job_id=?',(cid,jid)).fetchone()
            if not row:raise ValueError('Criterion not found for this job.')
            self.store.event(c,jid,'role_criterion_removed',dict(row));c.execute('DELETE FROM role_criteria WHERE id=?',(cid,))
    def suggest(self,jid):
        job=active_job(self.store,jid);text=plain(job.get('description',''));count=0
        existing={r['quote'] for r in self.criteria(jid)['criteria']}
        for line in re.split(r'\n+|(?<=[.!?])\s+',text):
            line=line.strip()
            if 15<len(line)<=700 and re.search(r'\b(required|must have|experience with|proficien|preferred|qualifications|ability to)\b',line,re.I) and line not in existing:
                self.save_criterion(jid,{'label':line[:500],'quote':line,'importance':'Preferred' if re.search(r'preferred|nice to have',line,re.I) else 'Required'})
                count+=1;existing.add(line)
                if count==12:break
        return {'added':count,'message':'Suggestions need your review; no candidate qualifications were inferred.'}
    def stories(self):
        facts={f['id']:f for f in Career(self.store).evidence()}
        with self.store.db() as c:rows=c.execute('SELECT * FROM interview_stories ORDER BY updated DESC').fetchall()
        return [dict(json.loads(r['data']),id=r['id'],stale=self.is_stale(json.loads(r['data']),facts)) for r in rows]
    def save_story(self,data,sid=''):
        with self.store.lock:
            value={k:text_field(data,k,200 if k in ('title','competency') else 4000,k=='title' or data.get('verified') is True) for k in ('title','competency','situation','task','action','result')}
            value['fact_hashes']=self.facts(data.get('fact_ids',[]));value['verified']=data.get('verified') is True
            if value['verified'] and not value['fact_hashes']:raise ValueError('Link verified facts before approving an interview story.')
            with self.store.db() as c:
                if sid and not c.execute('SELECT 1 FROM interview_stories WHERE id=?',(sid,)).fetchone():raise ValueError('Story not found in this workspace.')
                if sid:c.execute('UPDATE interview_stories SET data=?,updated=? WHERE id=?',(json.dumps(value),now(),sid))
                else:
                    if c.execute('SELECT count(*) FROM interview_stories').fetchone()[0]>=200:raise ValueError('Keep up to 200 stories.')
                    sid=uid();c.execute('INSERT INTO interview_stories VALUES(?,?,?,?)',(sid,json.dumps(value),now(),now()))
                self.store.event(c,'','interview_story_saved',{'id':sid,'verified':value['verified']})
            return {'id':sid}
    def delete_story(self,sid):
        with self.store.lock,self.store.db() as c:
            row=c.execute('SELECT * FROM interview_stories WHERE id=?',(sid,)).fetchone()
            if not row:raise ValueError('Story not found in this workspace.')
            self.store.event(c,'','interview_story_removed',dict(row));c.execute('DELETE FROM interview_stories WHERE id=?',(sid,))
    def context(self,jid,category):
        criteria=[{k:r[k] for k in ('label','importance','status','notes','facts')} for r in self.criteria(jid)['criteria'] if r['reviewed'] and not r['stale']] if jid else []
        stories=[{k:s[k] for k in ('title','competency','situation','task','action','result')} for s in self.stories() if s['verified'] and not s['stale']] if category=='interview' else []
        return {'reviewed_role_criteria':criteria,'reviewed_interview_stories':stories[:20],'instruction':'Criteria are job needs, not candidate facts. Gaps remain gaps. Stories are user-reviewed examples; do not add outcomes, numbers or qualifications.'}
    def health(self):
        with self.store.lock,self.store.db() as c:
            integrity=[r[0] for r in c.execute('PRAGMA quick_check')]
            rows=c.execute('SELECT id,job_id,category FROM documents WHERE approved=1').fetchall()
            packages=c.execute('SELECT id FROM application_packages').fetchall()
            sync=c.execute("SELECT created FROM events WHERE kind='storage_sync' ORDER BY created DESC LIMIT 1").fetchone()
        missing=[]
        for r in rows[:1000]:
            folder=self.store.root/'document_versions'/r['id']
            if any(not (folder/('document.'+e)).is_file() for e in ('pdf','tex')):missing.append({'id':r['id'],'category':r['category'],'job_id':r['job_id']})
        missing_packages=[r['id'] for r in packages[:1000] if not (self.store.root/'application_packages'/(r['id']+'.zip')).is_file()]
        return {'database_ok':integrity==['ok'],'approved_documents':len(rows),'documents_checked':min(len(rows),1000),'missing_documents':missing,'packages':len(packages),'packages_checked':min(len(packages),1000),'missing_packages':missing_packages,'last_backup_sync':sync[0] if sync else None,'verified_facts':len(Career(self.store).evidence()),'note':'Read-only local check. Up to 1,000 documents and packages are checked for presence; this does not verify PDF rendering, archive contents, cloud availability or recovery. Keep a separate full backup.'}
