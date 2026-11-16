import json, sqlite3
from pathlib import Path
import pytest
from coach.core import Store, canonical_url
from coach.services import Coach, AI, assess, parse_page, structured_job
from coach.web import create_app

@pytest.fixture
def store(tmp_path):return Store(tmp_path/'data')
def job(url='https://example.com/jobs/one',**kw):return dict(institution='Example University',title='Assistant Professor',url=url,description='Robotics and computer vision',**kw)

def test_canonical_source_id():
    assert canonical_url('https://www.academicjobsonline.org/ajo/jobs/123/apply?utm_source=x')==canonical_url('https://academicjobsonline.org/ajo/jobs/123')
    assert canonical_url('https://x.org/details.cfm?JobCode=123')!=canonical_url('https://x.org/details.cfm?JobCode=124')
    assert canonical_url('https://x.org/A?utm_source=a&id=1#top')=='https://x.org/A?id=1'

def test_clean_profile_defaults_and_linkedin_is_not_an_automatic_source(store):
    settings=store.settings()
    assert settings['profile']=={'full_name':'','verified':False,'facts':''}
    assert settings['queries']==[] and settings['preferred_keywords']==[]
    assert settings['sources']['LinkedIn']['enabled'] is False

def test_linkedin_network_fetch_is_blocked():
    from coach.services import fetch
    with pytest.raises(ValueError,match='LinkedIn is user-led only'):
        fetch('https://www.linkedin.com/jobs/',{'network_enabled':True,'browser_render':False})

def test_ignore_persists_restart(store):
    a,_=store.add_job(job('https://academicjobsonline.org/ajo/jobs/123'))
    store.edit_job(a,{'status':'Ignored'})
    restarted=Store(store.root)
    b,result=restarted.add_job(job('https://academicjobsonline.org/ajo/jobs/123/apply'))
    assert (b,result)==(a,'ignored');assert len(restarted.jobs())==1
    restarted.edit_job(a,{'status':'New'});assert restarted.job(a)['status']=='New'

def test_similar_titles_are_not_deleted(store):
    a,_=store.add_job(job());b,_=store.add_job(job('https://example.com/jobs/two'))
    assert a!=b and len(store.candidates())==1

def test_merge_transfers_aliases_and_ignore(store):
    a,_=store.add_job(job());b,_=store.add_job(job('https://other.org/jobs/two'))
    store.edit_job(b,{'status':'Ignored','notes':'important'})
    store.merge(a,b)
    assert len(store.jobs())==1 and store.job(a)['status']=='Ignored'
    assert store.add_job(job('https://other.org/jobs/two'))==(a,'ignored')
    assert 'important' in store.job(a)['notes']

def test_edit_url_preserves_alias_and_blocks_collision(store):
    a,_=store.add_job(job());b,_=store.add_job(job('https://other.org/jobs/two'))
    with pytest.raises(ValueError):store.edit_job(a,{'url':'https://other.org/jobs/two'})
    store.edit_job(a,{'url':'https://new.org/jobs/one'});store.edit_job(a,{'status':'Ignored'})
    assert store.add_job(job())[1]=='ignored'

def test_settings_immediate_and_validation(store):
    store.save_settings({'network_enabled':True,'minimum_salary':80000})
    assert Store(store.root).settings()['network_enabled']
    with pytest.raises(ValueError):store.save_settings({'network_enabled':'false'})
    with pytest.raises(ValueError):store.save_settings({'invalid':True})

def test_salary_location_exclusions(store):
    s=store.settings();s.update(countries=['Canada'],minimum_salary=80000,exclude_unknown_salary=True)
    j=job(country='United States',salary_min=90000,currency='USD')
    assert not assess(j,s)['eligible']
    j['country']='Canada';assert assess(j,s)['eligible']
    j['currency']='CAD';assert not assess(j,s)['eligible']
    s['exclude_unknown_salary']=False;assert assess(j,s)['eligible']

def test_hiring_probability_not_used(store):
    result=assess(job(),store.settings());assert 'estimated_chance' not in result
    assert result['keyword_match']<100

def test_ai_off_no_network(store,monkeypatch):
    def fail(*a,**k):raise AssertionError('Network called')
    monkeypatch.setattr('requests.post',fail)
    with pytest.raises(ValueError,match='AI is off'):AI(store).call('test')

def test_provider_settings_refresh(store,monkeypatch):
    calls=[]
    class R:
        status_code=200
        def json(self):return {'choices':[{'message':{'content':'ok'}}]}
    monkeypatch.setattr('requests.post',lambda url,**kw:(calls.append((url,kw)) or R()))
    ai=AI(store);store.save_settings({'network_enabled':True,'ai_enabled':True,'provider':'openai','model':'chosen-model'});store.save_key('openai','test-key')
    assert ai.call('hello')=='ok';assert calls[0][1]['json']['model']=='chosen-model'
    store.save_settings({'model':'changed-model'});ai.call('hello');assert calls[-1][1]['json']['model']=='changed-model'

