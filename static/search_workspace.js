/* 4-Tab Search Control Workspace:
   Tab 1: Universities (scDiscover)
   Tab 2: Websites & Job Boards (scSources)
   Tab 3: Social Networks (scSocial)
   Tab 4: Search Setup & Plan (scSetup)
*/
let searchWorkspaceDirty=false,sourceEditId='',discoveryData=null,discoveryChecked=new Set(),discoveryTicket=0;
const boardKinds=['board','url','api'];

function isSocialEntry(t){
  if(t.kind==='social')return true;
  const v=(t.value+' '+t.label).toLowerCase();
  return v.includes('linkedin')||v.includes('researchgate')||v.includes('twitter.com')||v.includes('x.com');
}

// STOP auto-adding unverified job-card universities into searchDraft!
window.addUniversityChoices=function(){
  if(!state)return;
  for(const u of state.universities){
    if(u.screen && u.career_verified && !searchDraft.some(t=>t.kind==='university'&&(t.value===u.id||t.value.toLowerCase()===u.name.toLowerCase()))){
      searchDraft.push({id:newPreviewToken(),kind:'university',value:u.id,label:u.name,enabled:true});
    }
  }
};

function hardDeleteConfirm(label){
  const ans=window.prompt(`PROTECTED DELETE:\nTo permanently delete "${label}" (and block it from returning), type DELETE in all caps:\n\n(Tip: Cancel and simply uncheck its checkbox if you only want to exclude it from search.)`);
  return ans==='DELETE';
}

