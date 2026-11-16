"""Structured documents, reviewed answers, outcome reports and practice records."""
import io,json,re,hashlib,shutil,tempfile,time
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter,defaultdict
from xml.sax.saxutils import escape
from .core import uid,now
from .career import text_field
from .packages import active_job,category_allowed

KINDS={'visual','answer','evaluation','practice','note'}
FIELDS={'first_name','last_name','email','phone','city','website','linkedin','question'}

def initialize(s):
 with s.lock:
  with s.db() as c:
   exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='suite_items'").fetchone();populated=c.execute('SELECT 1 FROM jobs LIMIT 1').fetchone()
  if not exists and populated:s.backup()
 with s.lock,s.db() as c:c.execute('CREATE TABLE IF NOT EXISTS suite_items(id TEXT PRIMARY KEY,kind TEXT NOT NULL,data TEXT NOT NULL,revision INTEGER NOT NULL,updated TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0)')

def signature(d):return hashlib.sha256(json.dumps(d,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def clean(d,kind):
 if not isinstance(d,dict):raise ValueError('Record must be an object.')
 out={}
 if kind=='visual':
  out={k:text_field(d,k,n,k in ('name','category','job_id')) for k,n in [('name',200),('category',100),('job_id',100),('contact',1000)]}
  if not isinstance(d.get('sections'),list) or not 1<=len(d['sections'])<=30:raise ValueError('Add 1–30 sections.')
  out['sections']=[]
  for section in d['sections']:
   if not isinstance(section,dict):raise ValueError('Invalid section.')
   lines=section.get('lines',[])
   if not isinstance(lines,list) or len(lines)>100 or any(not isinstance(x,str) or len(x)>3000 for x in lines):raise ValueError('Use up to 100 lines of 3000 characters per section.')
   out['sections'].append({'title':text_field(section,'title',150,False),'lines':[x.strip() for x in lines if x.strip()],'bullets':section.get('bullets') is True})
  if not any(x['lines'] for x in out['sections']):raise ValueError('Add document content before saving or previewing.')
 elif kind=='answer':
  out={k:text_field(d,k,n,k in ('label','value','field','source')) for k,n in [('label',300),('value',10000),('field',100),('source',1000),('job_id',100)]}
  if out['field'] not in FIELDS:raise ValueError('Unsupported field.')
  out.update(approved=d.get('approved') is True,sensitive=d.get('sensitive') is True)
 elif kind=='evaluation':
  out={k:text_field(d,k,n,k in ('candidate','prompt','output')) for k,n in [('candidate',200),('prompt',5000),('output',20000),('instruction',4000),('notes',3000)]}
  out['reviewed']=d.get('reviewed') is True
  for k in ('factual','rules','format'):out[k]=d.get(k) is True
 elif kind=='practice':
  out={k:text_field(d,k,n,k in ('job_id','mode','question','transcript')) for k,n in [('job_id',100),('mode',100),('question',3000),('transcript',30000),('feedback',20000)]}
  seconds=d.get('seconds',0)
  if type(seconds) not in (int,float) or not 0<=seconds<=7200:raise ValueError('Duration must be 0–7200 seconds.')
  out['seconds']=seconds;out['reviewed']=d.get('reviewed') is True
  if not out['reviewed']:raise ValueError('Review the corrected transcript first.')
 else:out={k:text_field(d,k,n,k=='text') for k,n in [('text',10000),('job_id',100)]}
 if len(json.dumps(out))>180000:raise ValueError('Record too large.')
 return out

class Suite:
 def __init__(self,s):self.s=s;initialize(s)
 def items(self,kind):
  if kind not in KINDS:raise ValueError('Unknown record type.')
  with self.s.db() as c:rows=c.execute('SELECT * FROM suite_items WHERE kind=? AND deleted=0 ORDER BY updated DESC',(kind,)).fetchall()
  return [dict(json.loads(r['data']),id=r['id'],revision=r['revision'],updated=r['updated']) for r in rows]
 def get(self,kind,i):
  return next((x for x in self.items(kind) if x['id']==i),None)
 def save(self,kind,d,i=''):
  if kind not in KINDS:raise ValueError('Unknown record type.')
  v=clean(d,kind)
  with self.s.lock,self.s.db() as c:
   c.execute('BEGIN IMMEDIATE')
   if v.get('job_id'):active_job(self.s,v['job_id'])
   if kind=='visual':category_allowed(self.s,v['job_id'],v['category'])
   row=c.execute('SELECT * FROM suite_items WHERE id=? AND kind=? AND deleted=0',(i,kind)).fetchone() if i else None
   if i and (not row or row['revision']!=d.get('revision')):raise ValueError('This record changed on another device. Reload; copy your unsaved text before replacing it.')
   i=i or uid();rev=row['revision']+1 if row else 1
   c.execute('INSERT OR REPLACE INTO suite_items VALUES(?,?,?,?,?,0)',(i,kind,json.dumps(v),rev,now()))
   self.s.event(c,v.get('job_id',''),'suite_'+kind+'_saved',{'id':i,'revision':rev})
  return {'id':i,'revision':rev}
 def delete(self,kind,i,rev):
  with self.s.lock,self.s.db() as c:
   cur=c.execute('UPDATE suite_items SET deleted=1,data=?,revision=revision+1,updated=? WHERE id=? AND kind=? AND revision=? AND deleted=0',('{}',now(),i,kind,rev))
   if not cur.rowcount:raise ValueError('Record changed or was deleted. Reload first.')
 def bundle(self,jid,ack=False):
  active_job(self.s,jid);answers=[]
  for a in self.items('answer'):
   if a['approved'] and not a['sensitive'] and a['job_id'] in ('',jid):answers.append({k:a[k] for k in ('id','label','value','field','source','updated')})
  # A job-specific answer overrides the same global field; ambiguous duplicates are unresolved.
  by=defaultdict(list)
  for a in self.items('answer'):
   if a['approved'] and not a['sensitive'] and a['job_id'] in ('',jid):by[a['field']].append(a)
  resolved=[];ambiguous=[]
  for field,values in by.items():
   chosen=[x for x in values if x['job_id']==jid] or values
   if len(chosen)==1 and field!='question':resolved.append({k:chosen[0][k] for k in ('field','value','label')})
   else:ambiguous.append(field)
  with self.s.db() as c:attachments=[dict(r) for r in c.execute('SELECT id,category,created FROM documents WHERE job_id=? AND approved=1 ORDER BY created DESC',(jid,))]
  return {'suggested_documents':attachments,'format':'job-coach-autofill-v1','job_id':jid,'created':now(),'fields':resolved,'unresolved':ambiguous,'notice':'Review on the target page. Sensitive answers, files and submission are never automatic.'}
 def evaluation(self):
  groups=defaultdict(list)
  for x in self.items('evaluation'):groups[x['candidate']].append(x)
  with self.s.db() as c:r=c.execute("SELECT value FROM kv WHERE key='suite_personalization'").fetchone()
  prefs=json.loads(r[0]) if r else {'active':'','instruction':'','previous':[]}
  return {'active':prefs['active'],'candidates':[{'name':name,'samples':len(v),'reviewed':sum(x['reviewed'] for x in v),'passed':sum(x['reviewed'] and all(x[k] for k in ('factual','rules','format')) for x in v),'eligible':len({x['prompt'] for x in v})>=3 and all(x['reviewed'] and all(x[k] for k in ('factual','rules','format')) for x in v) and len({x['instruction'] for x in v})==1,'instruction':v[0]['instruction']} for name,v in groups.items()]}
 def activate(self,name,rollback=False):
  with self.s.lock,self.s.db() as c:
   r=c.execute("SELECT value FROM kv WHERE key='suite_personalization'").fetchone();p=json.loads(r[0]) if r else {'active':'','instruction':'','previous':[]}
   if rollback:
    if not p['previous']:raise ValueError('No previous setting available.')
    old=p['previous'].pop();p.update(old)
   else:
    target=next((x for x in self.evaluation()['candidates'] if x['name']==name and x['eligible']),None)
    if not target:raise ValueError('Activation requires at least three reviewed samples, all checks passed, with one consistent instruction.')
    p['previous'].append({'active':p['active'],'instruction':p['instruction']});p.update(active=name,instruction=target['instruction']);p['previous']=p['previous'][-20:]
   c.execute("INSERT OR REPLACE INTO kv VALUES('suite_personalization',?)",(json.dumps(p),))
  return {'active':p['active']}
 def analytics(self):
  jobs=self.s.jobs();ids={j['id'] for j in jobs};status=Counter(j['status'] for j in jobs);groups=defaultdict(list)
  with self.s.db() as c:events=[dict(r) for r in c.execute("SELECT * FROM events WHERE kind IN ('edited','application_submitted','email_reviewed') ORDER BY created")]
  first={};history=defaultdict(list)
  for e in events:
   if e['job_id'] not in ids:continue
   d=json.loads(e['data']);s='Submitted' if e['kind']=='application_submitted' else d.get('status');history[e['job_id']].append({'at':e['created'],'status':s,'kind':e['kind']})
   if s in ('Submitted','Interviewing','Offer','Rejected','Withdrawn'):first.setdefault((e['job_id'],s),e['created'])
  with self.s.db() as c:
   submissions=[dict(r) for r in c.execute('SELECT p.id,p.job_id,p.manifest,s.data,s.created FROM application_packages p JOIN application_submissions s ON s.package_id=p.id')]
  versions=[{'package_id':x['id'],'job_id':x['job_id'],'submission':json.loads(x['data']),'documents':json.loads(x['manifest']).get('documents',[]),'current_status':next((j['status'] for j in jobs if j['id']==x['job_id']),'Unavailable')} for x in submissions if x['job_id'] in ids]
  waits=[]
  for j in jobs:
   key=(j['id'],'Submitted')
   if key in first:
    response=min([first[(j['id'],x)] for x in ('Interviewing','Offer','Rejected') if (j['id'],x) in first] or [''])
    if response and response>=first[key]:waits.append((datetime.fromisoformat(response)-datetime.fromisoformat(first[key])).total_seconds()/86400)
   for source in j.get('sources',[]) or ['Unknown']:groups[source].append(j)
  tracks=Counter(j.get('career_track','Unknown') or 'Unknown' for j in jobs)
  return {'submitted_versions':versions,'total':len(jobs),'statuses':dict(status),'tracks':dict(tracks),'sources':[{'source':k,'jobs':len(v),'currently_interviewing':sum(j['status']=='Interviewing' for j in v),'currently_offer':sum(j['status']=='Offer' for j in v)} for k,v in groups.items()], 'observed_response_days':{'samples':len(waits),'mean':round(sum(waits)/len(waits),1) if waits else None},'history':dict(history),'limitations':'Counts describe saved active cards and recorded status changes. Source attribution can overlap. Missing historical dates are not inferred; current stages are not conversion probabilities or causal evidence.'}

def active_instruction(s):
 with s.db() as c:r=c.execute("SELECT value FROM kv WHERE key='suite_personalization'").fetchone()
 return json.loads(r[0]).get('instruction','') if r else ''

def tex(v):
 def e(x):return ''.join({'\\':r'\textbackslash{}','&':r'\&','%':r'\%','$':r'\$','#':r'\#','_':r'\_','{':r'\{','}':r'\}','~':r'\textasciitilde{}','^':r'\textasciicircum{}'}.get(c,c) for c in x).replace('---',', ').replace('--',', ')
 lines=[r'\documentclass[11pt]{article}',r'\usepackage[margin=0.75in]{geometry}',r'\usepackage[T1]{fontenc}',r'\usepackage{helvet}',r'\renewcommand{\familydefault}{\sfdefault}',r'\usepackage{xcolor}',r'\definecolor{Navy}{HTML}{163D64}',r'\pagestyle{empty}',r'\setlength{\parindent}{0pt}',r'\begin{document}',r'{\Large\bfseries\color{Navy} '+e(v['name'])+'}\\',e(v['contact'])+r'\par']
 for s in v['sections']:
  if s['title']:lines.append(r'\section*{'+e(s['title'])+'}')
  if s['bullets']:lines.append(r'\begin{itemize}')
  lines.extend((r'\item ' if s['bullets'] else '')+e(t)+(r'\par' if not s['bullets'] else '') for t in s['lines'])
  if s['bullets']:lines.append(r'\end{itemize}')
 return '\n'.join(lines+[r'\end{document}'])

def render(v,folder):
 """One structured model exports three formats; layout may vary by renderer."""
 from docx import Document
 from docx.shared import Inches,Pt,RGBColor
 from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,ListFlowable,ListItem
 from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
 from reportlab.lib.colors import HexColor
 from reportlab.pdfbase import pdfmetrics
 from reportlab.pdfbase.ttfonts import TTFont
 fonts=Path(__file__).resolve().parent.parent/'assets'/'fonts'
 for name,file in [('CoachSans','DejaVuSans.ttf'),('CoachSansBold','DejaVuSans-Bold.ttf')]:
  if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(fonts/file)))
 from pypdf import PdfReader
 folder.mkdir(parents=True,exist_ok=True);(folder/'document.tex').write_text(tex(v),encoding='utf-8');(folder/'structured.json').write_text(json.dumps(v),encoding='utf-8')
 doc=Document();sec=doc.sections[0];sec.top_margin=sec.bottom_margin=Inches(.65);sec.left_margin=sec.right_margin=Inches(.75)
 normal=doc.styles['Normal'];normal.font.name='Arial';normal.font.size=Pt(10.5);normal.paragraph_format.space_after=Pt(5)
 p=doc.add_paragraph();run=p.add_run(v['name']);run.bold=True;run.font.size=Pt(20);run.font.color.rgb=RGBColor.from_string('163D64');doc.add_paragraph(v['contact'])
 styles=getSampleStyleSheet();styles.add(ParagraphStyle(name='Candidate',fontName='CoachSansBold',fontSize=20,leading=24,textColor=HexColor('#163D64'),spaceAfter=6));styles.add(ParagraphStyle(name='SectionTitle',fontName='CoachSansBold',fontSize=12,leading=15,textColor=HexColor('#163D64'),spaceBefore=9,spaceAfter=4,keepWithNext=True));styles['BodyText'].fontName='CoachSans';styles['BodyText'].fontSize=10.5;styles['BodyText'].leading=13
 blocks=[Paragraph(escape(v['name']),styles['Candidate']),Paragraph(escape(v['contact']),styles['BodyText']),Spacer(1,8)]
 for s in v['sections']:
  if s['title']:
   p=doc.add_paragraph();p.paragraph_format.keep_with_next=True;r=p.add_run(s['title']);r.bold=True;r.font.color.rgb=RGBColor.from_string('163D64');blocks.append(Paragraph(escape(s['title']),styles['SectionTitle']))
  for line in s['lines']:
   doc.add_paragraph(line,style='List Bullet' if s['bullets'] else 'Normal');blocks.append(Paragraph(('• ' if s['bullets'] else '')+escape(line),styles['BodyText']));blocks.append(Spacer(1,3))
 doc.save(folder/'document.docx');SimpleDocTemplate(str(folder/'document.pdf'),pagesize=(612,792),leftMargin=54,rightMargin=54,topMargin=47,bottomMargin=47).build(blocks)
 return len(PdfReader(folder/'document.pdf').pages)

