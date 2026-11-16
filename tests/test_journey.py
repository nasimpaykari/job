import sqlite3,zipfile
import pytest
from coach.core import Store
from coach.journey import Journey
from coach.packages import Removal
from coach.storage import Storage
from coach.web import create_app
from test_upgrade import setup,post,patch,PASSWORD
@pytest.fixture
def env(tmp_path):
 s=Store(tmp_path);j=s.add_job({'title':'Engineer','institution':'Example Inc','url':'https://example.com/a','contacts':'Original recruiter notes'})[0];return s,Journey(s,'Tester'),j
def event(**kw):
 d=dict(title='Technical interview',kind='Interview',start='2026-11-02T09:00',end='2026-11-02T10:00',zone='America/New_York',notes='Prepare STAR stories',contact_ids=[]);d.update(kw);return d
def test_contacts_links_conflicts_archive(env):
 s,w,j=env;c=w.contact({'name':'Recruiter','email':'a@example.com'});w.link(j,c['id']);assert w.contacts()[0]['job_ids']==[j]
 with pytest.raises(ValueError):w.contact({'name':'Duplicate','email':'A@example.com'})
 with pytest.raises(ValueError):w.contact({'name':'Edited','revision':0},c['id'])
 w.contact({'name':'Recruiter','email':'a@example.com','revision':1,'archived':True},c['id']);assert w.contacts()[0]['archived'];assert w.timeline(j)['legacy_contacts']=='Original recruiter notes'
 w.link(j,c['id'],True);assert not w.contacts()[0]['job_ids']
def test_event_updates_status_history_and_ics(env):
 s,w,j=env;c=w.contact({'name':'Professor'});w.link(j,c['id']);d=event(contact_ids=[c['id']]);e=w.save(j,d)
 assert s.job(j)['status']=='New';assert w.events(j)[0]['start_utc']=='2026-11-02T14:00:00+00:00'
 w.save(j,dict(d,revision=e['revision'],status='Completed',outcome='Second interview requested'),e['id'])
 with pytest.raises(ValueError):w.save(j,dict(d,revision=1),e['id'])
 s.edit_job(j,{'status':'Interviewing'});assert any('New → Interviewing' in r['title'] for r in w.timeline(j)['audit'])
 out=w.calendar(j).decode();assert 'DTSTART:20261102T140000Z' in out and 'SEQUENCE:2' in out and e['id']+'@job-coach.local' in out
 assert 'ATTENDEE' not in out and 'METHOD:REQUEST' not in out
@pytest.mark.parametrize('start,end,zone',[('2026-03-08T02:30','2026-03-08T03:30','America/New_York'),('2026-11-01T01:30','2026-11-01T02:30','America/New_York'),('2026-11-02T09:00','2026-11-02T08:00','UTC'),('2026-11-02T09:00','2026-11-02T10:00','Not/AZone'),('2026-02-30T09:00','2026-03-01T10:00','UTC')])
def test_invalid_times(env,start,end,zone):
 with pytest.raises(ValueError):env[1].save(env[2],event(start=start,end=end,zone=zone))
@pytest.mark.parametrize('patch_value',[{'meeting_url':'javascript:alert(1)'},{'kind':'Unknown'},{'status':'Unknown'},{'contact_ids':['foreign']},{'contact_ids':'abc'},{'all_day':True,'start':'2026-02-30'},{'title':[]}])
def test_invalid_event(env,patch_value):
 with pytest.raises(ValueError):env[1].save(env[2],event(**patch_value))
def test_calendar_escaping_and_all_day_cancel(env):
 s,w,j=env;w.save(j,event(title='Résumé '+('長'*90),notes='x\nBEGIN:VEVENT\nSUMMARY:injected,semi;slash\\',all_day=True,start='2026-12-31',status='Cancelled'))
 data=w.calendar().decode();assert 'DTEND;VALUE=DATE:20270101' in data and 'STATUS:CANCELLED' in data
 assert all(len(x.encode())<=75 for x in data.split('\r\n'));assert data.count('\r\nBEGIN:VEVENT\r\n')==1
 assert '\\nBEGIN:VEVENT\\nSUMMARY:injected\\,semi\\;slash\\\\' in data.replace('\r\n ','')
def test_merge_delete_restore_and_snapshot(env,tmp_path):
 s,w,j=env;j2=s.add_job({'title':'Other','institution':'Example Inc','url':'https://example.com/b'})[0];c=w.contact({'name':'Shared'});w.link(j,c['id']);w.link(j2,c['id']);e=w.save(j2,event(contact_ids=[c['id']]))
 s.merge(j,j2);assert w.events(j)[0]['id']==e['id'];assert w.contacts()[0]['job_ids']==[j]
 Removal(s).job(j);assert not w.events()
 with pytest.raises(ValueError):w.save(j,event())
 Removal(s).job(j,True);assert len(w.events())==1
 snap=Storage(s).snapshot(tmp_path/'backup.zip')
 with zipfile.ZipFile(snap) as z:
  target=tmp_path/'copy.db';target.write_bytes(z.read('coach.db'))
 with sqlite3.connect(target) as c:assert c.execute('SELECT count(*) FROM journey_events').fetchone()[0]==1
 Store(s.root);assert len(w.events())==1

def test_workspace_permissions_and_csrf(tmp_path):
 app=create_app(tmp_path);owner=setup(app);j=post(owner,'/api/jobs',{'title':'Engineer','institution':'Example','url':'https://example.com/j'}).json['id'];cid=post(owner,'/api/journey/contacts',{'name':'Owner contact'}).json['id']
 assert owner.post('/api/journey/contacts',json={'name':'No csrf'}).status_code==403
 post(owner,'/api/users',{'username':'staff','password':PASSWORD});staff=app.test_client();staff.get('/login');post(staff,'/api/login',{'username':'staff','password':PASSWORD})
 assert staff.get('/api/journey/contacts').json['contacts']==[];assert staff.get('/api/jobs/'+j+'/timeline').status_code==400
 assert patch(staff,'/api/journey/contacts/'+cid,{'name':'intruder','revision':1}).status_code==400
 shared=owner.get('/api/state').json['active_workspace'];post(owner,'/api/share',{'username':'staff','role':'viewer'});post(staff,'/api/workspace',{'id':shared})
 assert len(staff.get('/api/journey/contacts').json['contacts'])==1;assert post(staff,'/api/jobs/'+j+'/events',event()).status_code==403
 post(owner,'/api/share',{'username':'staff','role':'editor'});assert post(staff,'/api/jobs/'+j+'/events',event()).status_code==200
 assert staff.get('/api/journey/calendar.ics').status_code==200;assert staff.get('/api/jobs/'+j+'/timeline').json['events'][0]['actor']=='staff'

def test_unlinked_participant_history_still_editable(env):
 s,w,j=env;c=w.contact({'name':'Past interviewer'});w.link(j,c['id']);d=event(contact_ids=[c['id']]);e=w.save(j,d);w.link(j,c['id'],True)
 w.save(j,dict(d,revision=1,status='Completed'),e['id']);assert w.events(j)[0]['contact_ids']==[c['id']]
 with pytest.raises(ValueError):w.save(j,d)

def test_date_bounds(env):
 for d in [event(start='0001-01-01T01:00'),event(all_day=True,start='9999-12-31')]:
  with pytest.raises(ValueError):env[1].save(env[2],d)