function searchPane(id){
  document.querySelectorAll('.sc-pane').forEach(el=>el.hidden=el.id!==id);
  document.querySelectorAll('[data-search-pane]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.searchPane===id)));
  if(id==='scDiscover'){
    renderUniversities();
    if(!discoveryData)loadDiscovery().catch(e=>notice(e.message,true));
  }
  if(id==='scSources')renderSearchTargets();
  if(id==='scSocial')renderSocialTargets();
}
document.querySelectorAll('[data-search-pane]').forEach(b=>b.onclick=()=>searchPane(b.dataset.searchPane));

function searchChanged(){searchWorkspaceDirty=true;$('searchDirty').textContent='Unsaved changes';}

function searchSummary(){
  if(!state)return;
  const incUni=$('includeTabUniversities')?.checked??true;
  const incBoards=$('includeTabBoards')?.checked??true;
  const incSocial=$('includeTabSocial')?.checked??true;
  const activeUnis=incUni?state.universities.filter(u=>u.screen&&u.career_verified).length:0;
  const boards=searchDraft.filter(t=>boardKinds.includes(t.kind)&&!isSocialEntry(t));
  const socials=searchDraft.filter(t=>isSocialEntry(t));
  const activeBoards=incBoards?boards.filter(t=>t.enabled).length:0;
  const activeSocials=incSocial?socials.filter(t=>t.enabled).length:0;
  const jobCardUnis=state.universities.filter(u=>u.origin==='job_card'||(!u.career_verified&&u.source_urls?.length)).length;
  $('searchOverview').innerHTML=[
    [`${activeUnis} (${incUni?'Tab ON':'Tab OFF'})`,'Tab 1 · Checked Universities'],
    [`${activeBoards} (${incBoards?'Tab ON':'Tab OFF'})`,'Tab 2 · Checked Job Boards'],
    [`${activeSocials} (${incSocial?'Tab ON':'Tab OFF'})`,'Tab 3 · Checked Social Feeds'],
    [jobCardUnis,'Job-Card Universities (Cleanup Ready)']
  ].map(([value,label])=>`<div class="sc-metric"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`).join('');
}

for(const id of ['includeTabUniversities','includeTabBoards','includeTabSocial']){
  if($(id))$(id).addEventListener('change',()=>{searchChanged();searchSummary();});
}

// TAB 2: WEBSITES & JOB BOARDS (Strictly excludes university rows and social rows)
function visibleBoardSources(){
  const q=($('targetFilter')?.value||'').trim().toLowerCase();
  const type=$('sourceTypeFilter')?.value||'';
  const status=$('sourceStatusFilter')?.value||'';
  return searchDraft.filter(t=>boardKinds.includes(t.kind)&&!isSocialEntry(t)).filter(t=>{
    return (!type||t.kind===type)&&(!q||(t.label+' '+t.value).toLowerCase().includes(q))&&(!status||(status==='selected'&&t.enabled)||(status==='unselected'&&!t.enabled));
  });
}

window.renderSearchTargets=function(){
  if(!state)return;
  const rows=visibleBoardSources(),labels={board:'Job board',url:'Website',api:'JSON API'};
  if($('sourceCount'))$('sourceCount').textContent=rows.length+' websites/boards · '+rows.filter(t=>t.enabled).length+' checked';
  $('searchTargetList').innerHTML=rows.length?`<div class="sc-table-wrap"><table><thead><tr><th>Include</th><th>Website / Job Board</th><th>Type</th><th>Result Limit</th><th>Actions (Protected Delete)</th></tr></thead><tbody>${rows.map(t=>`<tr>
    <td><input type="checkbox" data-search-target="${esc(t.id)}" ${t.enabled?'checked':''} aria-label="Select ${esc(t.label)}"></td>
    <td><strong>${esc(t.label)}</strong><small>${esc(state.settings.sources[t.value]?.url||t.value)}</small></td>
    <td><span class="sc-tag">${labels[t.kind]||'Source'}</span></td>
    <td><input type="number" min="1" max="1000" data-target-limit="${esc(t.id)}" value="${t.limit??''}" placeholder="Default"></td>
    <td><button data-edit-source="${esc(t.id)}" type="button">Edit</button> <button data-remove-target="${esc(t.id)}" type="button" class="warning">Delete ×</button></td>
  </tr>`).join('')}</tbody></table></div>`:'<div class="sc-empty">No websites or job boards match this filter.</div>';
  renderSocialTargets();
  searchSummary();
};

// TAB 3: SOCIAL NETWORKS
function renderSocialTargets(){
  if(!state||!$('socialTargetList'))return;
  const rows=searchDraft.filter(t=>isSocialEntry(t));
  $('socialTargetList').innerHTML=rows.length?`<div class="sc-table-wrap"><table><thead><tr><th>Include</th><th>Social Network / Feed</th><th>Feed URL</th><th>Limit</th><th>Actions</th></tr></thead><tbody>${rows.map(t=>`<tr>
    <td><input type="checkbox" data-social-target="${esc(t.id)}" ${t.enabled?'checked':''}></td>
    <td><strong>${esc(t.label)}</strong></td>
    <td><small>${esc(state.settings.sources[t.value]?.url||t.value)}</small></td>
    <td><input type="number" min="1" max="1000" data-social-limit="${esc(t.id)}" value="${t.limit??''}" placeholder="Default"></td>
    <td><button type="button" data-remove-social="${esc(t.id)}" class="warning">Delete ×</button></td>
  </tr>`).join('')}</tbody></table></div>`:'<div class="sc-empty">No social feeds configured yet. Add LinkedIn or another social feed above.</div>';
}

if($('socialTargetList')){
  $('socialTargetList').onchange=e=>{
    const t=searchDraft.find(x=>x.id===(e.target.dataset.socialTarget||e.target.dataset.socialLimit));
    if(!t)return;
    if(e.target.dataset.socialTarget)t.enabled=e.target.checked;
    if(e.target.dataset.socialLimit){if(e.target.value==='')delete t.limit;else t.limit=Number(e.target.value);}
    searchChanged();searchSummary();
  };
  $('socialTargetList').onclick=e=>{
    const b=e.target.closest('button[data-remove-social]');
    if(!b)return;
    const t=searchDraft.find(x=>x.id===b.dataset.removeSocial);
    if(t&&hardDeleteConfirm(t.label)){
      searchDraft=searchDraft.filter(x=>x.id!==t.id);
      searchChanged();renderSocialTargets();searchSummary();
    }
  };
}

if($('socialAddForm')){
  $('socialAddForm').onsubmit=e=>{
    e.preventDefault();
    const label=$('socialLabel').value.trim(),url=$('socialUrl').value.trim();
    if(!label||!url)return notice('Enter both a label and URL',true);
    searchDraft.push({id:newPreviewToken(),kind:'social',label,value:url,enabled:true});
    $('socialAddForm').reset();
    searchChanged();renderSocialTargets();searchSummary();
    notice('Social network feed added. Click Save workspace to persist.');
  };
}

for(const id of ['sourceTypeFilter','sourceStatusFilter'])if($(id))$(id).onchange=renderSearchTargets;
if($('targetFilter'))$('targetFilter').oninput=renderSearchTargets;

$('searchTargetList').onchange=e=>{
  const t=searchDraft.find(x=>x.id===e.target.dataset.searchTarget);
  if(t)t.enabled=e.target.checked;
  const lim=searchDraft.find(x=>x.id===e.target.dataset.targetLimit);
  if(lim){if(e.target.value==='')delete lim.limit;else lim.limit=Number(e.target.value);}
  searchChanged();searchSummary();
};

$('searchTargetList').onclick=e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.removeTarget){
    const row=searchDraft.find(t=>t.id===b.dataset.removeTarget);
    if(!row||!hardDeleteConfirm(row.label))return;
    searchDraft=searchDraft.filter(t=>t.id!==row.id);
    searchChanged();renderSearchTargets();
  }
  if(b.dataset.editSource){
    const t=searchDraft.find(t=>t.id===b.dataset.editSource);
    if(!t)return;
    sourceEditId=t.id;$('sourceBuilder').open=true;$('targetKind').value=t.kind;$('targetValue').value=t.value;$('targetLabel').value=t.label;
    apiMappingVisibility();$('sourceBuilder').scrollIntoView({behavior:'smooth'});
  }
};

