"""Reviewed training examples, workspace-owned LoRA versions and explicit activation."""
import hashlib,json,os,re,subprocess,sys,threading,time,venv
from pathlib import Path
from .core import uid,now
from .runtime import runtime_root,hidden
LOCKS={};LOCK=threading.Lock()
WORKER=Path(__file__).with_name('learning_worker.py')

def trainer_python(store):return runtime_root(store)/'trainer'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')

def trainer_env(store):
    env=dict(os.environ,HF_HOME=str(runtime_root(store)/'hf-cache'),HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
    if not store.settings()['network_enabled']:env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    return env

def install_trainer(store,log):
    if not store.settings()['network_enabled']:raise ValueError('Enable network access to install the optional training environment.')
    from .runtime import INSTALL_LOCK
    with INSTALL_LOCK:
        folder=runtime_root(store)/'trainer';log('Creating a separate Python environment for model training.')
        if not trainer_python(store).exists():venv.EnvBuilder(with_pip=True).create(folder)
        args=[str(trainer_python(store)),'-m','pip','install','-r',str(Path(__file__).resolve().parents[1]/'requirements-training.txt')]
        log('Downloading PyTorch, Transformers and PEFT. This can take several minutes and several GB.')
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=3600,stdin=subprocess.DEVNULL,**hidden())
        if p.returncode:raise ValueError('Trainer installation failed: '+p.stdout.decode(errors='replace')[-1800:])
        (folder/'ready').write_text(now())
    return {'message':'Training environment installed. Review examples before starting a training run.'}

