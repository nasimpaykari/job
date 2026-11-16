"""Workspace-scoped contacts, application events and portable calendar export."""
import json,re
from datetime import datetime,timezone,date,timedelta
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from .core import uid,now,canonical_url
from .career import text_field
from .packages import active_job
KINDS=['Interview','Submission','Message','Follow-up','Note','Final deadline','Priority date','Reference letter']
STATES=['Planned','Completed','Cancelled']

def initialize(s):
    with s.db() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='journey_events'").fetchone()
        populated=c.execute('SELECT 1 FROM jobs LIMIT 1').fetchone()
    if not exists and populated:s.backup()
    with s.db() as c:c.executescript('''
    CREATE TABLE IF NOT EXISTS journey_contacts(id TEXT PRIMARY KEY,data TEXT NOT NULL,revision INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS journey_links(job_id TEXT NOT NULL,contact_id TEXT NOT NULL,PRIMARY KEY(job_id,contact_id));
    CREATE TABLE IF NOT EXISTS journey_events(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,data TEXT NOT NULL,revision INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS journey_job ON journey_events(job_id);
    ''')

def moment(value,zone):
    try:
        if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d',value):raise ValueError()
        d=datetime.fromisoformat(value);tz=ZoneInfo(zone)
        if not 1900<=d.year<=9998:raise ValueError()
    except (ValueError,TypeError,ZoneInfoNotFoundError):raise ValueError('Enter a valid local date/time and IANA time zone, such as America/New_York.') from None
    candidates={d.replace(tzinfo=tz,fold=f).astimezone(timezone.utc) for f in (0,1) if d.replace(tzinfo=tz,fold=f).astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)==d}
    if len(candidates)!=1:raise ValueError('This local time is skipped or repeated by daylight saving. Use UTC and the intended UTC time.')
    return candidates.pop().isoformat()

def pack(r):return dict(json.loads(r['data']),id=r['id'],revision=r['revision'])

