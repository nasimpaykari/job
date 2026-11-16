"""Public ATS API detection from supplied sites and linked career pages."""
import json,re,html
from urllib.parse import urlsplit,quote
from bs4 import BeautifulSoup

def api_for_url(url):
    p=urlsplit(url);host=(p.hostname or '').lower();parts=[x for x in p.path.split('/') if x]
    if host=='jobs.ashbyhq.com' and parts:
        return 'https://api.ashbyhq.com/posting-api/job-board/'+quote(parts[0],safe='')+'?includeCompensation=true','ashby',parts[0]
    if host=='api.ashbyhq.com' and len(parts)==3 and parts[:2]==['posting-api','job-board']:return url,'ashby',parts[2]
    if host in ('boards.greenhouse.io','job-boards.greenhouse.io') and parts:
        return 'https://boards-api.greenhouse.io/v1/boards/'+quote(parts[0],safe='')+'/jobs?content=true','greenhouse',parts[0]
    if host=='boards-api.greenhouse.io':
        m=re.search(r'/boards/([^/]+)/jobs',p.path)
        if m:return url,'greenhouse',m[1]
    if host in ('jobs.lever.co','jobs.eu.lever.co') and parts:
        domain='api.eu.lever.co' if host=='jobs.eu.lever.co' else 'api.lever.co'
        return 'https://'+domain+'/v0/postings/'+quote(parts[0],safe='')+'?mode=json','lever',parts[0]
    if host in ('api.lever.co','api.eu.lever.co') and len(parts)>=3:return url,'lever',parts[2]
    return None

def discover_apis(html,base):
    from urllib.parse import urljoin
    soup=BeautifulSoup(html,'html.parser');urls=[]
    for tag in soup.find_all(['a','iframe','link']):
        link=tag.get('href') or tag.get('src')
        if link:urls.append(urljoin(base,link))
    urls+=re.findall(r'https?://(?:job-boards\.greenhouse\.io|boards\.greenhouse\.io|jobs\.lever\.co|jobs\.eu\.lever\.co|jobs\.ashbyhq\.com)/[A-Za-z0-9_-]+',html)
    return list(dict.fromkeys(u for u in urls if api_for_url(u)))[:10]

def parse_api(payload,kind,employer,source):
    data=json.loads(payload);rows=data.get('jobs',[]) if isinstance(data,dict) else data
    if not isinstance(rows,list):raise ValueError('API response does not contain a job list')
    result=[]
    for j in rows[:5000]:
        if not isinstance(j,dict):continue
        extra={}
        if kind=='ashby':
            if j.get('isListed') is False:continue
            title=j.get('title');url=j.get('jobUrl');apply=j.get('applyUrl') or url;description=j.get('descriptionPlain') or j.get('descriptionHtml','');location=j.get('location','')
            address=(j.get('address') or {}).get('postalAddress') or {}
            extra={'work_mode':{'Remote':'Remote','Hybrid':'Hybrid','OnSite':'On-site'}.get(j.get('workplaceType'),'Unknown'),'job_type':j.get('employmentType',''),'department':j.get('department',''),'region':address.get('addressRegion',''),'country':address.get('addressCountry',''),'published_at':j.get('publishedAt','')}
            compensation=j.get('compensation') or {}
            extra['salary']=compensation.get('scrapeableCompensationSalarySummary','')
            extra['salary_period']='Unknown'
        elif kind=='greenhouse':
            title=j.get('title');url=j.get('absolute_url');description=j.get('content','');location=(j.get('location') or {}).get('name','');apply=url
        else:
            title=j.get('text');url=j.get('hostedUrl');apply=j.get('applyUrl') or url;description=j.get('descriptionPlain') or j.get('description','');location=(j.get('categories') or {}).get('location','')
            description+='\n'+'\n'.join(x.get('text','')+' '+BeautifulSoup(x.get('content',''),'html.parser').get_text(' ',strip=True) for x in j.get('lists',[]) if isinstance(x,dict))
            categories=j.get('categories') or {};salary=j.get('salaryRange') or {}
            extra={'work_mode':{'remote':'Remote','hybrid':'Hybrid','on-site':'On-site'}.get(j.get('workplaceType'),'Unknown'),'job_type':categories.get('commitment',''),'department':categories.get('department',''),'country':j.get('country') or ''}
            period={'year':'Annual','yearly':'Annual','annual':'Annual','month':'Monthly','week':'Weekly','hour':'Hourly','hourly':'Hourly'}.get(str(salary.get('interval','')).lower(),'Unknown')
            minimum=salary.get('min')
            if type(minimum) in (int,float) and __import__('math').isfinite(minimum) and minimum>=0:extra.update(salary_min=minimum,currency=salary.get('currency') or '',salary_period=period)
            extra['salary']=j.get('salaryDescriptionPlain') or json.dumps(salary) if salary else j.get('salaryDescriptionPlain','')
            description+='\n'+(j.get('additionalPlain') or BeautifulSoup(j.get('additional') or '','html.parser').get_text(' ',strip=True))

        if 'country' in extra:extra['country']={'US':'United States','USA':'United States','CA':'Canada','GB':'United Kingdom'}.get(extra['country'],extra['country'])
        if not isinstance(title,str) or not isinstance(url,str) or not title or not url:continue
        from .capture import web_url
        try:url=web_url(url);apply=web_url(apply)
        except ValueError:continue
        result.append({'institution':j.get('companyName') or employer,'title':title,'url':url,'apply_url':apply,'description':BeautifulSoup(html.unescape(description),'html.parser').get_text(' ',strip=True),'location':location,'sources':[source+' API'],'needs_review':True,'extraction_method':kind+' public API',**extra})
    return result

# Explicit public JSON feeds: one configured page, no credentials or inferred pagination.
def validate_feed(config):
    if not isinstance(config,dict) or set(config)-{'list_path','title','url','institution','description','location','employer'}:raise ValueError('Invalid JSON API field mapping')
    for key,value in config.items():
        if not isinstance(value,str) or len(value)>200:raise ValueError('API mappings must be short text values')
        if key!='employer' and value and not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*',value):raise ValueError('Use simple dot-separated field paths in API mappings')
    if not config.get('title') or not config.get('url'):raise ValueError('API mappings need title and URL fields')

def parse_feed(payload,config,source,base):
    from urllib.parse import urljoin
    from .capture import web_url
    validate_feed(config)
    def get(obj,path):
        for part in path.split('.') if path else []:
            if not isinstance(obj,dict):return None
            obj=obj.get(part)
        return obj
    rows=get(json.loads(payload),config.get('list_path',''))
    if not isinstance(rows,list):raise ValueError('Configured API list path does not contain an array')
    jobs=[]
    for row in rows[:5000]:
        if not isinstance(row,dict):continue
        title=get(row,config['title']);url=get(row,config['url'])
        if not isinstance(title,str) or not isinstance(url,str):continue
        try:url=web_url(urljoin(base,url))
        except ValueError:continue
        def field(key):
            value=get(row,config.get(key,'')) if config.get(key) else ''
            return value if isinstance(value,str) else ''
        employer=field('institution') or config.get('employer') or 'Unknown employer — verify'
        jobs.append({'title':title[:500],'institution':employer[:500],'url':url,'description':BeautifulSoup(field('description'),'html.parser').get_text(' ',strip=True)[:60000],'location':field('location')[:1000],'sources':[source+' API'],'needs_review':True,'extraction_method':'configured public JSON API'})
    return jobs
