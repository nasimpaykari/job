import json,tempfile,threading
from pathlib import Path
import pytest
from coach.core import Store
from coach.suite import Suite,Visual,render,tex,active_instruction
from coach.web import create_app
from test_upgrade import setup,post,PASSWORD
@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path);j=s.add_job({'title':'Robotics Engineer','institution':'Example Robotics','url':'https://example.org/job'})[0];return s,Suite(s),j

def doc(j,**kw):
 d={'name':'Example Candidate','contact':'candidate@example.org · New Jersey','job_id':j,'category':'Resume','sections':[{'title':'Experience','bullets':True,'lines':['Designed robot navigation experiments.','Evaluated visual landmarks across viewpoints.']},{'title':'Education','bullets':False,'lines':['Doctoral studies in Computer Science.']}]};d.update(kw);return d

def test_draft_revision_and_delete(env):
 s,w,j=env;d=doc(j);r=w.save('visual',d);d['revision']=1;w.save('visual',d,r['id'])
 with pytest.raises(ValueError,match='changed'):w.save('visual',d,r['id'])
 with pytest.raises(ValueError):w.delete('visual',r['id'],1)
 w.delete('visual',r['id'],2);assert not w.items('visual')

def test_preview_temporary_approval_three_formats(env,tmp_path):
 from pypdf import PdfReader
 from docx import Document
 s,w,j=env;v=Visual(s,tmp_path/'preview');d=doc(j);p=v.preview(d);assert p['pages']==1
 with s.db() as c:assert c.execute('SELECT count(*) FROM documents').fetchone()[0]==0
 changed=doc(j,name='Changed')
 with pytest.raises(ValueError,match='changed'):v.approve(dict(changed,token=p['token'],reviewed=True))
 r=v.approve(dict(d,token=p['token'],reviewed=True));assert not (v.root/p['token']).exists();folder=s.document_version(r['id']);assert all((folder/('document.'+ext)).exists() for ext in ('pdf','tex','docx'));assert 'Example Candidate' in PdfReader(folder/'document.pdf').pages[0].extract_text();assert 'Example Candidate' in '\n'.join(p.text for p in Document(folder/'document.docx').paragraphs)
 assert len(list(s.document_folder(j).glob('*.docx')))==1
 with pytest.raises(ValueError):v.approve(dict(d,token=p['token'],reviewed=True))

def test_resume_page_limit_and_tex_escape(env,tmp_path):
 s,w,j=env;v=Visual(s,tmp_path/'preview');d=doc(j,sections=[{'title':'Experience','bullets':True,'lines':['A long technical description '*20]*40}]);p=v.preview(d);assert p['pages']>1
 with pytest.raises(ValueError,match='one page'):v.approve(dict(d,token=p['token'],reviewed=True))
 assert r'\&' in tex(doc(j,name='A & B'));assert r'\input{' not in tex(doc(j,name=r'\input{secret}'))

def test_answer_export_scoping_ambiguity_and_sensitive(env):
 s,w,j=env
 def answer(value,job='',sensitive=False):return {'label':'Email','value':value,'field':'email','source':'User verified','job_id':job,'approved':True,'sensitive':sensitive}
 w.save('answer',answer('global@example.org'));w.save('answer',answer('job@example.org',j));w.save('answer',dict(answer('PRIVATE_DISCLOSURE_VALUE'),field='question',sensitive=True));b=w.bundle(j);assert b['fields'][0]['value']=='job@example.org';assert 'PRIVATE_DISCLOSURE_VALUE' not in json.dumps(b)
 w.save('answer',answer('ambiguous@example.org',j));assert not w.bundle(j)['fields'];assert 'email' in w.bundle(j)['unresolved']

def test_evaluated_activation_and_rollback(env):
 s,w,j=env
 d={'candidate':'concise','instruction':'Use short sentences.','prompt':'Rewrite verified fact.','output':'A concise fact.','reviewed':True,'factual':True,'rules':True,'format':True}
 w.save('evaluation',d)
 with pytest.raises(ValueError):w.activate('concise')
 w.save('evaluation',dict(d,prompt='Different prompt'));w.save('evaluation',dict(d,prompt='Third prompt'));w.activate('concise');assert active_instruction(s)=='Use short sentences.';w.activate('',True);assert not active_instruction(s)

def test_analytics_missing_dates_explicit(env):
 s,w,j=env;a=w.analytics();assert a['total']==1 and a['observed_response_days']['samples']==0;s.edit_job(j,{'status':'Submitted'});s.edit_job(j,{'status':'Interviewing'});a=w.analytics();assert a['observed_response_days']['samples']==1;assert a['sources'][0]['currently_interviewing']==1

