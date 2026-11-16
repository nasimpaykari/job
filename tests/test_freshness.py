import json,time
import pytest
from coach.core import Store
from coach.freshness import Freshness,extract
from coach.services import HTTPFailure
from coach.web import create_app
from test_upgrade import setup,post,PASSWORD
DESC='We seek a robotics faculty member to teach computer science and develop a research program. Submit a cover letter and a two-page research statement.'
def html(description=DESC,deadline='2027-01-15',title='Robotics Faculty'):
    return '<html><script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':title,'description':description,'validThrough':deadline})+'</script><main><h1>'+title+'</h1><p>'+description+'</p></main></html>'
@pytest.fixture
def env(tmp_path,monkeypatch):
    s=Store(tmp_path/'data');s.save_settings({'network_enabled':True})
    j=s.add_job({'title':'Robotics Faculty','institution':'Example University','url':'https://example.org/jobs/1','description':'Original saved description','deadline':'2026-12-01'})[0]
    monkeypatch.setattr('coach.freshness.fetch',lambda u,settings:(html(),u))
    return s,Freshness(s),j

def test_first_check_baseline_no_overwrite(env):
    s,f,j=env;before=s.job(j);f.check(j);d=f.detail(j)
    assert d['current']['deadline']=='2027-01-15' and not d['pending']
    assert s.job(j)==before and f.summaries()[j]['state']=='available'

def test_changes_preserved_until_selective_review(env,monkeypatch):
    s,f,j=env;f.check(j)
    monkeypatch.setattr('coach.freshness.fetch',lambda u,settings:(html(DESC.replace('two-page','three-page'),'2027-02-01'),u))
    f.check(j);d=f.detail(j)
    assert d['pending'] and 'three-page' in d['diff'] and s.job(j)['deadline']=='2026-12-01'
    result=f.review(j,{'check_id':d['current']['id'],'job_signature':d['job_signature'],'apply_deadline':True})
    assert not result['pending'] and s.job(j)['deadline']=='2027-02-01'
    assert s.job(j)['description']=='Original saved description' and s.job(j)['status']=='New'

def test_deadline_only_change(env,monkeypatch):
    s,f,j=env;f.check(j);monkeypatch.setattr('coach.freshness.fetch',lambda u,settings:(html(deadline='2027-03-01'),u));f.check(j)
    assert f.detail(j)['pending']

def test_acknowledge_without_apply(env,monkeypatch):
    s,f,j=env;f.check(j);monkeypatch.setattr('coach.freshness.fetch',lambda u,settings:(html(DESC+' Extra application instructions.'),u));f.check(j)
    before=s.job(j);d=f.detail(j);f.review(j,{'check_id':d['current']['id'],'job_signature':d['job_signature']})
    assert s.job(j)==before and not f.detail(j)['pending']

@pytest.mark.parametrize('status,expected',[(404,'missing'),(410,'missing'),(403,'unverified'),(429,'unverified'),(503,'failed')])
def test_http_failure_preserves_baseline_and_application(env,monkeypatch,status,expected):
    s,f,j=env;f.check(j);old=f.detail(j)['current']['id'];s.edit_job(j,{'status':'Submitted'})
    def fail(*args):raise HTTPFailure(status)
    monkeypatch.setattr('coach.freshness.fetch',fail);f.check(j);d=f.detail(j)
    assert d['latest']['state']==expected and d['current']['id']==old and s.job(j)['status']=='Submitted'
    with pytest.raises(ValueError,match='unsuccessful'):f.review(j,{'check_id':old,'job_signature':d['job_signature']})

def test_timeout_never_closed(env,monkeypatch):
    s,f,j=env
    def fail(*args):raise TimeoutError()
    monkeypatch.setattr('coach.freshness.fetch',fail);f.check(j)
    assert f.detail(j)['latest']['state']=='failed' and s.job(j)['status']=='New'

@pytest.mark.parametrize('body,state',[
    ('<main>This position has been filled.</main>','possibly_closed'),
    ('<main>Verify you are human</main>','unverified'),
    ('<title>Careers</title><main>Find all open positions at our university.</main>','unverified'),
    ('<main>Robotics Faculty</main>','unverified')])
def test_closed_and_unverifiable_pages(env,body,state):
    s,f,j=env;assert extract(body,s.job(j))['state']==state

def test_jsonld_wrong_job_not_used(env):
    s,f,j=env;assert extract(html(title='Other job'),s.job(j))['state']=='unverified'

def test_visible_text_fallback_and_no_guessed_deadline(env):
    s,f,j=env;data=extract('<main><h1>Robotics Faculty</h1><p>'+DESC+' Deadline 01/02/27.</p></main>',s.job(j))
    assert data['state']=='available' and data['deadline']==''

