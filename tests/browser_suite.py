"""Synthetic desktop/mobile toolkit workflow; no real providers or applications."""
import sys,tempfile,threading,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from coach.web import create_app
with tempfile.TemporaryDirectory() as tmp:
 app=create_app(tmp,testing=True);s=app.extensions['store'];j=s.add_job({'title':'Robotics Engineer','institution':'Example Robotics','url':'https://example.org/job'})[0]
 server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True)
   for width in (1440,390):
    page=browser.new_page(viewport={'width':width,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept());page.goto('http://127.0.0.1:'+str(server.server_port));page.locator('[data-tab=toolkit]').click();page.wait_for_function('typeof state!=="undefined" && !!state')
    page.locator('#visualJob').select_option(j);page.locator('#visualResume').click();page.locator('#visualName').fill('Example Candidate');page.locator('#visualContact').fill('candidate@example.org');page.locator('[data-v-lines]').nth(0).fill('Robotics researcher with engineering experience.');page.locator('[data-v-lines]').nth(1).fill('Python, ROS, Gazebo');page.locator('[data-v-lines]').nth(2).fill('Designed visual navigation experiments.');page.locator('[data-v-lines]').nth(3).fill('Doctoral studies in Computer Science.');page.locator('#visualSave').click();page.locator('#visualState').get_by_text('Working draft saved',exact=False).wait_for()
    page.locator('#visualForm button.primary').click();page.locator('#visualPDF').wait_for(state='visible');assert page.locator('#visualPDF').get_attribute('src');page.locator('#visualReviewed').check();page.locator('#visualApprove').click();page.locator('#visualDownloads').get_by_role('link',name='DOCX',exact=True).wait_for();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.locator('[data-tool=answers]').click();page.locator('#answerLabel').fill('First name '+str(width));page.locator('#answerValue').fill('Example');page.locator('#answerSource').fill('Candidate confirmed');page.locator('#answerApproved').check();page.locator('#answerForm button').first.click();page.locator('#answerList').get_by_text('First name '+str(width),exact=True).wait_for()
    page.locator('[data-tool=voice]').click();page.locator('#voiceJob').select_option(j);page.locator('#voiceQuestion').fill('Describe your research.');page.locator('#voiceTranscript').fill('I study cooperative visual navigation using shared landmarks.');page.locator('#voiceSeconds').fill('30');page.locator('#voiceReviewed').check();page.locator('#voiceForm button.primary').click();page.locator('#voiceAttempts details').first.wait_for();page.locator('[data-tool=insights]').click();page.locator('#suiteReportRefresh').click();page.locator('#suiteMetrics').get_by_text('saved positions',exact=False).wait_for();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.locator('[data-tool=mobile]').click();page.locator('#suiteNote').fill('Prepare for interview '+str(width));page.locator('#suiteNoteForm button').click();page.locator('#suiteNotes').get_by_text('Prepare for interview '+str(width),exact=True).wait_for();page.screenshot(path='/tmp/suite-'+str(width)+'.png',full_page=True);assert not errors,errors;page.close()
   browser.close()
  print('Toolkit Chromium desktop/mobile: draft, preview, approval, answers, practice, analytics and mobile notes passed.')
 finally:server.shutdown()