class Visual:
 def __init__(self,s,root):self.s=s;self.root=Path(root);self.root.mkdir(exist_ok=True,parents=True)
 def prune(self):
  for p in self.root.iterdir():
   if p.is_dir() and time.time()-p.stat().st_mtime>3600:shutil.rmtree(p,ignore_errors=True)
 def preview(self,d):
  self.prune();v=clean(d,'visual');category_allowed(self.s,v['job_id'],v['category']);token=uid();folder=self.root/token
  try:
   pages=render(v,folder)
   from .career import check_document
   report=check_document(self.s,v['job_id'],v['category'],tex(v),folder/'document.pdf')
  except Exception:shutil.rmtree(folder,ignore_errors=True);raise
  return {'token':token,'hash':signature(v),'pages':pages,'checks':report,'warnings':[x['message'] for x in report['items'] if x['severity'] in ('warning','error')],'pdf_url':'/api/suite/previews/'+token+'/pdf'}
 def folder(self,token):
  if not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid preview.')
  p=self.root/token
  if not p.exists():raise ValueError('Preview expired. Preview again.')
  return p
 def approve(self,d):
  with self.s.lock:
   v=clean(d,'visual');category_allowed(self.s,v['job_id'],v['category']);src=self.folder(d.get('token',''));saved=json.loads((src/'structured.json').read_text())
   if saved!=v:raise ValueError('Content changed after preview. Preview again.')
   if d.get('reviewed') is not True:raise ValueError('Review the content and PDF before approval.')
   from pypdf import PdfReader
   if v['category']=='Resume' and len(PdfReader(src/'document.pdf').pages)>1:raise ValueError('Industry resume must fit one page.')
   from .career import check_document
   report=check_document(self.s,v['job_id'],v['category'],tex(v),src/'document.pdf')
   if report['errors']:raise ValueError('Document checks failed: '+'; '.join(x['message'] for x in report['items'] if x['severity']=='error'))
   did=uid();folder=self.s.document_version(did);base=re.sub('[^A-Za-z0-9_-]','_',v['category'])+'_'+time.strftime('%Y%m%d_%H%M%S')+'_'+did[:8];targets=[]
   try:
    shutil.copytree(src,folder)
    for ext in ('pdf','tex','docx'):
     target=self.s.document_folder(v['job_id'])/(base+'.'+ext);shutil.copy2(folder/('document.'+ext),target);targets.append(target)
    with self.s.db() as c:
     c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?)',(did,v['job_id'],v['category'],tex(v),'Structured editor; user reviewed',1,now()))
     c.execute('INSERT INTO document_checks VALUES(?,?,?)',(did,json.dumps(report),now()))
     self.s.event(c,v['job_id'],'structured_document_approved',{'document_id':did,'category':v['category']})
   except Exception:
    shutil.rmtree(folder,ignore_errors=True)
    for p in targets:p.unlink(missing_ok=True)
    raise
   shutil.rmtree(src,ignore_errors=True)
   return {'id':did,'filename':base,'folder':str(self.s.document_folder(v['job_id']))}
