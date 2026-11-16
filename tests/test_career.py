import json,sqlite3,zipfile
from pathlib import Path
import pytest
from pypdf import PdfWriter
from coach.core import Store
from coach.career import Career,check_document
from coach.services import Coach
from coach.storage import Storage
from coach.web import create_app
from test_studio_previews import setup as studio_setup,TEX,TOKEN
from test_upgrade import setup,post,patch,PASSWORD

@pytest.fixture
def env(tmp_path):
    s=Store(tmp_path/'data');jid=s.add_job({'institution':'Example University','title':'Faculty','url':'https://example.edu/job','description':'Submit a two-page research statement. A cover letter is required. Include three references.'})[0]
    return s,Career(s),jid

def fact(c,text='Developed robot navigation experiments in ROS and Gazebo.'):
    sid=c.add_source({'label':'CV','content':text})['id']
    fid=c.save_fact({'text':text,'quote':text,'source_id':sid,'category':'research','status':'verified'})['id']
    return sid,fid

def test_source_snapshot_dedup_and_exact_quote(env):
    s,c,j=env;sid,fid=fact(c)
    assert c.add_source({'label':'renamed','content':c.source(sid)['content']})['id']==sid
    assert c.state()['verified_count']==1
    with pytest.raises(ValueError,match='exact excerpt'):c.save_fact({'text':'Invented','quote':'not in source','source_id':sid})

def test_edit_resets_verification_and_retirement_excludes_fact(env):
    s,c,j=env;sid,fid=fact(c);f=c.state()['facts'][0]
    r=c.save_fact(dict(f,text='Investigated visual navigation.',status='verified'),fid)
    assert r['status']=='pending' and c.evidence()==[]
    f=c.state()['facts'][0];c.save_fact(dict(f,status='verified'),fid);assert len(c.evidence())==1
    c.save_fact(dict(f,status='retired'),fid);assert not c.evidence()

def test_suggestions_pending_repeat_idempotent(env):
    s,c,j=env;sid=c.add_source({'label':'CV','content':'Developed robotic navigation methods in ROS and Gazebo.\nSupervised engineering teams during control-system commissioning.'})['id']
    assert c.suggest_facts(sid)['added']==2
    assert c.suggest_facts(sid)['added']==0
    assert c.state()['verified_count']==0

def test_strict_mode_uses_verified_facts_and_scoped_preferences(env):
    s,c,j=env;fact(c);s.save_settings({'profile':{'full_name':'Test','verified':True,'facts':'UNSUPPORTED_OLD_PROFILE'},'career_evidence_mode':'verified_facts'})
    prompt=Coach(s).prompt(j,'CV')
    assert 'UNSUPPORTED_OLD_PROFILE' not in prompt and 'ROS and Gazebo' in prompt
    assert 'verified_evidence' in prompt

def test_strict_mode_requires_verified_facts(env):
    s,c,j=env;s.save_settings({'career_evidence_mode':'verified_facts'})
    with pytest.raises(ValueError,match='Verify career facts'):Coach(s).prompt(j,'CV')
    assert check_document(s,j,'CV',TEX)['errors']==1

def test_requirements_extract_review_and_staleness(env):
    s,c,j=env;assert c.extract_requirements(j)['added']==3
    assert c.extract_requirements(j)['added']==0
    rows=c.requirements(j)['requirements'];assert next(r for r in rows if r['category']=='Research')['page_limit']==2
    with pytest.raises(ValueError,match='pending'):c.review_checklist(j)
    for r in rows:c.save_requirement(j,dict(r,required=bool(r['required']),status='confirmed'),r['id'])
    assert c.review_checklist(j)['reviewed']
    s.edit_job(j,{'description':'Submit a one-page research statement.'})
    assert not c.requirements(j)['reviewed']
    assert all(r['stale'] for r in c.requirements(j)['requirements'])
    with pytest.raises(ValueError,match='Posting changed'):c.save_requirement(j,dict(rows[0],required=True,status='confirmed'),rows[0]['id'])
    assert c.extract_requirements(j)['added']==1

def test_minimum_and_shared_page_limits_not_invented(env):
    s,c,j=env;s.edit_job(j,{'description':'Submit at least two pages for the research statement. A CV and teaching statement together must fit five pages.'})
    c.extract_requirements(j)
    assert all(r['page_limit'] is None for r in c.requirements(j)['requirements'])

