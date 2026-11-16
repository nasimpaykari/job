import json
from pathlib import Path
import pytest
from pypdf import PdfWriter
from coach.core import Store
from coach.services import Coach, Tasks
from coach.storage import Storage

TEX=r'\documentclass{article}\begin{document}Example\end{document}'
TOKEN='a'*32

def setup(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');coach=Coach(store)
    jid=store.add_job({'institution':'Example University','title':'Professor','url':'https://example.org/job'})[0]
    def compile_pdf(store,folder,log):
        w=PdfWriter();w.add_blank_page(width=612,height=792)
        with (folder/'document.pdf').open('wb') as f:w.write(f)
        (folder/'compiler.log').write_text('mock compilation')
        return {'engine':'test','pages':1,'approximate':False,'repairs':[]}
    monkeypatch.setattr('coach.pdf_engine.compile_pdf',compile_pdf)
    return store,coach,jid

def test_preview_is_temporary_and_approval_saves_pair(tmp_path,monkeypatch):
    store,coach,jid=setup(tmp_path,monkeypatch)
    coach.preview_document(TOKEN,jid,'CV',TEX)
    assert not store.documents_root().exists()
    with store.db() as c:assert c.execute('SELECT count(*) FROM documents').fetchone()[0]==0
    edited=TEX.replace('Example','Updated')
    result=coach.approve_preview(TOKEN,jid,'CV',edited,'prompt',TEX)
    folder=Path(result['folder'])
    assert (folder/(result['filename']+'.tex')).read_text()==edited
    assert (folder/(result['filename']+'.pdf')).exists()
    assert not (Path(coach.preview_root.name)/TOKEN).exists()
    with store.db() as c:
        assert c.execute('SELECT approved FROM documents').fetchone()[0]==1
        assert c.execute('SELECT status FROM career_feedback').fetchone()[0]=='pending'
        assert c.execute('SELECT count(*) FROM memory').fetchone()[0]==0

def test_failure_never_approves_and_discard_removes_files(tmp_path,monkeypatch):
    store,coach,jid=setup(tmp_path,monkeypatch)
    def fail(*args):raise ValueError('Compile failed')
    monkeypatch.setattr('coach.pdf_engine.compile_pdf',fail)
    with pytest.raises(ValueError):coach.approve_preview(TOKEN,jid,'CV',TEX)
    assert not store.documents_root().exists()
    with store.db() as c:assert c.execute('SELECT count(*) FROM documents').fetchone()[0]==0
    coach.discard_preview(TOKEN)
    assert not (Path(coach.preview_root.name)/TOKEN).exists()

def test_storage_switch_and_backup_includes_external_documents(tmp_path,monkeypatch):
    store,coach,jid=setup(tmp_path,monkeypatch)
    doc=coach.approve_preview(TOKEN,jid,'CV',TEX)
    old=store.documents_root();new=tmp_path/'Google Drive'/'Coach Documents'
    store.change_documents_root(str(new))
    assert old.exists()
    assert (store.document_version(doc['id'])/'document.pdf').exists()
    assert Store(store.root).documents_root()==new
    import zipfile,sqlite3
    out=tmp_path/'snapshot.zip';Storage(store).snapshot(out)
    with zipfile.ZipFile(out) as z:
        assert any(n.startswith('documents/') and n.endswith('.pdf') for n in z.namelist())
        z.extract('coach.db',tmp_path/'restored')
    with sqlite3.connect(tmp_path/'restored'/'coach.db') as c:
        assert json.loads(c.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])['documents_root']==''
    with pytest.raises(ValueError):store.change_documents_root(str(old))
    assert store.documents_root()==new

def test_copy_failure_rolls_back_approval(tmp_path,monkeypatch):
    store,coach,jid=setup(tmp_path,monkeypatch)
    coach.preview_document(TOKEN,jid,'CV',TEX)
    import shutil
    original=shutil.copy2
    def fail(src,dst,*a,**kw):
        if str(dst).endswith('.pdf') and Path(dst).parent==store.document_folder(jid):raise OSError('Disk full')
        return original(src,dst,*a,**kw)
    monkeypatch.setattr(shutil,'copy2',fail)
    with pytest.raises(OSError):coach.approve_preview(TOKEN,jid,'CV',TEX)
    with store.db() as c:assert c.execute('SELECT count(*) FROM documents').fetchone()[0]==0
    assert list(store.document_folder(jid).iterdir())==[]
    assert TOKEN in coach.previews

def test_ephemeral_task_does_not_write_source_to_db(tmp_path):
    import time
    store=Store(tmp_path);tasks=Tasks(store)
    tid=tasks.submit(lambda log:{'content':'PRIVATE DRAFT'},temporary=True)
    for _ in range(100):
        if tasks.ephemeral[tid]['state']!='running':break
        time.sleep(.01)
    assert tasks.ephemeral[tid]['result']['content']=='PRIVATE DRAFT'
    with store.db() as c:assert c.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
