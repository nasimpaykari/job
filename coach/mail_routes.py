"""Owner-only email endpoints; OAuth callback uses short-lived server-side state."""
import time,threading
from urllib.parse import urlsplit
from flask import request,jsonify,redirect,session,abort
from .mailbox import Mailbox,oauth_start

def register(app,store,owner_required,actor,accounts,testing,changed):
 pending={};lock=threading.Lock()
 def box():owner_required();return Mailbox(store._get_current_object())
 @app.get('/api/mail')
 def mail_state():return jsonify(box().state())
 @app.post('/api/mail/connect')
 def mail_connect():
  m=box();m.network();origin=urlsplit(request.host_url)
  if origin.hostname not in ('127.0.0.1','localhost') or origin.scheme!='http':raise ValueError('For mail setup, open Job Coach locally at http://127.0.0.1 or http://localhost on this computer.')
  callback=request.host_url.rstrip('/')+'/api/mail/oauth/callback'
  url,p=oauth_start(request.get_json(),callback);u=actor();p.update(mailbox=m,user=u,space=session.get('space'))
  with lock:
   for key in list(pending):
    if pending[key]['expires']<time.time():pending.pop(key)
   # Only the most recent authorization for a workspace can finish.
   for key in list(pending):
    if pending[key]['mailbox'].path==m.path:pending.pop(key)
   pending[p['state']]=p
  return jsonify(url=url,redirect_uri=callback)
 @app.get('/api/mail/oauth/callback')
 def mail_callback():
  with lock:p=pending.pop(request.args.get('state',''),None)
  if not p or p['expires']<time.time():raise ValueError('Authorization expired or already used. Return to Email and connect again.')
  if request.base_url!=p['redirect']:raise ValueError('Authorization callback address does not match.')
  if not testing:
   u=accounts.user(p['user']['id']);access=accounts.access(p['space'],p['user']['id'])
   if not u or u['version']!=p['user']['version'] or not access or access['role']!='owner':abort(403)
  if request.args.get('error'):raise ValueError('Email authorization was declined. No connection was saved.')
  code=request.args.get('code','')
  if not code or len(code)>10000:raise ValueError('Missing authorization code.')
  p['mailbox'].connect(p,code)
  return redirect('/?mail=connected')
 @app.get('/api/mail/folders')
 def mail_folders():return jsonify(folders=box().folders())
 @app.post('/api/mail/preferences')
 def mail_preferences():box().preferences(request.get_json());return jsonify(ok=True)
 @app.post('/api/mail/sync')
 def mail_sync():return jsonify(box().sync())
 @app.post('/api/mail/disconnect')
 def mail_disconnect():
  m=box()
  with lock:
   for key in list(pending):
    if pending[key]['mailbox'].path==m.path:pending.pop(key)
  return jsonify(m.disconnect())
 @app.post('/api/mail/clear')
 def mail_clear():box().clear();return jsonify(ok=True)
 @app.post('/api/mail/messages/<mid>/hide')
 def mail_hide(mid):box().hide(mid);return jsonify(ok=True)
 @app.post('/api/mail/messages/<mid>/review')
 def mail_review(mid):
  m=box();u=actor();d=request.get_json();result=m.review(mid,d,u['username'] if u else 'Local user')
  if not result['already_reviewed']:changed(d['job_id'],'Reviewed email update',{'reviewed':True},notify=d.get('notify_staff') is True)
  return jsonify(result)
 @app.post('/api/mail/drafts')
 def mail_draft():return jsonify(box().draft(request.get_json()))
 @app.patch('/api/mail/drafts/<did>')
 def mail_edit(did):return jsonify(box().draft(request.get_json(),did))
 @app.delete('/api/mail/drafts/<did>')
 def mail_delete(did):box().delete_draft(did);return jsonify(ok=True)
 @app.post('/api/mail/drafts/<did>/prepare')
 def mail_prepare(did):return jsonify(box().prepare(did,request.get_json().get('revision')))
 @app.post('/api/mail/drafts/<did>/send')
 def mail_send(did):return jsonify(box().send(did,request.get_json()))
