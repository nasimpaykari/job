"""Offline, review-first import of browser captures. No fetches or AI calls."""
import json,re,ipaddress
from urllib.parse import urlsplit,parse_qsl
from .core import identity_urls, is_linkedin_url

FIELDS={'title':500,'institution':500,'location':1000,'region':200,'country':200,'deadline':500,'description':60000,'url':4096,'apply_url':4096}
SENSITIVE={'access_token','refresh_token','id_token','token','password','passwd','secret','api_key','apikey','authorization','session','sessionid','sid','signature','sig','x-amz-signature','x-amz-credential'}

def web_url(value,optional=False):
    if not value and optional:return ''
    try:
        p=urlsplit(value)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.port not in (None,80,443):raise ValueError()
        host=p.hostname.lower()
        if is_linkedin_url(value):raise ValueError('LinkedIn pages cannot be captured by this extension. Add a selected job manually.')
        if '.' not in host or host.endswith(('.localhost','.local','.internal')) or host=='localhost':raise ValueError()
        try:address=ipaddress.ip_address(host)
        except ValueError:address=None
        if address and not address.is_global:raise ValueError('Private URL')
        if any(k.lower() in SENSITIVE for k,v in parse_qsl(p.query)):raise ValueError('Sensitive URL')
    except (ValueError,TypeError):raise ValueError('Use a public HTTP(S) job URL on a standard port, without login tokens or credentials.') from None
    return value

def normalize(payload,require_complete=False):
    if not isinstance(payload,dict) or payload.get('format')!='job-coach-capture' or payload.get('version')!=1:raise ValueError('Choose a Job Coach capture file (format version 1).')
    if len(json.dumps(payload).encode())>500000:raise ValueError('Capture exceeds 500 KB. Select only the job posting text.')
    data={}
    for field,limit in FIELDS.items():
        value=payload.get(field,'')
        if not isinstance(value,str):raise ValueError(field+' must be text.')
        if len(value)>limit:raise ValueError(field+' is too long (maximum '+str(limit)+' characters).')
        data[field]=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',value).strip()
    data['url']=web_url(data['url']);data['apply_url']=web_url(data['apply_url'],optional=True)
    if require_complete and (not data['title'] or not data['institution']):raise ValueError('Review and enter the position title and employer/university first.')
    warnings=[]
    if not data['title'] or not data['institution']:warnings.append('Missing title or employer: complete these before saving.')
    if not data['description']:warnings.append('No posting text captured. Paste the job description before drafting documents.')
    if payload.get('truncated') is True:warnings.append('Capture was shortened. Check the source for missing requirements or deadlines.')
    if urlsplit(data['url']).fragment:warnings.append('This URL contains a fragment. Use a job-specific permalink if the site lists different jobs after #; duplicate matching ignores fragments.')
    if not data['deadline']:warnings.append('No deadline supplied. Check the posting before applying.')
    return data,warnings

class Capture:
    def __init__(self,store):self.store=store
    def preview(self,payload):
        data,warnings=normalize(payload)
        urls=identity_urls(data)
        with self.store.db() as c:
            rows=c.execute('SELECT DISTINCT job_id FROM aliases WHERE url IN ('+','.join('?' for _ in urls)+')',tuple(urls)).fetchall()
        matches=[self.store.job(r[0]) for r in rows]
        return {'fields':data,'warnings':warnings,'matches':[{'id':j['id'],'title':j['title'],'institution':j['institution'],'status':j['status']} for j in matches]}
    def save(self,payload):
        data,warnings=normalize(payload,require_complete=True)
        data['sources']=['Browser saver'];data['needs_review']=False
        jid,result=self.store.add_job(data,preserve_existing=True)
        if result=='added':
            with self.store.db() as c:self.store.event(c,jid,'browser_capture',{'method':'Reviewed browser capture','source_url':data['url']})
        return {'id':jid,'result':result,'warnings':warnings}
