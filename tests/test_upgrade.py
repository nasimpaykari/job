import json, sqlite3, zipfile
from pathlib import Path
import pytest
from coach.core import Store
from coach.services import AI, Coach, profile_fit, Search
from coach.accounts import Accounts
from coach.storage import Storage
from coach.discovery import api_for_url, parse_api, discover_apis
from coach.web import create_app

PASSWORD='Long-password-123'
def token(c):
    with c.session_transaction() as s:return s['csrf']
def post(c,path,data=None):return c.post(path,json=data or {},headers={'X-CSRF-Token':token(c)})
def patch(c,path,data):return c.patch(path,json=data,headers={'X-CSRF-Token':token(c)})
def setup(app):
    c=app.test_client();c.get('/?token='+app.extensions['launch_token']);c.get('/login')
    assert post(c,'/api/setup',{'username':'owner','password':PASSWORD}).status_code==200
    return c

def test_setup_token_required_and_password_login(tmp_path):
    app=create_app(tmp_path);c=app.test_client();c.get('/login')
    assert post(c,'/api/setup',{'username':'owner','password':PASSWORD}).status_code==403
    owner=setup(app)
    assert owner.get('/api/state').status_code==200
    post(owner,'/api/logout');assert owner.get('/api/state').status_code==401
    owner.get('/login')
    assert post(owner,'/api/login',{'username':'owner','password':'bad'}).status_code==400
    assert post(owner,'/api/login',{'username':'owner','password':PASSWORD}).status_code==200

def test_isolated_workspaces_and_sharing_permissions(tmp_path):
    app=create_app(tmp_path);owner=setup(app)
    first=post(owner,'/api/jobs',{'title':'Professor','institution':'One','url':'https://example.org/job/1'}).json['id']
    assert post(owner,'/api/users',{'username':'staff','password':PASSWORD}).status_code==200
    staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD})
    assert staff.get('/api/state').json['jobs']==[]
    assert staff.get('/api/jobs/'+first).status_code==400
    own_space=staff.get('/api/state').json['active_workspace'];shared=owner.get('/api/state').json['active_workspace']
    assert post(staff,'/api/workspace',{'id':shared}).status_code==403
    assert post(owner,'/api/share',{'username':'staff','role':'viewer'}).status_code==200
    assert post(staff,'/api/workspace',{'id':shared}).status_code==200
    assert len(staff.get('/api/state').json['jobs'])==1
    assert patch(staff,'/api/jobs/'+first,{'status':'Submitted'}).status_code==403
    post(owner,'/api/share',{'username':'staff','role':'editor'})
    assert patch(staff,'/api/jobs/'+first,{'status':'Submitted','notify_staff':True}).status_code==200
    inbox=owner.get('/api/state').json['notifications'];assert len(inbox)==1 and inbox[0]['actor']=='staff'
    assert post(staff,'/api/settings',{'model':'changed'}).status_code==403
    assert post(staff,'/api/share',{'username':'owner','role':'remove'}).status_code==400
    assert post(staff,'/api/workspace',{'id':own_space}).status_code==200
    assert staff.get('/api/state').json['memory']==[]
    post(owner,'/api/share',{'username':'staff','role':'remove'})
    assert post(staff,'/api/workspace',{'id':shared}).status_code==403

def test_no_notification_when_opted_out(tmp_path):
    app=create_app(tmp_path);o=setup(app);post(o,'/api/users',{'username':'staff','password':PASSWORD});post(o,'/api/share',{'username':'staff','role':'editor'})
    jid=post(o,'/api/jobs',{'institution':'U','title':'Role','url':'https://example.org/j/1','notify_staff':False}).json['id']
    patch(o,'/api/jobs/'+jid,{'notes':'private edit','notify_staff':False})
    a=app.extensions['accounts'];staff=a.login('staff',PASSWORD)
    assert a.inbox(staff['id'])==[]
    h=o.get('/api/jobs/'+jid+'/history').json
    assert any(r['data'].get('actor')=='owner' for r in h)

def test_password_change_invalidates_other_sessions(tmp_path):
    app=create_app(tmp_path);a=setup(app);b=app.test_client();b.get('/login');post(b,'/api/login',{'username':'owner','password':PASSWORD})
    assert post(a,'/api/account',{'current_password':PASSWORD,'username':'newowner','password':'New-password-123'}).status_code==200
    assert a.get('/api/state').status_code==200 and b.get('/api/state').status_code==401
    b.get('/login');assert post(b,'/api/login',{'username':'newowner','password':'New-password-123'}).status_code==200