$('sourceSelectVisible').onclick=()=>{for(const t of visibleBoardSources())t.enabled=true;searchChanged();renderSearchTargets();};
$('sourceClearVisible').onclick=()=>{for(const t of visibleBoardSources())t.enabled=false;searchChanged();renderSearchTargets();};

function apiMappingVisibility(){$('sourceApiMapping').hidden=$('targetKind').value!=='api';if(!$('sourceApiMapping').hidden)$('sourceApiMapping').open=true;}
$('targetKind').addEventListener('change',apiMappingVisibility);$('targetKind').value='board';

submitForm('searchTargetAdd',async()=>{
  const kind=$('targetKind').value,raw=$('targetValue').value.trim(),label=$('targetLabel').value.trim()||raw;
  if(!raw)throw Error('Enter a source URL or selection.');
  const previous=searchDraft.find(t=>t.id===sourceEditId),entry={id:sourceEditId||newPreviewToken(),kind,value:raw,label,enabled:previous?.enabled??true};
  if(kind==='api'){
    entry.api={};
    for(const [id,key] of Object.entries({apiListPath:'list_path',apiTitle:'title',apiUrl:'url',apiInstitution:'institution',apiDescription:'description',apiLocation:'location',apiEmployer:'employer'}))entry.api[key]=$(id).value.trim();
  }
  searchDraft=searchDraft.filter(t=>t.id!==entry.id);searchDraft.push(entry);
  sourceEditId='';$('targetValue').value='';$('targetLabel').value='';if($('confirmTargetAdd'))$('confirmTargetAdd').checked=false;
  searchChanged();renderSearchTargets();notice('Source added. Click Save workspace to keep changes.');
});

