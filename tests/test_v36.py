"""Upgrade safety: archive migration, recovery, cleanup and effective search budgets."""
import shutil, zipfile, json
from pathlib import Path
import pytest
from coach.core import Store
from coach.services import Coach,Search
from coach.storage import Storage
from coach.search_plan import build_plan
from coach.universities import list_universities,update_university
from test_studio_previews import setup,TEX,TOKEN
from test_deadlines_search import target

def test_legacy_version_migration_and_backup_restore(tmp_path,monkeypatch):
    store,coach,jid=setup(tmp_path,monkeypatch)
    doc=coach.approve_preview(TOKEN,jid,'CV',TEX)
    public=store.document_folder(jid);legacy=public/doc['id']
    shutil.move(store.document_version(doc['id']),legacy)
    unrelated=public/'my-notes';unrelated.mkdir();(unrelated/'notes.txt').write_text('keep')
    reopened=Store(store.root)
    assert not legacy.exists()
    assert (reopened.document_version(doc['id'])/'document.pdf').is_file()
    assert (unrelated/'notes.txt').read_text()=='keep'
    assert len(list(public.glob('CV_*.pdf')))==1
    snapshot=Storage(reopened).snapshot(tmp_path/'backup.zip')
    restored=tmp_path/'restored'
    with zipfile.ZipFile(snapshot) as z:
        assert 'document_versions/'+doc['id']+'/document.pdf' in z.namelist()
        z.extractall(restored)
    after=Store(restored)
    assert (after.document_version(doc['id'])/'document.tex').read_text()==TEX
    assert (after.document_folder(jid)/(doc['filename']+'.pdf')).is_file()

def test_recovers_history_if_user_deleted_uuid_folder(tmp_path,monkeypatch):
    store,coach,jid=setup(tmp_path,monkeypatch)
    doc=coach.approve_preview(TOKEN,jid,'CV',TEX)
    shutil.rmtree(store.document_version(doc['id']))
    restored=Store(store.root)
    assert (restored.document_version(doc['id'])/'document.tex').read_text()==TEX
    assert (restored.document_version(doc['id'])/'document.pdf').is_file()

def test_conflicting_legacy_history_is_not_deleted(tmp_path,monkeypatch):
    store,coach,jid=setup(tmp_path,monkeypatch)
    doc=coach.approve_preview(TOKEN,jid,'CV',TEX)
    private=store.document_version(doc['id']);legacy=store.document_folder(jid)/doc['id']
    shutil.copytree(private,legacy);(legacy/'document.tex').write_text('different version')
    reopened=Store(store.root)
    assert reopened.document_version(doc['id'])==legacy
    assert (legacy/'document.tex').read_text()=='different version'
    assert (private/'document.tex').read_text()==TEX

def test_status_cleanup_does_not_remove_other_job_or_saved_files(tmp_path,monkeypatch):
    store,coach,jid=setup(tmp_path,monkeypatch)
    saved=coach.approve_preview(TOKEN,jid,'CV',TEX)
    other=store.add_job({'institution':'Other University','title':'Faculty','url':'https://example.org/other'})[0]
    coach.preview_document(TOKEN,jid,'CV',TEX)
    coach.preview_document('b'*32,other,'CV',TEX)
    coach.discard_job_previews(jid)
    assert not (Path(coach.preview_root.name)/TOKEN).exists()
    assert (Path(coach.preview_root.name)/('b'*32)/'document.pdf').exists()
    assert (store.document_version(saved['id'])/'document.pdf').exists()

def test_independent_page_and_result_limits_persist_and_apply(tmp_path):
    s=Store(tmp_path);s.add_job({'institution':'Example University','title':'Faculty','url':'https://example.edu/job'})
    u=list_universities(s)[0];update_university(s,u['id'],{'career_url':'https://example.edu/careers','career_verified':True})
    targets=[target('board','LinkedIn'),dict(target('university',u['id']),limit=31),dict(target('url','https://another.edu/careers'),limit=12)]
    s.save_settings({'max_pages':70,'search_result_limit':24,'saved_search_enabled':True,'search_targets':targets})
    reopened=Store(tmp_path);cfg,urls,_=build_plan(reopened,reopened.settings(),'both')
    assert cfg['max_pages']==70
    assert all(limit==24 for name,url,limit in urls if name=='LinkedIn')
    assert next(limit for name,url,limit in urls if url=='https://example.edu/careers')==31
    assert next(limit for name,url,limit in urls if url=='https://another.edu/careers')==12

@pytest.mark.parametrize('patch',[{'max_pages':0},{'max_pages':501},{'search_result_limit':0},{'search_result_limit':1001},{'search_result_limit':True},{'search_targets':[dict(target('board','CRA'),limit=0)]}])
def test_invalid_search_budgets_rejected(tmp_path,patch):
    with pytest.raises(ValueError):Store(tmp_path).save_settings(patch)

def test_execution_collects_more_than_eight_and_respects_limit(tmp_path,monkeypatch):
    s=Store(tmp_path);s.save_settings({'network_enabled':True,'max_pages':1,'search_result_limit':11,'saved_search_enabled':True,'search_targets':[target('url','https://example.edu/jobs')]})
    records=[{'@type':'JobPosting','title':'Faculty '+str(i),'hiringOrganization':{'name':'Example University'},'description':'Faculty role','url':'https://example.edu/job/'+str(i)} for i in range(20)]
    html='<script type="application/ld+json">'+json.dumps(records)+'</script>'+''.join('<a href="'+r['url']+'">Faculty job</a>' for r in records)
    monkeypatch.setattr('coach.services.fetch',lambda url,cfg:(html,url))
    result=Search(s).run('both',lambda text:None)
    assert result['added']==11
