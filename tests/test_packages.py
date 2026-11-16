import hashlib,json,zipfile
import pytest
from coach.packages import Packages,Removal,category_allowed
from coach.career import Career
from coach.storage import Storage
from coach.core import Store
from coach.web import create_app
from test_studio_previews import setup as studio_setup,TEX,TOKEN
from test_upgrade import setup,post,patch,token,PASSWORD

@pytest.fixture
def env(tmp_path,monkeypatch):
    s,coach,j=studio_setup(tmp_path,monkeypatch)
    d=coach.approve_preview(TOKEN,j,'CV',TEX)
    Career(s).review_checklist(j)
    return s,coach,j,d['id'],Packages(s)

def build(env):
    s,coach,j,d,p=env
    return p.create(j,{'document_ids':[d],'acknowledge_warnings':True})

def test_package_exact_versions_export_and_manifest(env):
    s,coach,j,d,p=env
    newer=coach.approve_preview('b'*32,j,'CV',TEX.replace('Example','Newer'))
    out=build(env);raw=p.download(out['id']).read_bytes()
    assert (s.document_folder(j)/out['filename']).read_bytes()==raw
    with zipfile.ZipFile(p.download(out['id'])) as z:
        m=json.loads(z.read('manifest.json'));doc=m['documents'][0]
        assert doc['id']==d and doc['id']!=newer['id']
        assert z.read('LaTeX/01_CV.tex').decode()==TEX
        for item in doc['files'].values():assert hashlib.sha256(z.read(item['name'])).hexdigest()==item['sha256']
    s.edit_job(j,{'title':'Changed role'})
    assert p.download(out['id']).read_bytes()==raw
    assert p.list(j)['packages'][0]['manifest']['job']['title']=='Professor'

@pytest.mark.parametrize('ids',[None,[],['same','same'],[1]])
def test_invalid_selections(env,ids):
    s,coach,j,d,p=env
    with pytest.raises(ValueError):p.inspect(j,ids)

def test_reject_drafts_cross_job_and_multiple_versions(env):
    s,coach,j,d,p=env
    draft=coach.save_document(j,'Research',TEX)['id']
    other=s.add_job({'title':'Other','institution':'Other','url':'https://example.org/other'})[0]
    second=coach.approve_preview('b'*32,j,'CV',TEX)['id']
    for job,ids in [(j,[draft]),(other,[d]),(j,[d,second])]:
        with pytest.raises(ValueError):p.inspect(job,ids)

def test_review_requirements_and_selected_categories_required(env):
    s,coach,j,d,p=env;c=Career(s)
    r=c.save_requirement(j,{'label':'Research statement','kind':'document','category':'Research','required':True,'status':'confirmed'})
    assert p.inspect(j,[d])['errors']>=2
    c.review_checklist(j)
    with pytest.raises(ValueError,match='Required document'):build(env)
    row=c.requirements(j)['requirements'][0];c.save_requirement(j,dict(row,required=True,status='waived'),r['id']);c.review_checklist(j)
    assert build(env)['id']

def test_new_page_limit_rechecks_old_approval(env):
    s,coach,j,d,p=env
    from pypdf import PdfWriter
    pdf=s.document_version(d)/'document.pdf';writer=PdfWriter()
    for _ in range(2):writer.add_blank_page(612,792)
    with pdf.open('wb') as f:writer.write(f)
    c=Career(s);c.save_requirement(j,{'label':'One page CV','kind':'document','category':'CV','page_limit':1,'required':True,'status':'confirmed'});c.review_checklist(j)
    with pytest.raises(ValueError,match='limit'):build(env)

def test_compiler_repair_requires_warning_ack(env):
    s,coach,j,d,p=env
    (s.document_version(d)/'document.tex').write_text(TEX.replace('article','report'))
    assert p.inspect(j,[d])['warnings']>0
    with pytest.raises(ValueError,match='acknowledge'):p.create(j,{'document_ids':[d]})
    assert build(env)['id']

@pytest.mark.parametrize('corrupt',[False,True])
def test_missing_or_changed_archive_never_silently_rebuilt(env,corrupt):
    s,coach,j,d,p=env;out=build(env);path=p.download(out['id'])
    if corrupt:path.write_bytes(b'changed')
    else:path.unlink()
    with pytest.raises(ValueError,match='missing or changed'):p.download(out['id'])

def test_submission_confirm_date_once_and_status(env):
    s,coach,j,d,p=env;out=build(env);data={'submitted_on':'2026-10-31','portal':'University portal','confirmed':True,'update_status':True,'confirmation':'REF-123'}
    for invalid in [dict(data,confirmed=False),dict(data,submitted_on='bad')]:
        with pytest.raises(ValueError):p.submit(out['id'],invalid)
    p.submit(out['id'],data)
    assert s.job(j)['status']=='Submitted'
    assert p.list(j)['packages'][0]['submission']['confirmation']=='REF-123'
    with pytest.raises(ValueError,match='already'):p.submit(out['id'],data)

