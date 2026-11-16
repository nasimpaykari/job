import io,json,zipfile,sqlite3
from pathlib import Path
import pytest
from coach.core import Store
from coach.career import Career,check_document
from coach.workbench import Workbench,job_track
from coach.services import Coach,assess
from coach.universities import list_universities,facets
from coach.discovery import api_for_url,parse_api,discover_apis
from coach.packages import Packages,Removal
from coach.storage import Storage
from coach.web import create_app
from test_upgrade import setup,post,patch,token,PASSWORD
from test_studio_previews import setup as studio_setup,TEX,TOKEN

@pytest.fixture
def env(tmp_path):
    s=Store(tmp_path/'data');j=s.add_job({'institution':'Example Robotics Inc','title':'Software Engineer','url':'https://example.org/jobs/1','description':'Python experience is required. Robotics experience is preferred.'})[0]
    c=Career(s);txt='Developed navigation software using Python and ROS.';sid=c.add_source({'label':'CV','content':txt})['id'];fid=c.save_fact({'source_id':sid,'quote':txt,'text':txt,'category':'skills','status':'verified'})['id']
    return s,c,Workbench(s),j,fid

def criterion(fid,**extra):return dict({'label':'Python experience','quote':'Python experience is required.','importance':'Required','status':'Supported','fact_ids':[fid],'reviewed':True},**extra)
def story(fid,**extra):return dict({'title':'Navigation project','competency':'Problem solving','situation':'Robot navigation study','task':'Investigate navigation software','action':'Developed Python and ROS software','result':'Completed a navigation implementation','fact_ids':[fid],'verified':True},**extra)

def test_industry_classification_and_directory(env):
    s,c,w,j,f=env;assert job_track(s.job(j))=='Industry';u=list_universities(s)[0];assert u['organization_type']=='Company'
    s.edit_job(j,{'career_track':'Industry','work_mode':'Hybrid','seniority':'Senior','priority':'High','next_action_due':'2026-11-01'})
    d=facets(s.job(j),u);assert (d['career_track'],d['work_mode'],d['seniority'])==('Industry','Hybrid','Senior')
    assert job_track({'institution':'Example University','title':'Research Engineer'})=='Academic'
    assert job_track({'institution':'Unknown Labs','title':'Research Engineer'})=='Unknown'
    assert job_track({'institution':'Example University','career_track':'Industry'})=='Industry'

@pytest.mark.parametrize('patch_value',[{'career_track':'magic'},{'work_mode':'anywhere'},{'next_action_due':'2026-02-30'},{'next_action_due':'tomorrow'},{'salary_min':float('nan')},{'salary_min':float('inf')},{'salary_min':-1},{'apply_url':'javascript:alert(1)'},{'apply_url':'https://secret:password@example.com'},{'title':[]},{'priority':'urgent'}])
def test_invalid_job_values_rejected(env,patch_value):
    s,c,w,j,f=env
    with pytest.raises(ValueError):s.edit_job(j,patch_value)

def test_hourly_not_compared_to_annual(env):
    s,c,w,j,f=env;cfg=s.settings();cfg.update(minimum_salary=50000,exclude_unknown_salary=False)
    job=dict(s.job(j),salary_min=60,currency='USD',salary_period='Hourly')
    assert assess(job,cfg)['eligible'] and assess(job,cfg)['warnings']
    cfg['exclude_unknown_salary']=True;assert not assess(job,cfg)['eligible']
    job.update(salary_min=60000,salary_period='Annual');assert assess(job,cfg)['eligible']

def test_reviewed_criterion_enters_prompt_and_stales_on_posting_change(env):
    s,c,w,j,f=env;s.save_settings({'career_evidence_mode':'verified_facts'})
    cid=w.save_criterion(j,criterion(f))['id'];assert len(w.context(j,'Resume')['reviewed_role_criteria'])==1
    prompt=Coach(s).prompt(j,'Resume');assert 'Python experience' in prompt and 'commercial experience' in prompt
    s.edit_job(j,{'description':'Python and C++ experience is required.'})
    assert w.criteria(j)['criteria'][0]['stale'];assert not w.context(j,'Resume')['reviewed_role_criteria']
    with pytest.raises(ValueError,match='excerpt'):w.save_criterion(j,criterion(f),cid)

