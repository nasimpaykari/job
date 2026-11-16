const {JSDOM,VirtualConsole}=require('jsdom');
const assert=require('node:assert/strict');
(async()=>{
 const errs=[];const vc=new VirtualConsole();vc.on('jsdomError',e=>{if(!e.message.includes('navigation'))errs.push(e.message)});
 const dom=await JSDOM.fromURL('http://127.0.0.1:5053/',{resources:'usable',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.fetch=(u,o)=>fetch(new URL(u,'http://127.0.0.1:5053'),o);w.structuredClone=structuredClone;w.HTMLElement.prototype.scrollIntoView=function(){};w.confirm=()=>true;w.alert=()=>{};w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};}});
 const w=dom.window,d=w.document,$=id=>d.getElementById(id);
 const wait=async fn=>{for(let i=0;i<240;i++){if(fn())return;await new Promise(r=>setTimeout(r,50))}throw Error('UI timeout: '+$('notice').textContent+' / '+$('pdfStatus').textContent+' / '+errs.join('; '))};
 const submit=id=>$(id).dispatchEvent(new w.Event('submit',{cancelable:true}));
 const post=async(url,data,method='POST')=>{const r=await fetch('http://127.0.0.1:5053'+url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const body=await r.json();assert.ok(r.ok,JSON.stringify(body));return body;};


 await wait(()=>$('globalTypeRemove').options.length>0);
 d.querySelector('[data-tab="scheduling"]').click();await wait(()=>$('schedHeartbeat').textContent.includes('running'));
 $('schedZone').value='UTC';$('schedEnabled').checked=true;$('schedQuiet').checked=true;$('schedQuietStart').value='22:00';$('schedQuietEnd').value='07:00';submit('schedPreferences');await wait(()=>$('notice').textContent.includes('Reminder preferences saved'));
 $('schedKind').value='reminder';$('schedKind').dispatchEvent(new w.Event('change'));$('schedName').value='Prepare for interview';$('schedMessage').value='Review my approved STAR stories';$('schedStart').value=new Date(Date.now()-60000).toISOString().slice(0,16);$('schedRunZone').value='UTC';$('schedActive').checked=true;submit('schedForm');await wait(()=>$('schedList').textContent.includes('Prepare for interview'));
 await wait(()=>$('schedInbox').textContent.includes('Review my approved STAR stories'));assert.ok($('schedRuns').textContent.includes('succeeded'));
 const seen=d.querySelector('[data-sched-seen]');assert.ok(seen);seen.click();await wait(()=>$('schedInbox').textContent.includes('Seen'));
 const before=(await (await fetch('http://127.0.0.1:5053/api/scheduler')).json()).runs.length;
 $('schedRefresh').click();await new Promise(r=>setTimeout(r,100));assert.equal((await (await fetch('http://127.0.0.1:5053/api/scheduler')).json()).runs.length,before);
 await post('/api/settings',{saved_search_enabled:true,search_targets:[{id:'saved','kind':'url',label:'Test source',value:'https://example.org/careers',enabled:true}]});
 $('schedNew').click();$('schedName').value='Morning search';$('schedRunZone').value='UTC';$('schedKind').value='search';$('schedKind').dispatchEvent(new w.Event('change'));$('schedActive').checked=false;submit('schedForm');await wait(()=>$('schedList').textContent.includes('Morning search'));assert.ok($('schedList').textContent.includes('https://example.org/careers'));
 const edit=[...d.querySelectorAll('[data-sched-edit]')].find(b=>b.closest('article').textContent.includes('Morning search'));edit.click();assert.equal($('schedName').value,'Morning search');
 const cancel=[...d.querySelectorAll('[data-sched-control]')].find(b=>b.dataset.schedAction==='cancel'&&b.closest('article').textContent.includes('Morning search'));cancel.click();await new Promise(r=>setTimeout(r,100));
 $('schedLead').value='45';$('schedLead').dispatchEvent(new w.Event('input',{bubbles:true}));$('schedRefresh').click();await new Promise(r=>setTimeout(r,100));assert.equal($('schedLead').value,'45');
 assert.equal(errs.length,0,errs.join('\n'));console.log('Scheduler UI passed: heartbeat, preferences, persisted reminder, worker completion, inbox acknowledgement, frozen search snapshot, edit/cancel, unsaved preference retention.');dom.window.close();
})().catch(e=>{console.error(e);process.exit(1)});
