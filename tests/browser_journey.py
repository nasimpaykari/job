"""Optional real-browser test; uses only synthetic temporary workspace data."""
import sys,tempfile,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from coach.web import create_app
from coach.journey import Journey
with tempfile.TemporaryDirectory() as tmp:
 app=create_app(tmp,testing=True);s=app.extensions['store'];j=s.add_job({'title':'Robotics Engineer','institution':'Example Robotics Inc','url':'https://example.org/j','contacts':'Recruiter introduced by a colleague.'})[0]
 w=Journey(s);c=w.contact({'name':'Alex Morgan','role':'Recruiter','email':'alex@example.org'});w.link(j,c['id'])
 w.save(j,{'title':'Technical interview · Round 2','kind':'Interview','start':'2026-11-05T10:00','end':'2026-11-05T11:00','zone':'America/New_York','contact_ids':[c['id']],'notes':'Prepare a navigation project example and questions for the robotics team.'})
 server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True)
   for width in [1440,390]:
    page=browser.new_page(viewport={'width':width,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:'+str(server.server_port));page.locator('[data-journey-open]').click();page.locator('#journeyEntries').get_by_text('Technical interview · Round 2',exact=True).wait_for()
    assert page.locator('#journeyLegacy').text_content()=='Recruiter introduced by a colleague.'
    page.locator('#journeyMonth').fill('2026-11');page.locator('#journeyMonth').dispatch_event('change')
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    page.screenshot(path='/tmp/journey-'+str(width)+'.png',full_page=True)
    page.locator('#journeyEventEditor summary').click();page.locator('#journeyAllDay').check();page.locator('#journeyTitle').fill('Final deadline');page.locator('#journeyKind').select_option('Final deadline');page.locator('#journeyStart').fill('2026-11-20');page.locator('#journeyEventForm button[type=button]').evaluate('(el)=>el.blur()')
    page.locator('#journeyEventForm button.primary').click();page.locator('#journeyEntries').get_by_text('Final deadline',exact=True).wait_for()
    assert not errors,errors
    page.close()
   browser.close()
  print('Chromium desktop/mobile: timeline, contacts, calendar, all-day form submission, no JS errors or horizontal overflow.')
 finally:server.shutdown()
