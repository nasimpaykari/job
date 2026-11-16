"""App-managed executables. Installs require an administrator and explicit UI action."""
import hashlib, json, os, platform, shutil, subprocess, tarfile, tempfile, threading, time, zipfile
from pathlib import Path
import requests

INSTALL_LOCK=threading.Lock()
SERVE_LOCK=threading.Lock()
PROCESSES={}

def hidden():
    return {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}

def runtime_root(store):return Path(getattr(store,'runtime_root',store.root/'runtime'))

def executable(store,kind):
    filename=kind+('.exe' if os.name=='nt' else '')
    local=runtime_root(store)/kind/filename
    return str(local) if local.is_file() else shutil.which(kind)

def download(url,path,log,limit=5_000_000_000):
    digest=hashlib.sha256();count=0;last=0;start=time.monotonic()
    with requests.get(url,stream=True,timeout=(15,60)) as r:
        r.raise_for_status()
        for part in r.iter_content(1024*1024):
            count+=len(part)
            if count>limit or time.monotonic()-start>3600:raise ValueError('Download exceeded size/time limit; retry on a faster connection.')
            with open(path,'ab') as f:f.write(part)
            digest.update(part)
            if time.monotonic()-last>3:log(f'Downloaded {count//1048576} MB');last=time.monotonic()
    return digest.hexdigest()

def install(store,kind,log):
    if kind not in ('tectonic','ollama'):raise ValueError('Unknown runtime')
    if not store.settings()['network_enabled']:raise ValueError('Enable network access before downloading software.')
    with INSTALL_LOCK:
        root=runtime_root(store);root.mkdir(parents=True,exist_ok=True)
        if executable(store,kind):return {'message':kind+' is already available.'}
        system=platform.system();machine=platform.machine().lower()
        if kind=='ollama' and system!='Windows':raise ValueError('Managed Ollama download currently supports Windows x64. Install Ollama normally on this platform, then use Start local engine.')
        if machine not in ('amd64','x86_64'):raise ValueError('Managed downloads currently support x64. Install the native runtime for your architecture.')
        repo='tectonic-typesetting/tectonic' if kind=='tectonic' else 'ollama/ollama'
        r=requests.get('https://api.github.com/repos/'+repo+'/releases/latest',timeout=30);r.raise_for_status();release=r.json()
        suffix={'Windows':'x86_64-pc-windows-msvc.zip','Linux':'x86_64-unknown-linux-musl.tar.gz','Darwin':'x86_64-apple-darwin.tar.gz'}.get(system)
        assets=[a for a in release['assets'] if (a['name']=='ollama-windows-amd64.zip' if kind=='ollama' else suffix and a['name'].endswith(suffix))]
        if len(assets)!=1:raise ValueError('No matching official release asset. See the runtime installation link in the guide.')
        a=assets[0];url=a['browser_download_url']
        if not url.startswith('https://github.com/'+repo+'/releases/download/'):raise ValueError('Unexpected release download address')
        log('Downloading '+a['name']+' from the official project release.')
        with tempfile.TemporaryDirectory(dir=root,prefix='install-') as tmp:
            tmp=Path(tmp);archive=tmp/'archive';stage=tmp/'unpacked';stage.mkdir()
            sha=download(url,archive,log)
            expected=a.get('digest')
            if not expected or not expected.startswith('sha256:'):raise ValueError('Official release has no SHA-256 digest; automatic installation stopped.')
            if sha!=expected[7:]:raise ValueError('Release checksum mismatch; nothing installed.')
            if a['name'].endswith('.zip'):
                with zipfile.ZipFile(archive) as z:
                    for entry in z.infolist():
                        target=(stage/entry.filename).resolve()
                        if not target.is_relative_to(stage.resolve()) or (entry.external_attr>>16)&0o170000==0o120000:raise ValueError('Unsafe archive member')
                    z.extractall(stage)
            else:
                # Tectonic archives need only the executable. Never extract links.
                with tarfile.open(archive) as t:
                    members=[m for m in t.getmembers() if m.isfile() and Path(m.name).name=='tectonic']
                    if len(members)!=1:raise ValueError('Runtime archive is missing its executable')
                    (stage/'tectonic').write_bytes(t.extractfile(members[0]).read());(stage/'tectonic').chmod(0o755)
            filename=kind+('.exe' if os.name=='nt' else '')
            matches=list(stage.rglob(filename))
            if len(matches)!=1:raise ValueError('Runtime archive has no unique executable')
            source=matches[0].parent
            (source/'release.json').write_text(json.dumps({'tag':release['tag_name'],'sha256':sha,'published_digest_verified':bool(expected)}))
            shutil.move(str(source),str(root/kind))
        log(kind+' installed in the app data directory.')
        return {'message':kind+' installed.','path':str(root/kind)}

