"""Durable local schedules. One worker process per data root; no external messages."""
import json,hashlib,os,re,threading,time,subprocess,shutil,sys
from datetime import datetime,timedelta,timezone,date
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .core import uid,now
from .career import text_field
from .journey import moment,Journey
from .deadlines import deadline_date
UTC=timezone.utc
CLOSED={'Ignored','Archived','Rejected','Withdrawn'}
DEFAULT={'enabled':False,'zone':'America/New_York','events':True,'deadlines':True,'actions':True,'lead_minutes':60,'deadline_days':7,'action_days':0,'daily_time':'09:00','quiet_enabled':True,'quiet_start':'22:00','quiet_end':'07:00','desktop':False,'digest':'off'}
class Cancelled(BaseException):pass

def stamp(d):return d.astimezone(UTC).isoformat(timespec='seconds')
def clock():return datetime.now(UTC)
def parse(s):return datetime.fromisoformat(s).astimezone(UTC)
def initialize(s):
    with s.db() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='schedules'").fetchone();populated=c.execute('SELECT 1 FROM jobs LIMIT 1').fetchone()
    if not exists and populated:s.backup()
    with s.db() as c:c.executescript('''
    CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY,data TEXT NOT NULL,revision INTEGER NOT NULL,enabled INTEGER NOT NULL,next_run TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS schedule_runs(id TEXT PRIMARY KEY,schedule_id TEXT NOT NULL,due TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL,attempt INTEGER NOT NULL,retry_at TEXT NOT NULL,UNIQUE(schedule_id,due));
    CREATE TABLE IF NOT EXISTS reminder_inbox(id TEXT PRIMARY KEY,dedupe TEXT UNIQUE NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL,seen INTEGER NOT NULL DEFAULT 0,desktop TEXT NOT NULL DEFAULT 'pending');
    CREATE TABLE IF NOT EXISTS scheduler_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    ''')

def wall(day,hhmm,zone):
    """For reminder delivery only: later repeated time; advance skipped time."""
    d=datetime.fromisoformat(str(day)+'T'+hhmm);tz=ZoneInfo(zone)
    for _ in range(181):
        choices={d.replace(tzinfo=tz,fold=f).astimezone(UTC) for f in (0,1) if d.replace(tzinfo=tz,fold=f).astimezone(UTC).astimezone(tz).replace(tzinfo=None)==d}
        if choices:return max(choices)
        d+=timedelta(minutes=1)
    raise ValueError('Could not resolve reminder time.')
def quiet(p,t):
    if not p['quiet_enabled']:return False
    h=t.astimezone(ZoneInfo(p['zone'])).strftime('%H:%M');a=p['quiet_start'];b=p['quiet_end']
    return a<=h<b if a<b else h>=a or h<b

