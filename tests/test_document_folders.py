from coach.core import Store
from coach.services import Coach

def add(store,url='https://example.org/1',university='Example University',title='Assistant Professor of Computer Science'):
    return store.add_job({'institution':university,'title':title,'url':url})[0]

def test_named_job_folder_and_independent_versions(tmp_path):
    store=Store(tmp_path);jid=add(store);a=Coach(store).save_document(jid,'CV','first');b=Coach(store).save_document(jid,'Cover','second')
    folder=store.document_folder(jid)
    assert folder.name=='Example University__Assistant Professor of Computer Science'
    assert (store.document_version(a['id'])/'document.tex').read_text()=='first'
    assert (store.document_version(b['id'])/'document.tex').read_text()=='second'
    assert a['folder']==str(folder)

def test_collisions_and_unsafe_names(tmp_path):
    store=Store(tmp_path);a=add(store);b=add(store,'https://example.org/2')
    assert store.document_folder(a)!=store.document_folder(b)
    j=add(store,'https://example.org/3','../University: A/B','Professor <AI> | "ML"?')
    folder=store.document_folder(j)
    assert folder.parent==tmp_path/'documents'
    assert not any(c in folder.name for c in '<>:"/\\|?*')

def test_existing_uuid_folders_migrate_on_open(tmp_path):
    store=Store(tmp_path);jid=add(store)
    old=tmp_path/'documents'/jid/'version';old.mkdir(parents=True);(old/'document.pdf').write_bytes(b'old pdf');(old/'document.tex').write_text('old source')
    reopened=Store(tmp_path);folder=reopened.document_folder(jid)
    assert (folder/'version'/'document.pdf').read_bytes()==b'old pdf'
    assert (folder/'version'/'document.tex').read_text()=='old source'
    assert not (tmp_path/'documents'/jid).exists()
    assert Store(tmp_path).document_folder(jid)==folder
