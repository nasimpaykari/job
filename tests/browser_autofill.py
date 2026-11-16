"""Exercise the exact extension inspection function on a synthetic supported-host form."""
from pathlib import Path
from playwright.sync_api import sync_playwright
source=(Path(__file__).resolve().parents[1]/'autofill_extension/popup.js').read_text();fn=source[source.index('function inspect('):source.index("$('preview').onclick")]
run='({fields,prior})=>{'+fn+';return inspect(fields,prior)}'
with sync_playwright() as p:
 b=p.chromium.launch(headless=True);page=b.new_page();page.route('https://jobs.lever.co/**',lambda r:r.fulfill(body='<form id="apply"><input name="first_name"><input name="email" type="email"><input type="password" name="password"><input name="sponsorship"><button>Submit</button></form><script>window.submits=0;document.querySelector("form").onsubmit=e=>{e.preventDefault();submits++}</script>',content_type='text/html'));page.goto('https://jobs.lever.co/example/apply')
 fields=[{'field':'first_name','value':'Example'},{'field':'email','value':'candidate@example.org'},{'field':'question','value':'DO NOT FILL'}];plan=page.evaluate(run,{'fields':fields,'prior':None});assert len(plan['fields'])==2;assert page.locator('[name=first_name]').input_value()==''
 result=page.evaluate(run,{'fields':[],'prior':plan});assert result['filled']==2;assert page.locator('[name=email]').input_value()=='candidate@example.org';assert page.locator('[name=sponsorship]').input_value()=='';assert page.evaluate('submits')==0
 try:page.evaluate(run,{'fields':[],'prior':plan});raise AssertionError('Stale form allowed')
 except Exception as e:assert 'Form changed' in str(e)
 b.close()
print('Autofill synthetic Greenhouse/Lever-style form: preview, explicit fill, no sensitive/password fill, no submission, stale plan rejection.')