def test_manual_checklist_scope_and_review_invalidation(env):
    s,c,j=env;r=c.save_requirement(j,{'label':'Reference received','kind':'reference','status':'done'})
    assert c.review_checklist(j)['ready']
    c.save_requirement(j,{'label':'Portfolio','kind':'instruction','status':'confirmed'})
    assert not c.requirements(j)['reviewed'] and not c.requirements(j)['ready']
    other=s.add_job({'institution':'Other','title':'Role','url':'https://example.edu/other'})[0]
    with pytest.raises(ValueError):c.save_requirement(other,{'label':'Change','kind':'reference'},r['id'])

def test_approved_files_complete_confirmed_document_requirement(tmp_path,monkeypatch):
    s,coach,j=studio_setup(tmp_path,monkeypatch);c=Career(s)
    c.save_requirement(j,{'label':'CV','kind':'document','category':'CV','status':'confirmed'})
    assert c.review_checklist(j)['unresolved']==1
    doc=coach.approve_preview(TOKEN,j,'CV',TEX)
    report=c.requirements(j)
    assert report['ready'] and report['requirements'][0]['files'][0]['id']==doc['id']

def test_feedback_scopes_and_correction_verification(env):
    s,c,j=env;other=s.add_job({'institution':'Other','title':'Role','url':'https://example.edu/other'})[0]
    for scope in ['job','category','global']:
        f=c.add_feedback({'job_id':j,'category':'Resume','kind':'preference','scope':scope,'text':'PREFERENCE_'+scope})['id']
        assert not c.preferences(j,'Resume') if scope=='job' else True
        c.decide_feedback(f,{'text':'PREFERENCE_'+scope,'kind':'preference','scope':scope,'status':'approved'})
    assert len(c.preferences(j,'Resume'))==3 and len(c.preferences(other,'Resume'))==2 and len(c.preferences(other,'Cover'))==1
    f=c.add_feedback({'job_id':j,'category':'CV','text':'Corrected employment date.'})['id']
    c.decide_feedback(f,{'text':'Employment ended in 2021.','kind':'correction','scope':'global','status':'approved'})
    assert any(r['text']=='Employment ended in 2021.' and r['status']=='pending' for r in c.state()['facts'])
    assert all('Employment' not in r['text'] for r in c.preferences(j,'CV'))
    with pytest.raises(ValueError,match='already resolved'):c.decide_feedback(f,{'text':'Again','kind':'correction','status':'approved'})

def test_one_time_and_unclassified_feedback(env):
    s,c,j=env;f=c.add_feedback({'job_id':j,'category':'Resume','text':'Shorten the summary.'})['id']
    with pytest.raises(ValueError,match='Choose'):c.decide_feedback(f,{'text':'Shorten the summary.','status':'approved'})
    c.decide_feedback(f,{'text':'Shorten the summary.','kind':'one_time','scope':'global','status':'approved'})
    assert c.preferences(j,'Resume')[0]['scope']=='job' and not c.preferences(j,'CV')

@pytest.mark.parametrize('content,code',[(r'\documentclass{article}\begin{document}\placeholder{University}\end{document}','placeholders'),('not latex','latex_structure'),(TEX.replace('Example','Dates 2020--2021'),'hyphens')])
def test_text_checks(env,content,code):
    s,c,j=env;r=check_document(s,j,'CV',content)
    assert any(i['code']==code and i['severity'] in ('error','warning') for i in r['items'])

def test_url_hyphens_not_flagged_and_preamble_placeholder_ignored(env):
    s,c,j=env;text=r'\documentclass{article}\newcommand{\placeholder}[1]{[#1]}\begin{document}\href{https://example.org/a--b}{Website}\end{document}'
    r=check_document(s,j,'CV',text)
    assert not r['errors'] and not r['warnings']

def test_pdf_limit_blocks_approval_and_no_partial_save(tmp_path,monkeypatch):
    s,coach,j=studio_setup(tmp_path,monkeypatch)
    def two(store,folder,log):
        w=PdfWriter();w.add_blank_page(width=612,height=792);w.add_blank_page(width=612,height=792)
        with (folder/'document.pdf').open('wb') as f:w.write(f)
        return {'engine':'fixture','pages':2,'approximate':False,'repairs':[]}
    monkeypatch.setattr('coach.pdf_engine.compile_pdf',two)
    preview=coach.preview_document(TOKEN,j,'Resume',TEX);assert preview['checks']['errors']==1
    with pytest.raises(ValueError,match='limit is 1'):coach.approve_preview(TOKEN,j,'Resume',TEX,review_ack=True)
    with s.db() as c:assert c.execute('SELECT count(*) FROM documents').fetchone()[0]==0

