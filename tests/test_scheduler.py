import json,sqlite3,zipfile,threading,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
from coach.core import Store
from coach.scheduler import Scheduler,Runner,ProcessLock,Cancelled,quiet,wall,stamp
from coach.journey import Journey
from coach.storage import Storage
from coach.services import Search
from coach.web import create_app
from test_upgrade import setup,post,patch,token,PASSWORD
UTC=timezone.utc
T=datetime(2026,11,2,14,0,tzinfo=UTC)
@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path);j=s.add_job({'title':'Engineer','institution':'Example Inc','url':'https://example.org/j','deadline':'2026-11-05','next_action':'Follow up','next_action_due':'2026-11-03'})[0];return s,Scheduler(s),j

def spec(**kw):
 d=dict(name='Check applications',kind='reminder',start='2026-11-02T14:00',zone='UTC',message='Prepare documents',interval_hours=0,retries=1,enabled=True);d.update(kw);return d

def run(w,t=T):
 w.enqueue(t);r=w.claim(t)
 if r:w.execute(r,t)
 return r

def test_once_persistent_no_duplicate(env):
 s,w,j=env;w.save(spec());assert run(w);assert not run(w);assert len(w.state()['notifications'])==1
 Store(s.root);w.recover();assert not run(w,T+timedelta(hours=1));assert len(w.state()['notifications'])==1

def test_overdue_recurrence_coalesces(env):
 s,w,j=env;w.save(spec(interval_hours=24));run(w,T+timedelta(days=8,hours=2));state=w.state();assert len(state['runs'])==1
 assert state['schedules'][0]['next_run']==stamp(T+timedelta(days=9))
 assert not run(w,T+timedelta(days=8,hours=3))

def test_pause_cancel_revision_and_retry(env):
 s,w,j=env;a=w.save(spec(interval_hours=24));w.control(a['id'],'pause',1);assert not run(w)
 with pytest.raises(ValueError):w.control(a['id'],'resume',1)
 w.control(a['id'],'resume',2);w.enqueue(T);r=w.claim(T);w.control(a['id'],'cancel',3);w.execute(r,T);assert w.state()['runs'][0]['status']=='cancelled'
 w.retry(r['id'],T);r=w.claim(T);w.execute(r,T);assert w.state()['runs'][0]['status']=='succeeded'

def test_recovery_does_not_replay_uncertain_run(env):
 s,w,j=env;w.save(spec());w.enqueue(T);w.claim(T);w.recover();assert w.state()['runs'][0]['status']=='interrupted';assert not w.claim(T)

def test_retries_and_partial_results(env,monkeypatch):
 s,w,j=env;s.save_settings({'network_enabled':True});w.save(spec(kind='freshness',job_ids=[j],retries=2))
 def broken(*a,**k):raise RuntimeError('Network unavailable')
 monkeypatch.setattr('coach.freshness.Freshness.check',broken);r=run(w);assert w.state()['runs'][0]['status']=='retry_wait';assert not w.claim(T+timedelta(minutes=4))
 r=w.claim(T+timedelta(minutes=5));w.execute(r,T+timedelta(minutes=5));assert w.state()['runs'][0]['retry_at']==stamp(T+timedelta(minutes=15))
 r=w.claim(T+timedelta(minutes=15));w.execute(r,T+timedelta(minutes=15));assert w.state()['runs'][0]['status']=='failed';assert not w.claim(T+timedelta(days=1))

def test_frozen_search_and_network_kill_switch(env,monkeypatch):
 s,w,j=env;s.save_settings({'network_enabled':True,'saved_search_enabled':True,'search_targets':[{'id':'one','kind':'url','label':'Saved board','value':'https://example.org/jobs','enabled':True}]})
 a=w.save(spec(kind='search'));plan=w.state()['schedules'][0]['plan'];assert plan[1][0][1]=='https://example.org/jobs';assert 'profile' not in plan[0]
 s.save_settings({'search_targets':[{'id':'two','kind':'url','label':'New board','value':'https://other.example.org/jobs','enabled':True}]})
 seen=[]
 def fake(self,kind,log,checkpoint,prepared_plan):checkpoint();seen.append(prepared_plan[1][0][1]);return {'added':1,'errors':[]}
 monkeypatch.setattr(Search,'run',fake);run(w);assert seen==['https://example.org/jobs']
 w.save(spec(kind='search',start='2026-11-02T15:00',retries=0));s.save_settings({'network_enabled':False});run(w,T+timedelta(hours=1));assert w.state()['runs'][0]['status']=='failed';assert len(seen)==1

