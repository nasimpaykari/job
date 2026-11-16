"""State-wide institution discovery with explicit provenance and review."""
import json,re
import requests
from .core import uid,now
from .universities import STATE_NAMES,state_value,ensure_university,list_universities,discover_careers
from .capture import web_url

KEY='state_discovery_runs'
def load(store):
    with store.db() as c:r=c.execute('SELECT value FROM kv WHERE key=?',(KEY,)).fetchone()
    return json.loads(r[0]) if r else []
def persist(store,run):
    with store.lock,store.db() as c:
        r=c.execute('SELECT value FROM kv WHERE key=?',(KEY,)).fetchone();rows=json.loads(r[0]) if r else []
        rows=[run]+[r for r in rows if r['id']!=run['id']]
        c.execute('INSERT OR REPLACE INTO kv VALUES(?,?)',(KEY,json.dumps(rows[:5])))
def clean_url(value):
    value=str(value or '').strip()
    if not value:return ''
    if not value.startswith(('https://','http://')):value='https://'+value
    try:return web_url(value)
    except ValueError:return ''
def discover_state(store,data,log=lambda text:None):
    if not isinstance(data,dict):raise ValueError('Discovery options must be an object.')
    state=state_value(data.get('state',''));method=data.get('method','directory');limit=data.get('limit',500)
    if state not in STATE_NAMES:raise ValueError('Choose a U.S. state or Washington, DC.')
    if type(limit) is not int or not 1<=limit<=2000:raise ValueError('Institution limit must be 1–2000.')
    if method not in ('directory','ai'):raise ValueError('Choose directory or AI discovery.')
    if not store.settings()['network_enabled']:raise ValueError('Enable network access in Profile & settings.')
    items=[];total=None;warnings=[];complete=False
    if method=='directory':
        key=store.key('college_scorecard')
        if not key:raise ValueError('Add your College Scorecard / data.gov API key in Discover universities first.')
        seen=set()
        with requests.Session() as session:
            session.trust_env=False
            for page in range(20):
                try:
                    r=session.get('https://api.data.gov/ed/collegescorecard/v1/schools',params={'school.state':state,'school.operating':1,'_fields':'id,school.name,school.state,school.city,school.school_url','_per_page':100,'_page':page},headers={'X-Api-Key':key},timeout=(8,30),allow_redirects=False)
                    if r.status_code!=200:raise ValueError('Directory returned HTTP '+str(r.status_code)+'. Check your key or try later.')
                    obj=r.json();rows=obj.get('results');total=int(obj.get('metadata',{}).get('total',0))
                    if not isinstance(rows,list):raise ValueError('Directory response has no institution list.')
                except Exception as e:
                    message=str(e) if isinstance(e,ValueError) and str(e).startswith('Directory') else 'Directory connection or response failed; retry later.'
                    if not items:raise ValueError(message) from None
                    warnings.append(message+' Partial results retained.');break
                for row in rows:
                    name=row.get('school.name') or (row.get('school') or {}).get('name','');code=row.get('school.state') or (row.get('school') or {}).get('state','')
                    if code!=state or not name or row.get('id') in seen:continue
                    seen.add(row.get('id'));items.append({'id':uid(),'name':str(name)[:400],'state':state,'country':'United States','website':clean_url(row.get('school.school_url') or (row.get('school') or {}).get('school_url')),'source':'College Scorecard','source_id':str(row.get('id','')),'city':str(row.get('school.city',''))[:200]})
                    if len(items)>=limit:break
                log(f'Directory: {len(items)} institutions collected; {total} reported by source.')
                if (page+1)*100>=total or not rows:complete=len(items)>=total;break
                if len(items)>=limit:break
        if not complete:warnings.append('Results are limited or incomplete. Increase the institution limit or retry to expand coverage.')
        warnings.append('Coverage is limited to institutions reported by College Scorecard; it is not a complete registry of every educational provider.')
    else:
        from .services import AI
        cap=min(limit,100)
        raw=AI(store).call('Suggest up to '+str(cap)+' distinct universities and colleges in '+STATE_NAMES[state]+', United States, including public, private and community colleges. Return ONLY a JSON array of objects with name, state (two-letter abbreviation), website. Use an empty website if uncertain. Do not invent institutions or URLs. This is a candidate list for review, not an exhaustive census.','You assist institutional discovery. Clearly avoid unsupported claims. Return the requested JSON only.')
        try:rows=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip()))
        except Exception:raise ValueError('AI did not return a valid list. Try again or use the directory.') from None
        if not isinstance(rows,list):raise ValueError('AI did not return an institution list.')
        seen=set()
        for row in rows[:cap]:
            if not isinstance(row,dict) or not isinstance(row.get('name'),str) or not row['name'].strip() or state_value(row.get('state',''))!=state:continue
            name=row['name'].strip()[:400]
            if name.casefold() in seen:continue
            seen.add(name.casefold());items.append({'id':uid(),'name':name,'state':state,'country':'United States','website':clean_url(row.get('website')),'source':'AI suggestion · unverified','source_id':'','city':''})
        warnings.append('AI suggestions may omit institutions or contain errors. No completeness or website-ownership claim is made. Review each candidate.')
    run={'id':uid(),'created':now(),'state':state,'method':method,'items':items,'source_total':total,'complete_within_source':complete,'warnings':warnings}
    persist(store,run);return {'run_id':run['id'],'count':len(items),'message':'Institution candidates are ready for review.'}
def import_candidates(store,data,log=lambda text:None):
    if not isinstance(data,dict):raise ValueError('Import options must be an object.')
    run=next((r for r in load(store) if r['id']==data.get('run_id')),None)
    if not run:raise ValueError('Discovery results are no longer available. Run discovery again.')
    ids=data.get('ids');find=data.get('find_careers',False)
    if type(find) is not bool:raise ValueError('Career discovery must be true or false.')
    if not isinstance(ids,list) or not ids or len(ids)>500 or any(not isinstance(x,str) for x in ids):raise ValueError('Select 1–500 institutions.')
    ids=set(ids);selected=[x for x in run['items'] if x['id'] in ids]
    if len(selected)!=len(ids):raise ValueError('Unknown discovery candidate.')
    if find and len(selected)>50:raise ValueError('Career-page discovery is limited to 50 institutions per run. Import more without discovery, then use directory batches.')
    if find and not store.settings()['network_enabled']:raise ValueError('Enable network access first.')
    keys=[]
    with store.lock,store.db() as c:
        for item in selected:
            key=ensure_university(c,{'institution':item['name'],'region':item['state'],'country':item['country']},origin='discovery',force=True)
            current=json.loads(c.execute('SELECT data FROM universities WHERE id=?',(key,)).fetchone()[0])
            if not current['website']:current['website']=item['website']
            current['directory_source']=item['source'];current['directory_source_id']=item['source_id']
            c.execute('UPDATE universities SET data=?,updated=? WHERE id=?',(json.dumps(current),now(),key));keys.append(key)
    errors=[]
    if find:
        for key in keys:
            try:discover_careers(store,key,log)
            except Exception:errors.append(key);log('One institution could not be checked; retry from its directory entry.')
    return {'imported':len(keys),'errors':errors,'message':f'{len(keys)} institutions added or matched. Review career pages in the directory before enabling screening.'}
