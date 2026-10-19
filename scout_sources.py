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
POLL_SECONDS = {'high': 0, 'medium': 86400, 'low': 86400}
MAX_WATCHLIST_POLLS_PER_RUN = 6

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

def _is_watchlist(entry):
    """True only for known generic pages that do not expose vacancy records."""
    return entry.get('source_mode')=='watchlist'

def _iso_timestamp(value):
    if isinstance(value,(int,float)):
        # The public feeds used here publish Unix seconds. Tolerate milliseconds
        # without passing a non-ISO value into the strict freshness gate.
        seconds=value/1000 if value>100000000000 else value
        try: return datetime.fromtimestamp(seconds,timezone.utc).isoformat()
        except (ValueError,OverflowError,OSError): return ''
    return str(value or '')

def _employment_label(value):
    if isinstance(value,dict):
        return str(value.get('name') or value.get('label') or value.get('value') or '')
    if isinstance(value,list): return '; '.join(filter(None,(_employment_label(v) for v in value)))
    return str(value or '')

def _target_location(location):
    return bool(re.search(r'delhi|\bncr\b|gurugram|gurgaon|noida|remote',location or '',re.I))

def _not_expired(value,now):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00')) if value else None
        return not parsed or (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))>=now
    except ValueError:
        return True # Unknown expiry is never turned into a fabricated date.

def _workday_facets(nodes):
    """Discover location identifiers from the public board, never hard-code tenant IDs."""
    facets={}
    for node in nodes:
        if not isinstance(node,dict): continue
        values=node.get('values') or []
        if node.get('facetParameter')=='locations':
            ids=[str(v['id']) for v in values if isinstance(v,dict) and v.get('id') and _target_location(v.get('descriptor',''))]
            if ids: facets['locations']=ids[:40]
        facets.update(_workday_facets(values))
    return facets

def _source_classification(status):
    if status.get('status')=='error': return 'temporarily_failing'
    if status.get('status') in ('link_only','watchlist_rotated'): return 'discovery_only'
    if status.get('leads',0): return 'productive'
    return 'verified_feed' if status.get('status')=='ok' else 'unverified'

def _publication_time(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError):
        return None

def _himalayas_location(job):
    restrictions=job.get('locationRestrictions') or []
    restrictions=[restrictions] if isinstance(restrictions,(str,dict)) else restrictions
    names=[]
    for value in restrictions:
        if isinstance(value,dict): value=value.get('name') or value.get('country') or value.get('region') or ''
        if value: names.append(str(value).strip())
    allowed=r'\b(?:india|worldwide|anywhere|global|asia|apac|south asia)\b'
    if names and not any(re.search(allowed,name,re.I) for name in names): return ''
    offsets=job.get('timezoneRestrictions') or []
    offsets=[offsets] if isinstance(offsets,(int,float,str)) else offsets
    numeric=[]
    for offset in offsets:
        try: numeric.append(float(offset))
        except (TypeError,ValueError): pass
    # IST is UTC+05:30. An explicit incompatible timezone restriction would
    # turn a nominally remote role into a night-shift or unusable vacancy.
    if numeric and not any(4.5<=offset<=6.5 for offset in numeric): return ''
    if not names: return 'Remote; Worldwide'
    if any(re.search(r'\bindia\b',name,re.I) for name in names): return 'Remote; India'
    return 'Remote; '+', '.join(names[:5])

