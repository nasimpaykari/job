import json,subprocess
from pathlib import Path
import pytest
from coach.core import Store,uid
from coach.services import Coach,AI
from coach.pdf_engine import compile_one
def compile_pdf(store,folder,log):return compile_one(store,folder,log,store.settings()['pdf_engine'])
from coach.learning import Learning
from coach.runtime import install
from coach.web import create_app
from test_upgrade import setup,post,PASSWORD

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path);s.save_settings({'pdf_engine':'tectonic'});return s

def test_tectonic_cached_and_no_dialog_flags(store,monkeypatch,tmp_path):
    monkeypatch.setattr('coach.pdf_engine.executable',lambda *a:'/tectonic')
    calls=[]
    def run(args,**kw):
        calls.append((args,kw));(tmp_path/'document.pdf').write_bytes(b'%PDF');return subprocess.CompletedProcess(args,0,b'ok')
    monkeypatch.setattr('coach.pdf_engine.subprocess.run',run)
    compile_pdf(store,tmp_path,lambda _:None)
    args,kw=calls[0];assert '--only-cached' in args and '--untrusted' in args
    assert kw['stdin']==subprocess.DEVNULL and kw['timeout']==600
    store.save_settings({'network_enabled':True});compile_pdf(store,tmp_path,lambda _:None)
    assert '--only-cached' not in calls[-1][0]

def test_miktex_installer_disabled(store,monkeypatch,tmp_path):
    store.save_settings({'pdf_engine':'pdflatex'});monkeypatch.setattr('coach.pdf_engine.executable',lambda *a:'/pdflatex');calls=[]
    def run(args,**kw):
        calls.append(args)
        if '--version' in args:return subprocess.CompletedProcess(args,0,b'MiKTeX 25.12')
        (tmp_path/'document.pdf').write_bytes(b'%PDF');return subprocess.CompletedProcess(args,0,b'ok')
    monkeypatch.setattr('coach.pdf_engine.subprocess.run',run);compile_pdf(store,tmp_path,lambda _:None)
    assert '--disable-installer' in calls[-1] and '-interaction=nonstopmode' in calls[-1]

def test_pdf_timeout_removes_partial_file(store,monkeypatch,tmp_path):
    monkeypatch.setattr('coach.pdf_engine.executable',lambda *a:'/tectonic')
    def run(*a,**k):
        (tmp_path/'document.pdf').write_bytes(b'partial');raise subprocess.TimeoutExpired('tectonic',600)
    monkeypatch.setattr('coach.pdf_engine.subprocess.run',run)
    with pytest.raises(ValueError,match='timed out'):compile_pdf(store,tmp_path,lambda _:None)
    assert not (tmp_path/'document.pdf').exists()

def test_install_requires_network(store):
    with pytest.raises(ValueError,match='Enable network'):install(store,'tectonic',lambda _:None)

def legacy_approved(store,jid,content,prompt):
    # Historical approved training data fixture; new approvals require a compiled PDF.
    result=Coach(store).save_document(jid,'CV',content,prompt)
    with store.db() as c:c.execute('UPDATE documents SET approved=1 WHERE id=?',(result['id'],))
    return result

def examples(store):
    rows=[]
    for j in range(3):
        jid,_=store.add_job({'institution':'U','title':'Role','url':f'https://example.org/{j}'})
        for i in range(4):rows.append(legacy_approved(store,jid,f'Approved answer {j} {i}',f'Prompt {j}')['id'])
    return rows

def test_training_dedup_and_review_gate(store):
    ids=examples(store);learning=Learning(store)
    with pytest.raises(ValueError,match='at least 10'):learning.train(ids[:5],'org/model',1,lambda _:None)
    jid=store.jobs()[0]['id'];Coach(store).save_document(jid,'CV','unapproved','prompt',False)
    assert len(learning.examples())==12
    legacy_approved(store,jid,'Approved answer 0 0','prompt')
    assert len(learning.examples())==12

def test_real_training_orchestration_and_activation(store,monkeypatch):
    ids=examples(store);learning=Learning(store)
    trainer=store.root/'runtime'/'trainer';trainer.mkdir(parents=True);(trainer/'ready').touch()
    class Process:
        returncode=0
        def __init__(self,args,**kwargs):
            cfg=json.loads(Path(args[-1]).read_text());folder=Path(cfg['folder'])
            tr=json.loads((folder/'train.json').read_text());ev=json.loads((folder/'eval.json').read_text())
            assert set(x['prompt'] for x in tr).isdisjoint(x['prompt'] for x in ev)
            (folder/'metrics.json').write_text(json.dumps({'base_loss':2.0,'candidate_loss':1.5}))
        def poll(self):return 0
    monkeypatch.setattr('coach.learning.subprocess.Popen',Process)
    result=learning.train(ids,'org/model',1,lambda _:None)
    assert result['eligible'] and store.settings()['provider']=='gemini'
    learning.activate(result['id']);assert store.settings()['provider']=='local_trained'
    learning.rollback();assert store.settings()['provider']=='gemini'
    other=Learning(Store(store.root/'other'))
    with pytest.raises(ValueError,match='not in this workspace'):other.activate(result['id'])