// TAB 1: UNIVERSITIES RENDERING & BULK CLEANUP
window.renderUniversities=function(){
  if(universityDirty||$('search').hidden)return;
  const q=($('universitySearch')?.value||'').toLowerCase();
  const originFilter=$('universityOriginFilter')?.value||'';
  const rows=state.universities.filter(u=>{
    const matchesQ=(u.name+' '+(u.state||'')).toLowerCase().includes(q);
    const isJobCard=u.origin==='job_card'||(!u.career_verified&&u.source_urls?.length);
    if(originFilter==='job_card'&&!isJobCard)return false;
    if(originFilter==='manual'&&isJobCard)return false;
    if(originFilter==='screen'&&!u.screen)return false;
    return matchesQ;
  });
  $('universityList').innerHTML=`<p><strong>${rows.length}</strong> matching universities (of ${state.universities.length} total). Showing up to 100. Check "Include in Search" to include a confirmed university in Tab 1 searches, or click Delete × to permanently remove and block it.</p>`+
  rows.slice(0,100).map(u=>{
    const isJobCard=u.origin==='job_card'||(!u.career_verified&&u.source_urls?.length);
    return `<details class="panel" data-university="${u.id}">
      <summary style="display:flex;justify-content:space-between;align-items:center;gap:10px">
        <span>
          <input type="checkbox" data-quick-uni-screen="${u.id}" ${u.screen?'checked':''} title="Include this university in search">
          <strong>${esc(u.name)}</strong> · ${esc(u.state||'State unknown')}
          <span class="sc-tag ${u.career_verified?'ready':'review'}">${u.career_verified?'Career URL Confirmed':'Unverified'}</span>
          ${isJobCard?'<span class="sc-tag review">From Job Card</span>':'<span class="sc-tag">Saved</span>'}
          ${u.api_url?'<span class="sc-tag ready">API Configured</span>':''}
        </span>
        <button type="button" data-delete-uni="${u.id}" data-uni-name="${esc(u.name)}" class="warning" style="padding:4px 10px">Delete ×</button>
      </summary>
      <form data-university-form="${u.id}">
        <div class="grid">
          <label>State / region<input data-ufield="state" value="${esc(u.state)}"></label>
          <label>Country<input data-ufield="country" value="${esc(u.country)}"></label>
          <label>Official website<input data-ufield="website" type="url" value="${esc(u.website)}"></label>
          <label>Career page URL<input data-ufield="career_url" type="url" value="${esc(u.career_url)}"></label>
          <label>University Job API URL (optional)<input data-ufield="api_url" type="url" value="${esc(u.api_url||'')}"></label>
          <label>API Key / Token Hint (optional)<input data-ufield="api_key_name" value="${esc(u.api_key_name||'')}"></label>
          <label>Employer type${universitySelect('organization_type',state.facets_options.organizations,u.organization_type||'Unknown')}</label>
          <label>Research classification${universitySelect('research_class',state.facets_options.research,u.research_class)}</label>
        </div>
        <label class="inline"><input data-ufield="career_verified" type="checkbox" ${u.career_verified?'checked':''}> I confirmed this career page URL belongs to this university</label>
        <label class="inline"><input data-ufield="screen" type="checkbox" ${u.screen?'checked':''}> Include this university in active search</label>
        <div class="toolbar">
          <button class="primary">Save university</button>
          <button type="button" data-discover="${u.id}">AI Find Career Page</button>
          <button type="button" data-delete-uni="${u.id}" data-uni-name="${esc(u.name)}" class="warning">Delete University (Protected)</button>
        </div>
      </form>
    </details>`;
  }).join('');
};

if($('universityOriginFilter'))$('universityOriginFilter').onchange=()=>renderUniversities();