class Journey:
    def __init__(self,s,actor='Local user'):self.s=s;self.actor=actor
    def contacts(self):
        with self.s.db() as c:
            rows=c.execute('SELECT * FROM journey_contacts').fetchall();links=c.execute('SELECT * FROM journey_links').fetchall()
        return [dict(pack(r),job_ids=[l['job_id'] for l in links if l['contact_id']==r['id']]) for r in rows]
    def contact(self,d,cid=''):
        if not isinstance(d,dict):raise ValueError('Contact must be an object.')
        value={k:text_field(d,k,n,k=='name') for k,n in [('name',200),('role',200),('organization',300),('email',254),('phone',100),('url',2000),('notes',10000)]}
        if value['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value['email']):raise ValueError('Enter a valid email address.')
        if value['url']:canonical_url(value['url'])
        value['archived']=d.get('archived') is True
        with self.s.lock,self.s.db() as c:
            row=c.execute('SELECT * FROM journey_contacts WHERE id=?',(cid,)).fetchone() if cid else None
            if cid and (not row or d.get('revision')!=row['revision']):raise ValueError('Contact changed or is unavailable. Reload before saving.')
            if value['email']:
                for r in c.execute('SELECT * FROM journey_contacts'):
                    if r['id']!=cid and json.loads(r['data']).get('email','').casefold()==value['email'].casefold():raise ValueError('A contact with this email already exists, including archived contacts.')
            cid=cid or uid();rev=row['revision']+1 if row else 1
            c.execute('INSERT OR REPLACE INTO journey_contacts VALUES(?,?,?)',(cid,json.dumps(value),rev))
            self.s.event(c,'','contact_saved',{'actor':self.actor,'id':cid,'before':pack(row) if row else None,'after':value})
        return {'id':cid,'revision':rev}
    def link(self,jid,cid,remove=False):
        with self.s.lock,self.s.db() as c:
            active_job(self.s,jid)
            row=c.execute('SELECT * FROM journey_contacts WHERE id=?',(cid,)).fetchone()
            if not row:raise ValueError('Contact not found.')
            if remove:c.execute('DELETE FROM journey_links WHERE job_id=? AND contact_id=?',(jid,cid))
            else:c.execute('INSERT OR IGNORE INTO journey_links VALUES(?,?)',(jid,cid))
            self.s.event(c,jid,'contact_unlinked' if remove else 'contact_linked',{'actor':self.actor,'contact':json.loads(row['data'])['name']})
    def save(self,jid,d,eid=''):
        if not isinstance(d,dict):raise ValueError('Event must be an object.')
        value={k:text_field(d,k,n,k in ('title','start')) for k,n in [('title',300),('start',30),('end',30),('zone',100),('location',1000),('meeting_url',2000),('notes',15000),('outcome',5000)]}
        value.update(kind=d.get('kind','Note'),status=d.get('status','Planned'),all_day=d.get('all_day') is True)
        if value['kind'] not in KINDS or value['status'] not in STATES:raise ValueError('Choose a valid event kind and status.')
        if value['meeting_url']:canonical_url(value['meeting_url'])
        if value['all_day']:
            try:
                assert re.fullmatch(r'\d{4}-\d\d-\d\d',value['start']);date.fromisoformat(value['start']);assert 1900<=date.fromisoformat(value['start']).year<=9998
            except (ValueError,AssertionError):raise ValueError('Enter an all-day date as YYYY-MM-DD.') from None
            value.update(end='',zone='',start_utc='',end_utc='')
        else:
            value['start_utc']=moment(value['start'],value['zone']);value['end_utc']=moment(value['end'],value['zone'])
            if value['end_utc']<=value['start_utc']:raise ValueError('End must be after start.')
        ids=d.get('contact_ids',[])
        if not isinstance(ids,list) or len(ids)>50 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise ValueError('Select up to 50 unique participants.')
        with self.s.lock,self.s.db() as c:
            active_job(self.s,jid)
            row=c.execute('SELECT * FROM journey_events WHERE id=? AND job_id=?',(eid,jid)).fetchone() if eid else None
            if eid and (not row or d.get('revision')!=row['revision']):raise ValueError('Event changed or is unavailable. Reload before saving.')
            previous_ids=json.loads(row['data']).get('contact_ids',[]) if row else []
            if any(i not in previous_ids and not c.execute('SELECT 1 FROM journey_links WHERE job_id=? AND contact_id=?',(jid,i)).fetchone() for i in ids):raise ValueError('Participants must be linked to this job first.')
            value['contact_ids']=ids
            value.update(updated=now(),actor=self.actor,created=json.loads(row['data'])['created'] if row else now())
            eid=eid or uid();rev=row['revision']+1 if row else 1
            c.execute('INSERT OR REPLACE INTO journey_events VALUES(?,?,?,?)',(eid,jid,json.dumps(value),rev))
            self.s.event(c,jid,'application_event_saved',{'actor':self.actor,'id':eid,'before':pack(row) if row else None,'after':value})
        return {'id':eid,'revision':rev}
    def events(self,jid=''):
        if jid:active_job(self.s,jid)
        active={j['id'] for j in self.s.jobs()}
        with self.s.db() as c:rows=c.execute('SELECT * FROM journey_events'+(' WHERE job_id=?' if jid else ''),(jid,) if jid else ()).fetchall()
        return sorted([dict(pack(r),job_id=r['job_id']) for r in rows if r['job_id'] in active],key=lambda x:x['start_utc'] or x['start'])
    def timeline(self,jid):
        j=active_job(self.s,jid)
        with self.s.db() as c:rows=c.execute('SELECT * FROM events WHERE job_id=? ORDER BY created DESC',(jid,)).fetchall()
        audit=[]
        for r in rows:
            d=json.loads(r['data']);kind=r['kind'];title=kind.replace('_',' ').capitalize()
            if kind=='edited':title='Status: '+d.get('before',{}).get('status','Unknown')+' → '+d.get('status','Unknown') if d.get('status')!=d.get('before',{}).get('status') else 'Job details edited'
            audit.append({'id':r['id'],'created':r['created'],'title':title,'actor':d.get('actor',''),'data':d})
        return {'events':self.events(jid),'audit':audit,'contacts':[x for x in self.contacts() if jid in x['job_ids']], 'legacy_contacts':j.get('contacts',''),'deadline':j.get('deadline',''),'next_action':j.get('next_action',''),'next_action_due':j.get('next_action_due','')}
    def calendar(self,jid=''):
        jobs={j['id']:j for j in self.s.jobs()};lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Unified Job Coach//Journey 5.1//EN','CALSCALE:GREGORIAN']
        for e in self.events(jid):
            j=jobs[e['job_id']];lines+=['BEGIN:VEVENT','UID:'+e['id']+'@job-coach.local','DTSTAMP:'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),'SEQUENCE:'+str(e['revision']),'SUMMARY:'+ics(e['title']+' · '+j['institution']+' · '+j['title'])]
            if e['all_day']:lines+=['DTSTART;VALUE=DATE:'+e['start'].replace('-',''),'DTEND;VALUE=DATE:'+(date.fromisoformat(e['start'])+timedelta(days=1)).strftime('%Y%m%d')]
            else:lines+=['DTSTART:'+datetime.fromisoformat(e['start_utc']).strftime('%Y%m%dT%H%M%SZ'),'DTEND:'+datetime.fromisoformat(e['end_utc']).strftime('%Y%m%dT%H%M%SZ')]
            lines+=['STATUS:'+('CANCELLED' if e['status']=='Cancelled' else 'CONFIRMED'),'LOCATION:'+ics(e['location']),'DESCRIPTION:'+ics(e['kind']+' · '+e['status']+'\n'+e['notes']+'\n'+e['meeting_url']+'\n'+e['outcome']),'END:VEVENT']
        lines+=['END:VCALENDAR'];return ('\r\n'.join(fold(x) for x in lines)+'\r\n').encode('utf-8')

def ics(s):return s.replace('\\','\\\\').replace('\r\n','\n').replace('\r','\n').replace('\n','\\n').replace(';','\\;').replace(',','\\,')
def fold(s):
    rows=[];line=''
    for ch in s:
        if len((line+ch).encode('utf-8'))>75:rows.append(line);line=' '
        line+=ch
    return '\r\n'.join(rows+[line])