def test_warnings_acknowledgement_and_saved_report(tmp_path,monkeypatch):
    s,coach,j=studio_setup(tmp_path,monkeypatch);tex=TEX.replace('Example','2020--2021')
    with pytest.raises(ValueError,match='acknowledgement'):coach.approve_preview(TOKEN,j,'CV',tex)
    saved=coach.approve_preview(TOKEN,j,'CV',tex,review_ack=True,baseline=TEX)
    with s.db() as c:
        report=json.loads(c.execute('SELECT report FROM document_checks WHERE document_id=?',(saved['id'],)).fetchone()[0])
        assert report['warnings'] and report['content_sha256']
        assert c.execute('SELECT kind,status FROM career_feedback').fetchone()[:]==('unclassified','pending')
        assert c.execute('SELECT count(*) FROM memory').fetchone()[0]==0

def test_legacy_approval_endpoint_cannot_bypass_checks(env):
    s,c,j=env
    with pytest.raises(ValueError,match='Save & approve'):Coach(s).save_document(j,'CV','plain text',approved=True)

def test_checks_use_current_requirements_after_preview(tmp_path,monkeypatch):
    s,coach,j=studio_setup(tmp_path,monkeypatch);coach.preview_document(TOKEN,j,'CV',TEX)
    # Placeholder introduced after preview must force recompile and fail checks.
    with pytest.raises(ValueError,match='placeholder'):coach.approve_preview(TOKEN,j,'CV',TEX.replace('Example',r'\placeholder{University}'))

def test_backup_restores_evidence_and_feedback(env,tmp_path):
    s,c,j=env;fact(c);c.add_feedback({'job_id':j,'category':'CV','text':'Prefer concise openings.'});c.extract_requirements(j)
    with zipfile.ZipFile(Storage(s).snapshot(tmp_path/'snapshot.zip')) as z:z.extractall(tmp_path/'restored')
    restored=Career(Store(tmp_path/'restored'))
    assert restored.state()['verified_count']==1 and len(restored.state()['feedback'])==1
    assert len(restored.requirements(j)['requirements'])==3

def test_sources_and_facts_are_workspace_isolated_and_viewer_read_only(tmp_path):
    app=create_app(tmp_path);owner=setup(app);sid=post(owner,'/api/career/sources',{'label':'private','content':'PRIVATE_EVIDENCE'}).json['id']
    post(owner,'/api/users',{'username':'staff','password':PASSWORD})
    staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD})
    assert staff.get('/api/career/sources/'+sid).status_code==400
    with owner.session_transaction() as ss:space=ss['space']
    post(owner,'/api/share',{'username':'staff','role':'viewer'});post(staff,'/api/workspace',{'id':space})
    assert staff.get('/api/career/sources/'+sid).status_code==200
    assert post(staff,'/api/career/sources',{'label':'x','content':'x'}).status_code==403
    assert owner.post('/api/career/sources',json={'label':'x','content':'x'}).status_code==403

def test_preference_can_be_revoked(env):
    s,c,j=env;f=c.add_feedback({'job_id':j,'category':'Resume','text':'Use concise bullets.'})['id']
    c.decide_feedback(f,{'text':'Use concise bullets.','kind':'preference','scope':'category','status':'approved'})
    assert c.preferences(j,'Resume')
    c.revoke_feedback(f)
    assert not c.preferences(j,'Resume')

def test_changed_requirement_rechecked_before_approval(tmp_path,monkeypatch):
    s,coach,j=studio_setup(tmp_path,monkeypatch)
    def two(store,folder,log):
        w=PdfWriter();w.add_blank_page(width=612,height=792);w.add_blank_page(width=612,height=792)
        with (folder/'document.pdf').open('wb') as f:w.write(f)
        return {'engine':'fixture','pages':2,'approximate':False,'repairs':[]}
    monkeypatch.setattr('coach.pdf_engine.compile_pdf',two)
    assert coach.preview_document(TOKEN,j,'Research',TEX)['checks']['errors']==0
    Career(s).save_requirement(j,{'label':'One-page research statement','kind':'document','category':'Research','status':'confirmed','page_limit':1})
    with pytest.raises(ValueError,match='limit is 1'):coach.approve_preview(TOKEN,j,'Research',TEX,review_ack=True)

def test_saved_check_evidence_is_immutable_after_fact_edit(tmp_path,monkeypatch):
    s,coach,j=studio_setup(tmp_path,monkeypatch);c=Career(s);sid,fid=fact(c)
    doc=coach.approve_preview(TOKEN,j,'CV',TEX)
    f=c.state()['facts'][0];c.save_fact(dict(f,text='Updated fact description.'),fid)
    with s.db() as db:r=json.loads(db.execute('SELECT report FROM document_checks WHERE document_id=?',(doc['id'],)).fetchone()[0])
    assert r['evidence'][0]['text']=='Developed robot navigation experiments in ROS and Gazebo.'
