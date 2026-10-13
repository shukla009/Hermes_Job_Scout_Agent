"""Public employer discovery: bounded requests, honest dates and durable polling state."""
from datetime import datetime, timezone, timedelta
from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import urlparse, urljoin, quote
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

ROLE = re.compile(r"analyst|research|intelligence|insights|reporting|\bmis\b|business intelligence|data (?:quality|operations)|power bi", re.I)
SENIOR = re.compile(r"\b(?:senior|sr|manager|director|principal|head|internship)\b", re.I)

def safe_url(url):
    try:
        p = urlparse(str(url))
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
            return False
        host = p.hostname.lower()
        if host == 'localhost' or '.' not in host or host.endswith(('.local', '.internal')) or 'naukri' in host:
            return False
        try:
            if not ipaddress.ip_address(host).is_global:
                return False
        except ValueError:
            pass
        return True
    except ValueError:
        return False

def text(value):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', str(value or '')))).strip()

def suitable(title):
    return bool(ROLE.search(title or '')) and not SENIOR.search(title or '')

def post(e, title, location, description, url, identity, posted='', updated='', employment=''):
    if not safe_url(url) or not identity or not title:
        return None
    if location and not re.search(r'delhi|ncr|gurugram|gurgaon|noida|remote|india',location,re.I):
        return None
    content = text(description)[:12000]
    return dict(title=title, job_title=title, company=e['name'], location=location,
                body=content[:1500], source_text=content, source_emails=re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', content),
                href=url, application_url=url, employment_type=employment, source_type='direct_apply',
                page_fetched=True, active_listing=True, source_id=str(identity), posted_at=posted,
                updated_at=updated, date_posted=posted)

class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.scripts=[]; self.links=[]; self._json=False; self._buf=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag == 'script':
            self._json=attrs.get('type','').lower()=='application/ld+json'; self._buf=[]
        if tag == 'a' and attrs.get('href'): self.links.append(attrs['href'])
    def handle_data(self, data):
        if self._json: self._buf.append(data)
    def handle_endtag(self, tag):
        if tag=='script' and self._json:
            self.scripts.append(''.join(self._buf)); self._json=False

def _locations(value):
    if isinstance(value, list): return '; '.join(filter(None, (_locations(v) for v in value)))
    if isinstance(value, str): return value
    if isinstance(value, dict):
        a=value.get('address',value)
        if isinstance(a,str): return a
        return ', '.join(str(a.get(k,'')) for k in ('addressLocality','addressRegion','addressCountry','city','region','country') if a.get(k))
    return ''