def test_practice_revision_validation_and_delete(env):
 s,w,j=env;d={'job_id':j,'mode':'Interview','question':'Describe your work.','transcript':'I study navigation.','feedback':'Grounded response.','seconds':20,'reviewed':True};r=w.save('practice',d);assert w.items('practice')[0]['seconds']==20;w.delete('practice',r['id'],1);assert not w.items('practice')
 with pytest.raises(ValueError):w.save('practice',dict(d,seconds=-1))

def test_routes_auth_export_and_pwa(tmp_path):
 app=create_app(tmp_path);a=setup(app);s=app.extensions['store'];j=s.add_job({'title':'Engineer','institution':'Example','url':'https://example.org/j'})[0]
 assert a.get('/manifest.webmanifest').status_code==200;assert app.test_client().get('/sw.js').status_code==200;assert app.test_client().get('/api/suite/answer').status_code==401
 assert a.post('/api/suite/note',json={'text':'Hello'}).status_code==403
 post(a,'/api/users',{'username':'helper','password':PASSWORD});post(a,'/api/share',{'username':'helper','role':'viewer'});space=a.get('/api/state').json['active_workspace'];b=app.test_client();b.get('/login');post(b,'/api/login',{'username':'helper','password':PASSWORD});post(b,'/api/workspace',{'id':space});assert post(b,'/api/suite/note',{'text':'No'}).status_code==403;assert b.get('/api/suite/autofill/'+j).status_code==403
 assert post(a,'/api/suite/transcribe',{}).status_code==400

def test_cross_workspace_and_preview_paths(tmp_path):
 a=Suite(Store(tmp_path/'a'));b=Suite(Store(tmp_path/'b'));a.save('note',{'text':'Private workspace'});assert not b.items('note');v=Visual(a.s,tmp_path/'temp')
 with pytest.raises(ValueError):v.folder('../../a')

def test_snapshot_contains_new_records(env,tmp_path):
 import zipfile,sqlite3
 from coach.storage import Storage
 s,w,j=env;w.save('note',{'text':'Preserved note'});p=Storage(s).snapshot(tmp_path/'backup.zip')
 with zipfile.ZipFile(p) as z:z.extract('coach.db',tmp_path/'restore')
 c=sqlite3.connect(tmp_path/'restore/coach.db');assert c.execute('SELECT count(*) FROM suite_items').fetchone()[0]==1;c.close()

def test_merge_preserves_scoped_records(env):
 s,w,j=env;w.save('note',{'text':'Linked note','job_id':j});other=s.add_job({'title':'Another Engineer','institution':'Other','url':'https://example.org/other'})[0];s.merge(other,j);assert w.items('note')[0]['job_id']==other and w.items('note')[0]['revision']==2

def test_local_transcription_temp_cleanup(env,tmp_path,monkeypatch):
 import sys,types,io
 s,w,j=env;model=s.root/'speech_model';model.mkdir();app=create_app(s.root,testing=True);paths=[]
 class FakeModel:
  def __init__(self,path,**kw):assert kw['local_files_only'] is True
  def transcribe(self,path,**kw):paths.append(path);return iter([types.SimpleNamespace(text='Correct this transcript.',end=10)]),types.SimpleNamespace(language='en')
 monkeypatch.setitem(sys.modules,'faster_whisper',types.SimpleNamespace(WhisperModel=FakeModel));c=app.test_client();r=c.post('/api/suite/transcribe',data={'consent':'local','audio':(io.BytesIO(b'FAKE AUDIO'),'audio.webm')},content_type='multipart/form-data');assert r.status_code==200 and r.json['transcript']=='Correct this transcript.';assert not Path(paths[0]).exists()

def test_preference_is_in_generation_context(env):
 from coach.services import Coach
 s,w,j=env;s.save_settings({'profile':{'verified':True,'full_name':'Example Candidate','facts':'Verified example career facts for a synthetic test.'}});d={'candidate':'short','instruction':'Prefer short sentences.','output':'Reviewed output','reviewed':True,'factual':True,'rules':True,'format':True}
 for prompt in ('Prompt A','Prompt B','Prompt C'):w.save('evaluation',dict(d,prompt=prompt))
 w.activate('short');assert 'Prefer short sentences.' in Coach(s).prompt(j,'Resume','','','template');w.activate('',True);assert 'Prefer short sentences.' not in Coach(s).prompt(j,'Resume','','','template')

def test_section_suggestion_does_not_mutate_draft(env,monkeypatch):
 s,w,j=env;s.save_settings({'profile':{'verified':True,'full_name':'Example','facts':'Candidate-confirmed facts.'}});app=create_app(s.root,testing=True);seen=[]
 monkeypatch.setattr('coach.suite_routes.AI.call',lambda self,prompt,system:seen.append(prompt) or 'A reviewed suggestion.')
 d=doc(j);r=app.test_client().post('/api/suite/visual-suggest',json=dict(d,section=0));assert r.status_code==200;assert r.json['lines']==['A reviewed suggestion.'];assert 'document_rules' in seen[0];assert not w.items('visual')