def post(e, title, location, description, url, identity, posted='', updated='', employment=''):
    if not safe_url(url) or not identity or not title:
        return None
    if location and not re.search(r'delhi|ncr|gurugram|gurgaon|noida|remote|india',location,re.I):
        return None
    content = text(description)[:12000]
    return dict(title=title, job_title=title, company=e['name'], location=location,
                body=content[:1500], source_text=content, source_emails=re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', content),
                href=url, application_url=url, employment_type=employment, source_type='direct_apply',
                page_fetched=True, active_listing=True, source_id=str(identity),
                source_name=e.get('source_name') or f"{e.get('ats', 'html')}:{e['name']}", posted_at=posted,
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
    jobs=[]; raw=0; incomplete=False; next_cursor=0; metrics={}
    ats=e.get('ats','html'); slug=quote(e.get('slug',''),safe='')
    def add(*args,**kwargs):
        item=post(e,*args,**kwargs)
        if item: jobs.append(item)
    if ats=='greenhouse':
        data=get(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs',params={'content':'true'},timeout=20).json()
        if not isinstance(data,dict) or not isinstance(data.get('jobs'),list): raise ValueError('schema_changed')
        raw=len(data['jobs'])
        for x in data['jobs']:
            if not suitable(x.get('title')): continue
            location=(x.get('location') or {}).get('name','')
            candidate=post(e,x['title'],location,x.get('content',''),x.get('absolute_url',''),x.get('id'),
                           x.get('first_published',''),x.get('updated_at',''),_employment_label(x.get('employment')))
            if not candidate: continue
            detail=x
            if not x.get('first_published'):
                if metrics.get('detail_requests',0)>=int(e.get('detail_limit',8)):
                    metrics['detail_truncated']=True
                else:
                    metrics['detail_requests']=metrics.get('detail_requests',0)+1
                    try:
                        fetched=get(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{quote(str(x.get("id","")),safe="")}',timeout=20).json()
                        if isinstance(fetched,dict) and str(fetched.get('id'))==str(x.get('id')): detail=fetched
                        else: metrics['detail_errors']=metrics.get('detail_errors',0)+1
                    except Exception:
                        # A detail failure leaves the job date unknown, so the
                        # downstream policy permits review but never auto-email.
                        metrics['detail_errors']=metrics.get('detail_errors',0)+1
            published=detail.get('first_published') or x.get('first_published') or ''
            deadline=detail.get('application_deadline') or x.get('application_deadline') or ''
            if detail is not x:
                enriched=post(e,detail.get('title') or x['title'],(detail.get('location') or {}).get('name',location),
                              detail.get('content') or x.get('content',''),detail.get('absolute_url') or x.get('absolute_url',''),
                              detail.get('id') or x.get('id'),published,detail.get('updated_at') or x.get('updated_at',''),
                              _employment_label(detail.get('employment') or x.get('employment')))
                if enriched: candidate=enriched
            candidate['first_published']=published
            candidate['posted_at']=published
            candidate['date_posted']=published
            candidate['application_deadline']=deadline
            if deadline: candidate['valid_through']=deadline
            jobs.append(candidate)
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
    elif ats=='workday':
        base=e.get('careers_url','').rstrip('/'); parsed=urlparse(base)
        tenant=e.get('tenant',''); board=e.get('board','')
        if (not safe_url(base) or not parsed.hostname.endswith('.myworkdayjobs.com')
                or not re.fullmatch(r'[A-Za-z0-9_-]+',tenant) or not re.fullmatch(r'[A-Za-z0-9_-]+',board)):
            raise ValueError('invalid_workday_board')
        origin=f'https://{parsed.hostname}'; endpoint=f'{origin}/wday/cxs/{tenant}/{board}'
        # This POST only searches the public vacancy index; it never submits an application.
        data=get(endpoint+'/jobs',method='POST',json={'limit':20,'offset':0,'searchText':'','appliedFacets':{}},timeout=20).json()
        if not isinstance(data,dict) or not isinstance(data.get('jobPostings'),list): raise ValueError('schema_changed')
        facets=_workday_facets(data.get('facets') or [])
        metrics['total_listings']=data.get('total',len(data['jobPostings'])); metrics['location_facets']=bool(facets)
        candidates={}; metrics['search_queries']=0
        for keyword in (e.get('search_keywords') or ['analyst','research','MIS'])[:4]:
            if not isinstance(keyword,str) or not re.fullmatch(r'[A-Za-z0-9 -]{1,50}',keyword): continue
            data=get(endpoint+'/jobs',method='POST',json={'limit':20,'offset':0,'searchText':keyword,'appliedFacets':facets},timeout=20).json()
            if not isinstance(data,dict) or not isinstance(data.get('jobPostings'),list): raise ValueError('schema_changed')
            raw+=len(data['jobPostings']); metrics['search_queries']+=1
            incomplete=incomplete or int(data.get('total',len(data['jobPostings'])))>len(data['jobPostings'])
            for item in data['jobPostings']:
                if isinstance(item,dict): candidates.setdefault(item.get('externalPath',''),item)
        for x in candidates.values():
            title=x.get('title',''); location=x.get('locationsText',''); path=x.get('externalPath','')
            if not suitable(title) or not _target_location(location): continue
            if not isinstance(path,str) or not path.startswith('/job/') or '?' in path or '#' in path or '..' in path: continue
            if metrics.get('detail_requests',0)>=min(8,int(e.get('detail_limit',8))):
                metrics['detail_truncated']=True; break
            metrics['detail_requests']=metrics.get('detail_requests',0)+1
            try:
                details=get(endpoint+path,timeout=20).json()
                info=details.get('jobPostingInfo') or {}
                if not info or info.get('canApply') is False or info.get('posted') is False or not _not_expired(info.get('endDate'),now): continue
                url=info.get('externalUrl') or base+path
                if urlparse(url).hostname!=parsed.hostname: continue
                published=info.get('startDate') or '' # Never parse relative postedOn as an original timestamp.
                item=post(e,info.get('title') or title,info.get('location') or location,info.get('jobDescription',''),
                          url,info.get('jobReqId') or info.get('id'),published,employment=info.get('timeType',''))
                if item:
                    item.update(first_published=published,date_provenance='workday.startDate' if published else 'unknown',
                                valid_through=info.get('endDate') or '',application_deadline=info.get('endDate') or '',
                                submission_route='account_form',source_attribution='Official Workday employer board')
                    jobs.append(item)
            except Exception:
                metrics['detail_errors']=metrics.get('detail_errors',0)+1
    elif ats=='oracle':
        base=e.get('careers_url','').rstrip('/'); parsed=urlparse(base); site=e.get('site','')
        if not safe_url(base) or not parsed.hostname.endswith('.oraclecloud.com') or not re.fullmatch(r'[A-Za-z0-9_-]+',site):
            raise ValueError('invalid_oracle_board')
        origin=f'https://{parsed.hostname}'; api=origin+'/hcmRestApi/resources/latest/'
        seen=set(); queries=e.get('search_keywords') or ['analyst','research','MIS']
        metrics['search_queries']=0
        for keyword in queries[:4]:
            if not isinstance(keyword,str) or not re.fullmatch(r'[A-Za-z0-9 -]{1,50}',keyword): continue
            data=get(api+'recruitingCEJobRequisitions',params={'onlyData':'true','expand':'requisitionList',
                'finder':f'findReqs;siteNumber={site},limit=25,offset=0,sortBy=POSTING_DATES_DESC,keyword={keyword}'},timeout=20).json()
            records=data.get('items') if isinstance(data,dict) else None
            if not isinstance(records,list) or not records or not isinstance(records[0].get('requisitionList'),list): raise ValueError('schema_changed')
            result=records[0]; listings=result['requisitionList']; raw+=len(listings); metrics['search_queries']+=1
            incomplete=incomplete or int(result.get('TotalJobsCount',len(listings)))>len(listings)
            for x in listings:
                ident=str(x.get('Id') or ''); location=x.get('PrimaryLocation',''); title=x.get('Title','')
                if not ident or ident in seen or not suitable(title) or not _target_location(location): continue
                seen.add(ident)
                if metrics.get('detail_requests',0)>=min(8,int(e.get('detail_limit',8))):
                    metrics['detail_truncated']=True; continue
                if not re.fullmatch(r'[A-Za-z0-9_-]+',ident): continue
                metrics['detail_requests']=metrics.get('detail_requests',0)+1
                try:
                    detail=get(api+'recruitingCEJobRequisitionDetails',params={'onlyData':'true','finder':f'ById;Id={ident}'},timeout=20).json()
                    entries=detail.get('items') if isinstance(detail,dict) else None
                    if not isinstance(entries,list) or not entries or str(entries[0].get('Id'))!=ident:
                        metrics['detail_errors']=metrics.get('detail_errors',0)+1; continue
                    info=entries[0]; expiry=info.get('ExternalPostedEndDate') or ''
                    if not _not_expired(expiry,now): continue
                    # The detail's external publication timestamp is authoritative;
                    # list PostedDate, modification dates and observation time are not.
                    published=info.get('ExternalPostedStartDate') or ''
                    description=' '.join(str(info.get(k) or '') for k in ('ShortDescriptionStr','ExternalDescriptionStr','ExternalResponsibilitiesStr','ExternalQualificationsStr'))
                    if info.get('JobShift'): description+=' Job Shift: '+str(info['JobShift'])
                    employment=' '.join(str(info.get(k) or '') for k in ('JobSchedule','ContractType','WorkerType'))
                    url=f'{origin}/hcmUI/CandidateExperience/en/sites/{site}/job/{quote(ident,safe="")}'
                    item=post(e,info.get('Title') or title,info.get('PrimaryLocation') or location,description,url,ident,published,employment=employment)
                    if item:
                        item.update(first_published=published,date_provenance='oracle.ExternalPostedStartDate' if published else 'unknown',
                                    application_deadline=expiry,valid_through=expiry,submission_route='account_form',
                                    source_attribution='Official Oracle Recruiting employer board')
                        jobs.append(item)
                except Exception:
                    metrics['detail_errors']=metrics.get('detail_errors',0)+1
    elif ats=='himalayas':
        max_pages=max(1,min(2,int(e.get('max_pages',2))))
        page_cursor=''
        for page_number in range(max_pages):
            params={'limit':20}
            if page_cursor: params['cursor']=page_cursor
            data=get('https://himalayas.app/jobs/api',params=params,timeout=20).json()
            if not isinstance(data,dict) or not isinstance(data.get('jobs'),list): raise ValueError('schema_changed')
            raw+=len(data['jobs']); metrics['pages']=page_number+1
            for x in data['jobs']:
                title=x.get('title',''); employment=_employment_label(x.get('employmentType'))
                if not suitable(title) or not re.search(r'\bfull[ -]?time\b',employment,re.I): continue
                if re.search(r'\b(?:contract|temporary|freelance|intern(?:ship)?)\b',employment,re.I): continue
                location=_himalayas_location(x)
                application=x.get('applicationLink','')
                if not location or not safe_url(application): continue
                published=_iso_timestamp(x.get('pubDate'))
                expiry=_iso_timestamp(x.get('expiryDate'))
                try:
                    expires=datetime.fromisoformat(expiry.replace('Z','+00:00')) if expiry else None
                    if expires and (expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc))<now: continue
                except ValueError: pass
                dynamic=dict(e,name=str(x.get('companyName') or 'Unknown employer'),
                             source_name=f"{e.get('ats', 'himalayas')}:{e['name']}")
                item=post(dynamic,title,location,x.get('description') or x.get('excerpt',''),application,
                          x.get('guid') or application,published,employment=employment)
                if not item: continue
                item.update(discovery_source='Himalayas',source_attribution='Himalayas public API',
                            source_listing_url=x.get('guid') if safe_url(x.get('guid','')) else application,
                            first_published=published,application_deadline=expiry,valid_through=expiry,
                            timezone_restrictions=x.get('timezoneRestrictions') or [],
                            salary_metadata={'min':x.get('minSalary'),'max':x.get('maxSalary'),
                                             'currency':x.get('currency'),'period':x.get('salaryPeriod')})
                jobs.append(item)
            page_cursor=str(data.get('nextCursor') or '')
            if not page_cursor or not data['jobs']: break
        # Always restart from the newest page at the next poll. This prevents
        # a backlog cursor from hiding new jobs inside the 48-hour window.
        incomplete=bool(page_cursor)
        next_cursor=0
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
    status=dict(status='ok' if jobs or ats in ('greenhouse','lever','smartrecruiters','ashby','workable','workday','oracle','himalayas','xml') else 'link_only',raw_listings=raw,leads=len(jobs),incomplete=incomplete)
    status.update(metrics)
    return jobs,status,next_cursor

def discover_sources(registry, request_fn, prior_state=None, now=None, max_candidates=80):
    """Return jobs, per-source diagnostics and JSON-serializable polling/cursor state.

    The caller owns retry/request/time budgets and redirect/response-size protection.
    Cached vacancies are reverified by callers before outreach; skipped polls retain dates.
    """
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None: now=now.replace(tzinfo=timezone.utc)
    prior_state=prior_state or {}; state=dict(prior_state); stats={}; groups=[]
    def poll_age(key,period):
        try: return (now-datetime.fromisoformat(prior_state.get(key,{}).get('last_poll',''))).total_seconds()
        except (ValueError,TypeError): return period
    watchlist_due=[]
    for e in registry:
        if e.get('polling_tier','medium')!='low' or not _is_watchlist(e): continue
        key=f'{e.get("ats","html")}:{e["name"]}'
        if poll_age(key,POLL_SECONDS['low'])>=POLL_SECONDS['low']:
            watchlist_due.append((prior_state.get(key,{}).get('last_poll',''),key))
    watchlist_allowed={key for _,key in sorted(watchlist_due)[:MAX_WATCHLIST_POLLS_PER_RUN]}
    def fetch_entry(e):
        key=f'{e.get("ats","html")}:{e["name"]}'; old=prior_state.get(key,{})
        tier=e.get('polling_tier','medium'); period=POLL_SECONDS.get(tier,POLL_SECONDS['medium'])
        age=poll_age(key,period)
        if _is_watchlist(e) and age>=period and key not in watchlist_allowed:
            cached=[dict(job,needs_reverification=True) for job in old.get('jobs',[])]
            return key,cached,{'status':'watchlist_rotated','leads':len(cached)},old
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
                previous=old_by_id.get(ident,{})
                original=previous.get('first_published') or previous.get('posted_at') or previous.get('date_posted')
                original_dt=_publication_time(original)
                latest_dt=_publication_time(x.get('first_published') or x.get('posted_at') or x.get('date_posted'))
                if original_dt and (not latest_dt or original_dt<latest_dt):
                    # An employer may refresh a posting's date. A known vacancy
                    # must not become a new 48-hour lead solely from that refresh.
                    x.update(first_published=original,posted_at=original,date_posted=original,
                             date_provenance=previous.get('date_provenance') or 'earliest_known_publication')
                x.setdefault('first_seen_at',old_by_id.get(ident,{}).get('first_seen_at',now.isoformat()))
                if ident in fresh_ids:
                    x['last_verified_at']=now.isoformat()
                    x.pop('needs_reverification',None)
                else:
                    x['needs_reverification']=True
            return key,found,status,{'last_poll':now.isoformat(),'cursor':cursor,'jobs':found}
        except Exception as exc:
            # Persist an attempt timestamp even on failure. Otherwise an
            # unavailable low-value page is retried on every scheduled run.
            failed=dict(old,last_poll=now.isoformat(),last_error=type(exc).__name__)
            cached=[dict(job,needs_reverification=True) for job in old.get('jobs',[])]
            return key,cached,{'status':'error','leads':len(cached),'error':type(exc).__name__},failed
    # map preserves registry order despite parallel I/O, so shortlists remain deterministic.
    with ThreadPoolExecutor(max_workers=min(6,max(1,len(registry)))) as pool:
        for key,found,status,entry in pool.map(fetch_entry,registry):
            status['classification']=(entry.get('classification') if status.get('status')=='cached' else None) or _source_classification(status)
            entry=dict(entry,classification=status['classification'])
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
