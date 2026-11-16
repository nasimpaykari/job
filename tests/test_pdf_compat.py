import subprocess,shutil
from pathlib import Path
import pytest
from pypdf import PdfWriter
from coach.core import Store
from coach.pdf_engine import compile_pdf
from coach.latex_compat import prepare_latex
from coach.browser_pdf import latex_to_clean_html_for_pdf

SOURCE=r'''\documentclass{article}
\usepackage[scaled=0.92]{FiraSans}
\usepackage{XCharter}
\begin{document}Candidate name. \textbf{Research}\end{document}'''

def test_specific_font_fix_preserves_body():
    repaired,notes=prepare_latex(SOURCE)
    assert repaired.startswith(r'\PassOptionsToPackage{no-math}{fontspec}')
    assert repaired.endswith(SOURCE) and notes
    ordinary=SOURCE.replace('FiraSans','OtherFont')
    assert prepare_latex(ordinary)==(ordinary,[])

def test_font_clash_reproduction_with_real_xelatex(tmp_path):
    exe=shutil.which('xelatex')
    if not exe:pytest.skip('XeLaTeX unavailable')
    # Reproduce the older font wrappers' load sequence with the real fontspec.
    (tmp_path/'FiraSans.sty').write_text(r'\ProvidesPackage{FiraSans}\DeclareOption*{}\ProcessOptions\relax\RequirePackage{fontspec}')
    (tmp_path/'XCharter.sty').write_text(r'\ProvidesPackage{XCharter}\RequirePackage[no-math]{fontspec}')
    def run(source):
        (tmp_path/'sample.tex').write_text(source)
        return subprocess.run([exe,'-no-shell-escape','-halt-on-error','-interaction=nonstopmode','sample.tex'],cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
    before=run(SOURCE)
    if b'fontspec.sty\' not found' in before.stdout:pytest.skip('fontspec unavailable')
    assert before.returncode!=0 and b'Option clash for package fontspec' in before.stdout
    after=run(prepare_latex(SOURCE)[0]);assert after.returncode==0,after.stdout.decode(errors='replace')[-1500:]
    assert (tmp_path/'sample.pdf').exists()

def test_auto_chain_with_consent_and_preserved_original(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');store.save_settings({'pdf_engine':'auto','network_enabled':True,'pdf_remote_consent':True})
    folder=tmp_path/'document';folder.mkdir();(folder/'document.tex').write_text(SOURCE)
    monkeypatch.setattr('coach.pdf_engine.executable',lambda store,engine:'/fake')
    attempts=[]
    def compile_one(store,work,log,engine):
        attempts.append(engine);assert (work/'document.tex').read_text().startswith(r'\PassOptionsToPackage')
        if engine!='online':raise ValueError('fixture failure')
        w=PdfWriter();w.add_blank_page(width=612,height=792)
        with open(work/'document.pdf','wb') as f:w.write(f)
    monkeypatch.setattr('coach.pdf_engine.compile_one',compile_one)
    result=compile_pdf(store,folder,lambda _:None)
    assert attempts==['pdflatex','tectonic','online']
    assert result['engine']=='online' and not result['approximate']
    assert (folder/'document.tex').read_text()==SOURCE
    assert 'fixture failure' in (folder/'compiler.log').read_text()

def test_no_silent_online_or_browser_fallback(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');store.save_settings({'pdf_engine':'auto','network_enabled':True})
    folder=tmp_path/'doc';folder.mkdir();(folder/'document.tex').write_text(SOURCE)
    monkeypatch.setattr('coach.pdf_engine.executable',lambda *a:'/fake')
    attempts=[]
    def fail(store,work,log,engine):attempts.append(engine);raise ValueError('failure')
    monkeypatch.setattr('coach.pdf_engine.compile_one',fail)
    with pytest.raises(ValueError):compile_pdf(store,folder,lambda _:None)
    assert attempts==['pdflatex','tectonic'] and not (folder/'document.pdf').exists()

def test_browser_fallback_escapes_markup():
    html=latex_to_clean_html_for_pdf(r'\begin{document}\textbf{Title}<script>alert(1)</script>\end{document}')
    assert '<strong>Title</strong>' in html and '<script>' not in html
