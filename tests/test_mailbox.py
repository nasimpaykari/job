import base64,json,time
from urllib.parse import urlsplit,parse_qs
import pytest
from coach.core import Store
from coach.mailbox import Mailbox,oauth_start,gmail_text
from coach.journey import Journey
from coach.web import create_app
from test_upgrade import setup,post,token,PASSWORD

@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path);s.save_settings({'network_enabled':True});j=s.add_job({'title':'Robotics Engineer','institution':'Example Robotics','url':'https://example.org/jobs/1'})[0];return s,Mailbox(s),j

def message(m,**kw):
 d=dict(provider='gmail',account='owner@example.org',provider_id='1',thread='t1',sender='recruiter@example.org',name='Recruiter',subject='Example Robotics: Robotics Engineer interview invitation',body='We invite you to interview. Please choose a time.',timestamp=time.time());d.update(kw);return m.ingest(d)
def connect(m,p='gmail',send=True):m.putconfig(dict(provider=p,identity='owner@example.org',send=send,generation='g1',folder='INBOX' if p=='gmail' else 'inbox',folder_name='Inbox',retention=30,tokens={'access_token':'SECRET_ACCESS','refresh_token':'SECRET_REFRESH'},expires=time.time()+3600,client_id='client',tenant='common'))
def approval(s,j,**kw):
 job=s.job(j);d=dict(job_id=j,job_updated=job['updated'],job_status=job['status'],summary='Interview invitation reviewed',status='Interviewing',share=True,add_contact=True,contact_email='recruiter@example.org',contact_name='Recruiter',event={'title':'First interview','start':'2026-11-05T14:00','end':'2026-11-05T15:00','zone':'UTC'});d.update(kw);return d

def test_private_encryption_dedupe_retention(env):
 s,m,j=env;connect(m);mid=message(m,body='PRIVATE_BODY');message(m,body='PRIVATE_BODY');assert len(m.state()['messages'])==1
 assert b'PRIVATE_BODY' not in m.path.read_bytes();assert b'SECRET_ACCESS' not in m.path.read_bytes();assert 'tokens' not in m.state()['connection']
 message(m,provider_id='old',timestamp=time.time()-40*86400);assert len(m.state()['messages'])==1
 m.hide(mid);assert not m.state()['messages'];message(m);assert not m.state()['messages']

def test_review_atomic_and_idempotent(env):
 s,m,j=env;mid=message(m);d=approval(s,j);assert s.job(j)['status']=='New';m.review(mid,d);m.review(mid,d)
 assert s.job(j)['status']=='Interviewing';assert len(Journey(s).contacts())==1;assert len(Journey(s).events(j))==1
 assert len([x for x in Journey(s).timeline(j)['audit'] if x['title']=='Email reviewed'])==1
 mid2=message(m,provider_id='2');m.review(mid2,approval(s,j));assert len(Journey(s).contacts())==1

def test_failed_event_rolls_back_contact_and_status(env):
 s,m,j=env;mid=message(m);d=approval(s,j);d['event']['end']='2026-11-05T13:00'
 with pytest.raises(ValueError):m.review(mid,d)
 assert not Journey(s).contacts();assert s.job(j)['status']=='New';assert not m.state()['messages'][0]['reviewed_job']

def test_stale_review_and_no_consent(env):
 s,m,j=env;mid=message(m);d=approval(s,j);s.edit_job(j,{'status':'Submitted'})
 with pytest.raises(ValueError,match='changed'):m.review(mid,d)
 with pytest.raises(ValueError,match='Confirm sharing'):m.review(mid,approval(s,j,share=False))

def test_matching_is_only_a_hint(env):
 s,m,j=env;mid=message(m);state=m.state();row=state['messages'][0];assert row['status_hint']=='Interviewing';assert row['candidates'][0]['strength']=='strong';assert s.job(j)['status']=='New'
 s.edit_job(j,{'status':'Ignored'});assert not m.state()['messages'][0]['candidates']

def test_gmail_mime_no_attachments_or_html_execution():
 enc=lambda x:base64.urlsafe_b64encode(x.encode()).decode()
 assert gmail_text({'parts':[{'mimeType':'text/html','body':{'data':enc('<b>Hello</b>')}},{'mimeType':'text/plain','body':{'data':enc('Plain')}},{'mimeType':'text/plain','filename':'secret.txt','body':{'data':enc('Attachment')}}]})=='Plain'
 assert gmail_text({'mimeType':'text/html','body':{'data':enc('<script>evil()</script><p>Hello</p>')}})=='Hello'

def test_oauth_pkce_and_tenant():
 url,p=oauth_start({'provider':'gmail','client_id':'test'},'http://localhost:5000/api/mail/oauth/callback');q=parse_qs(urlsplit(url).query);assert q['state']==[p['state']];assert q['code_challenge_method']==['S256'];assert 'gmail.send' not in q['scope'][0];assert len(p['verifier'])>=43
 with pytest.raises(ValueError):oauth_start({'provider':'outlook','client_id':'test','tenant':'evil.org/path'},'http://localhost')

