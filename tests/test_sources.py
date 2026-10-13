from datetime import datetime, timezone
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
    assert len(entries)==30 and len({e['name'] for e in entries})==30
    assert not any('naukri' in str(e).lower() for e in entries)
    assert next(e for e in entries if e['name']=='ProcDNA')['ats']=='html'

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
