"""Owner-private email cache. Provider messages never execute instructions or mutate jobs."""
import base64,contextlib,hashlib,json,os,re,secrets,sqlite3,time
from datetime import datetime,timedelta,timezone
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlencode,quote
import requests
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet,InvalidToken
from .core import uid,now,STATUSES
from .packages import active_job
from .journey import moment

GMAIL='https://gmail.googleapis.com/gmail/v1/users/me'
GRAPH='https://graph.microsoft.com/v1.0/me'

def field(d,k,n=500,required=False):
 v=d.get(k,'')
 if not isinstance(v,str) or len(v)>n or (required and not v.strip()):raise ValueError('Check '+k+' (maximum '+str(n)+' characters).')
 return v.strip()
def address(v):
 if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',v) or '\r' in v or '\n' in v:raise ValueError('Enter one valid email address.')
 return v

def request_json(method,url,**kwargs):
 """Fixed endpoints only; no redirect or unbounded response. Never log provider bodies."""
 try:
  with requests.request(method,url,timeout=(10,35),allow_redirects=False,stream=True,**kwargs) as r:
   if not 200<=r.status_code<300:raise ValueError('Mail provider returned HTTP '+str(r.status_code)+'. Check permission, quota or reconnect; no automatic send retry.')
   data=bytearray()
   for chunk in r.iter_content(65536):
    data.extend(chunk)
    if len(data)>4_000_000:raise ValueError('Provider response too large. Use a narrower recruiting folder.')
   return json.loads(data) if data else {}
 except (requests.RequestException,json.JSONDecodeError):raise ValueError('Mail provider request did not complete. Check the connection.') from None

def endpoints(p,tenant='common'):
 if p=='gmail':return 'https://accounts.google.com/o/oauth2/v2/auth','https://oauth2.googleapis.com/token'
 if p=='outlook' and (tenant in ('common','consumers','organizations') or re.fullmatch(r'[a-fA-F0-9-]{36}',tenant)):
  base='https://login.microsoftonline.com/'+tenant+'/oauth2/v2.0/'
  return base+'authorize',base+'token'
 raise ValueError('Choose Gmail or Outlook and a valid tenant.')

def scopes(p,send=False):
 return ('https://www.googleapis.com/auth/gmail.readonly'+(' https://www.googleapis.com/auth/gmail.send' if send else '')) if p=='gmail' else 'offline_access User.Read Mail.Read'+(' Mail.Send' if send else '')

def oauth_start(d,redirect):
 p=d.get('provider');tenant=d.get('tenant','common');auth,_=endpoints(p,tenant)
 cfg={'provider':p,'tenant':tenant,'client_id':field(d,'client_id',500,True),'client_secret':field(d,'client_secret',1000),'send':d.get('send') is True}
 verifier=secrets.token_urlsafe(64);state=secrets.token_urlsafe(32)
 params=dict(client_id=cfg['client_id'],response_type='code',redirect_uri=redirect,scope=scopes(p,cfg['send']),state=state,code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),code_challenge_method='S256')
 if p=='gmail':params.update(access_type='offline',prompt='consent')
 else:params.update(response_mode='query',prompt='select_account')
 return auth+'?'+urlencode(params),dict(config=cfg,verifier=verifier,state=state,redirect=redirect,expires=time.time()+600)

def token_data(cfg,d):
 d=dict(d,client_id=cfg['client_id'])
 if cfg.get('client_secret'):d['client_secret']=cfg['client_secret']
 return request_json('POST',endpoints(cfg['provider'],cfg.get('tenant','common'))[1],data=d)

def gmail_text(payload):
 plain=[];html=[]
 def walk(part,depth=0):
  if depth>15 or part.get('filename'):return
  mime=part.get('mimeType','');raw=part.get('body',{}).get('data','')
  if raw and mime in ('text/plain','text/html'):
   try:t=base64.urlsafe_b64decode(raw[:200000]+'='*(-len(raw[:200000])%4)).decode('utf-8','replace')
   except (ValueError,TypeError):t=''
   (plain if mime=='text/plain' else html).append(t)
  for child in part.get('parts',[])[:100]:walk(child,depth+1)
 walk(payload)
 return ('\n'.join(plain) if plain else BeautifulSoup('\n'.join(html),'html.parser').get_text(' ',strip=True))[:30000]