def test_changed_or_retired_facts_invalidate_criteria_and_stories(env):
    s,c,w,j,f=env;w.save_criterion(j,criterion(f));w.save_story(story(f))
    fact=c.state()['facts'][0];c.save_fact(dict(fact,status='retired'),f)
    assert w.criteria(j)['criteria'][0]['stale'] and w.stories()[0]['stale']
    assert not w.context(j,'interview')['reviewed_interview_stories']

@pytest.mark.parametrize('patch_value',[{'status':'Supported','fact_ids':[]},{'fact_ids':['missing']},{'fact_ids':[{}]},{'quote':'Invented excerpt'},{'importance':'Critical'}])
def test_criterion_validation(env,patch_value):
    s,c,w,j,f=env
    with pytest.raises(ValueError):w.save_criterion(j,criterion(f,**patch_value))

def test_unreviewed_gap_does_not_claim_skill(env):
    s,c,w,j,f=env;w.save_criterion(j,criterion(f,reviewed=False));assert not w.context(j,'Resume')['reviewed_role_criteria']
    w.save_criterion(j,criterion(f,label='C++',quote='',status='Gap',fact_ids=[]))
    assert w.context(j,'Resume')['reviewed_role_criteria'][0]['status']=='Gap'

def test_suggestions_exact_pending_and_idempotent(env):
    s,c,w,j,f=env;assert w.suggest(j)['added']==2;assert w.suggest(j)['added']==0
    assert all(not r['reviewed'] and r['quote'] in s.job(j)['description'] for r in w.criteria(j)['criteria'])

def test_cross_job_criterion_edit_delete_denied(env):
    s,c,w,j,f=env;cid=w.save_criterion(j,criterion(f))['id'];other=s.add_job({'institution':'Other','title':'Role','url':'https://example.org/other'})[0]
    for op in (lambda:w.delete_criterion(other,cid),lambda:w.save_criterion(other,criterion(f,quote=''),cid)):
        with pytest.raises(ValueError):op()
    w.delete_criterion(j,cid);assert not w.criteria(j)['criteria']

def test_story_verified_requires_evidence_and_complete_star(env):
    s,c,w,j,f=env
    for patch_value in [{'fact_ids':[]},{'result':''},{'fact_ids':['other']}]:
        with pytest.raises(ValueError):w.save_story(story(f,**patch_value))
    w.save_story(story(f,verified=False,result=''));assert not w.context(j,'interview')['reviewed_interview_stories']
    sid=w.save_story(story(f))['id'];assert len(w.context(j,'interview')['reviewed_interview_stories'])==1
    assert not w.context(j,'Resume')['reviewed_interview_stories']
    w.delete_story(sid);assert not w.context(j,'interview')['reviewed_interview_stories']

def test_merge_preserves_criteria_but_requires_review(env):
    s,c,w,j,f=env;other=s.add_job({'institution':'Other','title':'Role','description':'Other description','url':'https://example.org/other'})[0]
    w.save_criterion(j,criterion(f));s.merge(other,j)
    assert len(w.criteria(other)['criteria'])==1 and w.criteria(other)['criteria'][0]['stale']

def test_resume_checks_layout_bullet_and_size(env):
    s,c,w,j,f=env
    source=r'\documentclass[9pt]{article}\begin{document}\begin{tabular}{l}Experience\end{tabular}\begin{itemize}\item '+('long ' * 30)+r'\end{itemize}\end{document}'
    r=check_document(s,j,'Resume',source);codes={i['code'] for i in r['items']}
    assert {'resume_layout','resume_font','resume_bullets'}<=codes and r['page_limit']==1
    assert 'resume_layout' not in {i['code'] for i in check_document(s,j,'CV',source)['items']}

def test_archived_global_type_can_be_packaged(tmp_path,monkeypatch):
    s,coach,j=studio_setup(tmp_path,monkeypatch);doc=coach.approve_preview(TOKEN,j,'CV',TEX)['id'];Career(s).review_checklist(j);Removal(s).category('CV')
    assert Packages(s).create(j,{'document_ids':[doc],'acknowledge_warnings':True})['id']
    with pytest.raises(ValueError):check_document(s,j,'CV',TEX)

def test_health_no_mutation_and_reports_missing(env):
    s,c,w,j,f=env;before=s.path.stat().st_size;r=w.health();assert r['database_ok'] and r['verified_facts']==1 and r['last_backup_sync'] is None
    assert not (s.root/'documents').exists()
    with s.db() as db:db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?)',('missing',j,'Resume',TEX,'',1,'2026-01-01'))
    assert w.health()['missing_documents'][0]['id']=='missing'

