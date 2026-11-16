/* Passed as a self-contained function to scripting.executeScript; no remote code. */
function extractJobCapture(){
 if(/(^|\.)linkedin\.com$/i.test(location.hostname))throw new Error('LinkedIn is user-led only. Search in LinkedIn Jobs, then add a selected position manually.');
 const MAX=60000,clean=s=>typeof s==='string'?s.trim():'',norm=s=>clean(s).toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
 const plain=html=>{const doc=new DOMParser().parseFromString(String(html||'').replace(/<br\s*\/?>/gi,'\n').replace(/<\/(p|div|li|h[1-6]|section|tr)>/gi,'$&\n'),'text/html');doc.querySelectorAll('script,style,noscript,form').forEach(n=>n.remove());return (doc.body.textContent||'').trim();};
 const selected=window.getSelection()?.toString().trim()||'';
 const h1=document.querySelector('h1')?.innerText?.trim()||document.querySelector('h1')?.textContent?.trim()||'';
 const postings=[];
 function walk(x){if(Array.isArray(x))x.forEach(walk);else if(x&&typeof x==='object'){if(x['@type']==='JobPosting'||Array.isArray(x['@type'])&&x['@type'].includes('JobPosting'))postings.push(x);if(x['@graph'])walk(x['@graph']);}}
 for(const node of document.querySelectorAll('script[type="application/ld+json"]')){if(node.textContent.length>500000)continue;try{walk(JSON.parse(node.textContent));}catch{}}
 const matches=postings.filter(p=>norm(p.title)&&norm(p.title)===norm(h1));
 const one=matches.length===1?matches[0]:postings.length===1?postings[0]:null;
 // Selection always wins, and avoids combining metadata from another job on a listings page.
 const p=selected?null:one;
 let text=selected,method='selection';
 if(!text&&p&&clean(p.description)){text=plain(p.description);method='structured';}
 if(!text){const main=document.querySelector('main,article')||document.body;const clone=main.cloneNode(true);clone.querySelectorAll('script,style,noscript,nav,footer,header,aside,form,input,textarea,select,button,[hidden],[aria-hidden="true"]').forEach(n=>n.remove());clone.querySelectorAll('p,div,li,h1,h2,h3,section,tr,br').forEach(n=>n.appendChild(document.createTextNode('\n')));text=clone.textContent||'';method='page text';}
 const address=(Array.isArray(p?.jobLocation)?p.jobLocation[0]:p?.jobLocation)?.address||{};
 const country=typeof address.addressCountry==='string'?address.addressCountry:clean(address.addressCountry?.name);
 const institution=typeof p?.hiringOrganization==='string'?p.hiringOrganization:clean(p?.hiringOrganization?.name);
 const url=new URL(location.href);url.username='';url.password='';
 const secret=/^(access_token|refresh_token|id_token|token|password|passwd|secret|api_key|apikey|authorization|session|sessionid|sid|signature|sig|x-amz-signature|x-amz-credential)$/i;
 let stripped=false;for(const key of [...url.searchParams.keys()])if(secret.test(key)){url.searchParams.delete(key);stripped=true;}
 return {format:'job-coach-capture',version:1,title:clean(p?.title)||(selected?'':h1||document.title),institution:institution||'',url:url.href,apply_url:'',description:text.slice(0,MAX),deadline:clean(p?.validThrough),location:[clean(address.addressLocality),clean(address.addressRegion),country].filter(Boolean).join(', '),region:clean(address.addressRegion),country,method,truncated:text.length>MAX,removed_sensitive_query:stripped,multiple_postings:postings.length>1&&!p,captured_at:new Date().toISOString()};
}
if(typeof module!=='undefined')module.exports={extractJobCapture};
