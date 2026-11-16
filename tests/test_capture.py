import copy,json
import pytest
from coach.core import Store
from coach.capture import Capture,normalize
from coach.web import create_app
from test_upgrade import setup,post,PASSWORD

@pytest.fixture
def env(tmp_path):
    s=Store(tmp_path/'data');return s,Capture(s)

def payload(**changes):
    p={'format':'job-coach-capture','version':1,'title':'Robotics Faculty','institution':'Example University','url':'https://example.edu/jobs/123','description':'Teach robotics and conduct research. Submit a CV and teaching statement.','region':'New Jersey','country':'United States','deadline':'2027-01-15'}
    p.update(changes);return p

def test_preview_does_not_save_or_use_network(env,monkeypatch):
    s,c=env
    def fail(*args,**kw):raise AssertionError('Unexpected network call')
    monkeypatch.setattr('requests.sessions.Session.request',fail)
    assert not s.settings()['network_enabled'];r=c.preview(payload());assert not r['matches'] and not s.jobs()
    assert r['fields']['region']=='New Jersey'

def test_save_and_directory_registration(env):
    from coach.universities import list_universities
    s,c=env;r=c.save(payload());j=s.job(r['id'])
    assert r['result']=='added' and j['sources']==['Browser saver'] and j['status']=='New'
    assert j['region']=='New Jersey'
    assert list_universities(s)[0]['name']=='Example University'

def test_linkedin_capture_is_blocked(env):
    _,c=env
    with pytest.raises(ValueError,match='LinkedIn'):
        c.preview(payload(url='https://www.linkedin.com/jobs/view/123456789'))

def test_duplicate_is_read_only(env):
    s,c=env;r=c.save(payload());j=r['id'];s.edit_job(j,{'status':'Submitted','notes':'Keep my note'})
    before=s.job(j)
    with s.db() as db:events=db.execute('SELECT count(*) FROM events').fetchone()[0]
    result=c.save(payload(description='Changed text',deadline='2028-01-01'))
    assert result['result']=='duplicate' and s.job(j)==before
    with s.db() as db:assert db.execute('SELECT count(*) FROM events').fetchone()[0]==events

def test_ignored_stays_ignored(env):
    s,c=env;j=c.save(payload())['id'];s.edit_job(j,{'status':'Ignored'});before=s.job(j)
    assert c.preview(payload())['matches'][0]['status']=='Ignored'
    assert c.save(payload())['result']=='ignored' and s.job(j)==before

def test_tracking_url_matches_existing(env):
    s,c=env;j=c.save(payload())['id'];r=c.save(payload(url='https://example.edu/jobs/123?utm_source=mail'))
    assert r['id']==j and len(s.jobs())==1

def test_job_specific_apply_url_dedup(env):
    s,c=env;j=c.save(payload(url='https://apply.interfolio.com/1234'))['id']
    result=c.save(payload(url='https://example.edu/position',apply_url='https://apply.interfolio.com/1234'))
    assert result['id']==j and result['result']=='duplicate'

def test_conflicting_aliases_require_merge(env):
    s,c=env;c.save(payload());c.save(payload(url='https://apply.interfolio.com/1234',title='Another job'))
    with pytest.raises(ValueError,match='multiple existing'):c.save(payload(apply_url='https://apply.interfolio.com/1234'))

@pytest.mark.parametrize('url',['javascript:alert(1)','file:///tmp/file','https://user:secret@example.edu/job','http://127.0.0.1/job','http://localhost/job','http://10.0.0.1/job','https://example.edu:5000/job','https://example.edu/job?access_token=secret','https://example.edu/job?sessionid=secret'])
def test_bad_urls_rejected(env,url):
    s,c=env
    with pytest.raises(ValueError,match='public HTTP'):c.save(payload(url=url))
    assert not s.jobs()

@pytest.mark.parametrize('bad',[{},[],payload(version=2),payload(title={'x':'title'}),payload(description='x'*60001)])
def test_invalid_payload_rejected(env,bad):
    s,c=env
    with pytest.raises(ValueError):c.save(bad)
    assert not s.jobs()

def test_unknown_fields_not_imported(env):
    s,c=env;j=c.save(payload(status='Ignored',sources=['Invented'],manual_overrides=['title'],approved=True))['id']
    job=s.job(j);assert job['status']=='New' and job['sources']==['Browser saver'] and 'approved' not in job and 'manual_overrides' not in job

def test_missing_fields_and_truncation(env):
    s,c=env;p=payload(title='',institution='',description='',deadline='',truncated=True)
    assert len(c.preview(p)['warnings'])==4
    with pytest.raises(ValueError,match='employer'):c.save(p)

def test_review_confirmation_and_csrf(tmp_path):
    app=create_app(tmp_path);client=setup(app)
    assert client.post('/api/capture/save',json=payload(reviewed=True)).status_code==403
    assert post(client,'/api/capture/save',payload()).status_code==400
    assert post(client,'/api/capture/preview',payload()).status_code==200
    assert post(client,'/api/capture/save',payload(reviewed=True)).json['result']=='added'

def test_workspace_isolation_and_viewer_restrictions(tmp_path):
    app=create_app(tmp_path);owner=setup(app);first=post(owner,'/api/capture/save',payload(reviewed=True)).json['id']
    post(owner,'/api/users',{'username':'staff','password':PASSWORD});staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD})
    assert post(staff,'/api/capture/preview',payload()).json['matches']==[]
    second=post(staff,'/api/capture/save',payload(reviewed=True)).json['id'];assert first!=second
    space=owner.get('/api/state').json['active_workspace'];post(owner,'/api/share',{'username':'staff','role':'viewer'});post(staff,'/api/workspace',{'id':space})
    for endpoint in ('preview','save'):assert post(staff,'/api/capture/'+endpoint,payload(reviewed=True)).status_code==403
