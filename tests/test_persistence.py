import json
import requests
import pytest
from scout_persistence import RuntimeBudget,BudgetExhausted,GitHubWriter

class Response:
    def __init__(self,status=200,data=None,headers=None,chunks=None):
        self.status_code=status; self.data=data or {}; self.headers=headers or {}; self.chunks=chunks or [b'{}']; self.closed=False
    def json(self): return self.data
    def close(self): self.closed=True
    def raise_for_status(self):
        if self.status_code>=400: raise requests.HTTPError()
    def iter_content(self,chunk_size): yield from self.chunks

def budget(get,**kw): return RuntimeBudget(get=get,dns_checker=lambda h:True,sleep=lambda n:None,**kw)
def test_request_budget_count():
    b=budget(lambda *a,**k:Response(),max_requests=2)
    assert b.request('https://example.com')._content==b'{}'
    b.request('https://example.com')
    with pytest.raises(BudgetExhausted): b.request('https://example.com')
def test_retries_count_and_bound():
    calls=[]
    def get(*a,**kw): calls.append(kw); return Response(503 if len(calls)==1 else 200,headers={'Retry-After':'200'})
    b=budget(get); b.request('https://example.com',max_retries=10)
    assert len(calls)==2 and b.attempts==2
    assert all(c['allow_redirects'] is False and c['stream'] for c in calls)
def test_redirect_private_never_requested():
    calls=[]
    def get(url,**kw): calls.append(url); return Response(302,headers={'Location':'https://127.0.0.1/private'})
    with pytest.raises(ValueError): budget(get).request('https://example.com')
    assert len(calls)==1
def test_dns_private_rejected():
    b=RuntimeBudget(get=lambda *a,**k:pytest.fail('must not request'),dns_checker=lambda h:False)
    with pytest.raises(ValueError): b.request('https://example.com')
@pytest.mark.parametrize('url',['https://www.naukri.com','http://example.com','https://user:pw@example.com','https://example.com:444','https://serpapi.com/search','https://[::1]/'])
def test_bad_url(url):
    with pytest.raises(ValueError): budget(lambda *a,**k:pytest.fail('must not request')).request(url)
def test_response_cap():
    response=Response(chunks=[b'x'*600000,b'y'*600000])
    with pytest.raises(ValueError): budget(lambda *a,**k:response).request('https://example.com')
    assert response.closed
def test_deadline():
    now=[0.]; b=budget(lambda *a,**k:Response(),clock=lambda:now[0],deadline_seconds=5)
    now[0]=6
    with pytest.raises(BudgetExhausted): b.request('https://example.com')
def test_redirect_counted():
    n=[0]
    def get(url,**kw): n[0]+=1; return Response(302,headers={'Location':f'https://example.com/{n[0]}'})
    b=budget(get)
    with pytest.raises(ValueError): b.request('https://example.com')
    assert n[0]==4 and b.attempts==4

class Session:
    def __init__(self,moved=0,fail=False): self.headers={}; self.calls=[]; self.moved=moved; self.fail=fail; self.head=0
    def request(self,method,url,json=None,timeout=None):
        self.calls.append((method,url,json))
        if self.fail: return Response(403)
        if '/git/ref/heads/' in url:
            self.head+=1; return Response(data={'object':{'sha':f'head{self.head}'}})
        if method=='GET' and '/git/commits/' in url: return Response(data={'tree':{'sha':f'tree{self.head}'}})
        if '/git/blobs' in url: return Response(data={'sha':'blob'})
        if '/git/trees' in url: return Response(data={'sha':'newtree'})
        if method=='POST' and '/git/commits' in url: return Response(data={'sha':'commit'})
        if method=='PATCH':
            if self.moved: self.moved-=1; return Response(422)
            return Response(data={'ok':True})
        raise AssertionError(url)

def test_writer_preserves_latest_tree_and_only_owned_paths(tmp_path):
    (tmp_path/'state').mkdir(); (tmp_path/'state'/'scout.json').write_text('{}'); (tmp_path/'unrelated.txt').write_text('keep')
    session=Session(moved=1); writer=GitHubWriter('not-logged','owner/repo',root=tmp_path,session=session)
    assert writer(None)
    trees=[body for method,url,body in session.calls if '/git/trees' in url]
    assert [v['base_tree'] for v in trees]==['tree1','tree2']
    assert all([x['path'] for x in tree['tree']]==['state/scout.json'] for tree in trees)
    assert all(body['force'] is False for method,url,body in session.calls if method=='PATCH')
def test_writer_failure_and_invalid_data_fail_closed(tmp_path):
    (tmp_path/'applied_history.json').write_text('{}')
    assert not GitHubWriter('token','owner/repo',root=tmp_path,session=Session(fail=True))()
    (tmp_path/'applied_history.json').write_text('broken')
    assert not GitHubWriter('token','owner/repo',root=tmp_path,session=Session())()
def test_writer_ref_retry_cap(tmp_path):
    (tmp_path/'applied_history.json').write_text('{}'); session=Session(moved=10)
    assert not GitHubWriter('token','owner/repo',root=tmp_path,session=session)()
    assert session.head==3
def test_writer_allowlist():
    with pytest.raises(ValueError): GitHubWriter('token','owner/repo',paths=['workers/telegram_trigger_worker.js'])
    with pytest.raises(ValueError): GitHubWriter('token','../../bad')

def test_parallel_budget_never_overshoots():
    from concurrent.futures import ThreadPoolExecutor
    b=budget(lambda *a,**kw:Response(),max_requests=3)
    def attempt(n):
        try: b.request('https://example.com'); return True
        except BudgetExhausted: return False
    with ThreadPoolExecutor(max_workers=6) as pool: outcomes=list(pool.map(attempt,range(20)))
    assert sum(outcomes)==3 and b.attempts==3
