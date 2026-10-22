from datetime import datetime, timezone, timedelta
import json
import scout_sources as s
class Response:
    def __init__(self,data=None,html=''): self.data=data; self.text=html
    def json(self): return self.data
def test_unsafe_urls():
    for u in ('http://example.com','https://127.0.0.1/x','https://localhost/x','file:///x','https://u:p@example.com','https://www.naukri.com/jobs'): assert not s.safe_url(u)
    assert s.safe_url('https://jobs.lever.co/paytm/x')
def test_greenhouse_honest_dates():
    e={'name':'Example','ats':'greenhouse','slug':'example','polling_tier':'high'}
    def get(*a,**k): return Response({'jobs':[{'id':1,'title':'MIS Executive','location':{'name':'Noida'},'content':'Excel SQL','absolute_url':'https://example.com/jobs/1','updated_at':'2026-10-10'}]})
    jobs,stats,state=s.discover_sources([e],get)
    assert jobs[0]['posted_at']=='' and jobs[0]['updated_at']=='2026-10-10'
    assert jobs[0]['active_listing']

def test_greenhouse_detail_supplies_original_date_and_deadline():
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        if url.endswith('/jobs'):
            return Response({'jobs':[{'id':7,'title':'Research Analyst','location':{'name':'Gurugram'},
                'content':'Summary','absolute_url':'https://job-boards.greenhouse.io/example/jobs/7',
                'updated_at':'2026-10-18T05:00:00Z'}]})
        return Response({'id':7,'title':'Research Analyst','location':{'name':'Gurugram'},
            'content':'Detailed market intelligence role','absolute_url':'https://job-boards.greenhouse.io/example/jobs/7',
            'first_published':'2026-10-18T04:00:00Z','updated_at':'2026-10-18T05:00:00Z',
            'application_deadline':'2026-10-25T04:00:00Z','employment':{'name':'Full Time'}})
    jobs,stats,_=s.discover_sources([{'name':'Example','ats':'greenhouse','slug':'example','polling_tier':'high'}],get)
    assert len(calls)==2 and calls[1].endswith('/jobs/7')
    assert jobs[0]['date_posted']=='2026-10-18T04:00:00Z'
    assert jobs[0]['application_deadline']=='2026-10-25T04:00:00Z'
    assert jobs[0]['employment_type']=='Full Time'
    assert stats['greenhouse:Example']['detail_requests']==1

def test_greenhouse_list_date_avoids_unneeded_detail_call():
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        return Response({'jobs':[{'id':7,'title':'MIS Executive','location':{'name':'Noida'},
            'content':'Excel reporting','absolute_url':'https://job-boards.greenhouse.io/example/jobs/7',
            'first_published':'2026-10-18T04:00:00Z','application_deadline':'2026-10-25T04:00:00Z'}]})
    jobs,_,_=s.discover_sources([{'name':'Example','ats':'greenhouse','slug':'example','polling_tier':'high'}],get)
    assert len(calls)==1 and jobs[0]['first_published']=='2026-10-18T04:00:00Z'
def test_lever_cursor_fairness():
    def get(url,**kw): return Response([{'id':str(i),'text':'Research Analyst','categories':{'location':'Gurgaon'},'hostedUrl':f'https://example.com/jobs/{i}'} for i in range(100)])
    registry=[{'name':n,'ats':'lever','slug':n,'polling_tier':'high'} for n in ('a','b')]
    jobs,stats,state=s.discover_sources(registry,get,max_candidates=4)
    assert [x['company'] for x in jobs]==['a','b','a','b']
    assert state['lever:a']['cursor']==100

def test_lever_bounded_description_page():
    requested=[]
    def get(url,**kw):
        requested.append(kw['params'])
        return Response([{'id':str(i),'text':'Research Analyst','categories':{'location':'Gurgaon'},'hostedUrl':f'https://example.com/jobs/{i}'} for i in range(25)])
    _,stats,state=s.discover_sources([{'name':'a','ats':'lever','slug':'a','polling_tier':'high'}],get)
    assert requested[0]['limit']==25
    assert state['lever:a']['cursor']==25 and stats['lever:a']['incomplete']
