"""Evidence, posting requirements, document checks and scoped user feedback.

No model calls are needed for these features. Extraction is intentionally
conservative and advisory; human verification is distinct from extraction.
"""
import hashlib,json,re
from pathlib import Path
from bs4 import BeautifulSoup
from .core import uid,now
from .preview import readable_text

FACT_CATEGORIES=['general','education','employment','research','teaching','skills','publication','achievement']
KINDS=['document','reference','deadline','instruction']

def digest(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()
def plain(text):
    if '\\documentclass' in text or '\\begin{document}' in text:return readable_text(text)
    return BeautifulSoup(text,'html.parser').get_text(' ',strip=True) if re.search(r'<(?:p|div|li|br|html)\b',text,re.I) else text

def text_field(d,key,limit=4000,required=True):
    v=d.get(key,'')
    if not isinstance(v,str) or len(v)>limit or (required and not v.strip()):raise ValueError('Enter valid '+key.replace('_',' ')+' (up to '+str(limit)+' characters).')
    return v.strip()

def initialize(store):
    with store.db() as c:exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='career_sources'").fetchone()
    if not exists:
        with store.db() as c:populated=c.execute('SELECT count(*) FROM jobs').fetchone()[0] or c.execute('SELECT count(*) FROM documents').fetchone()[0]
        if populated:store.backup()
    with store.db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS career_sources(id TEXT PRIMARY KEY,label TEXT NOT NULL,content TEXT NOT NULL,sha256 TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS career_facts(id TEXT PRIMARY KEY,text TEXT NOT NULL,category TEXT NOT NULL,source_id TEXT NOT NULL,quote TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS application_requirements(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,label TEXT NOT NULL,kind TEXT NOT NULL,category TEXT NOT NULL,required INTEGER NOT NULL,page_limit INTEGER,status TEXT NOT NULL,evidence TEXT NOT NULL,posting_hash TEXT NOT NULL,origin TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checklist_reviews(job_id TEXT PRIMARY KEY,posting_hash TEXT NOT NULL,reviewed TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS document_checks(document_id TEXT PRIMARY KEY,report TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS career_feedback(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,category TEXT NOT NULL,kind TEXT NOT NULL,scope TEXT NOT NULL,text TEXT NOT NULL,before_text TEXT NOT NULL,after_text TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
        ''')
        seeded=c.execute("SELECT 1 FROM kv WHERE key='career_sources_seeded'").fetchone()
        if not seeded:
            profile=store.settings()['profile'];content=str(profile.get('facts',''))
            if len(content)>100:
                c.execute('INSERT INTO career_sources VALUES(?,?,?,?,?)',(uid(),'Imported profile snapshot (review before verifying facts)',content,digest(content),now()))
            c.execute("INSERT INTO kv VALUES('career_sources_seeded','true')")

class Career:
    def __init__(self,store):self.store=store
    def state(self):
        with self.store.db() as c:
            sources=[dict(r) for r in c.execute('SELECT id,label,sha256,created,length(content) AS characters FROM career_sources ORDER BY created DESC')]
            facts=[dict(r) for r in c.execute('SELECT * FROM career_facts ORDER BY updated DESC')]
            feedback=[dict(r) for r in c.execute('SELECT * FROM career_feedback ORDER BY created DESC LIMIT 100')]
        return {'sources':sources,'facts':facts,'feedback':feedback,'categories':FACT_CATEGORIES,'verified_count':sum(f['status']=='verified' for f in facts)}
    def source(self,sid):
        with self.store.db() as c:r=c.execute('SELECT * FROM career_sources WHERE id=?',(sid,)).fetchone()
        if not r:raise ValueError('Source not found in this workspace')
        return dict(r)
    def add_source(self,d):
        label=text_field(d,'label',160);content=text_field(d,'content',200000);sid=uid()
        with self.store.db() as c:
            old=c.execute('SELECT id FROM career_sources WHERE sha256=?',(digest(content),)).fetchone()
            if old:return {'id':old[0],'existing':True}
            c.execute('INSERT INTO career_sources VALUES(?,?,?,?,?)',(sid,label,content,digest(content),now()))
        return {'id':sid,'existing':False}
    def save_fact(self,d,fid=None):
        text=text_field(d,'text',2000);quote=text_field(d,'quote',6000);sid=text_field(d,'source_id',80)
        category=d.get('category','general');status=d.get('status','pending')
        if category not in FACT_CATEGORIES or status not in ('pending','verified','retired'):raise ValueError('Invalid fact category or status')
        source=self.source(sid)
        if quote not in source['content'] and quote not in plain(source['content']):raise ValueError('Evidence quote must be an exact excerpt from this source or its readable text.')
        with self.store.db() as c:
            old=c.execute('SELECT * FROM career_facts WHERE id=?',(fid,)).fetchone() if fid else None
            if fid and not old:raise ValueError('Fact not found')
            if old and any(old[k]!=v for k,v in [('text',text),('source_id',sid),('quote',quote),('category',category)]):status='pending'
            if old:c.execute('UPDATE career_facts SET text=?,category=?,source_id=?,quote=?,status=?,updated=? WHERE id=?',(text,category,sid,quote,status,now(),fid))
            else:
                fid=uid();c.execute('INSERT INTO career_facts VALUES(?,?,?,?,?,?,?,?)',(fid,text,category,sid,quote,status,now(),now()))
            self.store.event(c,'','career_fact',{'id':fid,'status':status,'before':dict(old) if old else None})
        return {'id':fid,'status':status}
    def suggest_facts(self,sid):
        source=self.source(sid);text=plain(source['content'])
        chunks=re.split(r'\n+|(?<=[.!?])\s+(?=[A-Z])',text)
        count=0
        with self.store.db() as c:known={r[0] for r in c.execute('SELECT quote FROM career_facts WHERE source_id=?',(sid,))}
        for chunk in chunks:
            quote=chunk.strip();candidate=quote.lstrip('•- ').strip()
            if not 35<=len(candidate)<=900 or quote in known:continue
            self.save_fact({'text':candidate,'quote':quote,'source_id':sid,'category':'general','status':'pending'});known.add(quote);count+=1
            if count>=100:break
        return {'added':count,'message':'Suggestions are unverified excerpts. Edit them into individual facts and verify their meaning before use.'}
    def evidence(self):
        with self.store.db() as c:return [dict(r) for r in c.execute("SELECT f.id,f.text,f.category,f.quote,s.label AS source,s.sha256 FROM career_facts f JOIN career_sources s ON s.id=f.source_id WHERE f.status='verified' ORDER BY f.created")]
    def posting_hash(self,jid):
        j=self.store.job(jid)
        return digest(json.dumps({k:j.get(k,'') for k in ('description','deadline','url','apply_url','institution','title')},sort_keys=True))
    def requirements(self,jid):
        job=self.store.job(jid);current=self.posting_hash(jid)
        with self.store.db() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM application_requirements WHERE job_id=? ORDER BY created',(jid,))]
            review=c.execute('SELECT * FROM checklist_reviews WHERE job_id=?',(jid,)).fetchone()
            docs=[dict(r) for r in c.execute('SELECT id,category,created FROM documents WHERE job_id=? AND approved=1 ORDER BY created DESC',(jid,))]
        for r in rows:
            r['stale']=r['origin']=='posting' and r['posting_hash']!=current
            r['files']=[]
            if r['kind']=='document':
                for d in docs:
                    if d['category'].casefold()!=r['category'].casefold():continue
                    folder=self.store.document_version(d['id'])
                    if (folder/'document.pdf').exists() and (folder/'document.tex').exists():r['files'].append(d)
            r['complete']=r['status']=='waived' or (not r['stale'] and (r['status']=='done' or r['status']=='confirmed' and bool(r['files'])))
        unresolved=sum(not r['complete'] and bool(r['required']) and r['status']!='dismissed' for r in rows)
        reviewed=bool(review and review['posting_hash']==current)
        return {'job_id':jid,'requirements':rows,'reviewed':reviewed,'unresolved':unresolved,'ready':reviewed and unresolved==0,'note':'Readiness covers the reviewed checklist only. Verify the portal before submitting. Approved files may still need content review.'}
    def save_requirement(self,jid,d,rid=None,origin='manual'):
        self.store.job(jid);label=text_field(d,'label',500);kind=d.get('kind','document');category=d.get('category','');status=d.get('status','pending');required=d.get('required',True);limit=d.get('page_limit')
        if kind not in KINDS or status not in ('pending','confirmed','done','waived','dismissed') or type(required) is not bool:raise ValueError('Invalid requirement type, status or required flag')
        if kind=='document' and category not in self.store.settings()['document_categories']:raise ValueError('Choose an existing document type (add custom types in Studio first)')
        if kind!='document':category=''
        if limit is not None and (kind!='document' or type(limit) is not int or not 1<=limit<=100):raise ValueError('Page limits must be 1 to 100 for a document requirement')
        evidence=text_field(d,'evidence',6000,False);ph=self.posting_hash(jid)
        with self.store.db() as c:
            old=c.execute('SELECT * FROM application_requirements WHERE id=? AND job_id=?',(rid,jid)).fetchone() if rid else None
            if rid and not old:raise ValueError('Requirement not found in this job')
            if old:
                origin=old['origin']
                # Stale evidence is kept until the user explicitly re-extracts or supplies a manual requirement.
                if origin=='posting' and old['posting_hash']!=ph and status not in ('dismissed','waived'):raise ValueError('Posting changed. Dismiss the stale suggestion and extract current requirements again.')
                ph=old['posting_hash'] if origin=='posting' else ph
                c.execute('UPDATE application_requirements SET label=?,kind=?,category=?,required=?,page_limit=?,status=?,evidence=?,posting_hash=?,updated=? WHERE id=?',(label,kind,category,int(required),limit,status,evidence,ph,now(),rid))
            else:
                rid=uid();c.execute('INSERT INTO application_requirements VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,jid,label,kind,category,int(required),limit,status,evidence,ph,origin,now(),now()))
            c.execute('DELETE FROM checklist_reviews WHERE job_id=?',(jid,))
        return {'id':rid}
    def extract_requirements(self,jid):
        job=self.store.job(jid);body=plain(job.get('description',''));ph=self.posting_hash(jid);added=0
        patterns=[('CV',r'\b(?:curriculum vitae|CV)\b'),('Resume',r'\br[eé]sum[eé]\b'),('Cover',r'\bcover (?:letter|statement)\b'),('Research',r'\bresearch statement\b|\bstatement of research\b'),('Teaching',r'\bteaching statement\b|\bstatement of teaching\b'),('Diversity',r'\bdiversity statement\b|\bstatement of (?:diversity|contributions to diversity)\b'),('Mentor',r'\bmentoring statement\b|\bmentoring philosophy\b')]
        with self.store.db() as c:known={(r['kind'],r['category'],r['posting_hash']) for r in c.execute('SELECT * FROM application_requirements WHERE job_id=?',(jid,))}
        for category,pattern in patterns:
            m=re.search(pattern,body,re.I)
            if not m or ('document',category,ph) in known:continue
            a=max(body.rfind('\n',0,m.start()),body.rfind('. ',0,m.start()))+1
            ends=[x for x in (body.find('\n',m.end()),body.find('. ',m.end())) if x>=0];b=min(ends) if ends else len(body)
            quote=body[max(a,m.start()-200):min(b,m.end()+300)].strip()
            # Only bind a page limit if this excerpt names one document category.
            limit=None
            if sum(bool(re.search(p,quote,re.I)) for _,p in patterns)==1:
                pm=re.search(r'\b(\d{1,2}|one|two|three|four|five)\s*(?:-|to\s*)?\s*pages?\b',quote,re.I)
                if pm and not re.search(r'\b(?:minimum|at least)\b',quote,re.I):
                    val=pm[1].lower();limit=int(val) if val.isdigit() else {'one':1,'two':2,'three':3,'four':4,'five':5}[val]
                    if not 1<=limit<=100:limit=None
            self.save_requirement(jid,{'label':category+' document (review posting wording)','kind':'document','category':category,'required':not bool(re.search(r'\boptional\b',quote,re.I)),'evidence':quote,'page_limit':limit},origin='posting');added+=1
        m=re.search(r'[^.\n]{0,120}\b(?:letters? of reference|recommendation letters?|references|referees)\b[^.\n]{0,180}',body,re.I)
        if m and ('reference','',ph) not in known:
            self.save_requirement(jid,{'label':'References: review number, format and delivery instructions','kind':'reference','evidence':m[0].strip()},origin='posting');added+=1
        if job.get('deadline') and ('deadline','',ph) not in known:
            self.save_requirement(jid,{'label':'Verify deadline and timezone: '+str(job['deadline'])[:300],'kind':'deadline','evidence':str(job['deadline'])[:6000]},origin='posting');added+=1
        return {'added':added,'message':'Suggestions only. Confirm required versus optional, document type, page limits and portal instructions. Extraction may miss requirements.'}
    def review_checklist(self,jid):
        report=self.requirements(jid)
        if any(r['status']=='pending' or (r['stale'] and r['status'] not in ('dismissed','waived')) for r in report['requirements']):raise ValueError('Resolve pending and stale suggestions before marking the posting reviewed.')
        with self.store.db() as c:c.execute('INSERT OR REPLACE INTO checklist_reviews VALUES(?,?,?)',(jid,self.posting_hash(jid),now()))
        return self.requirements(jid)
    def add_feedback(self,d):
        jid=text_field(d,'job_id',80);self.store.job(jid);category=d.get('category','general')
        if category not in self.store.settings()['document_categories']+['general','interview','career','presentation']:raise ValueError('Invalid feedback category')
        kind=d.get('kind','unclassified');scope=d.get('scope','job')
        if kind not in ('unclassified','preference','correction','one_time') or scope not in ('job','category','global'):raise ValueError('Invalid feedback type or scope')
        text=text_field(d,'text',4000);before=text_field(d,'before_text',200000,False);after=text_field(d,'after_text',200000,False);fid=uid()
        with self.store.db() as c:c.execute('INSERT INTO career_feedback VALUES(?,?,?,?,?,?,?,?,?,?,?)',(fid,jid,category,kind,scope,text,before,after,'pending',now(),now()))
        return {'id':fid}
    def decide_feedback(self,fid,d):
        with self.store.db() as c:r=c.execute('SELECT * FROM career_feedback WHERE id=?',(fid,)).fetchone()
        if not r:raise ValueError('Feedback not found')
        if r['status']!='pending':raise ValueError('Feedback is already resolved; create a new correction instead.')
        status=d.get('status');kind=d.get('kind',r['kind']);scope=d.get('scope',r['scope']);text=text_field(d,'text',4000)
        if status not in ('approved','dismissed') or kind not in ('unclassified','preference','correction','one_time') or scope not in ('job','category','global'):raise ValueError('Invalid feedback decision')
        if status=='approved' and kind=='unclassified':raise ValueError('Choose what this edit means before approving it.')
        if status=='approved' and text.startswith('Review this edit:'):raise ValueError('Replace the review prompt with the specific fact, preference or one-time instruction.')
        if kind=='correction' and len(text)>2000:raise ValueError('Keep a factual correction under 2000 characters.')
        if kind in ('one_time','correction'):scope='job'
        with self.store.lock,self.store.db() as c:
            latest=c.execute('SELECT status FROM career_feedback WHERE id=?',(fid,)).fetchone()
            if not latest or latest[0]!='pending':raise ValueError('Feedback was already resolved.')
            # Transaction includes correction source and pending fact: no partial approval.
            if status=='approved' and kind=='correction':
                sid=uid();fact=uid();c.execute('INSERT INTO career_sources VALUES(?,?,?,?,?)',(sid,'User correction from feedback '+fid[:8],text,digest(text),now()))
                c.execute('INSERT INTO career_facts VALUES(?,?,?,?,?,?,?,?)',(fact,text,'general',sid,text,'pending',now(),now()))
            c.execute('UPDATE career_feedback SET text=?,kind=?,scope=?,status=?,updated=? WHERE id=?',(text,kind,scope,status,now(),fid))
        return {'message':'Correction saved as a pending fact. Verify it and retire any superseded facts.' if status=='approved' and kind=='correction' else 'Feedback '+status+'.'}
    def revoke_feedback(self,fid):
        with self.store.lock,self.store.db() as c:
            r=c.execute('SELECT * FROM career_feedback WHERE id=?',(fid,)).fetchone()
            if not r or r['status']!='approved' or r['kind'] not in ('preference','one_time'):raise ValueError('Only an approved preference or one-time instruction can be revoked here. Retire factual corrections in Career facts.')
            c.execute("UPDATE career_feedback SET status='dismissed',updated=? WHERE id=?",(now(),fid))
        return {'message':'Preference revoked. It will no longer be included in new generation requests.'}

    def preferences(self,jid,category):
        with self.store.db() as c:rows=[dict(r) for r in c.execute("SELECT id,text,kind,scope,job_id,category FROM career_feedback WHERE status='approved' AND kind IN ('preference','one_time') ORDER BY updated DESC")]
        return [r for r in rows if (r['scope']=='global' or r['scope']=='category' and r['category']==category or r['scope']=='job' and r['job_id']==jid and r['category'] in ('general',category))]


def check_document(store,jid,category,content,pdf_path=None,log_path=None,allow_archived=False):
    job=store.job(jid)
    if category not in store.settings()['document_categories']:
        with store.db() as c:archived=c.execute('SELECT 1 FROM deleted_document_types WHERE name=?',(category,)).fetchone()
        if not allow_archived or not archived:raise ValueError('Invalid document category')
    if not isinstance(content,str) or len(content)>200000:raise ValueError('Document must be text up to 200000 characters')
    career=Career(store);items=[]
    def add(code,severity,message):items.append({'code':code,'severity':severity,'message':message})
    clean=re.sub(r'(?<!\\)%[^\n]*','',content)
    body=clean.split(r'\begin{document}',1)[-1].split(r'\end{document}',1)[0];text=plain(content)
    if not content.strip() or r'\documentclass' not in clean or r'\begin{document}' not in clean or r'\end{document}' not in clean:add('latex_structure','error','Use a complete LaTeX document with documentclass and begin/end document.')
    else:add('latex_structure','pass','Complete LaTeX document structure found.')
    if re.search(r'\\placeholder\s*\{|\[(?:target |insert |university|department|position|your |name|date|company)[^\]]*\]|\b(?:TODO|TBD|PLACEHOLDER)\b',body,re.I):add('placeholders','error','Unresolved placeholder or TODO found in the document body.')
    else:add('placeholders','pass','No recognized placeholders found.')
    prose=re.sub(r'\\(?:href|url)\{[^}]*\}', '',body)
    if '--' in prose:add('hyphens','warning','Double or triple hyphens found. Review your document rules; use “to” for date ranges if needed.')
    evidence=career.evidence();mode=store.settings()['career_evidence_mode']
    authority='\n'.join(f['text'] for f in evidence) if mode=='verified_facts' else plain(str(store.settings()['profile'].get('facts','')))+'\n'+'\n'.join(f['text'] for f in evidence)
    if mode=='verified_facts' and not evidence:add('evidence','error','Verify at least one career fact or switch back to the existing profile mode.')
    else:add('evidence','info',('Verified facts only' if mode=='verified_facts' else 'Existing profile plus verified facts')+' is the factual context. Matching text does not prove a claim is supported.')
    if len(authority)>50:
        nums=set(re.findall(r'(?<![\w/])\d+(?:[,\.]\d+)*(?:\s*%)?',text));known=set(re.findall(r'(?<![\w/])\d+(?:[,\.]\d+)*(?:\s*%)?',authority))
        extra=sorted(x for x in nums-known if len(x)>1)[:12]
        if extra:add('numbers','warning','Numbers not found verbatim in the factual context: '+', '.join(extra)+'. Check whether they are dates, contact details, or unsupported metrics.')
    # Mentioning another institution may be legitimate employment or collaboration.
    with store.db() as c:institutions=[json.loads(r[0]).get('institution','') for r in c.execute('SELECT data FROM jobs')]
    other=sorted({n for n in institutions if len(n)>5 and n.casefold()!=job['institution'].casefold() and n.casefold() in text.casefold() and n.casefold() not in authority.casefold()})
    if other:add('institution','warning','Other institutions mentioned: '+', '.join(other[:5])+'. Verify that these references belong in this application.')
    if category.casefold()=='resume':
        if re.search(r'\\(?:begin\{(?:tabular|tabularx|multicols|paracol)|includegraphics|fancyhead|fancyfoot)\b',content):add('resume_layout','warning','Tables, columns, graphics or page headers may disrupt resume parsing. Prefer a single column with contact details in the main body.')
        if re.search(r'\\(?:tiny|scriptsize|footnotesize)\b|\\documentclass\[(?:8|9)pt',content):add('resume_font','warning','Small text may reduce readability. Shorten content before shrinking the font.')
        bullets=re.findall(r'\\item(?:\[[^]]*\])?\s*(.*?)(?=\\item|\\end\{itemize\}|$)',content,re.S)
        if any(len(plain(b).split())>25 for b in bullets):add('resume_bullets','warning','Some bullets exceed 25 words. Consider shortening them; wrapping still depends on the rendered layout.')
        if not re.search(r'\b(experience|employment|projects)\b',text,re.I):add('resume_sections','warning','No standard Experience or Projects heading detected. Check that your work history is easy to find.')
    req=career.requirements(jid);limits=[r['page_limit'] for r in req['requirements'] if r['kind']=='document' and r['category']==category and r['status'] in ('confirmed','done') and not r['stale'] and r['page_limit']]
    if category.casefold()=='resume':limits.append(1)
    limit=min(limits) if limits else None;pages=None
    if pdf_path is not None:
        try:
            from pypdf import PdfReader
            reader=PdfReader(pdf_path);pages=len(reader.pages)
            if not pages:raise ValueError('empty')
            if limit and pages>limit:add('page_limit','error',f'PDF has {pages} pages; the applicable limit is {limit}. Shorten the content before approval.')
            else:add('page_limit','pass',f'PDF has {pages} page(s).'+(f' Limit: {limit}.' if limit else ' No confirmed page limit is configured.'))
            if not any((p.extract_text() or '').strip() for p in reader.pages):add('text_extraction','warning' if category.casefold()=='resume' else 'info','No selectable PDF text was extracted. Review readability and text extraction manually.')
        except Exception as e:
            add('pdf','error','PDF could not be read or contains no pages.')
    else:add('page_limit','info','Page count is not checked until Preview PDF compiles the current text.'+(f' Limit: {limit}.' if limit else ''))
    if log_path and Path(log_path).exists() and re.search(r'Overfull \\[hv]box',Path(log_path).read_text(errors='replace')):add('overflow','warning','The compiler reported overflowing text. Inspect the PDF before approving.')
    if not req['reviewed']:add('checklist','info','Posting requirements have not been reviewed for the current posting text.')
    add('manual_review','info','These checks are advisory and rule-based; they do not verify every claim or guarantee acceptance by an application portal.')
    return {'checked_at':now(),'content_sha256':digest(content),'posting_sha256':career.posting_hash(jid),'evidence_sha256':digest(authority),'evidence_mode':mode,'fact_ids':[f['id'] for f in evidence],'evidence':evidence,'items':items,'pages':pages,'page_limit':limit,'errors':sum(i['severity']=='error' for i in items),'warnings':sum(i['severity']=='warning' for i in items)}
