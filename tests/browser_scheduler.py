"""Optional synthetic desktop/mobile smoke test; no network sources or OS alerts."""
import sys,tempfile,threading
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from coach.web import create_app
from coach.scheduler import Scheduler,Runner
with tempfile.TemporaryDirectory() as tmp:
 app=create_app(tmp,testing=True);s=app.extensions['store'];w=Scheduler(s)
 j=s.add_job({'title':'Robotics Engineer','institution':'Example Robotics Inc','url':'https://example.org/j'})[0]
 w.save({'name':'Prepare for interview','kind':'reminder','start':(datetime.now(timezone.utc)-timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M'),'zone':'UTC','message':'Review approved STAR stories and prepare two questions for the team.','enabled':True,'interval_hours':0,'retries':0})
 runner=Runner(tmp,lambda:[s],notifier=lambda:'test request');runner.start()
 server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True)
   for width in [1440,390]:
    page=browser.new_page(viewport={'width':width,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:'+str(server.server_port));page.locator('[data-tab=scheduling]').click();page.locator('#schedInbox').get_by_text('Prepare for interview',exact=True).wait_for()
    assert 'running' in page.locator('#schedHeartbeat').text_content()
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    page.locator('#schedKind').select_option('freshness');page.locator('#schedJobs input').check();assert page.locator('#schedPostingHelp').is_visible()
    page.locator('#schedKind').select_option('reminder');page.locator('#schedName').fill('Review application '+str(width));page.locator('#schedMessage').fill('Check the approved PDF');page.locator('#schedForm button.primary').click();page.locator('#schedList').get_by_text('Review application '+str(width),exact=True).wait_for()
    page.evaluate('window.scrollTo(0,0)');page.screenshot(path='/tmp/scheduler-'+str(width)+'.png',full_page=True)
    assert not errors,errors;page.close()
   browser.close()
  print('Scheduler Chromium: desktop/mobile, heartbeat/inbox, task selection, native form submission, no JS errors or horizontal overflow.')
 finally:server.shutdown();runner.close();runner.thread.join(15)