def test_degraded_version_cannot_activate(store):
    learning=Learning(store);rid=uid();folder=learning.root/rid;folder.mkdir();(folder/'run.json').write_text(json.dumps({'state':'ready','eligible':False}))
    with pytest.raises(ValueError,match='non-worse'):learning.activate(rid)

def test_trained_provider_works_offline(store,monkeypatch):
    store.save_settings({'provider':'local_trained','trained_run':'abc','ai_enabled':True,'network_enabled':False})
    monkeypatch.setattr(Learning,'infer',lambda self,rid,prompt,system:'local trained result')
    assert AI(store).call('test')=='local trained result'

def test_runtime_admin_and_training_owner_permissions(tmp_path):
    app=create_app(tmp_path);owner=setup(app)
    post(owner,'/api/users',{'username':'staff','password':PASSWORD})
    staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD})
    assert post(staff,'/api/runtime/install',{'kind':'trainer'}).status_code==403
    space=owner.get('/api/state').json['active_workspace'];post(owner,'/api/share',{'username':'staff','role':'viewer'})
    post(staff,'/api/workspace',{'id':space})
    assert staff.get('/api/learning/status').status_code==403
    assert post(staff,'/api/learning/train',{'ids':[],'base':'org/model'}).status_code==403


def test_online_pdf_requires_explicit_consent(store,monkeypatch,tmp_path):
    store.save_settings({'pdf_engine':'online','network_enabled':True})
    with pytest.raises(ValueError,match='consent'):compile_pdf(store,tmp_path,lambda _:None)

def test_online_pdf_validates_response(store,monkeypatch,tmp_path):
    import requests
    store.save_settings({'pdf_engine':'online','network_enabled':True,'pdf_remote_consent':True})
    (tmp_path/'document.tex').write_text('synthetic fixture')
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_content(self,size):yield b'LaTeX error: undefined macro'
    monkeypatch.setattr(requests,'post',lambda *a,**k:Response())
    with pytest.raises(ValueError,match='Online LaTeX compilation failed'):compile_pdf(store,tmp_path,lambda _:None)
    assert not (tmp_path/'document.pdf').exists()
    assert 'undefined macro' in (tmp_path/'compiler.log').read_text()

def test_cancel_and_interrupt_are_workspace_scoped(store):
    learning=Learning(store);rid=uid();folder=learning.root/rid;folder.mkdir()
    (folder/'run.json').write_text(json.dumps({'id':rid,'state':'running','created':'now'}))
    assert learning.runs()[0]['state']=='interrupted'
    with pytest.raises(ValueError,match='Invalid'):learning.run_dir('../elsewhere')


def test_official_runtime_download_verifies_digest(store,monkeypatch,tmp_path):
    import hashlib,tarfile,io,shutil
    store.save_settings({'network_enabled':True})
    archive=tmp_path/'fixture.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        content=b'fixture executable';info=tarfile.TarInfo('tectonic');info.size=len(content);t.addfile(info,io.BytesIO(content))
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    asset={'name':'tectonic-test-x86_64-unknown-linux-musl.tar.gz','browser_download_url':'https://github.com/tectonic-typesetting/tectonic/releases/download/test/runtime.tar.gz','digest':'sha256:'+digest}
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'tag_name':'test','assets':[asset]}
    monkeypatch.setattr('coach.runtime.platform.system',lambda:'Linux');monkeypatch.setattr('coach.runtime.platform.machine',lambda:'x86_64')
    monkeypatch.setattr('coach.runtime.shutil.which',lambda _:None)
    monkeypatch.setattr('coach.runtime.requests.get',lambda *a,**k:Response())
    def copy(url,path,log):shutil.copyfile(archive,path);return digest
    monkeypatch.setattr('coach.runtime.download',copy)
    result=install(store,'tectonic',lambda _:None)
    assert (Path(result['path'])/'tectonic').read_bytes()==b'fixture executable'
    assert json.loads((Path(result['path'])/'release.json').read_text())['published_digest_verified']