// Quick checkbox toggle & protected delete inside University list
$('universityList').addEventListener('click',async e=>{
  const delBtn=e.target.closest('button[data-delete-uni]');
  if(delBtn){
    e.preventDefault();e.stopPropagation();
    const id=delBtn.dataset.deleteUni,name=delBtn.dataset.uniName||'this university';
    if(!hardDeleteConfirm(name))return;
    try{
      await api('/api/universities/'+id,'DELETE');
      dirtyUniversities.delete(id);universityDirty=false;
      await refresh();notice(`Deleted "${name}" and blocked it from auto-importing again.`);
    }catch(err){notice(err.message,true);}
    return;
  }
  const screenBox=e.target.closest('input[data-quick-uni-screen]');
  if(screenBox){
    e.stopPropagation();
    const id=screenBox.dataset.quickUniScreen,checked=screenBox.checked;
    try{
      await api('/api/universities/'+id,'PATCH',{screen:checked});
      await refresh();notice(checked?'University included in search.':'University unchecked from search.');
    }catch(err){
      screenBox.checked=!checked;
      notice(err.message+' (Open the university row, enter/confirm its career URL, then check Include.)',true);
    }
  }
});

// Bulk cleanup buttons for Job-Card Universities
action('bulkUncheckJobCardUnis',async()=>{
  const r=await api('/api/universities/bulk','POST',{action:'uncheck',origin:'job_card'});
  await refresh();notice(r.message);
});
action('bulkDeleteJobCardUnis',async()=>{
  if(!hardDeleteConfirm('ALL auto-imported job-card universities'))return;
  const r=await api('/api/universities/bulk','POST',{action:'delete',origin:'job_card'});
  await refresh();notice(r.message);
});

function visibleUniIds(){
  const q=($('universitySearch')?.value||'').toLowerCase(),originFilter=$('universityOriginFilter')?.value||'';
  return state.universities.filter(u=>{
    const isJobCard=u.origin==='job_card'||(!u.career_verified&&u.source_urls?.length);
    if(originFilter==='job_card'&&!isJobCard)return false;
    if(originFilter==='manual'&&isJobCard)return false;
    if(originFilter==='screen'&&!u.screen)return false;
    return (u.name+' '+(u.state||'')).toLowerCase().includes(q);
  }).map(u=>u.id);
}
if($('uniCheckVisible'))action('uniCheckVisible',async()=>{const r=await api('/api/universities/bulk','POST',{action:'check',ids:visibleUniIds()});await refresh();notice(r.message);});
if($('uniUncheckVisible'))action('uniUncheckVisible',async()=>{const r=await api('/api/universities/bulk','POST',{action:'uncheck',ids:visibleUniIds()});await refresh();notice(r.message);});
if($('uniDeleteVisible'))action('uniDeleteVisible',async()=>{
  const ids=visibleUniIds();if(!ids.length)return notice('No visible universities to delete.');
  if(!hardDeleteConfirm(`${ids.length} visible universities`))return;
  const r=await api('/api/universities/bulk','POST',{action:'delete',ids});await refresh();notice(r.message);
});

// Override universityAdd submit to include api_url and api_key_name
$('universityAdd').onsubmit=async e=>{
  e.preventDefault();
  try{
    await api('/api/universities','POST',{
      name:$('universityName').value,state:$('universityState').value,country:$('universityCountry').value,
      organization_type:$('universityOrganization').value,website:$('universityWebsite').value,
      career_url:$('universityCareerUrl').value,api_url:$('universityApiUrl')?.value||'',
      api_key_name:$('universityApiKey')?.value||'',career_verified:$('universityCareerConfirmed').checked,
      screen:$('universityCareerConfirmed').checked && !!$('universityCareerUrl').value
    });
    $('universityAdd').reset();await refresh();notice('University verified and added.');
  }catch(err){notice(err.message,true);}
};