def test_gmail_sync_only_selected_label(env,monkeypatch):
 s,m,j=env;connect(m);calls=[]
 def fake(method,url,**kw):
  calls.append((url,kw))
  if url.endswith('/messages'):return {'messages':[{'id':'1'}],'nextPageToken':'more'}
  return {'id':'1','internalDate':str(int(time.time()*1000)),'payload':{'headers':[{'name':'From','value':'Recruiter <r@example.org>'},{'name':'Subject','value':'Interview'}],'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'Hello').decode()}}}
 monkeypatch.setattr('coach.mailbox.request_json',fake);r=m.sync();assert r['fetched']==1 and r['more_available'];assert calls[0][1]['params']['labelIds']=='INBOX';assert len(m.state()['messages'])==1

def test_outlook_sync_does_not_follow_untrusted_pagination(env,monkeypatch):
 s,m,j=env;connect(m,'outlook');calls=[]
 def fake(method,url,**kw):calls.append(url);return {'value':[{'id':'1','from':{'emailAddress':{'name':'R','address':'r@example.org'}},'subject':'Interview','receivedDateTime':'2026-11-01T12:00:00Z','body':{'contentType':'html','content':'<b>Hello</b>'}}],'@odata.nextLink':'https://evil.example/steal'}
 monkeypatch.setattr('coach.mailbox.request_json',fake);assert m.sync()['more_available'];assert len(calls)==1;assert '/mailFolders/inbox/messages' in calls[0]

def test_draft_confirm_send_once_and_exact_content(env,monkeypatch):
 s,m,j=env;connect(m);d=m.draft(dict(to='r@example.org',subject='Follow-up',body='Thank you.'));p=m.prepare(d['id'],1);calls=[]
 monkeypatch.setattr('coach.mailbox.request_json',lambda *a,**kw:calls.append((a,kw)) or {})
 m.send(d['id'],p);assert len(calls)==1;assert b'Thank you.' in base64.urlsafe_b64decode(calls[0][1]['json']['raw'])
 with pytest.raises(ValueError):m.send(d['id'],p)
 assert len(calls)==1 and m.state()['drafts'][0]['status']=='accepted'

def test_uncertain_send_never_retried(env,monkeypatch):
 s,m,j=env;connect(m,'outlook');d=m.draft(dict(to='r@example.org',subject='Follow-up',body='Thank you.'));p=m.prepare(d['id'],1);calls=[]
 def fail(*a,**kw):calls.append(a);raise ValueError('timeout')
 monkeypatch.setattr('coach.mailbox.request_json',fail)
 with pytest.raises(ValueError,match='unknown'):m.send(d['id'],p)
 with pytest.raises(ValueError):m.send(d['id'],p)
 assert len(calls)==1 and m.state()['drafts'][0]['status']=='unknown'

def test_stale_confirmation_and_read_only(env):
 s,m,j=env;connect(m);d=m.draft(dict(to='r@example.org',subject='Hello',body='A'));p=m.prepare(d['id'],1);m.draft(dict(to='other@example.org',subject='Hello',body='B',revision=1),d['id'])
 with pytest.raises(ValueError):m.send(d['id'],p)
 connect(m,send=False)
 with pytest.raises(ValueError):m.prepare(d['id'],2)

def test_header_injection_rejected(env):
 s,m,j=env
 for to,subject in [('a@example.org\nBcc: b@example.org','Hi'),('a@example.org','Hi\nBcc: b@example.org')]:
  with pytest.raises(ValueError):m.draft(dict(to=to,subject=subject,body='A'))

def test_disconnect_and_network_switch(env):
 s,m,j=env;connect(m);message(m);m.draft(dict(to='r@example.org',subject='Hi',body='A'));s.save_settings({'network_enabled':False})
 with pytest.raises(ValueError,match='network'):m.sync()
 m.disconnect();assert not m.state()['messages'] and not m.state()['drafts'];assert not m.config()

def test_owner_authorization_and_csrf(tmp_path):
 app=create_app(tmp_path);a=setup(app);post(a,'/api/users',{'username':'helper','password':PASSWORD});post(a,'/api/share',{'username':'helper','role':'editor'});state=a.get('/api/state').json;space=state['active_workspace']
 b=app.test_client();b.get('/login');post(b,'/api/login',{'username':'helper','password':PASSWORD});post(b,'/api/workspace',{'id':space})
 assert b.get('/api/mail').status_code==403;assert post(b,'/api/mail/sync',{}).status_code==403
 assert a.post('/api/mail/clear',json={}).status_code==403
 assert a.get('/api/mail').status_code==200

def test_oauth_callback_without_cookie_bound_to_owner(tmp_path,monkeypatch):
 app=create_app(tmp_path);a=setup(app);app.extensions['store'].save_settings({'network_enabled':True});r=post(a,'/api/mail/connect',{'provider':'gmail','client_id':'test'});assert r.status_code==200;r=r.json;state=parse_qs(urlsplit(r['url']).query)['state'][0];seen=[]
 monkeypatch.setattr(Mailbox,'connect',lambda self,p,code:seen.append((str(self.path),code)))
 callback=app.test_client();assert callback.get('/api/mail/oauth/callback?state='+state+'&code=abc').status_code==302
 assert callback.get('/api/mail/oauth/callback?state='+state+'&code=abc').status_code==400;assert len(seen)==1

def test_oauth_rejects_remote_and_cancelled(tmp_path):
 app=create_app(tmp_path,testing=True);a=app.test_client();app.extensions['store'].save_settings({'network_enabled':True})
 assert a.post('/api/mail/connect',json={'provider':'gmail','client_id':'test'},base_url='http://remote.example').status_code==400
 r=a.post('/api/mail/connect',json={'provider':'gmail','client_id':'test'}).json;state=parse_qs(urlsplit(r['url']).query)['state'][0];a.post('/api/mail/disconnect',json={});assert a.get('/api/mail/oauth/callback?state='+state+'&code=x').status_code==400

def test_snapshot_excludes_private_mail(env,tmp_path):
 import zipfile
 from coach.storage import Storage
 s,m,j=env;connect(m);message(m,body='PRIVATE_BODY');m.draft(dict(to='r@example.org',subject='PRIVATE_SUBJECT',body='PRIVATE_DRAFT'));path=Storage(s).snapshot(tmp_path/'snapshot.zip')
 with zipfile.ZipFile(path) as z:
  assert not any('mail.db' in n or 'mail.key' in n for n in z.namelist());assert b'PRIVATE_BODY' not in z.read('coach.db');assert b'SECRET_ACCESS' not in z.read('coach.db')

def test_thread_match_and_merge_receipt(env):
 s,m,j=env;mid=message(m);m.review(mid,approval(s,j,event=None));second=message(m,provider_id='2',subject='Next steps',body='Please pick a time.');row=next(x for x in m.state()['messages'] if x['id']==second);assert row['candidates'][0]['id']==j;assert 'conversation' in row['candidates'][0]['reasons'][0]
 other=s.add_job({'title':'Surviving job','institution':'Other Employer','url':'https://example.org/other'})[0];s.merge(other,j);assert next(x for x in m.state()['messages'] if x['id']==mid)['reviewed_job']==other

def test_placeholder_server_guard(env):
 s,m,j=env;connect(m);d=m.draft(dict(to='r@example.org',subject='Hello [name]',body='Thanks'))
 with pytest.raises(ValueError,match='placeholders'):m.prepare(d['id'],1)

def test_expired_confirmation(env):
 s,m,j=env;connect(m);d=m.draft(dict(to='r@example.org',subject='Hello',body='Thanks'));p=m.prepare(d['id'],1)
 with m.db() as c:
  row=c.execute('SELECT data FROM drafts WHERE id=?',(d['id'],)).fetchone();v=m.decode(row[0]);v['expires']=0;c.execute('UPDATE drafts SET data=? WHERE id=?',(m.encode(v),d['id']))
 with pytest.raises(ValueError,match='expired'):m.send(d['id'],p)

def test_refresh_token_and_sanitized_errors(env,monkeypatch):
 s,m,j=env;connect(m);cfg=m.config();cfg['expires']=0;m.putconfig(cfg);calls=[]
 def fake(method,url,**kw):calls.append((url,kw));return {'access_token':'NEW_SECRET','expires_in':3600}
 monkeypatch.setattr('coach.mailbox.request_json',fake);cfg,h=m.access();assert h['Authorization']=='Bearer NEW_SECRET';assert cfg['tokens']['refresh_token']=='SECRET_REFRESH';assert calls[0][1]['data']['grant_type']=='refresh_token';assert 'NEW_SECRET' not in json.dumps(m.state())

def test_oauth_connect_preserves_existing_on_failure(env,monkeypatch):
 s,m,j=env;connect(m);mid=message(m);url,p=oauth_start({'provider':'gmail','client_id':'test'},'http://localhost/api/mail/oauth/callback')
 def fake(*a,**kw):raise ValueError('Provider refused')
 monkeypatch.setattr('coach.mailbox.request_json',fake)
 with pytest.raises(ValueError):m.connect(p,'abc')
 assert m.config()['identity']=='owner@example.org' and m.message(mid)

def test_workspace_isolation(env,tmp_path):
 s,m,j=env;message(m);other=Mailbox(Store(tmp_path/'second'));assert not other.state()['messages'];assert m.keypath.read_bytes()!=other.keypath.read_bytes()

def test_concurrent_send_only_once(env,monkeypatch):
 import concurrent.futures
 s,m,j=env;connect(m);d=m.draft(dict(to='r@example.org',subject='Hello',body='Thanks'));p=m.prepare(d['id'],1);calls=[]
 monkeypatch.setattr('coach.mailbox.request_json',lambda *a,**kw:calls.append(a) or {})
 def send():
  try:m.send(d['id'],p);return 'sent'
  except ValueError:return 'blocked'
 with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:send(),range(2)))
 assert sorted(results)==['blocked','sent'] and len(calls)==1
