"""Optional graphical smoke test; requires Playwright and Chromium. Uses temporary fixture data."""
import os,time,urllib.request,json,subprocess,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
OUTPUT=Path(tempfile.mkdtemp(prefix='job-coach-browser-'))
p=subprocess.Popen([__import__('sys').executable,str(ROOT/'tests'/'ui_server.py')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
 for _ in range(100):
  try:urllib.request.urlopen('http://127.0.0.1:5053/');break
  except Exception:time.sleep(.1)
 def post(path,data):return json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:5053'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})))
 job=post('/api/jobs',{'institution':'Example Robotics Inc','title':'Senior Robotics Software Engineer','career_track':'Industry','work_mode':'Hybrid','seniority':'Senior','next_action':'Tailor the one-page resume','next_action_due':'2026-01-01','priority':'High','description':'Python experience is required. Robotics experience is preferred.','url':'https://example.com/jobs/123'})
 text='Developed robot navigation software using Python and ROS.'
 src=post('/api/career/sources',{'label':'Master CV excerpt','content':text});fact=post('/api/career/facts',{'text':text,'quote':text,'source_id':src['id'],'category':'skills','status':'verified'})
 post('/api/jobs/'+job['id']+'/strategy',{'label':'Python development experience','quote':'Python experience is required.','status':'Supported','reviewed':True,'fact_ids':[fact['id']]})
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto('http://127.0.0.1:5053/');page.locator('#filter_career_track option[value="Industry"]').wait_for(state='attached')
   page.locator('#filter_career_track').select_option('Industry');page.locator('[data-strategy="'+job['id']+'"]').click()
   page.locator('#strategyList').get_by_text('Python development experience',exact=True).wait_for()
   page.screenshot(path=str(OUTPUT/f'strategy-{width}.png'),full_page=True)
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2'),f'Strategy overflow {width}'
   page.locator('[data-criterion-edit]').first.click();page.locator('#criterionLabel').fill('Python software development');page.locator('#criterionReviewed').check();page.locator('#strategyForm button[type="submit"], #strategyForm button:not([type])').click()
   page.locator('#strategyList').get_by_text('Python software development',exact=True).wait_for()
   # Restore label for the next viewport; keep fixture evidence unchanged.
   page.locator('[data-criterion-edit]').first.click();page.locator('#criterionLabel').fill('Python development experience');page.locator('#criterionReviewed').check();page.locator('#strategyForm button:not([type])').click()
   page.locator('[data-tab="search"]').click();page.screenshot(path=str(OUTPUT/f'search-{width}.png'),full_page=True)
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2'),f'Search overflow {width}'
   page.locator('[data-tab="jobs"]').click();page.screenshot(path=str(OUTPUT/f'jobs-{width}.png'),full_page=True)
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2'),f'Jobs overflow {width}'
   assert not errors,errors
   print(f'Chromium {width}px passed: industry card filtering, evidence review/edit, search and opportunity layouts; no JavaScript errors or horizontal page overflow.',flush=True)
   page.close()
  browser.close()
finally:p.terminate();p.wait()