def test_cached_no_request():
    now=datetime.now(timezone.utc); prior={'html:A':{'last_poll':now.isoformat(),'jobs':[]}}
    _,stats,_=s.discover_sources([{'name':'A','ats':'html'}],lambda *a,**k: (_ for _ in ()).throw(AssertionError()),prior,now)
    assert stats['html:A']['status']=='cached'
def test_board_not_job():
    jobs,stats,_=s.discover_sources([{'name':'A','ats':'html','careers_url':'https://example.com/careers'}],lambda *a,**k:Response(html='<h1>Careers</h1>'))
    assert jobs==[] and stats['html:A']['status']=='link_only'
def test_jsonld():
    node={'@type':'JobPosting','title':'Reporting Analyst','description':'Excel dashboards','url':'https://example.com/jobs/1','datePosted':'2026-10-10','jobLocation':{'address':{'addressLocality':'Noida','addressCountry':'IN'}}}
    html='<script type="application/ld+json">'+json.dumps(node)+'</script>'
    jobs,_=s._jsonld(html,'https://example.com/careers',{'name':'A'},datetime(2026,10,11,tzinfo=timezone.utc))
    assert jobs[0]['location']=='Noida, IN'
    node['validThrough']='2026-01-01'
    assert not s._jsonld('<script type="application/ld+json">'+json.dumps(node)+'</script>','https://example.com/careers',{'name':'A'},datetime(2026,10,11,tzinfo=timezone.utc))[0]
def test_schema_error():
    _,stats,_=s.discover_sources([{'name':'A','ats':'lever','slug':'a'}],lambda *a,**k:Response({'wrong':[]}))
    assert stats['lever:A']['status']=='error'
def test_sr_not_title_filtered():
    calls=[]
    def get(url,**kw): calls.append((url,kw)); return Response({'content':[],'totalFound':200})
    _,stats,_=s.discover_sources([{'name':'A','ats':'smartrecruiters','slug':'A'}],get)
    assert 'q' not in calls[0][1]['params'] and stats['smartrecruiters:A']['incomplete']

def test_registry_unique_bounded_and_no_naukri():
    from pathlib import Path
    entries=json.loads((Path(__file__).parents[1]/'config/employers.json').read_text())
    assert len(entries)==32 and len({e['name'] for e in entries})==32
    assert not any('naukri' in str(e).lower() for e in entries)
    assert next(e for e in entries if e['name']=='ProcDNA')['ats']=='html'
    assert next(e for e in entries if e['name']=='Himalayas Remote')['ats']=='himalayas'
    assert all(e.get('source_mode')=='watchlist' for e in entries if e.get('polling_tier')=='low')

def test_himalayas_keeps_only_india_eligible_full_time_remote_jobs():
    calls=[]; published=1792324403; expiry=1797508403
    good=lambda ident,restrictions,offsets=None: {
        'title':'Research Analyst','companyName':'Example '+ident,'employmentType':'Full Time',
        'locationRestrictions':restrictions,'timezoneRestrictions':[5] if offsets is None else offsets,
        'description':'Market intelligence, Excel and reporting','pubDate':published,'expiryDate':expiry,
        'applicationLink':f'https://himalayas.app/companies/example/jobs/{ident}',
        'guid':f'https://himalayas.app/companies/example/jobs/{ident}',
        'minSalary':650000,'maxSalary':700000,'currency':'INR','salaryPeriod':'annual'}
    def get(url,**kwargs):
        calls.append(kwargs['params'])
        if 'cursor' not in kwargs['params']:
            contract=good('contract',['India']); contract['employmentType']='Contract'
            return Response({'jobs':[good('worldwide',[]),good('us',['United States']),
                good('wrong-timezone',['Worldwide'],[-5]),contract],'nextCursor':'page-2'})
        return Response({'jobs':[good('india',['India'])],'nextCursor':''})
    jobs,stats,state=s.discover_sources([{'name':'Himalayas Remote','ats':'himalayas','polling_tier':'high','max_pages':2}],get,
                                        now=datetime(2026,10,18,tzinfo=timezone.utc))
    assert [job['company'] for job in jobs]==['Example worldwide','Example india']
    assert calls==[{'limit':20},{'limit':20,'cursor':'page-2'}]
    assert jobs[0]['date_posted']==datetime.fromtimestamp(published,timezone.utc).isoformat()
    assert jobs[0]['discovery_source']=='Himalayas'
    assert jobs[0]['source_name']=='himalayas:Himalayas Remote'
    assert jobs[0]['application_url']==jobs[0]['source_listing_url']
    assert jobs[0]['salary_metadata']['currency']=='INR'
    assert stats['himalayas:Himalayas Remote']['pages']==2
    assert state['himalayas:Himalayas Remote']['cursor']==0

