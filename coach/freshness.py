"""Reviewable posting snapshots. Website evidence never changes application status."""
import hashlib,json,re
from difflib import unified_diff
from bs4 import BeautifulSoup
from .core import now,uid,norm,canonical_url
from .deadlines import deadline_date
from .services import fetch,HTTPFailure

LIMIT=60000

def initialize(store):
    with store.db() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='posting_checks'").fetchone()
        populated=c.execute('SELECT 1 FROM jobs LIMIT 1').fetchone()
    if not exists and populated:store.backup()
    with store.db() as c:c.executescript('''
        CREATE TABLE IF NOT EXISTS posting_checks(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,created TEXT NOT NULL,data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS posting_checks_job ON posting_checks(job_id,created);
        CREATE TABLE IF NOT EXISTS posting_watch(job_id TEXT PRIMARY KEY,baseline_id TEXT,current_id TEXT,last_id TEXT);
    ''')

def compact(text):return re.sub(r'\s+',' ',str(text)).strip()
def digest(text):return hashlib.sha256(text.encode()).hexdigest()
def source_url(job):return job.get('url') or job.get('apply_url') or ''
def signature(job):return digest(json.dumps({k:job.get(k,'') for k in ('url','apply_url','title','description','deadline')},sort_keys=True))

def extract(html,job):
    soup=BeautifulSoup(html,'html.parser')
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    postings=[]
    def walk(obj):
        if isinstance(obj,list):
            for item in obj:walk(item)
        elif isinstance(obj,dict):
            kind=obj.get('@type',[])
            if kind=='JobPosting' or isinstance(kind,list) and 'JobPosting' in kind:postings.append(obj)
            if '@graph' in obj:walk(obj['@graph'])
    for script in soup.select('script[type="application/ld+json"]'):
        try:walk(json.loads(script.string or script.get_text()))
        except (ValueError,TypeError):pass
    matches=[p for p in postings if norm(p.get('title',''))==norm(job['title'])]
    chosen=matches[0] if len(matches)==1 else None
    for el in soup.select('script,style,noscript,nav,footer,header,aside'):el.decompose()
    main=soup.find('main') or soup.find('article') or soup.body or soup
    lines=[compact(x) for x in main.get_text('\n',strip=True).splitlines() if compact(x)]
    visible='\n'.join(lines)
    blocked=re.search(r'verify (?:that )?you are human|checking your browser|access denied|captcha|sign in to (?:view|continue)|enable javascript',title+'\n'+visible,re.I)
    if blocked:return {'state':'unverified','message':'Access challenge, sign-in page, or JavaScript-only content. No closure inferred.'}
    closed=re.search(r'(?:this|the) (?:job|position|vacancy|posting|requisition) (?:is |has been )?(?:now )?(?:closed|filled|no longer available)|(?:no longer|not currently) accepting applications',visible,re.I)
    if closed and len(visible)<LIMIT:
        return {'state':'possibly_closed','message':'Page contains a closure notice; verify before changing your application status.','closure_evidence':closed.group(0)}
    if chosen:
        description=BeautifulSoup(str(chosen.get('description','')),'html.parser').get_text('\n',strip=True)
        text='\n'.join([compact(chosen.get('title','')),description])
        raw_deadline=str(chosen.get('validThrough',''))
        deadline=raw_deadline if deadline_date(raw_deadline)['kind']=='dated' else ''
    else:
        text=visible;description=visible;deadline=''
        # Do not use search-result pages or unrelated redirects as a new baseline.
        if postings or not norm(job['title']) or norm(job['title']) not in norm(title+' '+visible):
            return {'state':'unverified','message':'Could not identify this specific job on the page. Check its URL or enable browser rendering.'}
    if len(text)<100 or len(text)>LIMIT:
        return {'state':'unverified','message':'Posting text is too short or too large to compare reliably.'}
    req=[line for line in text.splitlines() if re.search(r'deadline|valid through|closing date|review begins|cover letter|curriculum vitae|resume|résumé|statement|references|application materials|page limit|pages?\b',line,re.I)]
    return {'state':'available','message':'Posting text retrieved. Availability is not a guarantee that applications are still accepted.',
            'text':text,'description':description,'hash':digest(compact(text)+'\n'+deadline),'deadline':deadline,
            'signals':req[:30],'method':'JobPosting structured data' if chosen else 'Visible page text'}

