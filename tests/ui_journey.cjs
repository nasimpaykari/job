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
 const job=await post('/api/jobs',{institution:'Journey University',title:'Faculty',contacts:'Legacy recruiter note',url:'https://journey.example.com/1'});
 $('refresh').click();await wait(()=>d.querySelector('[data-journey-open="'+job.id+'"]'));d.querySelector('[data-journey-open="'+job.id+'"]').click();await wait(()=>$('journeyLegacy').textContent.includes('Legacy recruiter'));
 $('journeyContactEditor').open=true;$('journeyName').value='Alex Recruiter';$('journeyEmail').value='alex@example.com';submit('journeyContactForm');await wait(()=>$('journeyContactList').textContent.includes('Alex Recruiter'));
 $('journeyLinkSelect').selectedIndex=1;submit('journeyLinkForm');await wait(()=>d.querySelector('#journeyParticipants input'));
 $('journeyEventEditor').open=true;$('journeyTitle').value='Round 1';$('journeyKind').value='Interview';$('journeyStart').value='2026-11-02T09:00';$('journeyEnd').value='2026-11-02T10:00';$('journeyZone').value='America/New_York';d.querySelector('#journeyParticipants input').checked=true;submit('journeyEventForm');await wait(()=>$('journeyEntries').textContent.includes('Round 1'));
 $('journeyMonth').value='2026-11';$('journeyMonth').dispatchEvent(new w.Event('change'));assert.ok($('journeyAgenda').textContent.includes('Round 1'));
 d.querySelector('#journeyEntries [data-journey-event]').click();await wait(()=>$('journeyEventId').value);$('journeyEventStatus').value='Completed';$('journeyOutcome').value='Invited to next round';submit('journeyEventForm');await wait(()=>$('journeyEntries').textContent.includes('Invited to next round'));
 $('journeyStatus').value='Interviewing';submit('journeyStatusForm');await wait(()=>$('journeyAudit').textContent.includes('New → Interviewing'));
 const ics=await (await fetch('http://127.0.0.1:5053/api/journey/calendar.ics?job='+job.id)).text();assert.ok(ics.includes('DTSTART:20261102T140000Z'));assert.ok(!ics.includes('ATTENDEE'));
 $('journeyNewEvent').click();$('journeyAllDay').checked=true;$('journeyAllDay').dispatchEvent(new w.Event('change'));assert.equal($('journeyZone').required,false);$('journeyTitle').value='Priority review';$('journeyKind').value='Priority date';$('journeyStart').value='2026-11-15';submit('journeyEventForm');await wait(()=>$('journeyEntries').textContent.includes('Priority review'));
 d.querySelector('[data-journey-contact]').click();$('journeyContactArchived').checked=true;submit('journeyContactForm');await wait(()=>$('journeyContactList').textContent.includes('No matching'));$('journeyArchived').checked=true;$('journeyArchived').dispatchEvent(new w.Event('change'));assert.ok($('journeyContactList').textContent.includes('Archived'));
 assert.equal(errs.length,0,errs.join('\n'));console.log('Journey UI passed: contacts, links, timed/all-day events, edit/completion, calendar export, status history, archive.');dom.window.close();
})().catch(e=>{console.error(e);process.exit(1)});
