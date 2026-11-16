"""Noninteractive local LaTeX engines; no document upload or GUI installers."""
import os,re,subprocess
from .runtime import executable,hidden

def compile_one(store,folder,log,engine):
    settings=store.settings()
    if engine=='online':
        if not settings['network_enabled'] or not settings['pdf_remote_consent']:raise ValueError('Online PDF needs network access and the document-upload consent checkbox in PDF settings.')
        import requests
        log('Sending this LaTeX document to the selected public TeXLive.net compiler.')
        try:
            with requests.post('https://texlive.net/cgi-bin/latexcgi',files={'filecontents[]':('document.tex',(folder/'document.tex').read_bytes(),'text/plain'),'filename[]':(None,'document.tex'),'engine':(None,'pdflatex'),'return':(None,'pdf')},stream=True,timeout=(15,180),allow_redirects=False) as r:
                r.raise_for_status();content=bytearray()
                for part in r.iter_content(65536):
                    content.extend(part)
                    if len(content)>20_000_000:raise ValueError('Online compiler response exceeds 20 MB.')
            if not content.startswith(b'%PDF'):
                output=content.decode(errors='replace');(folder/'compiler.log').write_text(output,encoding='utf-8')
                raise ValueError('Online LaTeX compilation failed. Download compiler log for the returned details; no PDF was substituted.')
            (folder/'document.pdf').write_bytes(content);(folder/'compiler.log').write_text('Compiled by TeXLive.net after explicit document-upload consent.');return
        except requests.RequestException:raise ValueError('Online compiler could not be reached or timed out. Retry later or use local Tectonic; your source is saved.') from None
    exe=executable(store,engine)
    if not exe:raise ValueError('Open Profile & settings → PDF engine and click Install Tectonic. No MiKTeX setup is required.')
    env=dict(os.environ,openin_any='p',openout_any='p',TECTONIC_UNTRUSTED_MODE='1')
    if engine=='tectonic':
        # Official v33 bundle target, avoiding the relay redirect which can be blocked.
        args=[exe,'--untrusted','--keep-logs','--bundle','https://data1.fullyjustified.net/tlextras-2022.0r0.tar','--outdir',str(folder)]
        if not store.settings()['network_enabled']:args+=['--only-cached']
        args+=['document.tex'];limit=600
        log('Tectonic is compiling. First use may download LaTeX packages; no dialogs are required.')
    else:
        version=subprocess.run([exe,'--version'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,timeout=10,**hidden()).stdout.decode(errors='replace')
        args=[exe,'-no-shell-escape','-halt-on-error','-interaction=nonstopmode']
        if 'miktex' in version.lower():args+=['--disable-installer']
        args+=['document.tex'];limit=120
        log('Using legacy pdflatex with interactive package installation disabled.')
    try:
        proc=subprocess.run(args,cwd=folder,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,timeout=limit,**hidden())
    except subprocess.TimeoutExpired:
        (folder/'document.pdf').unlink(missing_ok=True)
        raise ValueError('PDF engine timed out. Tectonic may need a working connection to fill its package cache. Check network access, then retry; your source is saved.') from None
    output=proc.stdout.decode(errors='replace');(folder/'compiler.log').write_text(output,encoding='utf-8')
    if proc.returncode or not (folder/'document.pdf').exists():
        (folder/'document.pdf').unlink(missing_ok=True)
        if '429' in output:raise ValueError('The LaTeX package server is rate-limiting downloads. Retry later or select Online LaTeX with upload consent. Your source is saved; details are in the compiler log.')
        if '403' in output:raise ValueError('Access to the LaTeX package server was denied by the server or network. Use an unrestricted connection or select Online LaTeX with upload consent. See the compiler log.')
        missing=re.search(r"File [`']([^`'\n]+\.sty)' not found",output)
        if missing:raise ValueError('Missing '+missing[1]+'. Select Tectonic and enable network access for automatic package retrieval. See Download compiler log for details.')
        if engine=='tectonic' and not store.settings()['network_enabled']:raise ValueError('Tectonic could not compile using cached packages only. Enable network access for first use. See Download compiler log.')
        raise ValueError('LaTeX compilation failed: '+output[-2500:]+'\nUse Download compiler log for the full output.')


def compile_pdf(store,folder,log):
    from .latex_compat import prepare_latex
    from pypdf import PdfReader
    import tempfile,shutil,json
    source=(folder/'document.tex').read_text(encoding='utf-8')
    prepared,notes=prepare_latex(source)
    settings=store.settings();selected=settings['pdf_engine']
    engines=([e for e in ['pdflatex','tectonic'] if executable(store,e)] if selected=='auto' else [selected])
    if selected=='auto' and settings['network_enabled'] and settings['pdf_remote_consent']:engines.append('online')
    if selected=='auto' and settings['pdf_browser_fallback']:engines.append('browser')
    if not engines:raise ValueError('No compiler available. Install Tectonic or enable Online LaTeX with upload consent in PDF settings.')
    errors=[]
    (folder/'document.pdf').unlink(missing_ok=True)
    for engine in engines:
        log('Trying '+engine+' PDF generation.')
        with tempfile.TemporaryDirectory(dir=folder,prefix='compile-') as temp:
            work=__import__('pathlib').Path(temp);(work/'document.tex').write_text(prepared,encoding='utf-8')
            try:
                if engine=='browser':
                    from .browser_pdf import render_pdf
                    render_pdf(prepared,work/'document.pdf')
                else:compile_one(store,work,log,engine)
                pages=len(PdfReader(work/'document.pdf').pages)
                if not pages:raise ValueError('Compiler returned an empty PDF')
                shutil.copyfile(work/'document.pdf',folder/'document.pdf')
                (folder/'compiled-source.tex').write_text(prepared,encoding='utf-8')
                details={'engine':engine,'approximate':engine=='browser','repairs':notes,'pages':pages}
                (folder/'compilation.json').write_text(json.dumps(details))
                output=(work/'compiler.log').read_text(errors='replace') if (work/'compiler.log').exists() else 'Browser-generated approximate PDF.'
                (folder/'compiler.log').write_text('\n'.join(notes+errors+[engine+': '+output]),encoding='utf-8')
                return details
            except Exception as e:
                output=(work/'compiler.log').read_text(errors='replace') if (work/'compiler.log').exists() else str(e)
                errors.append(engine+': '+str(e)+'\n'+output)
                log(engine+' failed: '+str(e)[:500])
    (folder/'compiler.log').write_text('\n'.join(notes+errors),encoding='utf-8')
    raise ValueError('PDF generation failed. '+errors[-1][:1800]+'\nDownload compiler log for all attempts.')