class Freshness:
    def __init__(self,store):self.store=store
    def record(self,c,key):
        r=c.execute('SELECT * FROM posting_checks WHERE id=?',(key,)).fetchone() if key else None
        return dict(json.loads(r['data']),id=r['id'],checked_at=r['created']) if r else None
    def detail(self,jid):
        job=self.store.job(jid)
        with self.store.db() as c:
            w=c.execute('SELECT * FROM posting_watch WHERE job_id=?',(jid,)).fetchone()
            latest=self.record(c,w['last_id']) if w else None
            current=self.record(c,w['current_id']) if w else None
            baseline=self.record(c,w['baseline_id']) if w else None
            history=[dict(id=r['id'],checked_at=r['created'],**{k:v for k,v in json.loads(r['data']).items() if k in ('state','message','url','final_url')}) for r in c.execute('SELECT * FROM posting_checks WHERE job_id=? ORDER BY rowid DESC LIMIT 20',(jid,))]
        pending=bool(current and baseline and current['hash']!=baseline['hash'])
        source_changed=bool(latest and latest['url']!=source_url(job))
        diff=[]
        if current and baseline and pending:
            diff=list(unified_diff(baseline['text'].splitlines(),current['text'].splitlines(),fromfile='Reviewed posting',tofile='Latest posting',lineterm=''))[:400]
        return {'latest':latest,'current':current,'baseline':baseline,'pending':pending,'deadline_changed':bool(current and baseline and current.get('deadline')!=baseline.get('deadline')),'requirements_changed':bool(current and baseline and current.get('signals')!=baseline.get('signals')),'source_changed':source_changed,'diff':'\n'.join(diff),'history':history,'job_signature':signature(job),'saved_deadline':job.get('deadline','')}
    def summaries(self):
        with self.store.db() as c:
            rows=c.execute('SELECT w.*,l.created,l.data AS latest,b.data AS baseline,p.data AS current FROM posting_watch w LEFT JOIN posting_checks l ON l.id=w.last_id LEFT JOIN posting_checks b ON b.id=w.baseline_id LEFT JOIN posting_checks p ON p.id=w.current_id').fetchall()
        result={}
        for r in rows:
            l=json.loads(r['latest'] or '{}');b=json.loads(r['baseline'] or '{}');p=json.loads(r['current'] or '{}')
            result[r['job_id']]={'state':l.get('state','never'),'checked_at':r['created'],'url':l.get('url',''),'pending':bool(b and p and b.get('hash')!=p.get('hash')),'message':l.get('message',''),'deadline_changed':bool(b and p and b.get('deadline')!=p.get('deadline')),'requirements_changed':bool(b and p and b.get('signals')!=p.get('signals'))}
        return result
    def check(self,jid,log=lambda message:None,checkpoint=lambda:None):
        checkpoint()
        job=self.store.job(jid);url=source_url(job)
        if not url:raise ValueError('Add the specific posting URL first.')
        if not self.store.settings()['network_enabled']:raise ValueError('Enable network access in Profile & settings first.')
        log('Checking '+job['title'])
        try:
            html,final=fetch(url,self.store.settings());data=extract(html,job);data['final_url']=final
            # A move to a generic URL cannot be treated as proof of closure.
            if canonical_url(final)!=canonical_url(url) and data['state']=='possibly_closed':
                data.update(state='unverified',message='Redirected page has a closure notice. Verify the original posting manually.')
        except HTTPFailure as e:
            data={'state':'missing' if e.status in (404,410) else 'unverified' if e.status in (401,403,429) else 'failed',
                  'message':f'HTTP {e.status}. '+('Posting URL is missing; this does not prove the position is closed.' if e.status in (404,410) else 'Website could not be checked. Previous evidence retained.')}
        except Exception:
            data={'state':'failed','message':'Could not retrieve the posting (connection, rendering, or URL validation failure). Previous evidence retained.'}
        checkpoint()
        from .packages import active_job
        active_job(self.store,jid)
        data['url']=url;data['job_signature']=signature(job)
        with self.store.lock,self.store.db() as c:
            # An edit/merge while the network request runs must not attach stale evidence.
            current_job=self.store.job(jid)
            if signature(current_job)!=signature(job):raise ValueError('Job changed during the check. Please check it again.')
            key=uid();c.execute('INSERT INTO posting_checks VALUES(?,?,?,?)',(key,jid,now(),json.dumps(data)))
            w=c.execute('SELECT * FROM posting_watch WHERE job_id=?',(jid,)).fetchone()
            baseline=w['baseline_id'] if w else None;current=w['current_id'] if w else None
            if data['state']=='available':
                current=key
                old=self.record(c,baseline)
                if not old or old['url']!=url:baseline=key
            c.execute('INSERT OR REPLACE INTO posting_watch VALUES(?,?,?,?)',(jid,baseline,current,key))
            self.store.event(c,jid,'posting_checked',{'check_id':key,'state':data['state']})
        log(data['message']);return {'check_id':key,'state':data['state'],'message':data['message']}
    def review(self,jid,payload):
        with self.store.lock:
            detail=self.detail(jid);current=detail['current'];job=self.store.job(jid)
            if not current or current['id']!=payload.get('check_id'):raise ValueError('Posting changed since you opened it. Reopen the review.')
            if signature(job)!=payload.get('job_signature') or current['url']!=source_url(job):raise ValueError('Job was edited. Reopen the review or check its new URL.')
            if detail['latest']['id']!=current['id']:raise ValueError('The latest check was unsuccessful. Check again before accepting older evidence.')
            patch={}
            if payload.get('apply_description') is True:patch['description']=current['description']
            if payload.get('apply_deadline') is True:
                if not current.get('deadline'):raise ValueError('No unambiguous structured deadline was found. Edit the deadline manually.')
                patch['deadline']=current['deadline']
            if patch:self.store.edit_job(jid,patch)
            with self.store.db() as c:
                c.execute('UPDATE posting_watch SET baseline_id=current_id WHERE job_id=?',(jid,))
                self.store.event(c,jid,'posting_reviewed',{'check_id':current['id'],'applied_fields':list(patch)})
        return self.detail(jid)
