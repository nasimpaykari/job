"""Immutable application bundles, submission records and recoverable removal."""
import hashlib,json,re,zipfile
from datetime import date
from pathlib import Path
from .core import uid,now

def initialize(store):
    with store.db() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='application_packages'").fetchone()
        populated=c.execute('SELECT 1 FROM jobs LIMIT 1').fetchone()
    if not exists and populated:store.backup()
    with store.db() as c:c.executescript('''
    CREATE TABLE IF NOT EXISTS application_packages(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,created TEXT NOT NULL,manifest TEXT NOT NULL,sha256 TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS application_submissions(package_id TEXT PRIMARY KEY,data TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deleted_jobs(job_id TEXT PRIMARY KEY,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deleted_document_types(name TEXT PRIMARY KEY,data TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS hidden_job_types(job_id TEXT NOT NULL,category TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(job_id,category));
    ''')

def active_job(store,jid):
    job=store.job(jid)
    with store.db() as c:
        if c.execute('SELECT 1 FROM deleted_jobs WHERE job_id=?',(jid,)).fetchone():raise ValueError('Restore this job from Deleted jobs first.')
    return job

def category_allowed(store,jid,category):
    active_job(store,jid)
    if category not in store.settings()['document_categories']:raise ValueError('Restore this document type in Profile & settings first.')
    with store.db() as c:
        if c.execute('SELECT 1 FROM hidden_job_types WHERE job_id=? AND category=?',(jid,category)).fetchone():raise ValueError('Restore this document type for this job first.')

class Removal:
    def __init__(self,store):self.store=store
    def state(self):
        with self.store.db() as c:
            jobs=[dict(id=r['id'],title=json.loads(r['data'])['title'],institution=json.loads(r['data'])['institution'],status=r['status'],deleted_at=r['deleted_at']) for r in c.execute('SELECT j.*,d.created AS deleted_at FROM jobs j JOIN deleted_jobs d ON j.id=d.job_id ORDER BY d.created DESC')]
            types=[{'name':r['name'],'deleted_at':r['created']} for r in c.execute('SELECT * FROM deleted_document_types ORDER BY created DESC')]
            hidden=[dict(r) for r in c.execute('SELECT * FROM hidden_job_types')]
        return {'jobs':jobs,'types':types,'hidden_types':hidden}
    def job(self,jid,restore=False):
        self.store.job(jid)
        with self.store.lock,self.store.db() as c:
            if restore:c.execute('DELETE FROM deleted_jobs WHERE job_id=?',(jid,))
            else:c.execute('INSERT OR IGNORE INTO deleted_jobs VALUES(?,?)',(jid,now()))
            self.store.event(c,jid,'job_restored' if restore else 'job_deleted',{'recoverable':True})
    def category(self,name,restore=False,jid=''):
        if not isinstance(name,str) or not name:raise ValueError('Choose a document type.')
        if jid:
            active_job(self.store,jid)
            if name not in self.store.settings()['document_categories']:raise ValueError('Restore the global type in settings first.')
            with self.store.lock,self.store.db() as c:
                if restore:c.execute('DELETE FROM hidden_job_types WHERE job_id=? AND category=?',(jid,name))
                else:c.execute('INSERT OR IGNORE INTO hidden_job_types VALUES(?,?,?)',(jid,name,now()))
                self.store.event(c,jid,'job_document_type_restored' if restore else 'job_document_type_removed',{'category':name})
            return
        with self.store.lock:
            s=self.store.settings()
            with self.store.db() as c:row=c.execute('SELECT * FROM deleted_document_types WHERE name=?',(name,)).fetchone()
            if restore:
                if not row:raise ValueError('Deleted document type not found.')
                if name in s['document_categories']:raise ValueError('A type with this name already exists.')
                saved=json.loads(row['data']);s['document_categories'].append(name);s['rules'][name]=saved['rule']
                if saved['template'] is not None:s['templates'][name]=saved['template']
            else:
                if name not in s['document_categories']:raise ValueError('Document type not found.')
                if len(s['document_categories'])==1:raise ValueError('Keep at least one active document type.')
                saved={'rule':s['rules'].get(name,{}),'template':s['templates'].get(name)}
                s['document_categories'].remove(name);s['rules'].pop(name,None);s['templates'].pop(name,None)
            # Settings validation happens before the archive is changed.
            with self.store.db() as c:
                self.store.save_settings(s,connection=c)
                if restore:c.execute('DELETE FROM deleted_document_types WHERE name=?',(name,))
                else:c.execute('INSERT OR REPLACE INTO deleted_document_types VALUES(?,?,?)',(name,json.dumps(saved),now()))
                self.store.event(c,'','document_type_restored' if restore else 'document_type_deleted',{'category':name,'recoverable':True})

