import json
import pytest
from coach.core import Store
from coach.search_plan import build_plan
from coach.search_discovery import discover_state,import_candidates,load,persist
from coach.discovery import parse_feed,validate_feed
from coach.universities import list_universities,update_university
from coach.services import Search
from coach.web import create_app
from test_upgrade import setup,post,PASSWORD

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'data');s.save_settings({'network_enabled':True});return s

def feed():return {'list_path':'results','title':'name','url':'links.job','institution':'company','description':'body','location':'location','employer':'Fallback'}
def target():return {'id':'api1','kind':'api','value':'https://example.org/jobs.json','label':'Example API','enabled':True,'limit':2,'api':feed()}
def candidates(s):
    run={'id':'test-run','created':'2026-10-29','state':'NJ','method':'directory','source_total':1,'complete_within_source':True,'warnings':[],'items':[{'id':'one','name':'Test College','state':'NJ','country':'United States','website':'https://example.edu','source':'College Scorecard','source_id':'123','city':'Newark'}]};persist(s,run);return run

def test_api_plan_only_selected_feed(store):
    store.save_settings({'saved_search_enabled':True,'search_targets':[target()]});s,targets,selected=build_plan(store,store.settings(),'both')
    assert len(targets)==1 and targets[0][2]==2 and s['_api_sources'][target()['value']]==feed()

def test_feed_mapping_and_safe_urls():
    data={'results':[{'name':'Engineer','links':{'job':'/jobs/1'},'body':'<p>Build robots</p>','company':'One'},{'name':'Bad','links':{'job':'javascript:alert(1)'}}]}
    rows=parse_feed(json.dumps(data),feed(),'Source','https://example.org/api');assert len(rows)==1 and rows[0]['url']=='https://example.org/jobs/1' and rows[0]['description']=='Build robots'

@pytest.mark.parametrize('config',[{},dict(feed(),title='items[0]'),dict(feed(),unknown='field'),dict(feed(),url=23)])
def test_bad_api_mapping(config):
    with pytest.raises(ValueError):validate_feed(config)

def test_feed_search_respects_result_limit(store,monkeypatch):
    store.save_settings({'saved_search_enabled':True,'search_targets':[target()],'queries':[],'required_keywords':[],'excluded_keywords':[],'countries':[],'regions':[],'exclude_unknown_location':False,'exclude_unknown_salary':False,'minimum_salary':0})
    rows={'results':[{'name':'Engineer','company':'One','links':{'job':f'https://example.org/jobs/{i}'},'body':'Build robots'} for i in range(5)]}
    monkeypatch.setattr('coach.services.fetch',lambda u,s:(json.dumps(rows),u));r=Search(store).run('both',lambda x:None)
    assert r['added']==2 and r['pages']==1

def test_directory_requires_network_and_key(store):
    with pytest.raises(ValueError,match='API key'):discover_state(store,{'state':'NJ'})
    store.save_settings({'network_enabled':False})
    with pytest.raises(ValueError,match='network'):discover_state(store,{'state':'NJ'})

def test_directory_paginated_bounded_no_key_leak(store,monkeypatch):
    store.save_key('college_scorecard','private-key');calls=[]
    class Response:
        status_code=200
        def __init__(self,page):self.page=page
        def json(self):return {'metadata':{'total':102},'results':[{'id':i,'school.name':'College '+str(i),'school.state':'NJ','school.city':'Newark','school.school_url':'www.example.edu'} for i in range(self.page*100,min((self.page+1)*100,102))]}
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kwargs):calls.append((url,kwargs));return Response(kwargs['params']['_page'])
    monkeypatch.setattr('coach.search_discovery.requests.Session',Session)
    r=discover_state(store,{'state':'New Jersey','limit':101});run=load(store)[0]
    assert r['count']==101 and len(calls)==2 and not run['complete_within_source']
    assert calls[0][1]['headers']['X-Api-Key']=='private-key' and 'private-key' not in json.dumps(run)
    assert list_universities(store)==[]

def test_directory_partial_results_retained(store,monkeypatch):
    store.save_key('college_scorecard','key')
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kwargs):
            if kwargs['params']['_page']:raise RuntimeError('secret request details')
            class R:
                status_code=200
                def json(self):return {'metadata':{'total':120},'results':[{'id':i,'school.name':str(i),'school.state':'NJ'} for i in range(100)]}
            return R()
    monkeypatch.setattr('coach.search_discovery.requests.Session',Session)
    discover_state(store,{'state':'NJ'});run=load(store)[0]
    assert len(run['items'])==100 and not run['complete_within_source'] and 'secret' not in json.dumps(run)