def test_backup_restores_new_tables(env,tmp_path):
    s,c,w,j,f=env;w.save_criterion(j,criterion(f));w.save_story(story(f));out=tmp_path/'snapshot.zip';Storage(s).snapshot(out)
    with zipfile.ZipFile(out) as z:z.extractall(tmp_path/'restored')
    restored=Workbench(Store(tmp_path/'restored'));assert len(restored.criteria(j)['criteria'])==1 and len(restored.stories())==1

def test_api_permissions_and_full_backup(tmp_path):
    app=create_app(tmp_path);owner=setup(app);j=post(owner,'/api/jobs',{'institution':'Acme Inc','title':'Engineer'}).json['id']
    assert owner.get('/api/health').json['database_ok']
    response=post(owner,'/api/backup/full');assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.data)) as z:
        assert 'coach.db' in z.namelist() and not any('credentials' in n or 'accounts' in n for n in z.namelist())
    response.close()
    post(owner,'/api/users',{'username':'staff','password':PASSWORD});staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD})
    assert staff.get('/api/jobs/'+j+'/strategy').status_code==400
    space=owner.get('/api/state').json['active_workspace'];post(owner,'/api/share',{'username':'staff','role':'viewer'});post(staff,'/api/workspace',{'id':space})
    assert post(staff,'/api/jobs/'+j+'/strategy',{'label':'x'}).status_code==403
    assert staff.get('/api/health').status_code==403 and post(staff,'/api/backup/full').status_code==403
    assert owner.post('/api/stories',json={'title':'x'}).status_code==403

def test_ashby_api_detection_and_remote_not_inferred():
    endpoint,kind,employer=api_for_url('https://jobs.ashbyhq.com/example/abc');assert kind=='ashby' and employer=='example'
    assert api_for_url(endpoint)[1]=='ashby'
    assert discover_apis('<a href="https://jobs.ashbyhq.com/example">Careers</a>','https://example.com')
    job={'title':'Engineer','jobUrl':'https://jobs.ashbyhq.com/example/id','applyUrl':'https://jobs.ashbyhq.com/example/id/apply','descriptionPlain':'Python required','isRemote':True,'workplaceType':'Hybrid','isListed':True}
    rows=parse_api(json.dumps({'jobs':[job,dict(job,isListed=False)]}),'ashby','Example','Careers')
    assert len(rows)==1 and rows[0]['work_mode']=='Hybrid'
    del job['workplaceType'];assert parse_api(json.dumps({'jobs':[job]}),'ashby','Example','Careers')[0]['work_mode']=='Unknown'

def test_ats_html_entities_and_unsafe_links():
    payload={'jobs':[{'title':'Engineer','absolute_url':'https://example.com/job/1','content':'&lt;p&gt;Python required&lt;/p&gt;'}, {'title':'Bad','absolute_url':'javascript:alert(1)'}]}
    rows=parse_api(json.dumps(payload),'greenhouse','Example','Careers');assert len(rows)==1 and rows[0]['description']=='Python required'

def test_browser_fallback_keeps_names_sections_lists_and_macro_text():
    from coach.browser_pdf import latex_to_clean_html_for_pdf
    from bs4 import BeautifulSoup
    tex=r'\begin{document}{\LARGE\bfseries\color{Navy}Jordan Example}\par\ressection{Work Experience}\begin{itemize}\item Built navigation software.\end{itemize}\custom{Important text}\end{document}'
    html=latex_to_clean_html_for_pdf(tex);soup=BeautifulSoup(html,'html.parser')
    assert 'Jordan Example' in soup.get_text() and 'Work Experience' in soup.get_text() and 'Important text' in soup.get_text()
    assert soup.h1 and soup.h2 and soup.li