class Packages:
    def __init__(self,store):self.store=store
    def path(self,pid):
        if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',pid):raise ValueError('Invalid package ID.')
        return self.store.root/'application_packages'/(pid+'.zip')
    def list(self,jid):
        self.store.job(jid)
        with self.store.db() as c:
            rows=c.execute('SELECT p.*,s.data AS submission FROM application_packages p LEFT JOIN application_submissions s ON s.package_id=p.id WHERE p.job_id=? ORDER BY p.created DESC',(jid,)).fetchall()
            docs=[dict(r) for r in c.execute('SELECT id,category,created,approved FROM documents WHERE job_id=? ORDER BY created DESC',(jid,))]
        return {'packages':[dict(id=r['id'],created=r['created'],manifest=json.loads(r['manifest']),submission=json.loads(r['submission']) if r['submission'] else None) for r in rows],'documents':docs}
    def inspect(self,jid,ids):
        from .career import Career,check_document
        job=active_job(self.store,jid)
        if not isinstance(ids,list) or not ids or len(ids)>40 or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)):raise ValueError('Select 1–40 unique approved document versions.')
        req=Career(self.store).requirements(jid);items=[];documents=[];categories=set()
        def issue(level,text):items.append({'severity':level,'message':text})
        if not req['reviewed']:issue('error','Review the requirements checklist for the current posting before building a package.')
        for r in req['requirements']:
            if r['status']=='pending' or r['stale'] and r['status'] not in ('dismissed','waived'):issue('error','Resolve pending or stale requirement: '+r['label'])
        for did in ids:
            with self.store.db() as c:r=c.execute('SELECT * FROM documents WHERE id=? AND job_id=?',(did,jid)).fetchone()
            if not r or not r['approved']:raise ValueError('Choose approved versions belonging to this job.')
            if r['category'] in categories:raise ValueError('Select only one version per document type.')
            categories.add(r['category']);folder=self.store.document_version(did);pdf=folder/'document.pdf';tex=folder/'document.tex'
            if not pdf.is_file() or not tex.is_file():issue('error','Missing PDF or LaTeX file: '+r['category']);continue
            if pdf.stat().st_size>50*1024*1024:raise ValueError('A selected PDF exceeds 50 MB.')
            content=tex.read_text(encoding='utf-8')
            if content!=r['content']:issue('warning','Saved compiler source differs from the editor source, possibly due to font compatibility repairs. Review both sources and the PDF: '+r['category'])
            report=check_document(self.store,jid,r['category'],content,pdf,folder/'compiler.log',allow_archived=True)
            for item in report['items']:
                if item['severity'] in ('error','warning'):issue(item['severity'],r['category']+': '+item['message'])
            documents.append({'id':did,'category':r['category'],'created':r['created'],'editor_source_sha256':hashlib.sha256(r['content'].encode()).hexdigest(),'pages':report.get('pages'),'checks':report})
        for r in req['requirements']:
            if not r['required'] or r['status'] in ('dismissed','waived'):continue
            if r['kind']=='document':
                if r['category'].casefold() not in {x.casefold() for x in categories}:issue('error','Required document is not selected: '+r['label'])
            elif not r['complete']:issue('error','Complete or explicitly waive requirement: '+r['label'])
        return {'job':job,'requirements':req,'documents':documents,'items':items,'errors':sum(i['severity']=='error' for i in items),'warnings':sum(i['severity']=='warning' for i in items),'note':'Readiness covers your reviewed checklist and selected files. Check the actual portal before submitting.'}
    def create(self,jid,data):
        with self.store.lock:
            report=self.inspect(jid,data.get('document_ids'))
            if report['errors']:raise ValueError('Package is not ready: '+'; '.join(i['message'] for i in report['items'] if i['severity']=='error'))
            if report['warnings'] and data.get('acknowledge_warnings') is not True:raise ValueError('Review and acknowledge package warnings first.')
            pid=uid();created=now();path=self.path(pid);path.parent.mkdir(exist_ok=True);temp=path.with_suffix('.tmp');job=report['job'];entries=[]
            manifest={'id':pid,'created':created,'job':{k:job.get(k,'') for k in ('id','institution','title','url','apply_url','deadline')},'requirements':report['requirements'],'documents':entries,'readiness':{'items':report['items'],'note':report['note']}}
            filename='Application_package_'+created[:10]+'_'+pid[:8]+'.zip';export=self.store.document_folder(jid)/filename
            try:
                with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as z:
                    for index,doc in enumerate(report['documents'],1):
                        folder=self.store.document_version(doc['id']);label=re.sub('[^A-Za-z0-9_-]+','_',doc['category']).strip('_') or 'Document';record=dict(doc,files={})
                        for ext in ('pdf','tex'):
                            raw=(folder/('document.'+ext)).read_bytes();name=('' if ext=='pdf' else 'LaTeX/')+f'{index:02d}_{label}.{ext}'
                            z.writestr(name,raw);record['files'][ext]={'name':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
                        entries.append(record)
                    z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2));z.writestr('README.txt','PDF files are at the top level. LaTeX sources are in LaTeX/. The manifest records the exact approved versions and file hashes. This package has NOT been submitted automatically.\n')
                temp.replace(path);raw=path.read_bytes();export.write_bytes(raw)
                with self.store.db() as c:
                    c.execute('INSERT INTO application_packages VALUES(?,?,?,?,?)',(pid,jid,created,json.dumps(manifest),hashlib.sha256(raw).hexdigest()))
                    self.store.event(c,jid,'application_package_created',{'id':pid,'document_ids':data['document_ids']})
            except Exception:
                temp.unlink(missing_ok=True);path.unlink(missing_ok=True);export.unlink(missing_ok=True);raise
            return {'id':pid,'filename':filename,'folder':str(export.parent)}
    def download(self,pid):
        with self.store.db() as c:r=c.execute('SELECT * FROM application_packages WHERE id=?',(pid,)).fetchone()
        if not r:raise ValueError('Package not found.')
        path=self.path(pid)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=r['sha256']:raise ValueError('Package file is missing or changed. Restore it from backup.')
        return path
    def submit(self,pid,data):
        with self.store.lock:
            self.download(pid)
            with self.store.db() as c:r=c.execute('SELECT * FROM application_packages WHERE id=?',(pid,)).fetchone()
            job=active_job(self.store,r['job_id'])
            if data.get('confirmed') is not True:raise ValueError('Confirm you submitted the application outside Job Coach.')
            fields={}
            for key,limit in [('submitted_on',10),('portal',300),('confirmation',500),('notes',6000)]:
                value=data.get(key,'')
                if not isinstance(value,str) or len(value)>limit:raise ValueError('Invalid submission '+key)
                fields[key]=value.strip()
            try:date.fromisoformat(fields['submitted_on'])
            except ValueError:raise ValueError('Enter the actual submission date as YYYY-MM-DD.') from None
            fields['recorded_at']=now()
            with self.store.db() as c:
                if c.execute('SELECT 1 FROM application_submissions WHERE package_id=?',(pid,)).fetchone():raise ValueError('A submission has already been recorded for this package.')
                c.execute('INSERT INTO application_submissions VALUES(?,?,?)',(pid,json.dumps(fields),now()))
                if data.get('update_status') is True and job['status'] in ('New','Shortlist','Drafting','Ready to Submit'):
                    c.execute("UPDATE jobs SET status='Submitted',updated=? WHERE id=?",(now(),job['id']))
                self.store.event(c,job['id'],'application_submitted',{'package_id':pid,**fields})
            return {'ok':True,'job_id':job['id']}