def ollama_request(path,payload=None,timeout=15):
    s=requests.Session();s.trust_env=False
    r=s.get('http://127.0.0.1:11435'+path,timeout=timeout) if payload is None else s.post('http://127.0.0.1:11435'+path,json=payload,timeout=timeout)
    r.raise_for_status();return r.json()

def start_ollama(store,log):
    with SERVE_LOCK:
        try:ollama_request('/api/tags');return {'message':'Local engine is running on port 11435.'}
        except requests.RequestException:pass
        exe=executable(store,'ollama')
        if not exe:raise ValueError('Install the local engine first.')
        root=runtime_root(store);root.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ,OLLAMA_HOST='127.0.0.1:11435',OLLAMA_MODELS=str(root/'models'),OLLAMA_NO_CLOUD='1')
        with open(root/'ollama.log','ab') as out:
            proc=subprocess.Popen([exe,'serve'],env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,**hidden())
        PROCESSES[str(root)]=proc
        for _ in range(60):
            try:ollama_request('/api/tags',timeout=1);return {'message':'Local engine started.'}
            except requests.RequestException:
                if proc.poll() is not None:raise ValueError('Local engine stopped. See runtime/ollama.log.')
                time.sleep(.5)
        raise ValueError('Local engine has not become ready. See runtime/ollama.log.')

def pull_model(store,model,log):
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,100}',model) or 'cloud' in model.lower() or '..' in model:raise ValueError('Enter a local Ollama model name.')
    if not store.settings()['network_enabled']:raise ValueError('Enable network access to download a model.')
    start_ollama(store,log)
    s=requests.Session();s.trust_env=False;last=0;success=False;start=time.monotonic()
    with s.post('http://127.0.0.1:11435/api/pull',json={'model':model,'stream':True},stream=True,timeout=(10,120)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if time.monotonic()-start>7200:raise ValueError('Model download exceeded two hours; retry to resume.')
            if not line:continue
            d=json.loads(line)
            if d.get('error'):raise ValueError(d['error'])
            if d.get('status')=='success':success=True
            if time.monotonic()-last>2 or success:
                log(d.get('status','Downloading')+(' · '+str(round(100*d.get('completed',0)/d['total']))+'%' if d.get('total') else ''));last=time.monotonic()
    if not success:raise ValueError('Download ended before completion; retry to resume.')
    store.save_settings({'provider':'ollama','model':model,'ollama_url':'http://127.0.0.1:11435','ai_enabled':True})
    return {'message':'Local model downloaded and selected: '+model}


def install_browser(store,log):
    import sys
    if not store.settings()['network_enabled']:raise ValueError('Enable network access to install browser PDF support.')
    with INSTALL_LOCK:
        for args in [[sys.executable,'-m','pip','install','playwright>=1.50,<2'],[sys.executable,'-m','playwright','install','chromium']]:
            log('Installing browser PDF component. This may take several minutes.')
            r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,timeout=900,**hidden())
            if r.returncode:raise ValueError('Browser PDF setup failed: '+r.stdout.decode(errors='replace')[-1400:])
    return {'message':'Browser PDF component installed. Its output is approximate; enable it only if acceptable.'}