class Mailbox:
 def __init__(self,s):
  self.s=s;self.path=s.root/'mail.db';self.keypath=s.root/'mail.key'
  with s.lock:
   if not self.keypath.exists():
    try:
     with self.keypath.open('xb') as f:f.write(Fernet.generate_key())
     os.chmod(self.keypath,0o600)
    except FileExistsError:pass
   self.fernet=Fernet(self.keypath.read_bytes())
   with self.db() as c:c.executescript('''CREATE TABLE IF NOT EXISTS config(id TEXT PRIMARY KEY,data TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,data TEXT NOT NULL,received REAL NOT NULL,hidden INTEGER DEFAULT 0);
   CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,data TEXT NOT NULL,revision INTEGER NOT NULL,status TEXT NOT NULL);
   ''')
   os.chmod(self.path,0o600)
   with s.db() as c:c.execute('CREATE TABLE IF NOT EXISTS mail_reviews(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,created TEXT NOT NULL)')
 @contextlib.contextmanager
 def db(self):
  c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA secure_delete=ON')
  try:yield c;c.commit()
  except BaseException:c.rollback();raise
  finally:c.close()
 def encode(self,d):return self.fernet.encrypt(json.dumps(d).encode()).decode()
 def decode(self,s):
  try:return json.loads(self.fernet.decrypt(s.encode()))
  except (InvalidToken,ValueError):raise ValueError('Cannot decrypt mail data. Restore matching mail.key or reset the mail files while the app is stopped.') from None
 def config(self):
  with self.db() as c:r=c.execute("SELECT data FROM config WHERE id='connection'").fetchone()
  return self.decode(r[0]) if r else {}
 def putconfig(self,d):
  with self.db() as c:c.execute("INSERT OR REPLACE INTO config VALUES('connection',?)",(self.encode(d),))
 def network(self):
  if not self.s.settings()['network_enabled']:raise ValueError('Enable network access in Profile & settings first.')
 def connect(self,pending,code):
  self.network();cfg=pending['config'];tok=token_data(cfg,dict(grant_type='authorization_code',code=code,redirect_uri=pending['redirect'],code_verifier=pending['verifier']))
  if not tok.get('access_token'):raise ValueError('Provider did not return authorization.')
  p=cfg['provider'];profile=request_json('GET',GMAIL+'/profile' if p=='gmail' else GRAPH+'?$select=id,mail,userPrincipalName',headers={'Authorization':'Bearer '+tok['access_token']})
  identity=profile.get('emailAddress') or profile.get('mail') or profile.get('userPrincipalName')
  if not identity:raise ValueError('Provider did not identify the account.')
  granted=[x.lower().removeprefix('https://graph.microsoft.com/') for x in tok.get('scope','').split()];send_scope='https://www.googleapis.com/auth/gmail.send' if p=='gmail' else 'mail.send'
  cfg.update(tokens=tok,expires=time.time()+int(tok.get('expires_in',3600)),identity=identity,generation=uid(),folder='INBOX' if p=='gmail' else 'inbox',folder_name='Inbox',retention=30,send=cfg['send'] and send_scope in granted)
  with self.s.lock:self.clear();self.putconfig(cfg)
  return identity
 def access(self):
  self.network();cfg=self.config()
  if not cfg.get('tokens'):raise ValueError('Connect an email account first.')
  if cfg['expires']<time.time()+60:
   refresh=cfg['tokens'].get('refresh_token')
   if not refresh:raise ValueError('Authorization expired. Reconnect this account.')
   tok=token_data(cfg,dict(grant_type='refresh_token',refresh_token=refresh));tok.setdefault('refresh_token',refresh)
   if not tok.get('access_token'):raise ValueError('Reconnect the mail account.')
   cfg.update(tokens=tok,expires=time.time()+int(tok.get('expires_in',3600)));self.putconfig(cfg)
  return cfg,{'Authorization':'Bearer '+cfg['tokens']['access_token']}
 def folders(self):
  with self.s.lock:
   cfg,h=self.access()
   if cfg['provider']=='gmail':return [{'id':x['id'],'name':x['name']} for x in request_json('GET',GMAIL+'/labels',headers=h).get('labels',[])]
   return [{'id':x['id'],'name':x['displayName']} for x in request_json('GET',GRAPH+'/mailFolders',headers=h,params={'$top':100,'$select':'id,displayName'}).get('value',[])]
 def preferences(self,d):
  days=d.get('retention',30)
  if type(days)!=int or not 1<=days<=90:raise ValueError('Retention must be 1–90 days.')
  with self.s.lock:
   cfg=self.config()
   if not cfg:raise ValueError('Connect first.')
   folder=field(d,'folder',1000,True);choices=self.folders();selected=next((x for x in choices if x['id']==folder),None)
   if not selected:raise ValueError('Choose a listed label or top-level folder.')
   # Existing evidence stays until explicitly cleared or expires, but no hidden broad fetch.
   cfg.update(folder=folder,folder_name=selected['name'],retention=days);self.putconfig(cfg);self.prune()
 def prune(self):
  cfg=self.config();cut=time.time()-cfg.get('retention',30)*86400
  with self.db() as c:c.execute('DELETE FROM messages WHERE received<?',(cut,))
 def clear(self):
  with self.db() as c:c.execute('DELETE FROM messages');c.execute('DELETE FROM drafts')
 def disconnect(self):
  with self.s.lock:
   self.clear()
   with self.db() as c:c.execute('DELETE FROM config')
  return {'message':'Local connection, cache and drafts removed. Revoke Job Coach in your Google or Microsoft account permissions to remove the provider grant.'}
 def ingest(self,d):
  p=d['provider'];identifier=hashlib.sha256((p+'\0'+d['account']+'\0'+d['provider_id']).encode()).hexdigest()
  d=dict(d,id=identifier,subject=d.get('subject','')[:500],body=d.get('body','')[:30000],sender=d.get('sender','')[:254],name=d.get('name','')[:200])
  with self.db() as c:c.execute('INSERT OR IGNORE INTO messages(id,data,received) VALUES(?,?,?)',(identifier,self.encode(d),d['timestamp']))
  return identifier
 def sync(self):
  with self.s.lock:
   cfg,h=self.access();p=cfg['provider'];cut=datetime.now(timezone.utc)-timedelta(days=cfg['retention']);n=0
   if p=='gmail':
    result=request_json('GET',GMAIL+'/messages',headers=h,params={'labelIds':cfg['folder'],'q':'after:'+str(int(cut.timestamp())),'maxResults':50})
    for item in result.get('messages',[])[:50]:
     m=request_json('GET',GMAIL+'/messages/'+quote(item['id'],safe=''),headers=h,params={'format':'full'});hdr={x['name'].lower():x['value'] for x in m.get('payload',{}).get('headers',[])};name,sender=parseaddr(hdr.get('from',''))
     self.ingest(dict(provider=p,account=cfg['identity'],provider_id=m['id'],thread=m.get('threadId',''),subject=hdr.get('subject',''),sender=sender,name=name,body=gmail_text(m.get('payload',{})),timestamp=int(m.get('internalDate','0'))/1000));n+=1
    more=bool(result.get('nextPageToken'))
   else:
    h['Prefer']='outlook.body-content-type="text"'
    result=request_json('GET',GRAPH+'/mailFolders/'+quote(cfg['folder'],safe='')+'/messages',headers=h,params={'$top':50,'$filter':'receivedDateTime ge '+cut.isoformat().replace('+00:00','Z'),'$orderby':'receivedDateTime desc','$select':'id,conversationId,from,subject,receivedDateTime,body'})
    for m in result.get('value',[])[:50]:
     a=m.get('from',{}).get('emailAddress',{});body=m.get('body',{});text=body.get('content','')
     if body.get('contentType','').lower()=='html':text=BeautifulSoup(text,'html.parser').get_text(' ',strip=True)
     self.ingest(dict(provider=p,account=cfg['identity'],provider_id=m['id'],thread=m.get('conversationId',''),subject=m.get('subject',''),sender=a.get('address',''),name=a.get('name',''),body=text,timestamp=datetime.fromisoformat(m['receivedDateTime'].replace('Z','+00:00')).timestamp()));n+=1
    more=bool(result.get('@odata.nextLink'))
   self.prune();cfg=self.config();cfg.update(last_sync=now());self.putconfig(cfg)
   return {'fetched':n,'more_available':more,'message':'Read latest '+str(n)+' messages. '+('More exist: use a narrower recruiting folder; this release reads the newest 50 per sync.' if more else 'Review matches below; no cards changed.')}
 def message(self,mid):
  with self.db() as c:r=c.execute('SELECT data FROM messages WHERE id=?',(mid,)).fetchone()
  if not r:raise ValueError('Message expired or was deleted. Refresh the inbox.')
  return self.decode(r[0])
 def suggestions(self,m):
  text=(m['subject']+'\n'+m['body']).lower();candidates=[];prior=set()
  if m.get('thread'):
   with self.s.db() as c:reviewed={r['id']:r['job_id'] for r in c.execute('SELECT id,job_id FROM mail_reviews')}
   with self.db() as c:rows=c.execute('SELECT id,data FROM messages').fetchall()
   for row in rows:
    if row['id'] not in reviewed or row['id']==m['id']:continue
    other=self.decode(row['data'])
    if all(other.get(k)==m.get(k) for k in ('provider','account','thread')):prior.add(reviewed[row['id']])
  for j in self.s.jobs():
   if j['status'] in ('Ignored','Archived'):continue
   reasons=[]
   if j['id'] in prior:reasons.append('Earlier message in this conversation was approved for this job')
   employer=j['institution'].strip().lower();title=j['title'].strip().lower()
   if len(employer)>3 and employer in text:reasons.append('Employer name appears in message')
   if len(title)>5 and title in text:reasons.append('Position title appears in message')
   if j.get('url') and j['url'].lower() in text:reasons.append('Exact posting URL appears in message')
   if reasons:candidates.append({'id':j['id'],'label':j['institution']+' · '+j['title'],'reasons':reasons,'strength':'strong' if len(reasons)>1 else 'uncertain'})
  hint='';evidence=''
  for status,phrases in [('Rejected',['not selected','not moving forward','unable to offer']),('Offer',['offer of employment','pleased to offer you']),('Interviewing',['invite you to interview','schedule an interview','interview invitation']),('Submitted',['application has been received','received your application'])]:
   hit=next((x for x in phrases if x in text),None)
   if hit:hint=status;evidence=hit;break
  return dict(candidates=sorted(candidates,key=lambda x:-len(x['reasons']))[:10],status_hint=hint,evidence=evidence)
 def state(self):
  self.prune();cfg=self.config()
  with self.db() as c:rows=c.execute('SELECT * FROM messages WHERE hidden=0 ORDER BY received DESC LIMIT 100').fetchall();drafts=c.execute('SELECT * FROM drafts ORDER BY rowid DESC LIMIT 100').fetchall()
  with self.s.db() as c:reviews={r['id']:r['job_id'] for r in c.execute('SELECT * FROM mail_reviews')}
  messages=[]
  for r in rows:
   m=self.decode(r['data']);m.update(self.suggestions(m),reviewed_job=reviews.get(m['id'],''));messages.append(m)
  return {'connection':{k:cfg.get(k) for k in ('provider','identity','send','folder','folder_name','retention','last_sync')},'messages':messages,'drafts':[dict(self.decode(r['data']),id=r['id'],revision=r['revision'],status=r['status']) for r in drafts]}
 def hide(self,mid):
  with self.db() as c:c.execute('UPDATE messages SET hidden=1 WHERE id=?',(mid,))
 def review(self,mid,d,actor='Local user'):
  if d.get('share') is not True:raise ValueError('Confirm sharing the selected information with workspace collaborators.')
  jid=field(d,'job_id',100,True);summary=field(d,'summary',3000,True);status=d.get('status','')
  if status and status not in STATUSES:raise ValueError('Choose a valid status.')
  with self.s.lock:
   m=self.message(mid);j=active_job(self.s,jid)
   with self.s.db() as c:
    c.execute('BEGIN IMMEDIATE')
    receipt=c.execute('SELECT job_id FROM mail_reviews WHERE id=?',(mid,)).fetchone()
    if receipt:
     if receipt[0]!=jid:raise ValueError('This message was already approved for another job.')
     return {'ok':True,'already_reviewed':True}
    current=c.execute('SELECT updated,status FROM jobs WHERE id=?',(jid,)).fetchone()
    if d.get('job_updated')!=current['updated'] or d.get('job_status')!=current['status']:raise ValueError('The job changed. Reload and review again.')
    contact_ids=[]
    if d.get('add_contact') is True:
     email=address(field(d,'contact_email',254,True));name=field(d,'contact_name',200,True);cid=''
     for r in c.execute('SELECT * FROM journey_contacts'):
      v=json.loads(r['data'])
      if v.get('email','').casefold()==email.casefold():
       if v.get('archived'):raise ValueError('This contact is archived. Restore it in Timeline first.')
       cid=r['id'];break
     if not cid:
      cid=uid();v=dict(name=name,email=email,role='',organization=j['institution'],phone='',url='',notes='',archived=False);c.execute('INSERT INTO journey_contacts VALUES(?,?,1)',(cid,json.dumps(v)))
     c.execute('INSERT OR IGNORE INTO journey_links VALUES(?,?)',(jid,cid));contact_ids=[cid]
    if d.get('event'):
     e=d['event'];start=field(e,'start',30,True);end=field(e,'end',30,True);zone=field(e,'zone',100,True);a=moment(start,zone);b=moment(end,zone)
     if b<=a:raise ValueError('Interview end must be after start.')
     v=dict(title=field(e,'title',300,True),kind='Interview',status='Planned',start=start,end=end,zone=zone,start_utc=a,end_utc=b,all_day=False,location='',meeting_url='',notes=summary,outcome='',contact_ids=contact_ids,created=now(),updated=now(),actor=actor)
     c.execute('INSERT INTO journey_events VALUES(?,?,?,1)',(uid(),jid,json.dumps(v)))
    if status:
     c.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(status,now(),jid));self.s.event(c,jid,'edited',{'actor':actor,'before':{'status':j['status']},'status':status,'patch':{}})
    self.s.event(c,jid,'email_reviewed',{'actor':actor,'summary':summary,'contact_ids':contact_ids,'status':status or j['status']})
    c.execute('INSERT INTO mail_reviews VALUES(?,?,?)',(mid,jid,now()))
  return {'ok':True,'already_reviewed':False}
 def draft(self,d,did=''):
  value={k:field(d,k,n,True) for k,n in [('to',254),('subject',500),('body',20000)]};address(value['to'])
  if any(x in value['subject'] for x in '\r\n'):raise ValueError('Subject must be a single line.')
  with self.s.lock,self.db() as c:
   c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM drafts WHERE id=?',(did,)).fetchone() if did else None
   if did and (not r or r['revision']!=d.get('revision') or r['status']!='draft'):raise ValueError('Draft changed or sending already started. Reload.')
   did=did or uid();rev=r['revision']+1 if r else 1;c.execute('INSERT OR REPLACE INTO drafts VALUES(?,?,?,?)',(did,self.encode(value),rev,'draft'))
  return {'id':did,'revision':rev}
 def prepare(self,did,rev):
  with self.s.lock,self.db() as c:
   c.execute('BEGIN IMMEDIATE');cfg=self.config();r=c.execute('SELECT * FROM drafts WHERE id=?',(did,)).fetchone()
   if not cfg.get('send'):raise ValueError('Reconnect with optional sending permission first.')
   if not r or r['revision']!=rev or r['status']!='draft':raise ValueError('Draft changed or already sent. Reload.')
   d=self.decode(r['data'])
   if re.search(r'\[[^\]]+\]',d['subject']+' '+d['body']):raise ValueError('Replace bracketed template placeholders before sending.')
   d.update(confirm=secrets.token_urlsafe(32),expires=time.time()+300,generation=cfg['generation']);c.execute('UPDATE drafts SET data=? WHERE id=?',(self.encode(d),did))
   return dict(d,id=did,revision=rev,from_address=cfg['identity'])
 def send(self,did,d):
  with self.s.lock:
   cfg,h=self.access()
   if not cfg.get('send'):raise ValueError('Sending permission is not enabled.')
   with self.db() as c:
    c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM drafts WHERE id=?',(did,)).fetchone()
    if not r or r['status']!='draft' or r['revision']!=d.get('revision'):raise ValueError('Draft changed or sending already started.')
    value=self.decode(r['data'])
    if not value.get('confirm') or not secrets.compare_digest(value['confirm'],str(d.get('confirm',''))) or value['expires']<time.time() or value['generation']!=cfg['generation']:raise ValueError('Send confirmation expired. Review again.')
    # Commit uncertainty before external effect. Process death cannot cause a blind retry.
    c.execute("UPDATE drafts SET status='unknown' WHERE id=?",(did,))
   try:
    if cfg['provider']=='gmail':
     msg=EmailMessage();msg['From']=cfg['identity'];msg['To']=value['to'];msg['Subject']=value['subject'];msg.set_content(value['body']);request_json('POST',GMAIL+'/messages/send',headers=h,json={'raw':base64.urlsafe_b64encode(msg.as_bytes()).decode()})
    else:request_json('POST',GRAPH+'/sendMail',headers=h,json={'message':{'subject':value['subject'],'body':{'contentType':'Text','content':value['body']},'toRecipients':[{'emailAddress':{'address':value['to']}}]},'saveToSentItems':True})
   except Exception:raise ValueError('Send outcome is unknown. Check your provider Sent folder before creating another draft. Automatic retry is disabled.') from None
   with self.db() as c:c.execute("UPDATE drafts SET status='accepted' WHERE id=?",(did,))
   return {'message':'Provider accepted the message. This does not guarantee delivery.'}
 def delete_draft(self,did):
  with self.db() as c:c.execute('DELETE FROM drafts WHERE id=?',(did,))
