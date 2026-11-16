"""Isolated optional LoRA trainer/inference worker. No remote model code is executed."""
import json,sys,random,math
from pathlib import Path

def main():
    import torch
    from transformers import AutoModelForCausalLM,AutoTokenizer
    from peft import LoraConfig,get_peft_model,PeftModel
    mode=sys.argv[1];cfg=json.loads(Path(sys.argv[2]).read_text());folder=Path(cfg['folder'])
    def progress(message):
        if mode=='infer':return
        p=folder/'progress.tmp';p.write_text(json.dumps({'message':message}));p.replace(folder/'progress.json')
    torch.manual_seed(42);random.seed(42);torch.set_num_threads(min(4,torch.get_num_threads()))
    device='cuda' if torch.cuda.is_available() else 'cpu'
    progress('Loading base model on '+device+'. First use downloads its public model files.')
    revision=json.loads((folder/'metrics.json').read_text()).get('base_revision') if mode=='infer' else None
    tok=AutoTokenizer.from_pretrained(cfg['base'],trust_remote_code=False,revision=revision)
    if tok.pad_token_id is None:tok.pad_token=tok.eos_token
    model=AutoModelForCausalLM.from_pretrained(cfg['base'],trust_remote_code=False,revision=revision,use_safetensors=True,torch_dtype=torch.float32).to(device)
    if mode=='infer':
        model=PeftModel.from_pretrained(model,str(folder/'adapter')).to(device);model.eval()
        msgs=[{'role':'system','content':cfg['system']},{'role':'user','content':cfg['prompt']}]
        rendered=tok.apply_chat_template(msgs,tokenize=False,add_generation_prompt=True) if tok.chat_template else cfg['system']+'\n'+cfg['prompt']+'\nAnswer:'
        context=int(getattr(model.config,'max_position_embeddings',8192));budget=min(4096,context//2)
        if len(tok.encode(rendered))>min(6144,context-budget):raise ValueError('Prompt exceeds this personal model’s context budget. Shorten the profile/draft or use a provider with a larger context.')
        tokens=tok(rendered,return_tensors='pt').to(device)
        with torch.no_grad():out=model.generate(**tokens,max_new_tokens=budget,do_sample=False,pad_token_id=tok.pad_token_id)
        text=tok.decode(out[0][tokens.input_ids.shape[1]:],skip_special_tokens=True)
        if not text.strip():raise ValueError('Model returned empty text')
        Path(cfg['result']).write_text(json.dumps({'text':text}));return
    maxlen=cfg['max_length'];truncated=0
    def encode(row):
        nonlocal truncated
        msgs=[{'role':'user','content':row['prompt']}]
        prompt=tok.apply_chat_template(msgs,tokenize=False,add_generation_prompt=True) if tok.chat_template else row['prompt']+'\nAnswer:'
        p=tok.encode(prompt,add_special_tokens=False);y=tok.encode(row['content'],add_special_tokens=False)+[tok.eos_token_id]
        if len(p)>512:p=p[:128]+p[-384:];truncated+=1
        if len(y)>maxlen-len(p):y=y[:maxlen-len(p)];truncated+=1
        return {'input_ids':torch.tensor([p+y],device=device),'attention_mask':torch.ones((1,len(p)+len(y)),dtype=torch.long,device=device),'labels':torch.tensor([[-100]*len(p)+y],device=device)}
    train=[encode(r) for r in json.loads((folder/'train.json').read_text())];valid=[encode(r) for r in json.loads((folder/'eval.json').read_text())]
    def evaluate():
        model.eval();total=0;tokens=0
        with torch.no_grad():
            for row in valid:
                n=int((row['labels']!=-100).sum());total+=float(model(**row).loss)*n;tokens+=n
        return total/max(tokens,1)
    base_loss=evaluate();progress('Base held-out loss: '+str(round(base_loss,4)))
    model=get_peft_model(model,LoraConfig(r=8,lora_alpha=16,lora_dropout=.05,target_modules='all-linear',task_type='CAUSAL_LM'))
    model.config.use_cache=False;optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=1e-4)
    steps=0;total=len(train)*cfg['epochs']
    for epoch in range(cfg['epochs']):
        model.train();random.shuffle(train)
        for row in train:
            optimizer.zero_grad();loss=model(**row).loss
            if not torch.isfinite(loss):raise ValueError('Nonfinite training loss; no model activated')
            loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step();steps+=1
            progress(f'Training step {steps}/{total} · loss {float(loss.detach()):.4f}')
    candidate_loss=evaluate()
    if not math.isfinite(candidate_loss):raise ValueError('Evaluation loss is invalid')
    model.save_pretrained(folder/'adapter',safe_serialization=True);tok.save_pretrained(folder/'adapter')
    (folder/'metrics.json').write_text(json.dumps({'base_revision':getattr(model.config,'_commit_hash',None),'base_loss':base_loss,'candidate_loss':candidate_loss,'truncated_sequences':truncated,'training_examples':len(train),'evaluation_examples':len(valid),'device':device,'note':'Held-out response-token loss, not factual accuracy or hiring performance. Long examples are truncated.'}))
    progress('Completed. Model remains inactive pending review.')

if __name__=='__main__':main()
