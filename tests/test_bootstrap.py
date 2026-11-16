from pathlib import Path
from types import SimpleNamespace
import subprocess
import pytest
import bootstrap

@pytest.mark.parametrize('version,supported',[('3.8',False),('3.9',False),('3.10',True),('3.12',True),('3.13',True)])
def test_version_probe(monkeypatch,version,supported):
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='/python\n'+version))
    assert bool(bootstrap.probe(['python'])) is supported

def test_incompatible_environment_preserved(tmp_path,monkeypatch):
    old=tmp_path/'.venv';old.mkdir();(old/'old-marker').write_text('preserve')
    data=tmp_path/'data';data.mkdir();(data/'coach.db').write_text('do not touch')
    monkeypatch.setattr(bootstrap,'probe',lambda command: str(command[0]) if (old/'new-marker').exists() else None)
    def create(command,cwd):
        old.mkdir();(old/'new-marker').write_text('new')
    monkeypatch.setattr(bootstrap,'run_checked',create)
    bootstrap.ensure_environment(tmp_path,'supported-python')
    saved=list(tmp_path.glob('.venv-incompatible-*'))
    assert len(saved)==1 and (saved[0]/'old-marker').read_text()=='preserve'
    assert (data/'coach.db').read_text()=='do not touch'

def test_valid_environment_reused(tmp_path,monkeypatch):
    (tmp_path/'.venv').mkdir()
    monkeypatch.setattr(bootstrap,'probe',lambda command:str(command[0]))
    def fail(*a,**k):raise AssertionError('Should not recreate compatible environment')
    monkeypatch.setattr(bootstrap,'run_checked',fail)
    bootstrap.ensure_environment(tmp_path,'supported-python')
    assert not list(tmp_path.glob('.venv-incompatible-*'))

def test_no_python_no_mutation(monkeypatch,capsys):
    monkeypatch.setattr(bootstrap,'find_python',lambda:None)
    def fail(*a,**k):raise AssertionError('Should not create environment without supported Python')
    monkeypatch.setattr(bootstrap,'ensure_environment',fail)
    assert bootstrap.main()==1
    assert 'Python 3.10 or newer' in capsys.readouterr().out
