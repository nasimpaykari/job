"""Synthetic microphone and offline-shell checks; no real microphone or speech model."""
import tempfile,threading,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from coach.web import create_app
with tempfile.TemporaryDirectory() as tmp:
 app=create_app(tmp,testing=True);server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  with sync_playwright() as p:
   b=p.chromium.launch(headless=True,args=['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']);context=b.new_context(permissions=['microphone']);page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));base='http://127.0.0.1:'+str(server.server_port);page.goto(base);page.locator('[data-tab=toolkit]').click();page.locator('[data-tool=voice]').click();page.locator('#voiceRecord').click();page.locator('#voiceStop').wait_for(state='visible');page.wait_for_timeout(500);assert page.evaluate('()=>voiceRecorder && voiceRecorder.state==="recording"');page.wait_for_timeout(1200);page.locator('#voiceStop').click();page.locator('#voiceAudio').wait_for(state='visible');assert page.locator('#voiceAudio').get_attribute('src').startswith('blob:');page.evaluate('()=>document.getElementById("voiceAudio").play()');page.locator('#voiceClearAudio').click();assert page.locator('#voiceAudio').is_hidden();assert not page.locator('#voiceAudio').get_attribute('src');assert not errors,errors
   page.wait_for_timeout(500);assert page.evaluate('()=>!!navigator.serviceWorker.controller');assert page.evaluate('caches.keys()')==[];server.shutdown();server.server_close();page.goto(base);page.wait_for_timeout(500);assert 'Connect to your Job Coach server' in page.content();assert 'Example Candidate' not in page.content();b.close()
  print('Synthetic microphone: record/stop/playback/delete passed. Offline navigation shows only generic shell; no cache entries.')
 finally:server.shutdown()