def test_submission_preserves_later_status(env):
    s,coach,j,d,p=env;out=build(env);s.edit_job(j,{'status':'Interviewing'})
    p.submit(out['id'],{'submitted_on':'2026-10-31','confirmed':True,'update_status':True})
    assert s.job(j)['status']=='Interviewing'

def test_job_delete_restore_and_rediscovery_suppression(env):
    s,coach,j,d,p=env;out=build(env);r=Removal(s);original=s.job(j);r.job(j)
    assert s.jobs()==[] and r.state()['jobs'][0]['id']==j
    assert (s.document_version(d)/'document.pdf').exists() and p.download(out['id']).exists()
    assert s.add_job(original)==(j,'ignored')
    with pytest.raises(ValueError,match='Restore'):s.edit_job(j,{'notes':'changed'})
    with pytest.raises(ValueError,match='Restore'):build(env)
    r.job(j,True);assert s.jobs()[0]['id']==j and s.job(j)['status']==original['status']

def test_global_type_restores_rules_and_template(env):
    s,coach,j,d,p=env;r=Removal(s);settings=s.settings();settings['templates']['CV']=TEX;settings['rules']['CV']['prompt']='Keep my style';s.save_settings(settings)
    r.category('CV');assert 'CV' not in s.settings()['document_categories']
    assert (s.document_version(d)/'document.pdf').exists()
    with pytest.raises(ValueError,match='Restore'):coach.prompt(j,'CV')
    r.category('CV',True);assert s.settings()['templates']['CV']==TEX and s.settings()['rules']['CV']['prompt']=='Keep my style'

def test_per_job_type_removal_is_scoped(env):
    s,coach,j,d,p=env;r=Removal(s)
    other=s.add_job({'title':'Other','institution':'Other','url':'https://example.org/other'})[0]
    r.category('CV',jid=j);category_allowed(s,other,'CV')
    with pytest.raises(ValueError,match='Restore'):category_allowed(s,j,'CV')
    assert 'CV' in s.settings()['document_categories']
    assert build(env)['id'] # Previously approved files remain usable.
    r.category('CV',True,j);category_allowed(s,j,'CV')

def test_last_type_cannot_be_deleted_and_transaction_rollback(env,monkeypatch):
    s,coach,j,d,p=env;s.save_settings({'document_categories':['CV']})
    with pytest.raises(ValueError,match='at least one'):Removal(s).category('CV')
    s.save_settings({'document_categories':['CV','Research']})
    def fail(*args):raise RuntimeError('write failed')
    monkeypatch.setattr(s,'event',fail)
    with pytest.raises(RuntimeError):Removal(s).category('CV')
    assert 'CV' in s.settings()['document_categories'] and not Removal(s).state()['types']

def test_backup_preserves_packages_deletions_and_submission(env,tmp_path):
    s,coach,j,d,p=env;out=build(env)
    p.submit(out['id'],{'submitted_on':'2026-10-31','confirmed':True});Removal(s).job(j)
    dest=tmp_path/'backup.zip';Storage(s).snapshot(dest)
    with zipfile.ZipFile(dest) as z:z.extractall(tmp_path/'restored')
    restored=Store(tmp_path/'restored')
    assert Packages(restored).download(out['id']).read_bytes()==p.download(out['id']).read_bytes()
    assert Packages(restored).list(j)['packages'][0]['submission']['submitted_on']=='2026-10-31'
    assert restored.jobs()==[] and Removal(restored).state()['jobs'][0]['id']==j

def test_api_permissions_workspace_and_csrf(tmp_path):
    app=create_app(tmp_path);owner=setup(app);jid=post(owner,'/api/jobs',{'institution':'U','title':'Role','url':'https://example.org/j'}).json['id']
    assert owner.delete('/api/jobs/'+jid).status_code==403
    post(owner,'/api/users',{'username':'staff','password':PASSWORD});staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD})
    assert staff.get('/api/jobs/'+jid+'/packages').status_code==400
    space=owner.get('/api/state').json['active_workspace'];post(owner,'/api/share',{'username':'staff','role':'viewer'});post(staff,'/api/workspace',{'id':space})
    assert post(staff,'/api/document-types/remove',{'name':'CV','job_id':jid}).status_code==403
    post(owner,'/api/share',{'username':'staff','role':'editor'})
    assert post(staff,'/api/document-types/remove',{'name':'CV'}).status_code==403
    assert post(staff,'/api/document-types/remove',{'name':'CV','job_id':jid}).status_code==200
    assert owner.delete('/api/jobs/'+jid,headers={'X-CSRF-Token':token(owner)}).status_code==200
    assert post(owner,'/api/jobs/'+jid+'/undelete').status_code==200