// AI Helpers for Tab 2 (Boards) and Tab 3 (Social)
async function runAiSourceHelper(tab,inputId,mountId){
  const query=$(inputId).value.trim();
  const r=await runTask('/api/search/ai-sources',{tab,query});
  const items=r.items||[];
  $(mountId).innerHTML=items.length?`<div class="sc-table-wrap"><table><thead><tr><th>Suggestion</th><th>URL</th><th>Action</th></tr></thead><tbody>${items.map((x,idx)=>`<tr data-ai-row="${idx}">
    <td><strong>${esc(x.label||x.url)}</strong><small>${esc(x.notes||'')}</small></td>
    <td><a href="${esc(safeLink(x.url))}" target="_blank" rel="noopener noreferrer">${esc(x.url)}</a></td>
    <td><button type="button" data-add-ai-url="${esc(x.url)}" data-add-ai-label="${esc(x.label||x.url)}" data-add-ai-kind="${esc(tab==='social'?'social':'url')}">+ Add to list</button> <button type="button" data-dismiss-ai="${idx}">×</button></td>
  </tr>`).join('')}</tbody></table></div>`:'<p>No suggestions returned.</p>';
  $(mountId).onclick=e=>{
    const addB=e.target.closest('button[data-add-ai-url]');
    if(addB){
      searchDraft.push({id:newPreviewToken(),kind:addB.dataset.addAiKind,label:addB.dataset.addAiLabel,value:addB.dataset.addAiUrl,enabled:true});
      addB.closest('tr').remove();searchChanged();renderSearchTargets();notice('Added AI suggestion to list. Save workspace to persist.');
    }
    const disB=e.target.closest('button[data-dismiss-ai]');
    if(disB)disB.closest('tr').remove();
  };
}
if($('aiBoardSearchBtn'))action('aiBoardSearchBtn',()=>runAiSourceHelper('boards','aiBoardQuery','aiBoardResults'));
if($('aiSocialSearchBtn'))action('aiSocialSearchBtn',()=>runAiSourceHelper('social','aiSocialQuery','aiSocialResults'));

async function saveSearchWorkspace(){
  searchDraft=searchDraft.filter(t=>t.kind!=='university');
  for(const u of state.universities){
    if(u.screen&&u.career_verified){
      searchDraft.push({id:newPreviewToken(),kind:'university',value:u.id,label:u.name,enabled:true});
    }
  }
  const patch={
    saved_search_enabled:true,
    include_tab_universities:$('includeTabUniversities')?.checked??true,
    include_tab_boards:$('includeTabBoards')?.checked??true,
    include_tab_social:$('includeTabSocial')?.checked??true,
    max_pages:Number($('selectedPageLimit').value||8),
    search_result_limit:Number($('selectedResultLimit').value||8)
  };
  for(const k of ['queries','excluded_keywords','required_keywords','preferred_keywords','countries','regions','preferred_job_types','excluded_employers','custom_urls'])patch[k]=lines(k);
  for(const k of ['exclude_unknown_salary','exclude_unknown_location','browser_render','show_browser'])patch[k]=$(k).checked;
  patch.discovery_mode=$('discovery_mode').value;patch.minimum_salary=Number($('minimum_salary').value);patch.currency=$('currency').value.trim().toUpperCase();patch.sources={};
  document.querySelectorAll('[data-source]').forEach(row=>patch.sources[row.dataset.source]={enabled:row.querySelector('.sourceEnabled').checked,limit:Number(row.querySelector('.sourceLimit').value),url:row.querySelector('.sourceUrl').value});
  for(const [kind,key] of [['job','queries'],['state','regions'],['country','countries']]){
    const prior=searchDraft.filter(t=>t.kind===kind);searchDraft=searchDraft.filter(t=>t.kind!==kind);
    for(const value of patch[key]){const old=prior.find(t=>t.value.toLowerCase()===value.toLowerCase());searchDraft.push({id:old?.id||newPreviewToken(),kind,label:value,value,enabled:true});}
  }
  patch.search_targets=searchDraft;await api('/api/settings','POST',patch);searchWorkspaceDirty=false;await refresh(true);$('searchDirty').textContent='All changes saved';
}
action('saveSearchWorkspace',async()=>{await saveSearchWorkspace();notice('Search workspace saved.');});
action('saveSearchTargets',async()=>{await saveSearchWorkspace();notice('Search selections saved.');});