def test_ai_candidates_are_not_confirmed(store,monkeypatch):
    monkeypatch.setattr('coach.services.AI.call',lambda *a:json.dumps([{'name':'Example College','state':'NJ','website':'www.example.edu'},{'name':'Wrong State','state':'CA','website':'https://other.edu'}]))
    discover_state(store,{'state':'NJ','method':'ai'});run=load(store)[0]
    assert len(run['items'])==1 and not run['complete_within_source'] and 'unverified' in run['items'][0]['source']
    assert not list_universities(store)

def test_review_import_and_confirmed_page_preserved(store,monkeypatch):
    candidates(store);r=import_candidates(store,{'run_id':'test-run','ids':['one']});u=list_universities(store)[0]
    assert r['imported']==1 and u['state']=='NJ' and not u['career_verified'] and not u['screen']
    update_university(store,u['id'],{'career_url':'https://example.edu/careers','career_verified':True,'screen':True})
    import_candidates(store,{'run_id':'test-run','ids':['one']});u=list_universities(store)[0]
    assert u['career_verified'] and u['screen'] and u['career_url']=='https://example.edu/careers'

def test_import_discovery_uses_selected_only(store,monkeypatch):
    candidates(store);calls=[];monkeypatch.setattr('coach.search_discovery.discover_careers',lambda s,key,log:calls.append(key))
    import_candidates(store,{'run_id':'test-run','ids':['one'],'find_careers':True});assert len(calls)==1
    with pytest.raises(ValueError):import_candidates(store,{'run_id':'test-run','ids':['bad']})

@pytest.mark.parametrize('data',[{'state':'ZZ'},{'state':'NJ','limit':0},{'state':'NJ','limit':True},{'state':'NJ','method':'bad'}])
def test_bad_discovery_options(store,data):
    with pytest.raises(ValueError):discover_state(store,data)

def test_directory_history_bounded_and_restart(store):
    for i in range(7):persist(store,{'id':str(i),'items':[]})
    assert len(load(store))==5 and load(Store(store.root))[0]['id']=='6'

def test_manual_career_page_and_owner_key_permissions(tmp_path):
    app=create_app(tmp_path);owner=setup(app)
    r=post(owner,'/api/universities',{'name':'Test University','state':'NJ','website':'https://example.edu','career_url':'https://example.edu/jobs','career_verified':True})
    assert r.status_code==200 and owner.get('/api/state').json['universities'][0]['career_verified']
    post(owner,'/api/users',{'username':'staff','password':PASSWORD});post(owner,'/api/share',{'username':'staff','role':'editor'})
    staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD});post(staff,'/api/workspace',{'id':owner.get('/api/state').json['active_workspace']})
    assert post(staff,'/api/search/discovery/key',{'key':'secret'}).status_code==403
    assert post(owner,'/api/search/discovery/key',{'key':'secret'}).status_code==200
    assert owner.get('/api/search/discovery').json['key_present'] and 'secret' not in owner.get('/api/state').text
    assert owner.post('/api/search/discovery',json={'state':'NJ'}).status_code==403

def test_large_university_selection_library_still_bounded_plan(store):
    from coach.search_plan import validate_targets
    rows=[{'id':str(i),'kind':'url','value':'https://example.org/'+str(i),'label':str(i),'enabled':i==0} for i in range(1001)]
    validate_targets(rows,store.settings()['sources'])
    store.save_settings({'saved_search_enabled':True,'search_targets':rows})
    assert len(build_plan(store,store.settings(),'both')[1])==1

def test_discovery_workspace_isolation_and_viewer(tmp_path):
    app=create_app(tmp_path);owner=setup(app);persist(app.extensions['store'],{'id':'private-run','items':[]})
    post(owner,'/api/users',{'username':'staff','password':PASSWORD});staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD})
    assert not staff.get('/api/search/discovery').json['runs']
    post(owner,'/api/share',{'username':'staff','role':'viewer'});post(staff,'/api/workspace',{'id':owner.get('/api/state').json['active_workspace']})
    for path in ['/api/search/discovery','/api/search/discovery/import','/api/search/discovery/key']:
        assert post(staff,path,{}).status_code==403