def test_cancel_checkpoint_does_not_import(env,monkeypatch):
 s,w,j=env;s.save_settings({'network_enabled':True,'saved_search_enabled':True,'search_targets':[{'id':'one','kind':'url','label':'Saved','value':'https://example.org/jobs','enabled':True}]})
 a=w.save(spec(kind='search'));w.enqueue(T);r=w.claim(T)
 def fetch(*a,**k):
  w.control(r['schedule_id'],'cancel',1)
  return '<script type="application/ld+json">{"@type":"JobPosting","title":"New job","hiringOrganization":{"name":"Example"},"url":"https://example.org/jobs","description":"A job"}</script>','https://example.org/jobs'
 monkeypatch.setattr('coach.services.fetch',fetch);w.execute(r,T);assert w.state()['runs'][0]['status']=='cancelled';assert len(s.jobs())==1

def test_reminders_quiet_delivery_and_dedupe(env):
 s,w,j=env;w.preferences({'enabled':True,'zone':'UTC','desktop':True,'quiet_enabled':True,'quiet_start':'13:00','quiet_end':'15:00','lead_minutes':60})
 Journey(s).save(j,{'title':'Round 1','kind':'Interview','start':'2026-11-02T15:00','end':'2026-11-02T16:00','zone':'UTC'})
 w.reminders(T);assert len(w.state()['notifications'])==2 # event and future deadline
 sent=[];w.deliver(T,lambda:sent.append(1) or 'requested');assert not sent
 w.reminders(T);w.deliver(T+timedelta(hours=2),lambda:sent.append(1) or 'requested');assert len(sent)==1
 w.deliver(T+timedelta(hours=3),lambda:sent.append(1));assert len(sent)==1
 s.edit_job(j,{'status':'Submitted'});w.reminders(T+timedelta(days=1));assert len(w.state()['notifications'])==3 # next action, no repeated deadline

def test_no_invented_deadlines_and_completed_events(env):
 s,w,j=env;w.preferences({'enabled':True,'zone':'UTC'});s.edit_job(j,{'deadline':'Open until filled','next_action_due':''})
 Journey(s).save(j,{'title':'Done','all_day':True,'start':'2026-11-02','status':'Completed'})
 w.reminders(T);assert not w.state()['notifications']
 s.edit_job(j,{'deadline':'Priority review November 2, 2026'});w.reminders(T);assert not w.state()['notifications']

def test_digest_and_desktop_failure(env):
 s,w,j=env;w.preferences({'enabled':True,'zone':'UTC','digest':'daily','desktop':True,'quiet_enabled':False});w.reminders(T);w.reminders(T)
 assert len([n for n in w.state()['notifications'] if n['title']=='Daily review'])==1
 w.deliver(T,lambda:(_ for _ in ()).throw(RuntimeError('No desktop')));assert all('failed' in n['desktop'] for n in w.state()['notifications'])

def test_dst_reminder_resolution():
 assert wall('2026-11-01','01:30','America/New_York').hour==6
 assert wall('2026-03-08','02:30','America/New_York').hour==7

@pytest.mark.parametrize('d',[{'interval_hours':-1},{'interval_hours':True},{'retries':3},{'kind':'email'},{'start':'2026-11-01T01:30','zone':'America/New_York'},{'kind':'freshness','job_ids':['other']},{'name':''}])
def test_invalid_schedules(env,d):
 with pytest.raises(ValueError):env[1].save(spec(**d))
@pytest.mark.parametrize('d',[{'quiet_start':'25:00'},{'quiet_start':'07:00','quiet_end':'07:00'},{'zone':'Mars/Olympus'},{'lead_minutes':-1},{'enabled':'yes'},{'digest':'hourly'}])
def test_invalid_preferences(env,d):
 with pytest.raises(ValueError):env[1].preferences(d)

def test_snapshot_inert_and_original_untouched(env,tmp_path):
 s,w,j=env;w.preferences({'enabled':True,'desktop':True});w.save(spec());w.enqueue(T);w.claim(T)
 out=Storage(s).snapshot(tmp_path/'backup.zip');copy=tmp_path/'restored';copy.mkdir()
 with zipfile.ZipFile(out) as z:z.extractall(copy)
 restored=Scheduler(Store(copy));assert not restored.prefs()['enabled'];assert not restored.prefs()['desktop'];assert not restored.state()['schedules'][0]['enabled'];assert restored.state()['runs'][0]['status']=='interrupted'
 assert w.prefs()['enabled'] and w.state()['runs'][0]['status']=='running'