async function runWorkspace(kind){
  await saveSearchWorkspace();
  const result=await runTask('/api/search',{kind});
  notice(`Search finished: ${result.added} new · ${result.duplicate} known · ${result.ignored} ignored · ${result.filtered} filtered · ${result.errors.length} errors.`);
  searchPane('scSetup');
}
action('searchRunPrimary',()=>runWorkspace('both'));
document.querySelectorAll('[data-run]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await runWorkspace(b.dataset.run);}catch(e){notice(e.message,true);}finally{b.disabled=false;}});

window.renderSearchPlan=r=>{
  searchPane('scSetup');
  $('searchPlanCards').innerHTML=`<p><strong>${r.targets.length} active source/query URLs</strong> · up to ${r.page_limit} pages each · default ${r.default_result_limit} results each</p>`+
    r.targets.map(t=>`<div class="sc-plan-row"><div><strong>${esc(t.source)}</strong><small>${esc(t.url)}</small></div><span class="sc-tag">${t.limit} results</span></div>`).join('');
};

if($('manageUniversities'))$('manageUniversities').onclick=()=>searchPane('scDiscover');
$('discoverManual').onclick=()=>{$('universityDirectory').open=true;$('universityName').focus();};

let searchRefreshWorkspace=null;
window.searchWorkspaceRefresh=()=>{
  if(state?.settings){
    if($('includeTabUniversities'))$('includeTabUniversities').checked=state.settings.include_tab_universities??true;
    if($('includeTabBoards'))$('includeTabBoards').checked=state.settings.include_tab_boards??true;
    if($('includeTabSocial'))$('includeTabSocial').checked=state.settings.include_tab_social??true;
  }
  searchSummary();
  const space=state.active_workspace||'local';
  if(searchRefreshWorkspace!==space){discoveryTicket++;discoveryData=null;discoveryChecked.clear();$('discoveryResults').textContent='';$('scorecardKey').value='';searchRefreshWorkspace=space;}
  if(!searchWorkspaceDirty)$('searchDirty').textContent=state.settings.saved_search_enabled?'Saved configuration':'Ready · save to lock selections';
};

async function loadDiscovery(){
  const ticket=++discoveryTicket;const d=await api('/api/search/discovery');if(ticket!==discoveryTicket)return;
  const previous=$('discoveryRun').value,previousState=$('discoverState').value;discoveryData=d;
  $('discoverState').innerHTML=Object.entries(d.states).map(([code,name])=>`<option value="${code}">${esc(name)} (${code})</option>`).join('');
  $('discoverState').value=previousState||'NJ';
  $('scorecardKeyStatus').textContent=d.key_present?'API key saved.':'No directory API key saved. AI state search works without a Scorecard key.';
  $('discoveryRun').innerHTML=d.runs.map(r=>`<option value="${r.id}">${esc(d.states[r.state])} · ${r.items.length} candidates · ${esc(r.method)} · ${esc(r.created.slice(0,10))}</option>`).join('');
  if(d.runs.some(r=>r.id===previous))$('discoveryRun').value=previous;
  renderDiscovery();
}
function currentDiscovery(){return discoveryData?.runs.find(r=>r.id===$('discoveryRun').value);}
function discoveredRows(){const q=$('discoveryFilter').value.toLowerCase();return (currentDiscovery()?.items||[]).filter(i=>(i.name+' '+i.city).toLowerCase().includes(q));}

function renderDiscovery(){
  const run=currentDiscovery(),rows=discoveredRows();
  $('discoverySummary').innerHTML=run?`<p>${run.items.length} candidates in ${esc(run.state)}.</p>`+run.warnings.map(w=>`<p class="warning">${esc(w)}</p>`).join(''):'<div class="sc-empty">Choose a state above and click "Search state for universities".</div>';
  $('discoveryResults').innerHTML=rows.length?`<div class="sc-table-wrap"><table><thead><tr><th>Include</th><th>University</th><th>Website / Source</th><th>Remove</th></tr></thead><tbody>${rows.map(i=>`<tr>
    <td><input type="checkbox" data-discovered="${i.id}" ${discoveryChecked.has(i.id)?'checked':''}></td>
    <td><strong>${esc(i.name)}</strong><small>${esc(i.city||'')} · ${esc(i.state)}</small></td>
    <td>${safeLink(i.website)?`<a href="${esc(safeLink(i.website))}" target="_blank" rel="noopener noreferrer">${esc(i.website)}</a>`:'Website pending'}<small>${esc(i.source)}</small></td>
    <td><button type="button" data-remove-discovered="${i.id}">×</button></td>
  </tr>`).join('')}</tbody></table></div>`:'';
  $('discoverySelection').textContent=discoveryChecked.size+' checked';
}
$('discoveryRun').onchange=()=>{discoveryChecked.clear();renderDiscovery();};
$('discoveryFilter').oninput=renderDiscovery;
$('discoveryResults').onchange=e=>{const id=e.target.dataset.discovered;if(id){if(e.target.checked)discoveryChecked.add(id);else discoveryChecked.delete(id);$('discoverySelection').textContent=discoveryChecked.size+' checked';}};
$('discoveryResults').onclick=e=>{
  const b=e.target.closest('button[data-remove-discovered]');
  if(!b)return;
  const id=b.dataset.removeDiscovered,run=currentDiscovery();
  if(run){run.items=run.items.filter(x=>x.id!==id);discoveryChecked.delete(id);renderDiscovery();}
};
$('discoverySelect').onclick=()=>{discoveredRows().forEach(i=>discoveryChecked.add(i.id));renderDiscovery();};
$('discoveryClear').onclick=()=>{discoveryChecked.clear();renderDiscovery();};
action('discoveryRefresh',loadDiscovery);
action('saveScorecardKey',async()=>{await api('/api/search/discovery/key','POST',{key:$('scorecardKey').value.trim()});$('scorecardKey').value='';await loadDiscovery();notice('Directory API key saved.');});

$('stateDiscoverForm').onsubmit=async e=>{
  e.preventDefault();$('discoverStart').disabled=true;
  try{
    const r=await runTask('/api/search/discovery',{state:$('discoverState').value,method:$('discoverMethod').value,limit:Number($('discoverLimit').value)});
    await loadDiscovery();$('discoveryRun').value=r.run_id;
    currentDiscovery()?.items.forEach(i=>discoveryChecked.add(i.id));
    renderDiscovery();notice(r.message);
  }catch(e){notice(e.message,true);}finally{$('discoverStart').disabled=false;}
};

async function importDiscovery(find){
  const run=currentDiscovery();if(!run)throw Error('Search a state first.');
  const ids=run.items.filter(i=>discoveryChecked.has(i.id)).map(i=>i.id);
  if(!ids.length)throw Error('Check at least one university to add.');
  const r=await runTask('/api/search/discovery/import',{run_id:run.id,ids,find_careers:find});
  await refresh();$('universityDirectory').open=true;renderUniversities();notice(r.message);
}
action('importInstitutions',()=>importDiscovery(false));
action('importAndDiscover',()=>importDiscovery(true));

const styleEl=document.createElement('style');
styleEl.textContent=`.sc-tab-item{display:flex;align-items:center;gap:4px;padding-left:10px;border-bottom:3px solid transparent}.sc-tab-item input[type=checkbox]{margin:0;width:16px;height:16px;cursor:pointer}`;
document.head.appendChild(styleEl);

if(state){renderSearchTargets();window.searchWorkspaceRefresh();}

action('screenLinkRun',async()=>{if(!confirm('Send the public page text to your configured AI provider for review?'))return;const r=await runTask('/api/search/screen-link',{url:$('screenLinkUrl').value.trim()});$('screenLinkResult').textContent=r.review+'\n\nSource: '+r.url;});