def test_no_network_and_no_snapshot(env):
    s,f,j=env;s.save_settings({'network_enabled':False})
    with pytest.raises(ValueError,match='network'):f.check(j)
    assert f.detail(j)['latest'] is None

def test_edit_during_request_discards_result(env,monkeypatch):
    s,f,j=env
    def race(u,settings):s.edit_job(j,{'description':'Concurrent edit'});return html(),u
    monkeypatch.setattr('coach.freshness.fetch',race)
    with pytest.raises(ValueError,match='during'):f.check(j)
    assert f.detail(j)['latest'] is None

def test_review_rejects_stale_card_and_snapshot(env):
    s,f,j=env;f.check(j);d=f.detail(j);s.edit_job(j,{'deadline':'2027-04-01'})
    with pytest.raises(ValueError,match='edited'):f.review(j,{'check_id':d['current']['id'],'job_signature':d['job_signature']})
    f.check(j)
    with pytest.raises(ValueError,match='since'):f.review(j,{'check_id':d['current']['id'],'job_signature':d['job_signature']})

def test_new_url_baseline_resets(env):
    s,f,j=env;f.check(j);s.edit_job(j,{'url':'https://example.org/jobs/2'})
    assert f.detail(j)['source_changed'];f.check(j)
    assert not f.detail(j)['pending'] and not f.detail(j)['source_changed']

def test_merge_preserves_history_resets_baseline(env):
    s,f,j=env;f.check(j);other=s.add_job({'title':'Another role','institution':'Example University','url':'https://example.org/other'})[0]
    s.merge(other,j);assert f.detail(other)['latest'] is None and len(f.detail(other)['history'])==1

def test_schema_restart_preserves_records(env):
    s,f,j=env;f.check(j);before=f.detail(j);reopened=Store(s.root)
    assert Freshness(reopened).detail(j)==before

def test_auth_workspace_viewer_and_csrf(tmp_path):
    app=create_app(tmp_path);owner=setup(app);j=post(owner,'/api/jobs',{'title':'Faculty','institution':'One','url':'https://example.org/job'}).json['id']
    assert owner.post('/api/jobs/'+j+'/posting/check',json={}).status_code==403
    post(owner,'/api/users',{'username':'staff','password':PASSWORD})
    staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD})
    assert staff.get('/api/jobs/'+j+'/posting').status_code==400
    space=owner.get('/api/state').json['active_workspace'];post(owner,'/api/share',{'username':'staff','role':'viewer'});post(staff,'/api/workspace',{'id':space})
    assert staff.get('/api/jobs/'+j+'/posting').status_code==200
    for path in ['/api/jobs/'+j+'/posting/check','/api/jobs/'+j+'/posting/review','/api/postings/check']:
        assert post(staff,path,{'job_ids':[j]}).status_code==403

def test_batch_limit_and_task(tmp_path,monkeypatch):
    app=create_app(tmp_path,testing=True);c=app.test_client();s=app.extensions['store'];s.save_settings({'network_enabled':True})
    j=s.add_job({'title':'Robotics Faculty','institution':'Example','url':'https://example.org/job'})[0]
    monkeypatch.setattr('coach.freshness.fetch',lambda u,settings:(html(),u))
    assert c.post('/api/postings/check',json={'job_ids':[j]*51}).status_code==400
    r=c.post('/api/postings/check',json={'job_ids':[j]}).json
    for _ in range(100):
        t=c.get('/api/tasks/'+r['task_id']).json
        if t['state']!='running':break
        time.sleep(.01)
    assert t['state']=='done' and t['result']['checked']==1
    assert c.get('/api/state').json['jobs'][0]['freshness']['state']=='available'

def test_redirect_closure_not_trusted(env,monkeypatch):
    s,f,j=env
    monkeypatch.setattr('coach.freshness.fetch',lambda u,settings:('<main>This position has been filled.</main>','https://example.org/careers'))
    f.check(j);assert f.detail(j)['latest']['state']=='unverified'

def test_pending_changes_survive_failed_recheck(env,monkeypatch):
    s,f,j=env;f.check(j)
    monkeypatch.setattr('coach.freshness.fetch',lambda u,settings:(html(DESC+' Additional requirements.','2027-03-01'),u));f.check(j)
    def fail(*args):raise HTTPFailure(503)
    monkeypatch.setattr('coach.freshness.fetch',fail);f.check(j)
    summary=f.summaries()[j]
    assert summary['state']=='failed' and summary['pending'] and summary['deadline_changed']

def test_apply_description_invalidates_checklist_review(env):
    from coach.career import Career
    s,f,j=env;career=Career(s);career.review_checklist(j);f.check(j);d=f.detail(j)
    f.review(j,{'check_id':d['current']['id'],'job_signature':d['job_signature'],'apply_description':True})
    assert s.job(j)['description']==DESC
    assert not career.requirements(j)['reviewed']
