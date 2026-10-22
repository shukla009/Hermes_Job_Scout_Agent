import {readFile} from 'node:fs/promises';
import {webcrypto, createHmac} from 'node:crypto';
import assert from 'node:assert/strict';
if (!globalThis.crypto) Object.defineProperty(globalThis, 'crypto', {value:webcrypto});
const source = await readFile(new URL('../workers/telegram_trigger_worker.js', import.meta.url), 'utf8');
const {default: worker, verifyCallback, createCallback, storeFeedback, reserveDispatch} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const ident = 'a'.repeat(24), now = 1800000000;
const payload = `applied:${ident}:${now + 60}`;
const sig = createHmac('sha256', 'bot').update(payload).digest('hex').slice(0,12);
assert.deepEqual(await verifyCallback(`${payload}:${sig}`, 'bot', now), {action:'applied',job_id:ident});
assert.equal(await verifyCallback(`${payload}:${sig}`, 'wrong', now), null);
assert.equal(await verifyCallback(`${payload}:${sig}`, 'bot', now+61), null);
assert.equal(await verifyCallback('../../secrets', 'bot', now), null);
let calls = [];
globalThis.fetch = async (url, options) => {
  calls.push({url,options});
  return new Response('{}', {status:201});
};
const env = {TELEGRAM_WEBHOOK_SECRET:'secret',TELEGRAM_BOT_TOKEN:'bot',TELEGRAM_CHAT_ID:'10',GITHUB_PAT:'gh',GITHUB_STATE_PAT:'state',HANDS_OFF_ONLY:'false'};
assert.equal((await worker.fetch(new Request('https://w',{method:'POST',body:'{}'}),env)).status,403);
assert.equal(calls.length,0);
const request = update => new Request('https://w',{method:'POST',headers:{'X-Telegram-Bot-Api-Secret-Token':'secret'},body:JSON.stringify(update)});
await worker.fetch(request({message:{chat:{id:10},from:{id:999},text:'/run'}}),env);
assert.equal(calls.length,0);
await storeFeedback(env,123,{job_id:ident,action:'applied',sender_id:'10'});
assert.match(calls[0].url,/state\/feedback\/123.json$/);
assert.equal(calls[0].options.headers.Authorization,'Bearer state');
assert.ok(!calls.some(c=>c.url.includes('workflow')));
globalThis.fetch = async ()=>new Response('{}',{status:500});
await assert.rejects(()=>storeFeedback(env,124,{job_id:ident,action:'applied'}));
await assert.rejects(()=>storeFeedback(env,-1,{}));
// A state outage cannot bypass a paused budget by dispatching /run.
calls = [];
globalThis.fetch = async (url,options)=>{calls.push({url,options});return new Response('{}',{status:500});};
assert.equal((await worker.fetch(request({message:{chat:{id:10},from:{id:10},text:'/run'}}),env)).status,503);
assert.ok(!calls.some(c=>c.url.includes('/dispatches')));
// A paused persisted budget refuses dispatch and sends only an explanatory reply.
calls = [];
globalThis.fetch = async (url,options)=>{
  calls.push({url,options});
  if (url.includes('contents/state/scout.json')) return new Response(JSON.stringify({content:Buffer.from(JSON.stringify({version:1,jobs:{},sends:{},budget:{paused:true}})).toString('base64')}));
  return new Response(JSON.stringify({ok:true}));
};
assert.equal((await worker.fetch(request({message:{chat:{id:10},from:{id:10},text:'/run'}}),env)).status,200);
assert.ok(!calls.some(c=>c.url.includes('/dispatches')));
calls=[];
globalThis.fetch=async(url,options)=>{
  calls.push({url,options});
  if(url.includes('contents/state/scout.json'))return new Response(JSON.stringify({content:Buffer.from(JSON.stringify({version:1,jobs:{[ident]:{id:ident,title:'Analyst',url:'https://company/jobs/1',tier:'core',last_seen:new Date().toISOString()},legacy:{id:'b'.repeat(24),title:'Unverified old alert',url:'https://old/jobs/1'}},sends:{},budget:{}})).toString('base64')}));
  return new Response(JSON.stringify({ok:true}));
};
assert.equal((await worker.fetch(request({message:{chat:{id:10},from:{id:10},text:'/queue'}}),env)).status,200);
const card=JSON.parse(calls.find(c=>c.url.includes('/sendMessage')).options.body);
assert.equal(calls.filter(c=>c.url.includes('/sendMessage')).length,1);
assert.equal(card.reply_markup.inline_keyboard.flat().length,6);
for(const button of card.reply_markup.inline_keyboard.flat()) {
  assert.ok(Buffer.byteLength(button.callback_data)<=64);
  assert.equal((await verifyCallback(button.callback_data,'bot')).job_id,ident);
}
const fresh=await createCallback('interview',ident,'bot',now);
assert.equal((await verifyCallback(fresh,'bot',now)).action,'interview');
calls=[];
assert.equal((await worker.fetch(request({message:{chat:{id:10},from:{id:10},text:'/queue'}}),{...env,HANDS_OFF_ONLY:'true'})).status,200);
const automaticQueue = JSON.parse(calls.find(c=>c.url.includes('/sendMessage')).options.body);
assert.match(automaticQueue.text,/No manual approval queue/);
assert.equal(automaticQueue.reply_markup,undefined);
// A cadence skip is distinct from the last search, not a fresh zero-result run.
calls=[];
globalThis.fetch=async(url,options)=>{
  calls.push({url,options});
  if(url.includes('contents/state/scout.json'))return new Response(JSON.stringify({content:Buffer.from(JSON.stringify({version:1,jobs:{},sends:{},budget:{mode:'private_allowance'},
    last_attempt:{at:'2026-10-22T12:30:00Z',status:'private_cadence_skip',reason:'Minimum interval < 5h',next_due_at:'2026-10-22T15:30:00Z'},
    last_run:{at:'2026-10-22T10:30:00Z',status:'partial',results_discovered:16,discovery_health:{failing_sources:3,alert_failure_categories:{http_access_denied:6}}}})).toString('base64')}));
  return new Response(JSON.stringify({ok:true}));
};
assert.equal((await worker.fetch(request({message:{chat:{id:10},from:{id:10},text:'/status'}}),env)).status,200);
const attemptStatus=JSON.parse(calls.find(c=>c.url.includes('/sendMessage')).options.body).text;
assert.match(attemptStatus,/Latest attempt: private_cadence_skip/);
assert.match(attemptStatus,/Last scout execution: partial/);
assert.match(attemptStatus,/Discovered: 16/);
assert.match(attemptStatus,/Next eligible scheduled run: 22 Oct 2026, 21:00 IST/);
assert.match(attemptStatus,/Minimum interval &lt; 5h/);
assert.match(attemptStatus,/Failing discovery sources: 3/);
assert.match(attemptStatus,/http access denied: 6/);
// Telegram retries must not dispatch twice, even if the first reply is lost.
calls=[];
let reservedEvent;
globalThis.fetch=async(url,options)=>{
  calls.push({url,options});
  if(url.includes('contents/state/scout.json'))return new Response(JSON.stringify({content:Buffer.from(JSON.stringify({version:1,jobs:{},sends:{},budget:{}})).toString('base64')}));
  if(url.includes('contents/state/commands/77.json')) {
    if(options.method==='PUT') {
      if(reservedEvent)return new Response('{}',{status:422});
      reservedEvent=JSON.parse(Buffer.from(JSON.parse(options.body).content,'base64').toString());
      return new Response('{}',{status:201});
    }
    return new Response(JSON.stringify({content:Buffer.from(JSON.stringify(reservedEvent)).toString('base64')}));
  }
  if(url.includes('/dispatches'))return new Response(null,{status:204});
  return new Response('{}',{status:500}); // First success acknowledgement fails.
};
const runUpdate={update_id:77,message:{chat:{id:10},from:{id:10},text:'/run'}};
assert.equal((await worker.fetch(request(runUpdate),env)).status,503);
assert.equal((await worker.fetch(request(runUpdate),env)).status,200);
assert.equal(calls.filter(c=>c.url.includes('/dispatches')).length,1);
globalThis.fetch=async()=>new Response('{}',{status:500});
await assert.rejects(()=>reserveDispatch(env,88,'10'));
console.log('Worker fixture tests passed');