def _jsonld(content, base, e, now):
    content=content[:1000000]
    page=Page(); page.feed(content); jobs=[]
    def walk(node):
        if isinstance(node,list):
            for item in node: walk(item)
        elif isinstance(node,dict):
            types=node.get('@type',[])
            if 'JobPosting' in ([types] if isinstance(types,str) else types):
                expiry=node.get('validThrough','')
                try:
                    dt=datetime.fromisoformat(expiry.replace('Z','+00:00')) if expiry else None
                    if dt and (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)) < now: return
                except ValueError: pass
                title=node.get('title','')
                if not suitable(title): return
                url=urljoin(base,node.get('url') or base)
                # A board URL with an embedded vacancy lacking a distinct URL is not actionable.
                if url.rstrip('/')==base.rstrip('/') and not re.search(r'/job(?:s)?/[^/]+|/requisition/',url,re.I): return
                identifier=node.get('identifier',{})
                ident=identifier.get('value',url) if isinstance(identifier,dict) else identifier or url
                location=_locations(node.get('jobLocation'))
                if node.get('jobLocationType')=='TELECOMMUTE': location='Remote; '+_locations(node.get('applicantLocationRequirements'))+'; '+location
                item=post(e,title,location,node.get('description',''),url,ident,node.get('datePosted',''),node.get('dateModified',''),str(node.get('employmentType','')))
                if item:
                    item['valid_through']=expiry
                    item['salary_metadata']=node.get('baseSalary') or {}
                    jobs.append(item)
            for k,v in node.items():
                if k!='@type' and isinstance(v,(list,dict)): walk(v)
    for script in page.scripts:
        try: walk(json.loads(script))
        except (ValueError,TypeError): pass
    # ProcDNA publishes vacancy data as server-rendered Next.js props, not JSON-LD.
    if urlparse(base).hostname=='www.procdna.com' and '/careers/jobs/job_' in base:
        match=re.search(r'<script[^>]+id=[\"\']__NEXT_DATA__[\"\'][^>]*>(.*?)</script>',content,re.S)
        try:
            node=json.loads(match.group(1))['props']['pageProps']['job'] if match else {}
            if suitable(node.get('title')) and node.get('applyUrl') and node.get('description'):
                item=post(e,node['title'],node.get('location',''),node['description'],node['applyUrl'],node.get('id'),node.get('datePosted',''),node.get('updatedAt',''),node.get('employmentType',''))
                if item: jobs.append(item)
        except (ValueError,KeyError,TypeError): pass
    # Conservative server-rendered vacancy fallback: distinct job URL, heading,
    # labelled location and an explicit application action are all required.
    if not jobs and re.search(r'/jobs?/[^/?#]+|/requisition/[^/?#]+',urlparse(base).path,re.I):
        heading=re.search(r'<h1\b[^>]*>(.*?)</h1>',content,re.S|re.I)
        visible=text(re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>',' ',content,flags=re.S|re.I))
        loc=re.search(r'(?:job\s+location|location)\s*:\s*([^:]{3,90})',visible,re.I)
        applications=re.findall(r'<a\b[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>',content,re.S|re.I)
        allowed=set(e.get('detail_hosts',[]))|{urlparse(base).hostname}
        apply=next((urljoin(base,href) for href,label in applications if re.search(r'\bapply\b',text(label),re.I) and safe_url(urljoin(base,href)) and urlparse(urljoin(base,href)).hostname in allowed),'')
        title=text(heading.group(1)) if heading else ''
        if suitable(title) and loc and apply and len(visible)>200:
            item=post(e,title,loc.group(1).strip(),visible,apply,base)
            if item:
                item['source_emails']=[] # Footer contacts are not recruiter contacts.
                jobs.append(item)
    return jobs,page.links

def _fetch(e, get, now, cursor=0):
    jobs=[]; raw=0; incomplete=False; next_cursor=0
    ats=e.get('ats','html'); slug=quote(e.get('slug',''),safe='')
    def add(*args,**kwargs):
        item=post(e,*args,**kwargs)
        if item: jobs.append(item)
    if ats=='greenhouse':
        data=get(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs',params={'content':'true'},timeout=20).json()
        if not isinstance(data,dict) or not isinstance(data.get('jobs'),list): raise ValueError('schema_changed')
        raw=len(data['jobs'])
        for x in data['jobs']:
            if suitable(x.get('title')): add(x['title'],(x.get('location') or {}).get('name',''),x.get('content',''),x.get('absolute_url',''),x.get('id'),updated=x.get('updated_at',''))
    elif ats=='lever':
        # Descriptions on large employer boards exceed the public response-size
        # cap at 100 jobs. Smaller pages preserve that safety cap and advance
        # across scheduled runs rather than silently disabling the board.
        page_size=25
        data=get(f'https://api.lever.co/v0/postings/{slug}',params={'mode':'json','skip':cursor,'limit':page_size},timeout=20).json()
        if not isinstance(data,list): raise ValueError('schema_changed')
        raw=len(data); incomplete=raw>=page_size; next_cursor=cursor+raw if incomplete else 0
        for x in data:
            if not suitable(x.get('text')): continue
            c=x.get('categories') or {}; loc='; '.join(c.get('allLocations') or [c.get('location','')])
            if x.get('workplaceType')=='remote': loc='Remote; '+loc
            desc=' '.join(str(x.get(k) or '') for k in ('description','descriptionPlain','additional','additionalPlain'))+' '.join(str(z.get('content','')) for z in x.get('lists',[]) if isinstance(z,dict))
            created=x.get('createdAt'); created=datetime.fromtimestamp(created/1000,timezone.utc).isoformat() if isinstance(created,(int,float)) else ''
            add(x['text'],loc,desc,x.get('hostedUrl') or x.get('applyUrl',''),x.get('id'),created,employment=c.get('commitment',''))
    elif ats=='smartrecruiters':
        # No title query: MIS/research positions must not disappear behind 'analyst'.
        data=get(f'https://api.smartrecruiters.com/v1/companies/{slug}/postings',params={'limit':100,'offset':cursor,'country':'in'},timeout=20).json()
        if not isinstance(data,dict) or not isinstance(data.get('content'),list): raise ValueError('schema_changed')
        raw=len(data['content']); incomplete=cursor+raw<int(data.get('totalFound',raw)); next_cursor=cursor+raw if incomplete else 0
        for x in data['content']:
            if not suitable(x.get('name')): continue
            loc=x.get('location') or {}; location=loc.get('fullLocation') or ', '.join(str(loc.get(k,'')) for k in ('city','region','country') if loc.get(k))
            if not re.search(r'gurugram|gurgaon|noida|delhi|remote',location,re.I): continue
            ref=x.get('ref','')
            if not safe_url(ref) or urlparse(ref).hostname!='api.smartrecruiters.com': continue
            detail=get(ref,timeout=20).json(); sections=((detail.get('jobAd') or {}).get('sections') or {})
            desc=' '.join(str(v.get('text','')) for v in sections.values() if isinstance(v,dict))
            add(x['name'],location,desc,detail.get('applyUrl',''),x.get('id'),x.get('releasedDate',''),employment=(x.get('typeOfEmployment') or {}).get('label',''))
    elif ats=='ashby':
        data=get(f'https://api.ashbyhq.com/posting-api/job-board/{slug}',timeout=20).json()
        if not isinstance(data,dict) or not isinstance(data.get('jobs'),list): raise ValueError('schema_changed')
        raw=len(data['jobs'])
        for x in data['jobs']:
            if suitable(x.get('title')):
                loc=x.get('location',''); loc=('Remote; ' if x.get('isRemote') else '')+loc
                add(x['title'],loc,x.get('descriptionHtml') or x.get('descriptionPlain',''),x.get('jobUrl') or x.get('applyUrl',''),x.get('id') or x.get('jobUrl'),x.get('publishedAt',''),employment=x.get('employmentType',''))
    elif ats=='workable':
        data=get(f'https://www.workable.com/api/accounts/{slug}',params={'details':'true'},timeout=20).json()
        if not isinstance(data,dict) or not isinstance(data.get('jobs'),list): raise ValueError('schema_changed')
        raw=len(data['jobs'])
        for x in data['jobs']:
            if suitable(x.get('title')):
                url=x.get('url') or f'https://apply.workable.com/{slug}/j/{x.get("shortcode","")}/'
                description=x.get('description') or ''
                if description:
                    add(x['title'],_locations(x.get('location')),description,url,x.get('shortcode') or x.get('id'),x.get('published_on',''),employment=x.get('employment_type',''))
                else:
                    content=get(url,timeout=20).text if safe_url(url) else ''
                    found,_=_jsonld(content,url,e,now); jobs.extend(found)
    else:
        base=e.get('careers_url','')
        if not safe_url(base): raise ValueError('unsafe_source_url')
        response=get(base,timeout=20); content=response.text
        jobs,links=_jsonld(content,base,e,now)
        # Employer-hosted feed support; no authenticated/private ATS endpoints.
        if ats=='xml':
            root=ET.fromstring(content)
            for node in root.findall('.//job'):
                raw+=1; title=node.findtext('title') or node.findtext('position') or ''
                if suitable(title): add(title,node.findtext('location') or '',node.findtext('description') or '',node.findtext('url') or node.findtext('link') or '',node.findtext('id') or node.findtext('url'),node.findtext('date') or '')
        else:
            candidates=[]; host=urlparse(base).hostname
            allowed=set(e.get('detail_hosts',[]))|{host}
            for href in links:
                url=urljoin(base,href)
                if safe_url(url) and urlparse(url).hostname in allowed and re.search(r'/jobs?/[^/?#]+|/careers/[^/?#]+|applytojob\.com/apply/',url,re.I) and url!=base and url not in candidates: candidates.append(url)
            raw=len(candidates)+len(jobs)
            for url in candidates[cursor:cursor+5]:
                found,_=_jsonld(get(url,timeout=20).text,url,e,now); jobs.extend(found)
            incomplete=cursor+5<len(candidates); next_cursor=cursor+5 if incomplete else 0
    return jobs,dict(status='ok' if jobs or ats in ('greenhouse','lever','smartrecruiters','ashby','workable','xml') else 'link_only',raw_listings=raw,leads=len(jobs),incomplete=incomplete),next_cursor

def discover_sources(registry, request_fn, prior_state=None, now=None, max_candidates=80):
    """Return jobs, per-source diagnostics and JSON-serializable polling/cursor state.

    The caller owns retry/request/time budgets and redirect/response-size protection.
    Cached vacancies are reverified by callers before outreach; skipped polls retain dates.
    """
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None: now=now.replace(tzinfo=timezone.utc)
    prior_state=prior_state or {}; state=dict(prior_state); stats={}; groups=[]
    def fetch_entry(e):
        key=f'{e.get("ats","html")}:{e["name"]}'; old=prior_state.get(key,{})
        period={'high':0,'medium':86400,'low':604800}.get(e.get('polling_tier','medium'),86400)
        try: age=(now-datetime.fromisoformat(old.get('last_poll',''))).total_seconds()
        except (ValueError,TypeError): age=period
        if age<period:
            cached=[dict(job, needs_reverification=True) for job in old.get('jobs',[])]
            return key,cached,{'status':'cached','leads':len(cached)},old
        try:
            found,status,cursor=_fetch(e,request_fn,now,int(old.get('cursor',0)))
            # Paginated sources retain prior pages while full cycle is incomplete.
            fresh_ids={x.get('source_id') or x['href'] for x in found}
            old_by_id={x.get('source_id') or x['href']:x for x in old.get('jobs',[])}
            merged={x.get('source_id') or x['href']:x for x in (old.get('jobs',[]) if old.get('cursor') else [])}
            merged.update({x.get('source_id') or x['href']:x for x in found})
            status['cache_truncated']=len(merged)>50
            found=list(merged.values())[-50:]
            for x in found:
                ident=x.get('source_id') or x['href']
                x.setdefault('first_seen_at',old_by_id.get(ident,{}).get('first_seen_at',now.isoformat()))
                if ident in fresh_ids:
                    x['last_verified_at']=now.isoformat()
                    x.pop('needs_reverification',None)
                else:
                    x['needs_reverification']=True
            return key,found,status,{'last_poll':now.isoformat(),'cursor':cursor,'jobs':found}
        except Exception as exc:
            return key,[],{'status':'error','leads':0,'error':type(exc).__name__},old
    # map preserves registry order despite parallel I/O, so shortlists remain deterministic.
    with ThreadPoolExecutor(max_workers=min(6,max(1,len(registry)))) as pool:
        for key,found,status,entry in pool.map(fetch_entry,registry):
            stats[key]=status
            if found:
                offset=int(prior_state.get(key,{}).get('pick_cursor',0))%len(found)
                found=found[offset:]+found[:offset]
                entry=dict(entry,pick_cursor=(offset+5)%len(found))
            if entry: state[key]=entry
            groups.append(found)
    # Keep state bounded fairly across employers, rather than dropping small sources.
    allocations={key:[] for key in state}
    for index in range(50):
        for key,entry in state.items():
            if sum(len(v) for v in allocations.values())>=200: break
            values=entry.get('jobs',[])
            if index<len(values): allocations[key].append(values[index])
    for key,entry in state.items():
        if len(entry.get('jobs',[]))>len(allocations[key]): stats.setdefault(key,{})['cache_truncated']=True
        state[key]=dict(entry,jobs=allocations[key])
    jobs=[]; seen=set()
    # Fair interleaving; large employers do not monopolise the candidate budget.
    for i in range(max((len(g) for g in groups),default=0)):
        for g in groups:
            if i>=len(g): continue
            x=g[i]; identity=(x['company'],x.get('source_id') or x['href'])
            if identity not in seen: jobs.append(x); seen.add(identity)
            if len(jobs)>=max_candidates: return jobs,stats,state
    return jobs,stats,state

def _safe_found(found,status):
    return found if status.get('status')=='ok' else []