def test_password_hash_and_rate_limit(tmp_path):
    a=Accounts(tmp_path);u=a.create('owner',PASSWORD,first=True)
    with a.db() as c:assert PASSWORD not in c.execute('SELECT password FROM users').fetchone()[0]
    for _ in range(8):
        with pytest.raises(ValueError):a.login('owner','wrong')
    with pytest.raises(ValueError,match='Too many'):a.login('owner',PASSWORD)

def test_custom_document_type_in_prompt(tmp_path):
    s=Store(tmp_path);cfg=s.settings();cfg['document_categories'].append('Leadership Statement');cfg['rules']['Leadership Statement']={'dos':'Be specific','donts':'No invention','prompt':'Explain lab leadership'};cfg['profile']={'verified':True,'facts':'Test facts'};s.save_settings(cfg)
    jid,_=s.add_job({'institution':'U','title':'Role','url':'https://x.org/job/1'})
    p=Coach(s).document(jid,'Leadership Statement',manual=True)
    assert 'Explain lab leadership' in p['prompt']
    assert Coach(s).save_document(jid,'Leadership Statement','text')['id']

def test_legacy_settings_gain_new_fields(tmp_path):
    s=Store(tmp_path);cfg=s.settings()
    for k in ['document_categories','storage','ollama_url','discovery_mode']:cfg.pop(k)
    with s.db() as c:c.execute("UPDATE kv SET value=? WHERE key='settings'",(json.dumps(cfg),))
    assert s.settings()['storage']['mode']=='local'
    assert 'Cover' in s.settings()['document_categories']

def test_profile_fit_uses_profile_evidence():
    s=Store.__new__(Store)
    cfg={'profile':{'verified':True,'facts':'I teach Python and research robotics.'},'preferred_keywords':['Python','machine learning']}
    fit=profile_fit({'title':'Robotics faculty','description':'Teach Python and machine learning.'},cfg)
    assert any(m['skill'].lower()=='python' and 'python' in m['profile_evidence'].lower() for m in fit['evidence'])
    assert 'machine learning' in fit['not_in_profile']
    assert not any(m['skill']=='machine learning' for m in fit['evidence'])

def test_ollama_works_with_external_network_off(tmp_path,monkeypatch):
    s=Store(tmp_path);s.save_settings({'provider':'ollama','model':'local-model','ai_enabled':True,'network_enabled':False})
    calls=[]
    class R:
        status_code=200
        def json(self):return {'message':{'content':'local answer'}}
    class Session:
        def post(self,url,**kw):calls.append((url,kw));return R()
    monkeypatch.setattr('requests.Session',Session)
    assert AI(s).call('hello')=='local answer'
    assert calls[0][0]=='http://127.0.0.1:11434/api/chat' and calls[0][1]['json']['stream'] is False
    s.save_settings({'ollama_url':'https://external.example'})
    with pytest.raises(ValueError,match='loopback'):AI(s).call('test')

def test_ollama_rejects_cloud_model(tmp_path):
    s=Store(tmp_path);s.save_settings({'provider':'ollama','model':'model:cloud','ai_enabled':True})
    with pytest.raises(ValueError,match='cloud models'):AI(s).call('test')

def test_folder_snapshot_excludes_credentials_and_accounts(tmp_path):
    s=Store(tmp_path/'data');s.save_key('openai','secret');(s.root/'accounts.db').write_bytes(b'private accounts')
    backup=tmp_path/'backups';s.save_settings({'storage':{'mode':'folder','destination':str(backup),'auto_sync':False}})
    result=Storage(s).sync()
    with zipfile.ZipFile(backup/result['name']) as z:
        assert 'coach.db' in z.namelist() and 'snapshot.json' in z.namelist()
        assert 'credentials.json' not in z.namelist() and 'accounts.db' not in z.namelist()

def test_cloud_sync_blocks_when_network_off(tmp_path):
    s=Store(tmp_path);s.save_settings({'storage':{'mode':'google_drive','destination':'drive:JobCoach','auto_sync':False}})
    with pytest.raises(ValueError,match='network'):Storage(s).sync()

def test_supported_api_detection_and_parse():
    info=api_for_url('https://job-boards.greenhouse.io/example')
    assert info[0]=='https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true'
    jobs=parse_api(json.dumps({'jobs':[{'title':'Engineer','absolute_url':'https://example.com/job/1','content':'Python','location':{'name':'NY'}}]}),'greenhouse','Example','Source')
    assert jobs[0]['title']=='Engineer'
    lever=api_for_url('https://jobs.lever.co/example');assert 'mode=json' in lever[0]
    found=discover_apis('<iframe src="https://jobs.lever.co/example"></iframe>','https://u.edu/careers')
    assert found==['https://jobs.lever.co/example']

