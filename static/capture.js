/* Clipboard/file capture is temporary until the user explicitly saves it. */
let captureDraft=null,captureEpoch=0,captureWorkspace=null;
const captureFields=['title','institution','url','apply_url','location','region','country','deadline','description'];
function clearCapture(){captureEpoch++;captureDraft=null;$('captureRaw').value='';$('captureFile').value='';$('captureEditor').hidden=true;$('captureResult').textContent='';$('captureReviewed').checked=false;for(const f of captureFields)$('capture_'+f).value='';}
function openCapture(){clearCapture();$('captureWorkspace').textContent=state?.workspaces?.find(w=>w.id===state.active_workspace)?.name||'Current workspace';$('captureDialog').showModal();$('captureRaw').focus();}
window.captureRefresh=()=>{const workspace=state?.active_workspace||'local';if(captureWorkspace!==null&&captureWorkspace!==workspace){clearCapture();$('captureDialog').close();}captureWorkspace=workspace;if(location.hash==='#browser-import'){history.replaceState(null,'',location.pathname+location.search);openTab('jobs');openCapture();}};
window.addEventListener('hashchange',()=>{if(state)window.captureRefresh();});
function closeCapture(){clearCapture();$('captureDialog').close();}
$('captureOpen').onclick=openCapture;$('captureClose').onclick=closeCapture;$('captureDialog').addEventListener('cancel',clearCapture);
$('workspaceSelect').addEventListener('change',closeCapture);
async function previewCapture(){const epoch=++captureEpoch;captureDraft=null;$('captureEditor').hidden=true;$('captureReviewed').checked=false;$('captureResult').textContent='';try{
 const text=$('captureRaw').value;if(new TextEncoder().encode(text).length>500000)throw Error('Capture exceeds 500 KB. Select only the posting text.');let payload;try{payload=JSON.parse(text);}catch{throw Error('Paste the capture JSON from the extension, or select its downloaded file.');}
 const result=await api('/api/capture/preview','POST',payload);if(epoch!==captureEpoch)return;captureDraft={format:'job-coach-capture',version:1,truncated:payload.truncated===true,...result.fields};
 for(const f of captureFields)$('capture_'+f).value=result.fields[f]||'';$('captureEditor').hidden=false;
 $('captureResult').innerHTML=result.warnings.map(t=>`<p class="warning">${esc(t)}</p>`).join('')+(result.matches.length?`<p><strong>Already saved. Saving again will keep the existing record unchanged.</strong></p>${result.matches.map(j=>`<p>${esc(j.institution)} · ${esc(j.title)} · ${esc(j.status)} <button type="button" data-capture-existing="${esc(j.id)}">Open existing card</button></p>`).join('')}`:'<p>No existing card uses this source URL. Review the employer, title, state, deadline and description.</p>');
 }catch(e){if(epoch===captureEpoch)$('captureResult').textContent=e.message;}}
$('capturePreview').onclick=previewCapture;
$('captureFile').onchange=async()=>{const file=$('captureFile').files[0];if(!file)return;const epoch=++captureEpoch;captureDraft=null;$('captureEditor').hidden=true;try{if(file.size>500000)throw Error('Capture file exceeds 500 KB.');const text=await file.text();if(epoch!==captureEpoch)return;$('captureRaw').value=text;await previewCapture();}catch(e){if(epoch===captureEpoch)$('captureResult').textContent=e.message;}};
$('captureRaw').addEventListener('input',()=>{captureEpoch++;captureDraft=null;$('captureEditor').hidden=true;$('captureResult').textContent='';});
for(const f of captureFields)$('capture_'+f).addEventListener('input',()=>{$('captureReviewed').checked=false;$('captureResult').textContent='Details edited. Check them before saving; duplicate matching will run again on save.';});
$('captureResult').onclick=async e=>{const b=e.target.closest('[data-capture-existing]');if(b){const id=b.dataset.captureExisting;closeCapture();try{await editJob(id);}catch(error){notice(error.message,true);}}};
$('captureForm').onsubmit=async e=>{e.preventDefault();if(!captureDraft)return;const epoch=captureEpoch,payload={...captureDraft,reviewed:$('captureReviewed').checked,notify_staff:$('captureNotify').checked};for(const f of captureFields)payload[f]=$('capture_'+f).value;$('captureSave').disabled=true;try{
 const result=await api('/api/capture/save','POST',payload);if(epoch!==captureEpoch)return;closeCapture();await refresh();notice(result.result==='added'?'Browser capture saved. The university/employer is in your directory.':result.result==='ignored'?'This job is already ignored. It remains ignored; no saved fields were changed.':'This job is already saved. Its status, description, deadline and other fields were kept unchanged.');
 }catch(error){if(epoch===captureEpoch)$('captureResult').textContent=error.message;}finally{$('captureSave').disabled=false;}};
if(state)window.captureRefresh();
