"""Real Chromium synthetic review workflow. No external accounts or messages."""
import sys,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from coach.web import create_app
from coach.mailbox import Mailbox
with tempfile.TemporaryDirectory() as tmp:
 app=create_app(tmp,testing=True);s=app.extensions['store'];j=s.add_job({'title':'Robotics Engineer','institution':'Example Robotics','url':'https://example.org/job'})[0];m=Mailbox(s)
 server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True)
   for width in (1440,390):
    mid=m.ingest(dict(provider='gmail',account='owner@example.org',provider_id=str(width),thread='t',sender='recruiter@example.org',name='Recruiter',subject='Example Robotics: Robotics Engineer interview invitation '+str(width),body='We invite you to interview. <script>throw new Error("unsafe")</script>',timestamp=time.time()))
    page=browser.new_page(viewport={'width':width,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:'+str(server.server_port));page.locator('[data-tab=mail]').click();page.locator('[data-mail-review="'+mid+'"]').wait_for();assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    page.locator('[data-mail-review="'+mid+'"]').click();page.locator('#mailJob').select_option(j);page.locator('#mailSummary').fill('Reviewed invitation for '+str(width));page.locator('#mailStatus').select_option('Interviewing');page.locator('#mailAddContact').check();page.locator('#mailAddEvent').check();page.locator('#mailStart').fill('2026-11-05T14:00');page.locator('#mailEnd').fill('2026-11-05T15:00');page.locator('#mailZone').fill('UTC');page.locator('#mailShare').check();page.locator('#mailReviewForm button.primary').click();page.locator('#mailReviewDialog').wait_for(state='hidden');assert s.job(j)['status']=='Interviewing'
    page.locator('#mailThanks').click();page.locator('#mailTo').fill('recruiter@example.org');page.locator('#mailSubject').fill('Thank you '+str(width));page.locator('#mailBody').fill('Thank you for discussing the role with me.');page.locator('#mailDraftForm button.primary').click();page.locator('#mailDrafts').get_by_text('Thank you '+str(width),exact=True).wait_for()
    page.screenshot(path='/tmp/mail-'+str(width)+'.png',full_page=True);assert not errors,errors;page.close()
   browser.close()
  print('Email Chromium desktop/mobile: message review, contact, interview, status, private draft, no script execution or horizontal overflow.')
 finally:server.shutdown()
