import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../workers/telegram_trigger_worker.js', import.meta.url), 'utf8');
const {optionalCardText} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const job = {job_title:'Research Associate', company:'Fixture <Employer>', manual_draft:'Fixture application',
  date_posted:'2026-10-23T03:00:00Z', manual_expires_at:'2099-01-01T00:00:00Z'};
const application = optionalCardText(job);
assert.match(application, /Application draft:/);
assert.doesNotMatch(application, /knows your work|2099/);
assert.match(application, /25 Oct 2026, 08:30 IST/);
assert.match(application, /Fixture &lt;Employer&gt;/);
assert.match(optionalCardText({...job, manual_route:'referral'}), /Referral draft \(only for someone who knows your work\):/);
assert.match(optionalCardText({...job, date_posted:'invalid', manual_expires_at:'invalid'}), /eligibility ends: Unknown/);
console.log('Manual card tests passed');