def test_category_rules_and_memory_used(store):
    s=store.settings();s['profile']={'full_name':'Test','verified':True};s['rules']['Cover']['prompt']='EXACT_CUSTOM_RULE';store.save_settings(s)
    jid,_=store.add_job(job());store.add_memory('Cover','APPROVED_STYLE',True);store.add_memory('Cover','UNAPPROVED_STYLE',False)
    prompt=Coach(store).prompt(jid,'Cover')
    assert 'EXACT_CUSTOM_RULE' in prompt and 'APPROVED_STYLE' in prompt and 'UNAPPROVED_STYLE' not in prompt

def test_document_versions_isolated(store):
    a,_=store.add_job(job());b,_=store.add_job(job('https://x.org/jobs/2'))
    coach=Coach(store);d1=coach.save_document(a,'Cover','first');d2=coach.save_document(b,'Cover','second');d3=coach.save_document(a,'Cover','third',baseline='first')
    assert len({d1['id'],d2['id'],d3['id']})==3
    assert (store.document_version(d1['id'])/'document.tex').read_text()=='first'
    assert not store.memory()

def test_compile_missing_engine_no_fake_pdf(store,monkeypatch):
    jid,_=store.add_job(job());coach=Coach(store);d=coach.save_document(jid,'Cover',r'\documentclass{article}\begin{document}Hi\end{document}')
    monkeypatch.setattr('shutil.which',lambda x:None)
    with pytest.raises(ValueError,match='Install Tectonic'):coach.compile(d['id'])
    assert not list(store.root.rglob('*.pdf'))

def test_failed_recompile_removes_stale_pdf(store,monkeypatch):
    store.save_settings({'network_enabled':True})
    jid,_=store.add_job(job());coach=Coach(store);d=coach.save_document(jid,'Cover',r'\documentclass{article}\begin{document}Hi\end{document}')
    pdf=store.document_version(d['id'])/'document.pdf';pdf.write_bytes(b'old pdf')
    monkeypatch.setattr('shutil.which',lambda x:'/fake/pdflatex')
    class Result:returncode=1;stdout=b'bad latex'
    monkeypatch.setattr('subprocess.run',lambda *a,**k:Result())
    with pytest.raises(ValueError,match='compilation failed'):coach.compile(d['id'])
    assert not pdf.exists()

def test_interview_memory_reused_and_score_saved(store,monkeypatch):
    store.save_settings({'profile':{'verified':True,'facts':'Test'}});jid,_=store.add_job(job());coach=Coach(store)
    monkeypatch.setattr(coach.ai,'call',lambda *a,**k:'Why this role?')
    session=coach.question(jid,'Academic')
    monkeypatch.setattr(coach.ai,'call',lambda *a,**k:json.dumps({'score':7,'strengths':['clear'],'improvements':['add evidence'],'sample_answer':'Example','follow_up':'Why?'}))
    coach.evaluate(session['id'],'My answer');ctx=coach.context(jid,'interview')
    assert ctx['recent_practice'][0]['score']==7 and 'add evidence' in ctx['recent_practice'][0]['feedback']

def test_legacy_migration_idempotent(store,tmp_path):
    src=tmp_path/'legacy-fixture.db';db=sqlite3.connect(src)
    db.executescript('CREATE TABLE jobs(id INTEGER,university TEXT,position_title TEXT,job_description_url TEXT,direct_apply_url TEXT,raw_description TEXT,source_board TEXT,application_status TEXT,url_hash TEXT); CREATE TABLE blacklist(url_hash TEXT);')
    db.executemany('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',[
        (1,'Example University','Assistant Professor','https://example.edu/jobs/1','','Teaching and research','Example Board','New',''),
        (2,'Example University','Assistant Professor','https://example.edu/jobs/1','','Duplicate listing','Example Board','New',''),
        (3,'Sample College','Research Scientist','https://sample.edu/jobs/2','','Robotics research','Sample Board','New','suppressed'),
    ])
    db.execute("INSERT INTO blacklist VALUES('suppressed')");db.commit();db.close()
    before=src.read_bytes();r=store.migrate(src)
    assert r['read']==3 and not r['errors'];assert len(store.jobs())==2
    assert r['duplicate']==1 and r['added']==2
    assert any(j['status']=='Ignored' for j in store.jobs())
    with store.db() as c:assert c.execute('SELECT count(*) FROM legacy').fetchone()[0]==3
    assert store.migrate(src)['already_imported']==3
    assert src.read_bytes()==before

def test_second_schema_blacklist(store,tmp_path):
    src=tmp_path/'second.db';c=sqlite3.connect(src)
    c.executescript('CREATE TABLE jobs(id INTEGER,url_hash TEXT,university TEXT,position_title TEXT,url TEXT,status TEXT); CREATE TABLE blacklist(url_hash TEXT);')
    c.execute('INSERT INTO jobs VALUES(1,?,?,?,?,?)',('hash','Example','Professor','https://example.com/job/1','New'));c.execute("INSERT INTO blacklist VALUES('hash')");c.commit();c.close()
    r=store.migrate(src);assert not r['errors'] and store.jobs()[0]['status']=='Ignored'