class Scheduler:
    def __init__(self,s):self.s=s
    def meta(self,key,default=None):
        with self.s.db() as c:r=c.execute('SELECT value FROM scheduler_meta WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def setmeta(self,key,value):
        with self.s.db() as c:c.execute('INSERT OR REPLACE INTO scheduler_meta VALUES(?,?)',(key,json.dumps(value)))
    def prefs(self):return dict(DEFAULT,**self.meta('prefs',{}))
    def preferences(self,d):
        p=self.prefs()
        if not isinstance(d,dict) or set(d)-set(DEFAULT):raise ValueError('Invalid reminder preferences.')
        p.update(d)
        for k in ['enabled','events','deadlines','actions','quiet_enabled','desktop']:
            if type(p[k]) is not bool:raise ValueError('Use checkbox values for reminder preferences.')
        for k,limit in [('lead_minutes',10080),('deadline_days',90),('action_days',90)]:
            if type(p[k]) is not int or not 0<=p[k]<=limit:raise ValueError('Invalid '+k)
        try:ZoneInfo(p['zone'])
        except (ZoneInfoNotFoundError,ValueError,TypeError):raise ValueError('Choose an IANA time zone.') from None
        for k in ['daily_time','quiet_start','quiet_end']:
            if not isinstance(p[k],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',p[k]):raise ValueError('Times must be HH:MM.')
        if p['quiet_enabled'] and p['quiet_start']==p['quiet_end']:raise ValueError('Quiet hours need different start and end times.')
        if p['digest'] not in ('off','daily','weekly'):raise ValueError('Choose off, daily or weekly summaries.')
        self.setmeta('prefs',p);return p
    def state(self):
        with self.s.db() as c:
            schedules=[dict(json.loads(r['data']),id=r['id'],revision=r['revision'],enabled=bool(r['enabled']),next_run=r['next_run']) for r in c.execute('SELECT * FROM schedules ORDER BY rowid DESC')]
            runs=[dict(json.loads(r['data']),id=r['id'],schedule_id=r['schedule_id'],due=r['due'],status=r['status'],attempt=r['attempt'],retry_at=r['retry_at']) for r in c.execute('SELECT * FROM schedule_runs ORDER BY rowid DESC LIMIT 100')]
            notices=[dict(json.loads(r['data']),id=r['id'],created=r['created'],seen=bool(r['seen']),desktop=r['desktop']) for r in c.execute('SELECT * FROM reminder_inbox ORDER BY rowid DESC LIMIT 100')]
        heartbeat=self.meta('heartbeat',{})
        return {'preferences':self.prefs(),'schedules':schedules,'runs':runs,'notifications':notices,'heartbeat':heartbeat,'worker_recent':bool(heartbeat.get('at') and clock()-parse(heartbeat['at'])<timedelta(seconds=45))}
    def save(self,d,sid=''):
        if not isinstance(d,dict):raise ValueError('Schedule must be an object.')
        name=text_field(d,'name',200);kind=d.get('kind')
        if kind not in ('search','freshness','reminder'):raise ValueError('Choose search, freshness or reminder.')
        interval=d.get('interval_hours',0);retries=d.get('retries',1)
        if type(interval) is not int or interval not in (0,1,6,12,24,168):raise ValueError('Choose an allowed repeat interval.')
        if type(retries) is not int or not 0<=retries<=2:raise ValueError('Choose zero, one or two retries.')
        start=text_field(d,'start',30);zone=text_field(d,'zone',100);due=moment(start,zone)
        value={'name':name,'kind':kind,'interval_hours':interval,'retries':retries,'start':start,'zone':zone,'created':now()}
        with self.s.lock,self.s.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM schedules WHERE id=?',(sid,)).fetchone() if sid else None
            if sid and (not old or d.get('revision')!=old['revision']):raise ValueError('Schedule changed. Refresh before saving.')
            if old and c.execute("SELECT 1 FROM schedule_runs WHERE schedule_id=? AND status IN ('queued','running','retry_wait','cancel_requested')",(sid,)).fetchone():raise ValueError('Cancel or finish its active run before editing this schedule.')
            if not old and c.execute('SELECT count(*) FROM schedules').fetchone()[0]>=100:raise ValueError('Keep up to 100 schedules per workspace.')
            if kind=='search':
                from .search_plan import build_plan
                prepared,targets,selected=build_plan(self.s,self.s.settings(),'both')
                keys=['network_enabled','browser_render','show_browser','max_pages','discovery_mode','excluded_keywords','excluded_employers','required_keywords','countries','regions','exclude_unknown_location','minimum_salary','currency','exclude_unknown_salary','preferred_keywords','preferred_job_types','_api_sources']
                value['plan']=[{k:prepared[k] for k in keys},targets,selected]
                value['source_count']=len(targets)
            elif kind=='freshness':
                ids=d.get('job_ids',[]);active={j['id'] for j in self.s.jobs() if j['status'] not in CLOSED}
                if not isinstance(ids,list) or not 1<=len(ids)<=50 or any(not isinstance(x,str) or x not in active for x in ids) or len(set(ids))!=len(ids):raise ValueError('Choose 1–50 unique active positions.')
                value['job_ids']=ids
            else:value['message']=text_field(d,'message',3000)
            sid=sid or uid();rev=old['revision']+1 if old else 1
            c.execute('INSERT OR REPLACE INTO schedules VALUES(?,?,?,?,?)',(sid,json.dumps(value),rev,int(d.get('enabled') is True),stamp(parse(due))))
            self.s.event(c,'','schedule_saved',{'id':sid,'name':name,'revision':rev})
        return {'id':sid,'revision':rev}
    def control(self,sid,action,revision):
        with self.s.lock,self.s.db() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM schedules WHERE id=?',(sid,)).fetchone()
            if not r or revision!=r['revision']:raise ValueError('Schedule changed. Refresh first.')
            if action not in ('pause','resume','cancel'):raise ValueError('Invalid schedule action.')
            c.execute('UPDATE schedules SET enabled=?,revision=revision+1 WHERE id=?',(int(action=='resume'),sid))
            if action=='cancel':
                c.execute("UPDATE schedule_runs SET status=CASE WHEN status='running' THEN 'cancel_requested' ELSE 'cancelled' END WHERE schedule_id=? AND status IN ('running','queued','retry_wait')",(sid,))
            self.s.event(c,'','schedule_'+action,{'id':sid})
    def retry(self,rid,t=None):
        t=t or clock()
        with self.s.lock,self.s.db() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM schedule_runs WHERE id=?',(rid,)).fetchone()
            if not r or r['status'] not in ('failed','interrupted','cancelled'):raise ValueError('Only a failed, interrupted or cancelled run can be retried.')
            if c.execute("SELECT 1 FROM schedule_runs WHERE schedule_id=? AND status IN ('queued','running','retry_wait','cancel_requested')",(r['schedule_id'],)).fetchone():raise ValueError('This schedule already has an active run.')
            c.execute("UPDATE schedule_runs SET status='queued',retry_at=? WHERE id=?",(stamp(t),rid))
    def recover(self):
        with self.s.db() as c:c.execute("UPDATE schedule_runs SET status='interrupted' WHERE status IN ('running','cancel_requested')")
    def enqueue(self,t):
        with self.s.lock,self.s.db() as c:
            c.execute('BEGIN IMMEDIATE')
            for r in c.execute('SELECT * FROM schedules WHERE enabled=1 AND next_run<=?',(stamp(t),)).fetchall():
                if c.execute("SELECT 1 FROM schedule_runs WHERE schedule_id=? AND status IN ('queued','running','retry_wait','cancel_requested')",(r['id'],)).fetchone():continue
                d=json.loads(r['data']);rid=uid();data={'schedule':d,'log':[],'result':None,'error':'','queued_at':stamp(t)}
                c.execute("INSERT OR IGNORE INTO schedule_runs VALUES(?,?,?,'queued',?,0,?)",(rid,r['id'],r['next_run'],json.dumps(data),stamp(t)))
                hours=d['interval_hours'];next_due=parse(r['next_run'])
                if hours:next_due+=timedelta(hours=hours*(int((t-next_due).total_seconds()//(hours*3600))+1))
                c.execute('UPDATE schedules SET enabled=?,next_run=? WHERE id=?',(int(bool(hours)),stamp(next_due),r['id']))
    def claim(self,t):
        with self.s.lock,self.s.db() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute("SELECT * FROM schedule_runs WHERE status IN ('queued','retry_wait') AND retry_at<=? ORDER BY retry_at,rowid LIMIT 1",(stamp(t),)).fetchone()
            if not r:return None
            c.execute("UPDATE schedule_runs SET status='running',attempt=attempt+1 WHERE id=?",(r['id'],));return dict(r,attempt=r['attempt']+1)
    def checkpoint(self,rid,network=False):
        with self.s.db() as c:r=c.execute('SELECT status FROM schedule_runs WHERE id=?',(rid,)).fetchone()
        if not r or r[0]!='running':raise Cancelled()
        if network and not self.s.settings()['network_enabled']:raise ValueError('Network access is disabled in Profile & settings.')
    def notice(self,key,title,body,t,job_id=''):
        with self.s.db() as c:c.execute('INSERT OR IGNORE INTO reminder_inbox(id,dedupe,data,created) VALUES(?,?,?,?)',(uid(),key,json.dumps({'title':title,'body':body,'job_id':job_id}),stamp(t)))
    def execute(self,r,t=None,stop_event=None):
        d=json.loads(r['data']);cfg=d['schedule'];rid=r['id'];network=cfg['kind'] in ('search','freshness');start=clock();d['started']=stamp(start);d['error']='';d['result']=None;d['log']=(d.get('log',[])+['Attempt '+str(r['attempt'])])[-100:]
        def check():
            if stop_event is not None and stop_event.is_set():raise Cancelled()
            self.checkpoint(rid,network)
            if clock()-start>timedelta(minutes=30):raise ValueError('Run reached the 30-minute limit. Narrow the source/page budget.')
        def log(msg):
            check();d['log']=(d['log']+[str(msg)[:2000]])[-100:]
            with self.s.db() as c:c.execute('UPDATE schedule_runs SET data=? WHERE id=?',(json.dumps(d),rid))
        status='succeeded';retry_at=stamp(t or clock())
        try:
            check()
            if cfg['kind']=='search':
                from .services import Search
                result=Search(self.s).run('both',log,checkpoint=check,prepared_plan=cfg['plan']);d['result']=result
                if result['errors']:raise ValueError(str(len(result['errors']))+' source/page errors; partial imports were retained. See run result.')
            elif cfg['kind']=='freshness':
                from .freshness import Freshness
                active={j['id'] for j in self.s.jobs() if j['status'] not in CLOSED};result=[];d['result']=result;failures=0
                for jid in cfg['job_ids']:
                    check()
                    if jid not in active:log('Skipped removed or closed position.');continue
                    item=Freshness(self.s).check(jid,log,checkpoint=check);result.append(dict(item,job_id=jid,changes_pending=Freshness(self.s).detail(jid)['pending']));failures+=item['state'] in ('failed','unverified')
                d['result']=result
                if failures:raise ValueError(str(failures)+' posting checks failed or remain unverified; previous evidence retained.')
            else:self.notice('run:'+rid,cfg['name'],cfg['message'],t or clock())
            check()
        except Cancelled:status='cancelled';d['error']='Cancelled at a safe checkpoint; completed imports/checks are retained.'
        except Exception as e:
            d['error']=str(e)[:4000];status='retry_wait' if r['attempt']<=cfg['retries'] else 'failed';retry_at=stamp((t or clock())+timedelta(minutes=5*(2**(r['attempt']-1))))
        d['finished']=stamp(t or clock())
        with self.s.lock,self.s.db() as c:
            current=c.execute('SELECT status FROM schedule_runs WHERE id=?',(rid,)).fetchone()
            if current and current[0]=='cancel_requested':status='cancelled'
            c.execute('UPDATE schedule_runs SET status=?,data=?,retry_at=? WHERE id=?',(status,json.dumps(d),retry_at,rid))
        if cfg['kind']!='reminder' or status!='succeeded':self.notice('run-result:'+rid+':'+str(r['attempt']),cfg['name']+' · '+status,d.get('error') or json.dumps(d['result'])[:2500],t or clock())
    def reminders(self,t):
        p=self.prefs()
        if not p['enabled']:return
        jobs={j['id']:j for j in self.s.jobs() if j['status'] not in CLOSED};entries=[]
        if p['events']:
            for e in Journey(self.s).events():
                if e['status']!='Planned' or e['job_id'] not in jobs:continue
                start=wall(e['start'],p['daily_time'],p['zone']) if e['all_day'] else parse(e['start_utc'])
                due=start if e['all_day'] else start-timedelta(minutes=p['lead_minutes']);key='event:'+e['id']+':'+e['start']+':'+e['zone']
                entries.append((key,e['title'],e['kind']+' · '+jobs[e['job_id']]['institution']+' · '+e['start']+' '+e['zone'],due,start,e['job_id']))
        for j in jobs.values():
            for typ,raw,days,title in [('deadline',deadline_date(j.get('deadline',''))['date'],p['deadline_days'],'Application deadline'),('action',j.get('next_action_due',''),p['action_days'],j.get('next_action') or 'Next action')]:
                if not p['deadlines' if typ=='deadline' else 'actions'] or not raw:continue
                if typ=='deadline' and j['status'] in ('Submitted','Interviewing','Offer'):continue
                try:start=wall(date.fromisoformat(raw),p['daily_time'],p['zone'])
                except ValueError:continue
                entries.append((typ+':'+j['id']+':'+raw,title,j['institution']+' · '+j['title']+' · '+raw+' (date reminder; verify the exact cutoff)',start-timedelta(days=days),start,j['id']))
        for key,title,body,due,start,jid in entries:
            if due<=t<=start+timedelta(days=1):self.notice(key,title,body,t,jid)
        local=t.astimezone(ZoneInfo(p['zone']));daily=wall(local.date(),p['daily_time'],p['zone'])
        if p['digest']!='off' and t>=daily and (p['digest']=='daily' or local.weekday()==0):
            upcoming=[x for x in entries if t<=x[4]<=t+timedelta(days=7)]
            body='Next 7 days: '+str(len(upcoming))+' dated items.\n'+'\n'.join(x[1]+' · '+x[2] for x in upcoming[:30])
            self.notice('digest:'+p['digest']+':'+str(local.date()),'Daily review' if p['digest']=='daily' else 'Weekly review',body,t)
    def deliver(self,t,notifier):
        p=self.prefs()
        if not p['desktop'] or quiet(p,t):return
        with self.s.lock,self.s.db() as c:
            c.execute('BEGIN IMMEDIATE');rows=c.execute("SELECT id FROM reminder_inbox WHERE desktop='pending' AND seen=0").fetchall()
            if not rows:return
            c.executemany("UPDATE reminder_inbox SET desktop='attempted' WHERE id=?",[(r[0],) for r in rows])
        try:result=notifier()
        except Exception as e:result='failed: '+str(e)[:200]
        with self.s.db() as c:c.executemany('UPDATE reminder_inbox SET desktop=? WHERE id=?',[(result,r[0]) for r in rows])
    def seen(self,nid):
        with self.s.db() as c:c.execute('UPDATE reminder_inbox SET seen=1 WHERE id=?',(nid,))

class ProcessLock:
    def __init__(self,path):self.path=Path(path);self.file=None
    def acquire(self):
        f=self.path.open('a+b');f.seek(0);f.write(b'0');f.flush();f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl;fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:f.close();return False
        self.file=f;return True
    def close(self):
        if self.file:
            if os.name=='nt':
                import msvcrt;self.file.seek(0);msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl;fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
            self.file.close();self.file=None

def desktop_notice():
    title='Job Coach';message='New reminders or scheduled-run results are ready. Open Job Coach to review.'
    if os.name=='nt':
        import base64
        script="Add-Type -AssemblyName System.Windows.Forms; $n=New-Object System.Windows.Forms.NotifyIcon; $n.Icon=[System.Drawing.SystemIcons]::Information; $n.Visible=$true; $n.ShowBalloonTip(5000,'Job Coach','New reminders or scheduled-run results are ready. Open Job Coach to review.',[System.Windows.Forms.ToolTipIcon]::Info); Start-Sleep -Seconds 6; $n.Dispose()"
        cmd=['powershell.exe','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()]
    elif sys.platform=='darwin':cmd=['osascript','-e','display notification "'+message+'" with title "'+title+'"']
    elif shutil.which('notify-send'):cmd=['notify-send',title,message]
    else:return 'unavailable: desktop notification service not installed'
    p=subprocess.run(cmd,capture_output=True,timeout=12,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
    return 'requested (OS display not guaranteed)' if p.returncode==0 else 'failed: desktop notification service rejected request'

class Runner:
    def __init__(self,root,stores,notifier=desktop_notice):
        self.lock=ProcessLock(Path(root)/'scheduler.lock');self.stores=stores;self.notifier=notifier;self.stop=threading.Event();self.thread=None;self.started=set();self.future=None;self.pool=None;self.cursor=0
    def start(self):
        if not self.lock.acquire():return False
        self.pool=ThreadPoolExecutor(max_workers=1);self.thread=threading.Thread(target=self.loop,name='job-coach-scheduler',daemon=True);self.thread.start();return True
    def loop(self):
        try:
            while not self.stop.is_set():
                try:
                    stores=self.stores()
                    if stores:
                        offset=self.cursor%len(stores);stores=stores[offset:]+stores[:offset];self.cursor+=1
                    for s in stores:
                        w=Scheduler(s);key=str(s.root)
                        if key not in self.started:w.recover();self.started.add(key)
                        w.setmeta('heartbeat',{'at':stamp(clock()),'pid':os.getpid(),'state':'running'})
                        try:
                            w.reminders(clock());w.enqueue(clock());w.deliver(clock(),self.notifier)
                            if self.future is None or self.future.done():
                                r=w.claim(clock())
                                if r:self.future=self.pool.submit(w.execute,r,None,self.stop)
                        except Exception as e:w.setmeta('heartbeat',{'at':stamp(clock()),'pid':os.getpid(),'state':'error','error':str(e)[:1000]})
                except Exception as e:print('Scheduler scan failed:',str(e),file=sys.stderr)
                self.stop.wait(10)
        finally:
            # Keep the process lock until an in-flight network operation exits.
            if self.pool:self.pool.shutdown(wait=True)
            self.lock.close()
    def close(self):self.stop.set()