def test_search_auto_falls_back_to_web(tmp_path,monkeypatch):
    s=Store(tmp_path);s.save_settings({'network_enabled':True,'custom_urls':['https://jobs.lever.co/example']})
    calls=[]
    def fetch(url,settings):
        calls.append(url)
        if 'api.lever' in url:raise ValueError('API unavailable')
        return '<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Robotics professor','hiringOrganization':{'name':'U'},'description':'Robotics'})+'</script>',url
    monkeypatch.setattr('coach.services.fetch',fetch)
    r=Search(s).run('universities',lambda x:None)
    assert r['added']==1 and len(calls)==2

def test_background_task_keeps_workspace_when_other_user_visits(tmp_path,monkeypatch):
    import time
    app=create_app(tmp_path);owner=setup(app)
    post(owner,'/api/settings',{'profile':{'verified':True,'facts':'OWNER FACTS'}})
    jid=post(owner,'/api/jobs',{'institution':'U','title':'Role','url':'https://u.org/job/1'}).json['id']
    post(owner,'/api/users',{'username':'staff','password':PASSWORD})
    other=app.test_client();other.get('/login');post(other,'/api/login',{'username':'staff','password':PASSWORD})
    def generate(self,*args,**kwargs):
        time.sleep(.1)
        return r'\documentclass{article}\begin{document}OWNER FACTS\end{document}'
    monkeypatch.setattr(AI,'call',generate)
    tid=post(owner,'/api/documents/generate',{'job_id':jid,'category':'Cover'}).json['task_id']
    assert other.get('/api/state').json['documents']==[]
    assert other.get('/api/tasks/'+tid).status_code==404
    for _ in range(50):
        t=owner.get('/api/tasks/'+tid).json
        if t['state']!='running':break
        time.sleep(.05)
    assert t['state']=='done'
    assert owner.get('/api/state').json['documents']==[]
    assert 'OWNER FACTS' in t['result']['content']
    assert other.get('/api/state').json['documents']==[]

def test_new_user_profile_does_not_inherit_owner(tmp_path):
    app=create_app(tmp_path);owner=setup(app);post(owner,'/api/settings',{'profile':{'full_name':'Private Owner','verified':True,'facts':'PRIVATE FACT'}})
    post(owner,'/api/users',{'username':'staff','password':PASSWORD})
    c=app.test_client();c.get('/login');post(c,'/api/login',{'username':'staff','password':PASSWORD})
    assert 'PRIVATE FACT' not in str(c.get('/api/state').json['settings']['profile'])
    assert c.get('/api/state').json['settings']['profile']['full_name']==''


def test_reading_preview_without_compiler_and_isolation(tmp_path,monkeypatch):
    monkeypatch.setattr('shutil.which',lambda _:None)
    app=create_app(tmp_path);owner=setup(app)
    jid=post(owner,'/api/jobs',{'institution':'U','title':'Role','url':'https://u.org/job/preview'}).json['id']
    source=r"\documentclass{article}\begin{document}\section{Experience}My \textbf{research} & <script>alert(1)</script>\end{document}"
    did=post(owner,'/api/documents/save',{'job_id':jid,'category':'Cover','content':source}).json['id']
    r=owner.get('/api/documents/'+did+'/preview')
    assert r.status_code==200
    assert b'My research &amp; &lt;script&gt;' in r.data
    assert b'<script>' not in r.data
    assert "frame-ancestors 'self'" in r.headers['Content-Security-Policy']
    post(owner,'/api/users',{'username':'staff','password':PASSWORD})
    other=app.test_client();other.get('/login');post(other,'/api/login',{'username':'staff','password':PASSWORD})
    assert other.get('/api/documents/'+did+'/preview').status_code==404


def test_pdf_view_uses_compiled_file_and_isolates_users(tmp_path):
    app=create_app(tmp_path);owner=setup(app)
    jid=post(owner,'/api/jobs',{'institution':'U','title':'Role','url':'https://u.org/job/pdf'}).json['id']
    did=post(owner,'/api/documents/save',{'job_id':jid,'category':'CV','content':'content'}).json['id']
    url='/api/documents/'+did+'/view'
    assert b'PDF not compiled yet' in owner.get(url).data
    pdf=app.extensions['store'].document_version(did)/'document.pdf';pdf.write_bytes(b'%PDF-1.4\nfixture')
    r=owner.get(url);assert r.status_code==302 and r.location.endswith('/pdf')
    r=owner.get(r.location);assert r.mimetype=='application/pdf' and r.data.startswith(b'%PDF')
    assert "frame-ancestors 'self'" in r.headers['Content-Security-Policy']
    post(owner,'/api/users',{'username':'staff','password':PASSWORD})
    other=app.test_client();other.get('/login');post(other,'/api/login',{'username':'staff','password':PASSWORD})
    assert other.get(url).status_code==404
