"""Reusable checked search selections; source selection and posting filters are distinct."""
import copy,re
from urllib.parse import quote_plus
from .core import canonical_url,norm
from .universities import list_universities,state_value,ROLES,TENURES,facets,institution_key
KINDS=['job','board','social','university','state','country','keyword','role','tenure','url','api','track','work_mode','seniority']

def is_social_target(t):
    if t.get('kind')=='social':return True
    val=(t.get('value','')+' '+t.get('label','')).lower()
    return any(s in val for s in ('linkedin','researchgate','twitter.com','x.com','bsky.app','mastodon'))

def validate_targets(targets,sources):
    if not isinstance(targets,list) or len(targets)>5000:raise ValueError('Use up to 5000 saved search targets')
    seen=set()
    for t in targets:
        if not isinstance(t,dict) or set(t)-{'id','kind','label','value','enabled','limit','api'}:raise ValueError('Invalid saved search target')
        if not isinstance(t.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',t['id']) or t['id'] in seen:raise ValueError('Search targets need unique IDs')
        if t.get('limit') is not None and (type(t['limit']) is not int or not 1<=t['limit']<=1000):raise ValueError('Result limit must be between 1 and 1000')
        seen.add(t['id'])
        if t.get('kind') not in KINDS or type(t.get('enabled')) is not bool:raise ValueError('Invalid search kind or checkbox')
        if not all(isinstance(t.get(k),str) and t[k].strip() and len(t[k])<=2000 for k in ('label','value')):raise ValueError('Enter a label and value for each search target')
        if t['kind'] in ('url','social') or t['kind']=='board' and t['value'] not in sources:canonical_url(t['value'])
        if t['kind']=='api':
            from .capture import web_url
            from .discovery import validate_feed
            web_url(t['value']);validate_feed(t.get('api',{}))
        elif 'api' in t:raise ValueError('API mappings belong to API sources only')
        from .workbench import TRACKS,WORK_MODES,LEVELS
        choices={'track':TRACKS,'work_mode':WORK_MODES,'seniority':LEVELS}
        if t['kind'] in choices and t['value'] not in choices[t['kind']]:raise ValueError('Choose a supported '+t['kind'])
        if t['kind']=='role' and t['value'] not in ROLES:raise ValueError('Choose a supported job role')
        if t['kind']=='tenure' and t['value'] not in TENURES:raise ValueError('Choose a supported tenure status')

def build_plan(store,settings,kind):
    s=copy.deepcopy(settings);selected={k:[] for k in KINDS}
    inc_uni=s.get('include_tab_universities',True)
    inc_boards=s.get('include_tab_boards',True)
    inc_social=s.get('include_tab_social',True)
    if s['saved_search_enabled']:
        for t in s['search_targets']:
            if not t['enabled']:continue
            if t['kind']=='university' and not inc_uni:continue
            if t['kind']=='social' or (t['kind'] in ('board','url') and is_social_target(t)):
                if not inc_social:continue
            elif t['kind'] in ('board','url','api') and not inc_boards:
                continue
            selected[t['kind']].append(t)
        if not any(selected.values()):raise ValueError('Check at least one saved search target (and ensure its tab checkbox is enabled)')
        if any(t['kind']=='job' for t in s['search_targets']):s['queries']=[t['value'] for t in selected['job']]
        if any(t['kind']=='state' for t in s['search_targets']):s['regions']=[state_value(t['value']) for t in selected['state']]
        if any(t['kind']=='country' for t in s['search_targets']):s['countries']=[t['value'] for t in selected['country']]
        s['required_keywords']=list(dict.fromkeys(s['required_keywords']+[t['value'] for t in selected['keyword']]))
    explicit_sources=s['saved_search_enabled'] and any(t['kind'] in ('board','social','university','url','api') for t in s['search_targets'])
    targets=[];universities=list_universities(store)
    selected_unis=[]
    if inc_uni:
        for t in selected['university']:
            u=next((u for u in universities if t['value']==u['id'] or norm(t['value'])==norm(u['name'])),None)
            if not u:raise ValueError('Add '+t['value']+' in Tab 1 (Universities) and confirm its career page first')
            if not u['career_verified'] or not u['career_url']:raise ValueError('Confirm the career page for '+u['name']+' before searching')
            selected_unis.append(dict(u,search_limit=t.get('limit') or s['search_result_limit']))
    if kind in ('boards','both'):
        board_targets=selected['board']+selected['social']
        if board_targets:
            boards=[(t['label'],dict(s['sources'].get(t['value'],{'url':t['value']}),limit=t.get('limit') or s['search_result_limit'])) for t in board_targets]
        else:
            boards=[] if explicit_sources else [
                (name,cfg) for name,cfg in s['sources'].items()
                if cfg['enabled'] and ((inc_social if name.lower()=='linkedin' else inc_boards))
            ]
        if selected_unis and not board_targets:boards=[]
        locations=[t['value'] for t in selected['state']] or [', '.join(s['regions']+s['countries']) or ('' if s['saved_search_enabled'] else 'United States')]
        for name,cfg in boards:
            for q in s['queries'] or ([''] if s['saved_search_enabled'] else ['computer science']):
                for location in locations:
                    targets.append((name,cfg['url'].replace('{query}',quote_plus(q)).replace('{location}',quote_plus(location)),cfg['limit']))
    if kind in ('universities','both') and inc_uni:
        if selected_unis:
            for u in selected_unis:
                targets.append((u['name'],u.get('api_url') or u['career_url'],u['search_limit']))
        elif not explicit_sources:
            targets.extend(('University',url,s['search_result_limit']) for url in s['custom_urls'])
            targets.extend((u['name'],u.get('api_url') or u['career_url'],s['search_result_limit']) for u in universities if u['screen'] and u['career_verified'] and u['career_url'])
    targets.extend((t['label'],t['value'],t.get('limit') or s['search_result_limit']) for t in selected['url'])
    if selected['url'] and not selected['board'] and not selected['social'] and not selected_unis and kind in ('boards','both'):
        targets=[(t['label'],t['value'],t.get('limit') or s['search_result_limit']) for t in selected['url']]
    s['_api_sources']={t['value']:t['api'] for t in selected['api']}
    targets.extend((t['label'],t['value'],t.get('limit') or s['search_result_limit']) for t in selected['api'])
    unique={}
    for source,url,limit in targets:unique.setdefault(url,(source,url,limit))
    if len(unique)>500:raise ValueError('This selection expands to more than 500 source/query URLs. Narrow the sources, keywords or states.')
    if not unique:raise ValueError('No sources selected for this search. Check your tab inclusion checkboxes and source checkboxes.')
    return s,list(unique.values()),selected

def selection_reasons(job,selected):
    reasons=[]
    from .services import match_text
    text=match_text(' '.join(str(job.get(k,'')) for k in ('title','description','job_type')))
    if selected['job'] and not any(all(word in text.split() for word in match_text(t['value']).split()) for t in selected['job']):reasons.append('Does not match selected job keywords')
    if selected['university'] and not any(t['value']==institution_key(job.get('institution','')) or norm(t['value'])==norm(job.get('institution','')) for t in selected['university']):reasons.append('Outside selected universities')
    f=facets(job)
    for field,kind in [('role_type','role'),('tenure_status','tenure'),('career_track','track'),('work_mode','work_mode'),('seniority','seniority')]:
        if selected.get(kind) and f[field] not in [t['value'] for t in selected[kind]]:reasons.append('Outside selected '+kind)
    return reasons
