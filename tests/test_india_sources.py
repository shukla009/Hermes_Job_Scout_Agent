"""Contracts observed on the public EXL Oracle and Genpact Workday boards."""
from datetime import datetime, timezone

import scout_sources as s


NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)


class Response:
    def __init__(self,value): self.value=value
    def json(self): return self.value


def test_workday_uses_discovered_location_facets_and_detail_publication_date():
    calls=[]
    entry={'name':'Genpact','ats':'workday','tenant':'genpact','board':'External_Careers',
           'careers_url':'https://genpact.wd108.myworkdayjobs.com/External_Careers','polling_tier':'high'}
    def get(url,**kwargs):
        calls.append((url,kwargs))
        if url.endswith('/jobs'):
            if not kwargs['json']['searchText']:
                return Response({'total':1000,'jobPostings':[],'facets':[{'facetParameter':'locationMainGroup',
                    'values':[{'facetParameter':'locations','values':[{'id':'ncr-id','descriptor':'Noida, India'},
                              {'id':'us-id','descriptor':'New York, US'}]}]}]})
            assert kwargs['method']=='POST' and kwargs['json']['appliedFacets']=={'locations':['ncr-id']}
            return Response({'total':1,'jobPostings':[{'title':'Research Analyst','locationsText':'Noida, India',
                              'externalPath':'/job/Noida/Research_JR1','postedOn':'Posted Today'}]})
        return Response({'jobPostingInfo':{'id':'x','jobReqId':'JR1','title':'Research Analyst','location':'Noida, India',
             'jobDescription':'Market research. Job Type: Regular. Work Shift: Day Job (India).',
             'startDate':'2026-10-18','endDate':'2026-11-01','timeType':'Full time','canApply':True,
             'externalUrl':'https://genpact.wd108.myworkdayjobs.com/External_Careers/job/Noida/Research_JR1'}})
    jobs,stats,_=s.discover_sources([entry],get,now=NOW)
    assert len(jobs)==1 and jobs[0]['posted_at']=='2026-10-18'
    assert jobs[0]['date_provenance']=='workday.startDate'
    assert stats['workday:Genpact']['classification']=='productive'
    assert len(calls)==5 # one facet discovery, three role searches, one deduplicated detail


def test_workday_never_converts_relative_posted_today_to_original_date():
    entry={'name':'A','ats':'workday','tenant':'a','board':'External','polling_tier':'high',
           'careers_url':'https://a.wd1.myworkdayjobs.com/External','search_keywords':['research']}
    def get(url,**kwargs):
        if url.endswith('/jobs'):
            return Response({'total':1,'jobPostings':[{'title':'Research Analyst','locationsText':'Gurgaon',
                            'externalPath':'/job/Gurgaon/Research_1','postedOn':'Posted Today'}]})
        return Response({'jobPostingInfo':{'id':'1','title':'Research Analyst','location':'Gurgaon',
                        'jobDescription':'Excel reporting','timeType':'Full time','postedOn':'Posted Today'}})
    jobs,_,_=s.discover_sources([entry],get,now=NOW)
    assert jobs[0]['posted_at']=='' and jobs[0]['date_provenance']=='unknown'


def test_workday_rejects_off_tenant_urls_closed_jobs_and_untrusted_paths():
    entry={'name':'A','ats':'workday','tenant':'a','board':'External','polling_tier':'high',
           'careers_url':'https://a.wd1.myworkdayjobs.com/External','search_keywords':['research']}
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        if url.endswith('/jobs'):
            return Response({'total':3,'jobPostings':[
                {'title':'Research Analyst','locationsText':'Gurgaon','externalPath':'/job/../account'},
                {'title':'Research Analyst','locationsText':'Gurgaon','externalPath':'/job/closed'},
                {'title':'Research Analyst','locationsText':'Gurgaon','externalPath':'/job/other'}]})
        return Response({'jobPostingInfo':{'id':'1','title':'Research Analyst','location':'Gurgaon',
                         'canApply':not url.endswith('closed'),'externalUrl':'https://evil.example/job/1'}})
    jobs,_,_=s.discover_sources([entry],get,now=NOW)
    assert jobs==[] and not any('/job/..' in url for url in calls)