def test_structured_job_parser():
    html='<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Professor','hiringOrganization':{'name':'University'},'description':'Robotics','jobLocation':{'address':{'addressCountry':'US','addressRegion':'NY'}},'baseSalary':{'currency':'USD','value':{'minValue':90000,'unitText':'YEAR'}}})+'</script>'
    jobs,links,text,title=parse_page(html,'https://example.com/job/1');j=structured_job(jobs[0],'https://example.com/job/1','Test')
    assert j['salary_min']==90000 and j['country']=='United States'

def test_api_roundtrip_and_export(tmp_path):
    app=create_app(tmp_path/'app',testing=True);c=app.test_client()
    assert c.get('/').status_code==200
    jid=c.post('/api/jobs',json=job()).json['id']
    assert c.post('/api/jobs/'+jid+'/ignore').status_code==200
    assert c.get('/api/state').json['jobs'][0]['status']=='Ignored'
    assert c.get('/api/export').status_code==200
    assert c.patch('/api/jobs/'+jid,json={'status':'bad'}).status_code==400
    assert c.get('/api/jobs/missing').status_code==400

def test_local_session_auth_and_csrf(tmp_path):
    app=create_app(tmp_path/'auth');c=app.test_client()
    assert c.get('/api/state').status_code==401
    assert c.get('/?token='+app.extensions['launch_token']).status_code==302
    with c.session_transaction() as session:setup_token=session['csrf']
    assert c.post('/api/setup',json={'username':'owner','password':'Long-password-123'},headers={'X-CSRF-Token':setup_token}).status_code==200
    assert c.get('/api/state').status_code==200
    assert c.post('/api/jobs',json=job()).status_code==403
    with c.session_transaction() as s:token=s['csrf']
    assert c.post('/api/jobs',json=job(),headers={'X-CSRF-Token':token}).status_code==200
    assert c.get('/api/state',headers={'Host':'evil.example'}).status_code==403

def test_csv_formula_escaping(store):
    store.add_job(dict(job(),title='=HYPERLINK("bad")'))
    assert "'=HYPERLINK" in store.export_csv()

def test_merge_preserves_document_files(store):
    a,_=store.add_job(job());b,_=store.add_job(job('https://other.org/job/2'))
    d=Coach(store).save_document(b,'CV','original content')
    store.merge(a,b)
    assert (store.document_version(d['id'])/'document.tex').read_text()=='original content'
    with store.db() as c:assert c.execute('SELECT job_id FROM documents WHERE id=?',(d['id'],)).fetchone()[0]==a

def test_real_pdf_compile(store):
    import shutil
    if not shutil.which('pdflatex'):pytest.skip('pdflatex not installed')
    store.save_settings({'pdf_engine':'pdflatex'})
    jid,_=store.add_job(job());coach=Coach(store)
    d=coach.save_document(jid,'Cover',r'\documentclass{article}\begin{document}Page one\newpage Page two\end{document}')
    result=coach.compile(d['id'])
    assert result['pages']==2 and 'exceeds one page' in result['warning']

def test_search_mocked_records_and_ignore(store,monkeypatch):
    from coach.services import Search
    s=store.settings();s['network_enabled']=True;s['sources']={k:dict(v,enabled=k=='CRA') for k,v in s['sources'].items()};store.save_settings(s)
    html='<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Robotics professor','hiringOrganization':{'name':'Test University'},'description':'robotics'})+'</script>'
    monkeypatch.setattr('coach.services.fetch',lambda url,settings:(html,url))
    result=Search(store).run('boards',lambda x:None)
    assert result['added']==1
    jid=store.jobs()[0]['id'];store.edit_job(jid,{'status':'Ignored'})
    assert Search(store).run('boards',lambda x:None)['ignored']==1

def test_refresh_updates_source_fields_but_preserves_user_edits(store):
    jid,_=store.add_job(job(deadline='2027-01-01'))
    store.add_job(job(deadline='2027-02-01'))
    assert store.job(jid)['deadline']=='2027-02-01'
    store.edit_job(jid,{'deadline':'2027-03-01'})
    store.add_job(job(deadline='2027-04-01'))
    assert store.job(jid)['deadline']=='2027-03-01'

def test_shared_specific_application_url_links_cross_board_ignore(store):
    app_url='https://apply.interfolio.com/123456'
    a,_=store.add_job(job('https://board-a.org/job/role',apply_url=app_url))
    store.edit_job(a,{'status':'Ignored'})
    b,state=store.add_job(job('https://board-b.org/job/role',apply_url=app_url))
    assert a==b and state=='ignored'
    assert len(store.job(a)['source_urls'])==3

def test_generic_application_portal_does_not_merge_jobs(store):
    a,_=store.add_job(job('https://board.org/job/first',apply_url='https://university.edu/careers'))
    b,_=store.add_job(job('https://board.org/job/second',apply_url='https://university.edu/careers'))
    assert a!=b