class Learning:
    def __init__(self,store):self.store=store;self.root=store.root/'learning';self.root.mkdir(exist_ok=True)
    def examples(self):
        with self.store.db() as c:rows=[dict(r) for r in c.execute("SELECT id,job_id,category,prompt,content,created FROM documents WHERE approved=1 AND prompt<>'' ORDER BY created DESC")]
        seen=set();out=[]
        for row in rows:
            key=hashlib.sha256(row['content'].encode()).hexdigest()
            if key in seen:continue
            seen.add(key);out.append(row)
        return out
    def runs(self):
        result=[]
        for p in self.root.glob('*/run.json'):
            try:
                data=json.loads(p.read_text())
                with LOCK:running=LOCKS.get(str(self.root))
                if data.get('state')=='running' and not (running and running.locked()):
                    data.update(state='interrupted',error='App restarted before completion. Start a new run; the previous active model is unchanged.');p.write_text(json.dumps(data))
                result.append(data)
            except (OSError,ValueError):pass
        return sorted(result,key=lambda x:x['created'],reverse=True)
    def run_dir(self,rid):
        if not re.fullmatch(r'[a-f0-9]{32}',str(rid)):raise ValueError('Invalid training version')
        folder=self.root/rid
        if not (folder/'run.json').exists():raise ValueError('Training version is not in this workspace')
        return folder
    def train(self,ids,base,epochs,log):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',base):raise ValueError('Use a public Hugging Face model ID, organization/model. Remote custom code is disabled.')
        if not isinstance(ids,list):raise ValueError('Select reviewed examples')
        rows=[r for r in self.examples() if r['id'] in ids]
        # Group by job: near-identical drafts for one vacancy cannot leak into validation.
        groups=sorted(set(r['job_id'] for r in rows))
        if len(rows)<10 or len(groups)<3:raise ValueError('Select at least 10 distinct approved documents spanning at least 3 jobs. This permits a held-out job for evaluation.')
        if len(rows)>200:raise ValueError('Select at most 200 examples per run.')
        epochs=int(epochs)
        if not 1<=epochs<=3:raise ValueError('Choose 1–3 training epochs.')
        py=trainer_python(self.store)
        if not (py.parent.parent/'ready').exists():raise ValueError('Install the training environment first.')
        with LOCK:lock=LOCKS.setdefault(str(self.root),threading.Lock())
        if not lock.acquire(blocking=False):raise ValueError('This workspace already has a training run in progress.')
        rid=uid();folder=self.root/rid;folder.mkdir();meta={'id':rid,'created':now(),'base_model':base,'state':'running','examples':len(rows),'epochs':epochs}
        try:
            (folder/'run.json').write_text(json.dumps(meta))
            # Stable grouping leaves at least two training jobs.
            test_job=groups[-1];train=[r for r in rows if r['job_id']!=test_job];test=[r for r in rows if r['job_id']==test_job]
            for name,items in [('train',train),('eval',test)]:
                (folder/(name+'.json')).write_text(json.dumps([{'prompt':r['prompt'],'content':r['content']} for r in items]))
            config={'base':base,'epochs':epochs,'folder':str(folder),'max_length':1536}
            (folder/'config.json').write_text(json.dumps(config))
            log(f'Training on {len(train)} examples; evaluating on {len(test)} examples from a separate job. A first model download may take time.')
            with open(folder/'worker.log','w',encoding='utf-8') as out:
                proc=subprocess.Popen([str(py),str(WORKER),'train',str(folder/'config.json')],stdout=out,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,env=trainer_env(self.store),**hidden())
                start=time.monotonic();last=''
                while proc.poll() is None:
                    if (folder/'cancel').exists() or time.monotonic()-start>21600:
                        proc.terminate()
                        try:proc.wait(10)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait()
                        raise ValueError('Training cancelled or exceeded six hours. Previous active model is unchanged.')
                    if (folder/'progress.json').exists():
                        try:msg=json.loads((folder/'progress.json').read_text()).get('message','')
                        except (OSError,ValueError):msg=''
                        if msg and msg!=last:log(msg);last=msg
                    time.sleep(1)
            if proc.returncode:raise ValueError('Training failed. '+(folder/'worker.log').read_text(errors='replace')[-1800:])
            metrics=json.loads((folder/'metrics.json').read_text());meta.update(state='ready',metrics=metrics,eligible=metrics['candidate_loss']<=metrics['base_loss'])
            log('Evaluation finished. Review the loss comparison and a sample answer before activating.')
        except Exception as e:meta.update(state='failed',error=str(e));raise
        finally:(folder/'run.json').write_text(json.dumps(meta));lock.release()
        return meta
    def activate(self,rid):
        folder=self.run_dir(rid);meta=json.loads((folder/'run.json').read_text())
        if meta['state']!='ready' or not meta.get('eligible'):raise ValueError('Only a completed model with non-worse held-out loss can be activated. Loss alone does not establish factual quality.')
        s=self.store.settings()
        previous=s['learning_previous'] if s['provider']=='local_trained' else {'provider':s['provider'],'model':s['model']}
        self.store.save_settings({'trained_run':rid,'provider':'local_trained','model':meta['base_model'],'learning_previous':previous,'ai_enabled':True})
        return {'message':'Personal trained model activated. You can switch to another eligible version or return to your previous provider.'}
    def rollback(self):
        s=self.store.settings();old=s['learning_previous'] or {'provider':'gemini','model':''}
        self.store.save_settings({'provider':old['provider'],'model':old['model'],'trained_run':''})
        return {'message':'Previous provider restored; training versions are retained.'}
    def infer(self,rid,prompt,system):
        folder=self.run_dir(rid);meta=json.loads((folder/'run.json').read_text())
        if meta['state']!='ready':raise ValueError('Model is not ready')
        import tempfile
        with tempfile.TemporaryDirectory(dir=self.root,prefix='inference-') as tmp:
            tmp=Path(tmp);config=tmp/'request.json';result=tmp/'result.json'
            config.write_text(json.dumps({'folder':str(folder),'base':meta['base_model'],'prompt':prompt,'system':system,'result':str(result)}))
            p=subprocess.run([str(trainer_python(self.store)),str(WORKER),'infer',str(config)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=900,stdin=subprocess.DEVNULL,env=trainer_env(self.store),**hidden())
            if p.returncode:raise ValueError('Personal model inference failed: '+p.stdout.decode(errors='replace')[-1200:])
            return json.loads(result.read_text())['text']