def test_oracle_uses_precise_detail_date_and_all_external_requirement_sections():
    calls=[]
    entry={'name':'EXL','ats':'oracle','site':'CX_2','polling_tier':'high',
           'careers_url':'https://example.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/jobs'}
    def get(url,**kwargs):
        calls.append((url,kwargs))
        if url.endswith('recruitingCEJobRequisitions'):
            assert 'sortBy=POSTING_DATES_DESC' in kwargs['params']['finder']
            return Response({'items':[{'TotalJobsCount':1,'requisitionList':[{'Id':'21648','Title':'Associate - Business Analyst',
                         'PrimaryLocation':'Noida, India','PostedDate':'2026-10-19'}]}]})
        return Response({'items':[{'Id':'21648','Title':'Associate - Business Analyst','PrimaryLocation':'Noida, India',
              'ExternalPostedStartDate':'2026-10-18T07:30:00+00:00','ExternalPostedEndDate':'2026-11-01T00:00:00Z',
              'JobSchedule':'Full time','ContractType':'Regular','JobShift':'Day',
              'ExternalDescriptionStr':'<p>Business process research.</p>',
              'ExternalQualificationsStr':'<p>Required: Excel.</p>',
              'ExternalResponsibilitiesStr':'<p>Prepare dashboards.</p>'}]})
    jobs,stats,_=s.discover_sources([entry],get,now=NOW)
    assert len(jobs)==1 and jobs[0]['posted_at']=='2026-10-18T07:30:00+00:00'
    assert 'Required: Excel.' in jobs[0]['source_text'] and 'dashboards' in jobs[0]['source_text']
    assert jobs[0]['date_provenance']=='oracle.ExternalPostedStartDate'
    assert jobs[0]['application_url'].endswith('/en/sites/CX_2/job/21648')
    assert stats['oracle:EXL']['detail_requests']==1 and len(calls)==4


def test_oracle_detail_limit_schema_failures_and_expiry_fail_closed():
    entry={'name':'EXL','ats':'oracle','site':'CX_2','polling_tier':'high','detail_limit':1,
           'careers_url':'https://example.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/jobs'}
    def get(url,**kwargs):
        if url.endswith('recruitingCEJobRequisitions'):
            return Response({'items':[{'TotalJobsCount':50,'requisitionList':[
                {'Id':str(i),'Title':'Research Analyst','PrimaryLocation':'Noida'} for i in range(10)]}]})
        return Response({'items':[{'Id':'0','Title':'Research Analyst','PrimaryLocation':'Noida',
                         'ExternalPostedStartDate':'2026-10-19','ExternalPostedEndDate':'2026-10-01'}]})
    jobs,stats,_=s.discover_sources([entry],get,now=NOW)
    assert not jobs and stats['oracle:EXL']['detail_requests']==1 and stats['oracle:EXL']['detail_truncated']
    _,stats,_=s.discover_sources([entry],lambda *a,**k:Response({'items':[]}),now=NOW)
    assert stats['oracle:EXL']['classification']=='temporarily_failing'


def test_html_careers_page_is_discovery_only_not_a_vacancy():
    class HtmlResponse:
        text='<h1>Join our company</h1><a href="/careers/">Careers</a>'
    jobs,stats,_=s.discover_sources([{'name':'A','ats':'html','careers_url':'https://a.example/careers'}],
                                    lambda *a,**k:HtmlResponse(),now=NOW)
    assert not jobs and stats['html:A']['classification']=='discovery_only'


def test_reposted_same_identity_keeps_original_publication_and_cached_classification():
    entry={'name':'A','ats':'ashby','slug':'a','polling_tier':'medium'}
    original=s.post(entry,'Research Analyst','Noida','Research and Excel','https://jobs.ashbyhq.com/a/1','1','2026-10-01')
    prior={'ashby:A':{'last_poll':'2026-10-01T00:00:00Z','jobs':[original]}}
    def get(*args,**kwargs):
        return Response({'jobs':[{'id':'1','title':'Research Analyst','location':'Noida',
            'jobUrl':'https://jobs.ashbyhq.com/a/1','publishedAt':'2026-10-19','descriptionPlain':'Research and Excel'}]})
    jobs,stats,state=s.discover_sources([entry],get,prior,now=NOW)
    assert jobs[0]['posted_at']=='2026-10-01' and jobs[0]['first_published']=='2026-10-01'
    _,cached,_=s.discover_sources([entry],lambda *a,**k:None,state,now=NOW)
    assert cached['ashby:A']['status']=='cached' and cached['ashby:A']['classification']=='productive'
