import json,time
import pytest
from coach.core import Store
from coach.web import create_app
from coach.universities import list_universities,update_university,facets,discover_careers,institution_key
from coach.services import profile_fit,Search

def job(**kw):return dict(institution='Example University',title='Assistant Professor',url='https://example.edu/job/1',region='New York',description='Python and robotics. Tenure-track position.',**kw)

def test_registry_tracks_all_ingestion_and_edits(tmp_path):
    s=Store(tmp_path);jid=s.add_job(job())[0];u=list_universities(s)[0]
    assert u['state']=='NY' and u['job_count']==1 and u['research_class']=='Unknown'
    s.add_job(job());assert len(list_universities(s))==1
    update_university(s,u['id'],{'state':'NJ','research_class':'R2'})
    s.edit_job(jid,{'institution':'Other College','region':'CA'})
    rows={x['name']:x for x in list_universities(s)}
    assert rows['Other College']['state']=='CA' and rows['Example University']['job_count']==0
    assert rows['Example University']['research_class']=='R2'
    assert Store(tmp_path).job(jid)['institution']=='Other College'

def test_legacy_backfill_and_existing_classification_survives(tmp_path):
    s=Store(tmp_path);s.add_job(job())
    with s.db() as c:c.execute('DELETE FROM universities')
    s=Store(tmp_path);u=list_universities(s)[0]
    assert u['state']=='NY' and u['discovery_status']=='needs_input'
    update_university(s,u['id'],{'research_class':'R1','institution_type':'University'})
    assert list_universities(Store(tmp_path))[0]['research_class']=='R1'

def test_facets_keep_tenure_portal_and_roles_separate():
    j=job(apply_url='https://apply.interfolio.com/123')
    f=facets(j,{'research_class':'R2','state':'NY'})
    assert (f['role_type'],f['tenure_status'],f['application_portal'])==('Faculty','Tenure-track','Interfolio')
    j['description']='Non-tenure-track faculty';assert facets(j)['tenure_status']=='Non-tenure-track'
    j['description']='Teaching position';assert facets(j)['tenure_status']=='Unknown'
    j['application_portal']='University website';assert facets(j)['application_portal']=='University website'
    j['application_portal']='';j['apply_url']='https://evilinterfolio.com/1';assert facets(j)['application_portal']=='Other'
    j['region']='';j['location']='Albany, NY';assert facets(j)['job_state']=='NY'
    j['title']='Postdoctoral fellow';assert facets(j)['role_type']=='Postdoctoral'
    assert facets(j)['research_class']=='Unknown'

def test_career_discovery_is_review_only_and_preserves_concurrent_edits(tmp_path,monkeypatch):
    s=Store(tmp_path);s.add_job(job());key=list_universities(s)[0]['id'];s.save_settings({'network_enabled':True,'ai_enabled':True})
    monkeypatch.setattr('coach.services.AI.call',lambda *a,**k:json.dumps({'website':'https://example.edu','career_url':'https://example.edu/careers'}))
    def fetch(url,cfg):
        update_university(s,key,{'research_class':'R1'})
        return '<title>Example University careers</title><a href="https://apply.interfolio.com/123">Faculty positions</a>',url
    monkeypatch.setattr('coach.services.fetch',fetch)
    result=discover_careers(s,key);u=list_universities(s)[0]
    assert result['candidates']>0 and u['discovery_status']=='review'
    assert not u['career_verified'] and not u['screen'] and not u['career_url']
    assert u['research_class']=='R1'
    with pytest.raises(ValueError):update_university(s,key,{'screen':True})
    update_university(s,key,{'career_url':'https://example.edu/careers','career_verified':True,'screen':True})
    with pytest.raises(ValueError):update_university(s,key,{'career_url':'https://example.edu/other'})

def test_confirmed_directory_page_is_screened(tmp_path,monkeypatch):
    s=Store(tmp_path);s.add_job(job());u=list_universities(s)[0]
    s.save_settings({'network_enabled':True,'required_keywords':[],'excluded_keywords':[],'countries':[],'regions':[]})
    update_university(s,u['id'],{'career_url':'https://example.edu/careers','career_verified':True,'screen':True})
    calls=[]
    def fetch(url,cfg):
        calls.append(url)
        return '<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Robotics Professor','hiringOrganization':{'name':'Example University'},'description':'Robotics research','url':url})+'</script>',url
    monkeypatch.setattr('coach.services.fetch',fetch)
    r=Search(s).run('universities',lambda x:None)
    assert calls==['https://example.edu/careers'] and r['added']==1

def test_profile_score_refreshes_on_edit_and_separates_languages(tmp_path):
    app=create_app(tmp_path,testing=True);client=app.test_client();s=app.extensions['store']
    s.save_settings({'profile':{'verified':True,'facts':'Python and C++ experience'},'preferred_keywords':[],'required_keywords':[]})
    jid=s.add_job(dict(institution='U',title='Position',url='https://u.edu/1',description='Python C++ Java'))[0]
    before=client.get('/api/state').json['jobs'][0]['fit'];assert before['topic_coverage']==67
    r=client.patch('/api/jobs/'+jid,json={'description':'Python C++'});assert r.status_code==200 and r.json['fit']['topic_coverage']==100
    assert client.get('/api/state').json['jobs'][0]['fit']['topic_coverage']==100
    f=profile_fit({'title':'Role','description':'C# JavaScript'},s.settings())
    assert f['topic_coverage']==0 and not f['evidence']
    s.save_settings({'profile':{'verified':False,'facts':'Python'}})
    assert client.get('/api/state').json['jobs'][0]['fit']['topic_coverage'] is None

def test_network_off_discovery_does_not_queue(tmp_path):
    app=create_app(tmp_path,testing=True);c=app.test_client()
    key=c.post('/api/universities',json={'name':'New University','state':'CA'}).json['id']
    assert c.post('/api/universities/'+key+'/discover',json={}).status_code==400
    assert c.get('/api/state').json['universities'][0]['discovery_status']=='pending'

def test_automatic_discovery_runs_once_for_new_institution(tmp_path,monkeypatch):
    app=create_app(tmp_path,testing=True);client=app.test_client();s=app.extensions['store'];calls=[]
    monkeypatch.setattr('coach.services.AI.call',lambda *a,**kw:(calls.append(1) or json.dumps({'website':'https://example.edu','career_url':'https://example.edu/careers'})))
    monkeypatch.setattr('coach.services.fetch',lambda url,cfg:('<title>Example University careers</title>Faculty jobs',url))
    s.save_settings({'network_enabled':True,'ai_enabled':True,'auto_university_discovery':True})
    s.add_job(job())
    client.get('/api/state')
    for _ in range(100):
        if list_universities(s)[0]['discovery_status']!='queued':break
        time.sleep(.02)
    assert list_universities(s)[0]['discovery_status']=='review'
    client.get('/api/state');client.get('/api/state')
    assert len(calls)==1