def test_industry_search_filters_and_named_employer_api(env,monkeypatch):
    from coach.search_plan import validate_targets,selection_reasons,build_plan
    from coach.universities import update_university
    from coach.services import Search
    s,c,w,j,f=env;u=list_universities(s)[0]
    update_university(s,u['id'],{'career_url':'https://jobs.ashbyhq.com/example','career_verified':True})
    targets=[{'id':'employer','kind':'university','value':u['id'],'label':u['name'],'enabled':True},{'id':'industry','kind':'track','value':'Industry','label':'Industry','enabled':True}]
    s.save_settings({'network_enabled':True,'saved_search_enabled':True,'search_targets':targets,'queries':[],'excluded_keywords':[]})
    job={'title':'New engineer','jobUrl':'https://jobs.ashbyhq.com/example/new','descriptionPlain':'Build software','isListed':True,'workplaceType':'Hybrid'}
    monkeypatch.setattr('coach.services.fetch',lambda *args:(json.dumps({'jobs':[job]}),args[0]))
    result=Search(s).run('both',lambda _:None);assert result['added']==1 and not result['errors']
    new=next(x for x in s.jobs() if x['title']=='New engineer');assert new['institution']==u['name'] and new['career_track']=='Industry'
    _,_,selected=build_plan(s,s.settings(),'both')
    assert selection_reasons({'institution':'Test University','title':'Professor'},selected)

def test_edit_application_identity_collision_is_rejected(env):
    s,c,w,j,f=env;other=s.add_job({'institution':'Other','title':'Role','url':'https://example.org/jobs/2'})[0]
    with pytest.raises(ValueError,match='belongs'):s.edit_job(j,{'apply_url':'https://example.org/jobs/2'})
    assert not s.job(j).get('apply_url')

def test_merge_collision_preserves_files_and_both_jobs(env):
    s,c,w,j,f=env;other=s.add_job({'institution':'Other','title':'Role','url':'https://example.org/jobs/2'})[0]
    a=s.document_folder(j);b=s.document_folder(other);(a/'notes.txt').write_text('first');(b/'notes.txt').write_text('second')
    with pytest.raises(ValueError,match='Conflicting'):s.merge(j,other)
    assert (a/'notes.txt').read_text()=='first' and (b/'notes.txt').read_text()=='second' and len(s.jobs())==2

def test_merge_move_failure_rolls_back_files(env,monkeypatch):
    import shutil
    s,c,w,j,f=env;other=s.add_job({'institution':'Other','title':'Role','url':'https://example.org/jobs/2'})[0]
    a=s.document_folder(j);b=s.document_folder(other)
    (b/'first.txt').write_text('first');(b/'second.txt').write_text('second');original=shutil.move;calls=[]
    def fail(src,dst):
        calls.append((src,dst))
        if len(calls)==2:raise OSError('Disk error')
        return original(src,dst)
    monkeypatch.setattr(shutil,'move',fail)
    with pytest.raises(OSError):s.merge(j,other)
    assert len(list(b.iterdir()))==2 and not list(a.iterdir()) and len(s.jobs())==2

def test_verified_facts_mode_card_coverage_ignores_unverified_profile(tmp_path):
    app=create_app(tmp_path,testing=True);client=app.test_client();s=app.extensions['store'];c=Career(s)
    j=s.add_job({'title':'Python engineer','institution':'Example Inc','description':'Python and Kubernetes required.'})[0]
    text='Developed Python software.';src=c.add_source({'label':'CV','content':text})['id'];c.save_fact({'source_id':src,'quote':text,'text':text,'category':'skills','status':'verified'})
    s.save_settings({'profile':{'verified':True,'facts':'Kubernetes expert'},'career_evidence_mode':'verified_facts','preferred_keywords':['Python','Kubernetes']})
    fit=client.get('/api/state').json['jobs'][0]['fit']
    assert 'kubernetes' in fit['not_in_profile'] and fit['verified_profile']

def test_lever_workplace_pay_period_and_country():
    row={'text':'Engineer','hostedUrl':'https://jobs.lever.co/example/id','descriptionPlain':'Python role','workplaceType':'remote','country':'US','categories':{'commitment':'Full-time'},'salaryRange':{'currency':'USD','interval':'hour','min':50,'max':70}}
    job=parse_api(json.dumps([row]),'lever','Example','Careers')[0]
    assert job['work_mode']=='Remote' and job['salary_period']=='Hourly' and job['salary_min']==50 and job['country']=='United States'

def test_industry_starter_templates_do_not_overwrite_settings(tmp_path):
    app=create_app(tmp_path,testing=True);client=app.test_client();s=app.extensions['store'];before=s.settings()['templates'].copy()
    for kind in ('industry-resume','industry-cover'):
        r=client.get('/api/templates/'+kind);assert r.status_code==200 and r.json['content'].startswith('% Industry')
    assert s.settings()['templates']==before
    assert client.get('/api/templates/credentials.json').status_code==404