def test_low_tier_sources_rotate_with_bounded_requests():
    calls=[]
    registry=[{'name':f'A{i}','ats':'html','polling_tier':'low','source_mode':'watchlist',
               'careers_url':f'https://a{i}.example/careers'} for i in range(8)]
    def get(url,**kwargs): calls.append(url); return Response(html='<h1>Careers</h1>')
    _,stats,_=s.discover_sources(registry,get,now=datetime(2026,10,18,tzinfo=timezone.utc))
    assert len(calls)==s.MAX_WATCHLIST_POLLS_PER_RUN
    assert sum(item['status']=='watchlist_rotated' for item in stats.values())==2

def test_productive_feeds_never_wait_behind_watchlist_rotation_and_stay_within_48h():
    start=datetime(2026,10,18,2,47,tzinfo=timezone.utc) # 08:17 IST
    run_times=[start,start+timedelta(hours=6,minutes=20),
               start+timedelta(hours=12,minutes=6),start+timedelta(days=1)]
    productive=[{'name':f'Feed{i}','ats':'greenhouse','slug':f'feed{i}','polling_tier':'low'} for i in range(8)]
    watchlists=[{'name':f'Watch{i}','ats':'html','polling_tier':'low','source_mode':'watchlist',
                 'careers_url':f'https://watch{i}.example/careers'} for i in range(21)]
    polls={entry['name']:[] for entry in productive+watchlists}; current=[start]
    def get(url,**kwargs):
        for entry in productive+watchlists:
            marker=entry.get('slug') or f'watch{entry["name"][5:]}.example'
            if marker in url:
                polls[entry['name']].append(current[0]); break
        if 'boards-api.greenhouse.io' in url: return Response({'jobs':[]})
        return Response(html='<h1>Careers</h1>')
    state={}
    for run_at in run_times:
        current[0]=run_at
        _,_,state=s.discover_sources(productive+watchlists,get,prior_state=state,now=run_at)
    # Eight discoverable feeds exceed the six-watchlist budget, but every one
    # is still polled immediately and again after 24 hours.
    assert all(times==[run_times[0],run_times[-1]] for name,times in polls.items() if name.startswith('Feed'))
    assert all((times[-1]-times[-2])<=timedelta(hours=48) for name,times in polls.items()
               if name.startswith('Feed') and len(times)>1)
    # The generic pages consume only their own bounded slots across the three
    # daily runs and therefore cannot delay a discoverable source.
    assert all(polls[f'Watch{i}'] for i in range(21))

def test_failed_source_attempt_is_cooled_down():
    calls=[]; start=datetime(2026,10,18,tzinfo=timezone.utc)
    entry={'name':'A','ats':'html','polling_tier':'low','careers_url':'https://a.example/careers'}
    def fail(*args,**kwargs): calls.append(1); raise TimeoutError()
    _,stats,state=s.discover_sources([entry],fail,now=start)
    assert stats['html:A']['status']=='error' and state['html:A']['last_poll']==start.isoformat()
    _,stats,_=s.discover_sources([entry],fail,prior_state=state,now=start+timedelta(hours=1))
    assert len(calls)==1 and stats['html:A']['status']=='cached'

def test_ashby_remote_country_retained():
    def get(*a,**k): return Response({'jobs':[{'id':'1','title':'BI Analyst','location':'India','isRemote':True,'jobUrl':'https://jobs.ashbyhq.com/a/1','descriptionPlain':'Power BI SQL','publishedAt':'2026-10-01'}]})
    jobs,_,_=s.discover_sources([{'name':'A','ats':'ashby','slug':'a'}],get)
    assert jobs[0]['location']=='Remote; India' and jobs[0]['posted_at']=='2026-10-01'

