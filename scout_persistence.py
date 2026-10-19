"""Bounded public HTTP and compare-and-swap GitHub state persistence."""
import base64
from collections import defaultdict
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import urlparse,urljoin,quote
import requests

class BudgetExhausted(RuntimeError):
    pass

class RuntimeBudget:
    def __init__(self,max_requests=120,deadline_seconds=480,dns_checker=None,get=None,clock=None,sleep=None):
        self.max_requests=max_requests; self.deadline_seconds=deadline_seconds
        self.clock=clock or time.monotonic; self.started=self.clock(); self.attempts=0
        self.dns_checker=dns_checker or self._public_dns; self.get=get or requests.get
        self.transport = requests.request
        self.sleep=sleep or time.sleep; self.lock=threading.Lock()
        self.active=threading.BoundedSemaphore(6); self.hosts=defaultdict(lambda:threading.BoundedSemaphore(2))
    @staticmethod
    def _public_dns(host):
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(x[4][0]).is_global for x in addresses)
    def remaining(self):
        return self.deadline_seconds-(self.clock()-self.started)
    def _validate(self,url):
        try:
            p=urlparse(str(url)); host=(p.hostname or '').lower()
            if p.scheme!='https' or not host or p.username or p.password or p.port not in (None,443): raise ValueError('unsafe_url')
            if '.' not in host or host.endswith(('.local','.internal','.localhost')) or any(v in host for v in ('naukri','serpapi','integrate.api.nvidia')): raise ValueError('excluded_host')
            try:
                if not ipaddress.ip_address(host).is_global: raise ValueError('private_address')
            except ValueError:
                # Non-IP domains still require resolving every redirect target.
                if re.fullmatch(r'[0-9.]+',host) or ':' in host: raise
            if not self.dns_checker(host): raise ValueError('private_dns')
            return host
        except (ValueError,OSError) as exc: raise ValueError('unsafe_public_url') from exc
    def _charge(self):
        with self.lock:
            if self.attempts>=self.max_requests or self.remaining()<=0: raise BudgetExhausted('public_http_budget_exhausted')
            self.attempts+=1
    def request(self,url,params=None,timeout=25,max_retries=None,method='GET',**payload):
        method = str(method).upper()
        if method not in ('GET', 'POST') or set(payload) - {'json', 'data', 'files', 'headers', 'allow_redirects', 'return_http_errors'}:
            raise ValueError('unsupported_public_request')
        follow_redirects = payload.pop('allow_redirects', True)
        return_http_errors = payload.pop('return_http_errors', False)
        max_attempts=min(2,max(1,(max_retries+1) if max_retries is not None else 2))
        # Form POSTs are never retried: a lost response may still mean accepted.
        if method == 'POST': max_attempts = 1
        current=url; current_params=params; redirects=0; attempt=0
        while True:
            host=self._validate(current)
            remain=self.remaining()
            if remain<=0: raise BudgetExhausted('public_http_deadline')
            with self.lock: host_slot=self.hosts[host]
            acquired_global=self.active.acquire(timeout=max(0,remain))
            if not acquired_global: raise BudgetExhausted('public_http_deadline')
            acquired_host=False
            response=None
            try:
                acquired_host=host_slot.acquire(timeout=max(0,self.remaining()))
                if not acquired_host: raise BudgetExhausted('public_http_deadline')
                self._charge()
                options=dict(params=current_params,timeout=max(.1,min(float(timeout),self.remaining())),allow_redirects=False,stream=True,**payload)
                response = self.get(current, **options) if method == 'GET' else self.transport(method,current,**options)
                status=response.status_code
                if status in (301,302,303,307,308) and follow_redirects:
                    target=urljoin(current,response.headers.get('Location',''))
                    response.close(); redirects+=1
                    if redirects>3 or not target or target==current: raise ValueError('redirect_limit')
                    self._validate(target)
                    if method == 'POST': raise ValueError('post_redirect_requires_confirmation')
                    current=target; current_params=None
                    continue
                attempt+=1
                if status in (429,500,502,503,504) and attempt<max_attempts:
                    try: delay=min(2,max(0,float(response.headers.get('Retry-After','1'))))
                    except ValueError: delay=1
                    response.close()
                    if delay>=self.remaining(): raise BudgetExhausted('public_http_deadline')
                    self.sleep(delay); continue
                if not return_http_errors: response.raise_for_status()
                chunks=[]; size=0
                for chunk in response.iter_content(chunk_size=65536):
                    size+=len(chunk)
                    if size>1000000: response.close(); raise ValueError('public_response_too_large')
                    chunks.append(chunk)
                    if self.remaining()<=0: response.close(); raise BudgetExhausted('public_http_deadline')
                response._content=b''.join(chunks); response._content_consumed=True; response.close()
                return response
            except requests.RequestException:
                attempt+=1
                if attempt>=max_attempts: raise
            finally:
                if response is not None: response.close()
                if acquired_host: host_slot.release()
                self.active.release()

STATE_PATHS=('state/scout.json','state/sources.json','applied_history.json','follow_up.json','suppressed_emails.json')

class GitHubWriter:
    """Commit allowlisted local state against latest HEAD, preserving unrelated paths."""
    def __init__(self,token,repo,branch='main',paths=None,root='.',session=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo): raise ValueError('invalid_repo')
        if not branch or '..' in branch: raise ValueError('invalid_branch')
        self.paths=tuple(paths or STATE_PATHS)
        if any(p not in STATE_PATHS for p in self.paths): raise ValueError('state_path_not_allowed')
        self.root=Path(root).resolve(); self.branch=branch
        self.api='https://api.github.com/repos/'+repo
        self.session=session or requests.Session()
        self.session.headers.update({'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
    def _call(self,method,path,body=None):
        response=self.session.request(method,self.api+path,json=body,timeout=10)
        if response.status_code in (409,422): raise _RefMoved()
        response.raise_for_status()
        return response.json()
    def __call__(self,store=None):
        # store is intentionally not inspected: authoritative serializers already
        # wrote allowlisted files atomically before calling this persistence gate.
        try:
            contents=[]; size=0
            for relative in self.paths:
                path=(self.root/relative).resolve()
                if self.root not in path.parents: return False
                if not path.is_file(): continue
                if path.stat().st_size>3000000: return False
                data=path.read_bytes(); size+=len(data)
                if len(data)>3000000 or size>5000000: return False
                from scout_storage import decode_state, encode_state
                value = decode_state(json.loads(data), relative)
                if os.getenv('SCOUT_ENCRYPT_STATE', 'false').lower() == 'true':
                    data = json.dumps(encode_state(value, relative), separators=(',', ':')).encode()
                contents.append((relative,data))
            if not contents: return False
            for attempt in range(3):
                try:
                    head=self._call('GET','/git/ref/heads/'+quote(self.branch,safe='/'))['object']['sha']
                    commit=self._call('GET','/git/commits/'+head)
                    entries=[]
                    for name,data in contents:
                        sha=self._call('POST','/git/blobs',{'content':base64.b64encode(data).decode('ascii'),'encoding':'base64'})['sha']
                        entries.append({'path':name,'mode':'100644','type':'blob','sha':sha})
                    tree=self._call('POST','/git/trees',{'base_tree':commit['tree']['sha'],'tree':entries})['sha']
                    updated=self._call('POST','/git/commits',{'message':'Persist scout state','tree':tree,'parents':[head]})['sha']
                    self._call('PATCH','/git/refs/heads/'+quote(self.branch,safe='/'),{'sha':updated,'force':False})
                    return True
                except _RefMoved:
                    continue
            return False
        except Exception:
            return False

class _RefMoved(Exception):
    pass