def test_process_lock(tmp_path):
 a=ProcessLock(tmp_path/'scheduler.lock');b=ProcessLock(tmp_path/'scheduler.lock');assert a.acquire();assert not b.acquire();a.close();assert b.acquire();b.close()

def test_permissions_and_workspace_isolation(tmp_path):
 app=create_app(tmp_path);o=setup(app);assert o.post('/api/scheduler/schedules',json=spec()).status_code==403
 sid=post(o,'/api/scheduler/schedules',spec()).json['id'];post(o,'/api/users',{'username':'staff','password':PASSWORD});c=app.test_client();c.get('/login');post(c,'/api/login',{'username':'staff','password':PASSWORD});assert not c.get('/api/scheduler').json['schedules']
 assert post(c,'/api/scheduler/schedules/'+sid+'/control',{'action':'pause','revision':1}).status_code==400
 space=o.get('/api/state').json['active_workspace'];post(o,'/api/share',{'username':'staff','role':'editor'});post(c,'/api/workspace',{'id':space});assert len(c.get('/api/scheduler').json['schedules'])==1
 assert post(c,'/api/scheduler/preferences',{'enabled':True}).status_code==403
 assert post(c,'/api/scheduler/schedules',spec()).status_code==403

def test_runner_without_browser(env):
 s,w,j=env;d=datetime.now(UTC)-timedelta(minutes=1);w.save(spec(start=d.strftime('%Y-%m-%dT%H:%M')))
 runner=Runner(s.root,lambda:[s],notifier=lambda:'test');assert runner.start()
 try:
  for _ in range(100):
   if w.state()['notifications']:break
   time.sleep(.05)
  assert w.state()['notifications'];assert w.state()['worker_recent']
 finally:runner.close();runner.thread.join(10)
 assert not runner.thread.is_alive()

def test_all_day_reminder_and_retry_success(env,monkeypatch):
 s,w,j=env;w.preferences({'enabled':True,'zone':'UTC','daily_time':'09:00','lead_minutes':120,'deadlines':False,'actions':False})
 Journey(s).save(j,{'title':'All day','all_day':True,'start':'2026-11-02'})
 w.reminders(T.replace(hour=8));assert not w.state()['notifications'];w.reminders(T.replace(hour=9));assert len(w.state()['notifications'])==1
 s.save_settings({'network_enabled':True});w.save(spec(kind='freshness',job_ids=[j]))
 def fail(*a,**k):raise RuntimeError('Temporary error')
 monkeypatch.setattr('coach.freshness.Freshness.check',fail);r=run(w);assert w.state()['runs'][0]['error']
 monkeypatch.setattr('coach.freshness.Freshness.check',lambda *a,**k:{'state':'available','message':'OK'})
 r=w.claim(T+timedelta(minutes=5));w.execute(r,T+timedelta(minutes=5));assert w.state()['runs'][0]['status']=='succeeded';assert not w.state()['runs'][0]['error']

def test_atomic_claim(env):
 s,w,j=env;w.save(spec());w.enqueue(T);out=[]
 threads=[threading.Thread(target=lambda:out.append(Scheduler(s).claim(T))) for _ in range(4)]
 for t in threads:t.start()
 for t in threads:t.join()
 assert sum(x is not None for x in out)==1

def test_real_worker_restart_without_browser(tmp_path):
 import subprocess,sys,signal
 root=tmp_path/'live';app=create_app(root);o=setup(app)
 d=datetime.now(UTC)-timedelta(minutes=1);post(o,'/api/scheduler/schedules',spec(start=d.strftime('%Y-%m-%dT%H:%M')))
 script=Path(__file__).resolve().parents[1]/'main.py'
 for attempt in range(2):
  p=subprocess.Popen([sys.executable,str(script),'--data',str(root),'--scheduler-only'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  try:
   w=Scheduler(Store(root))
   for _ in range(200):
    state=w.state()
    if state['notifications'] and state['heartbeat'].get('pid')==p.pid:break
    if p.poll() is not None:raise AssertionError(p.communicate())
    time.sleep(.05)
   assert len(state['notifications'])==1 and state['heartbeat']['pid']==p.pid
  finally:
   p.send_signal(signal.SIGINT);p.communicate(timeout=15)
  assert p.returncode==0