def test_sr_detail_untrusted_ref_never_fetched():
    calls=[]
    def get(url,**kw):
        calls.append(url)
        return Response({'content':[{'id':'1','name':'Research Analyst','location':{'city':'Noida'},'ref':'https://evil.example/jobs/1'}]})
    jobs,_,_=s.discover_sources([{'name':'A','ats':'smartrecruiters','slug':'a'}],get)
    assert jobs==[] and len(calls)==1

def test_source_state_cap_and_no_false_verification_of_prior_pages():
    now=datetime(2026,10,11,tzinfo=timezone.utc)
    oldjob=s.post({'name':'A'},'Research Analyst','Noida','Excel','https://example.com/jobs/old','old')
    oldjob['last_verified_at']='2026-10-01T00:00:00+00:00'
    prior={'lever:A':{'last_poll':'2026-10-01T00:00:00+00:00','cursor':100,'jobs':[oldjob]}}
    def get(*a,**k): return Response([{'id':'new','text':'MIS Executive','categories':{'location':'Noida'},'hostedUrl':'https://example.com/jobs/new'}])
    jobs,_,_=s.discover_sources([{'name':'A','ats':'lever','slug':'a','polling_tier':'high'}],get,prior,now)
    assert next(x for x in jobs if x['source_id']=='old')['last_verified_at']=='2026-10-01T00:00:00+00:00'

def test_procdna_published_nextjs_job():
    node={'props':{'pageProps':{'job':{'id':'job_1','title':'Research Analyst','location':'Gurugram, Haryana','employmentType':'Full Time','description':'Market research insights, Excel','applyUrl':'https://procdna.applytojob.com/apply/abc/Research-Analyst'}}}}
    html='<script id="__NEXT_DATA__" type="application/json">'+json.dumps(node)+'</script>'
    jobs,_=s._jsonld(html,'https://www.procdna.com/careers/jobs/job_1',{'name':'ProcDNA'},datetime.now(timezone.utc))
    assert jobs[0]['application_url']=='https://procdna.applytojob.com/apply/abc/Research-Analyst'
    assert jobs[0]['employment_type']=='Full Time'

def test_workable_official_details():
    calls=[]
    def get(url,**kw):
        calls.append((url,kw)); return Response({'jobs':[{'shortcode':'abc','title':'MIS Executive','location':{'city':'Noida','country':'India'},'description':'Excel reporting','url':'https://apply.workable.com/a/j/abc/'}]})
    jobs,_,_=s.discover_sources([{'name':'A','ats':'workable','slug':'a'}],get)
    assert calls[0][0]=='https://www.workable.com/api/accounts/a'
    assert calls[0][1]['params']=={'details':'true'} and jobs[0]['source_text']=='Excel reporting'

def test_plain_html_requires_all_vacancy_signals():
    html='<h1>Research Analyst</h1><p>Location: Noida</p><p>'+('Market research Excel insights. '*15)+'</p><a href="/jobs/1/apply">Apply now</a>'
    jobs,_=s._jsonld(html,'https://example.com/jobs/1',{'name':'A'},datetime.now(timezone.utc))
    assert jobs[0]['active_listing'] and jobs[0]['source_emails']==[]
    assert not s._jsonld(html,'https://example.com/careers',{'name':'A'},datetime.now(timezone.utc))[0]
    assert not s._jsonld(html.replace('Location:','Based in'),'https://example.com/jobs/1',{'name':'A'},datetime.now(timezone.utc))[0]

def test_expiry_offsets_respected():
    node={'@type':'JobPosting','title':'Research Analyst','url':'https://example.com/jobs/1','validThrough':'2026-10-11T01:00:00+05:30'}
    html='<script type="application/ld+json">'+json.dumps(node)+'</script>'
    assert not s._jsonld(html,'https://example.com/careers',{'name':'A'},datetime(2026,10,10,22,tzinfo=timezone.utc))[0]
