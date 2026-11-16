"""Optional real tiny-model training smoke test; not a coaching-quality benchmark."""
from pathlib import Path
import tempfile
import json,subprocess,sys
import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import GPT2Config,GPT2LMHeadModel,PreTrainedTokenizerFast
root=Path(tempfile.mkdtemp(prefix='jobcoach-learning-smoke-'));root.mkdir(exist_ok=True);base=root/'tiny-base';base.mkdir(exist_ok=True);run=root/'run';run.mkdir(exist_ok=True)
vocab={'[UNK]':0,'[PAD]':1,'[EOS]':2,'User':3,'Answer':4,':':5,'write':6,'a':7,'short':8,'letter':9,'I':10,'teach':11,'robotics':12,'.':13}
tokenizer=Tokenizer(WordLevel(vocab,unk_token='[UNK]'));tokenizer.pre_tokenizer=Whitespace()
tok=PreTrainedTokenizerFast(tokenizer_object=tokenizer,unk_token='[UNK]',pad_token='[PAD]',eos_token='[EOS]');tok.save_pretrained(base)
torch.manual_seed(7)
model=GPT2LMHeadModel(GPT2Config(vocab_size=len(vocab),n_positions=128,n_embd=32,n_layer=1,n_head=2,bos_token_id=2,eos_token_id=2,pad_token_id=1));model.save_pretrained(base,safe_serialization=True)
rows=[{'prompt':'write a short letter','content':'I teach robotics .'}]*4
for n in ['train','eval']:(run/(n+'.json')).write_text(json.dumps(rows))
cfg={'base':str(base),'epochs':1,'folder':str(run),'max_length':64};(run/'config.json').write_text(json.dumps(cfg))
worker=Path(__file__).resolve().parents[1]/'coach'/'learning_worker.py'
p=subprocess.run([sys.executable,str(worker),'train',str(run/'config.json')],capture_output=True,text=True,timeout=180)
print('Training exit',p.returncode,p.stderr[-2000:],flush=True)
if p.returncode:raise SystemExit(1)
print((run/'metrics.json').read_text(),flush=True)
from safetensors.torch import load_file
weights=load_file(run/'adapter'/'adapter_model.safetensors');assert any(torch.count_nonzero(v)>0 for k,v in weights.items() if 'lora_B' in k)
cfg.update(prompt='write a short letter',system='User',result=str(run/'answer.json'));(run/'infer.json').write_text(json.dumps(cfg))
p=subprocess.run([sys.executable,str(worker),'infer',str(run/'infer.json')],capture_output=True,text=True,timeout=180)
print('Inference exit',p.returncode,p.stderr[-1000:],flush=True)
if p.returncode:raise SystemExit(1)
assert json.loads((run/'answer.json').read_text())['text'];print('Actual weight update, adapter persistence and inference passed.',flush=True)
