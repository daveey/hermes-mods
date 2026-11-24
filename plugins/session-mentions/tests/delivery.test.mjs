import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
Error.stackTraceLimit = 1
async function load(host) {
 globalThis.__smHost = host
 const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
 return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}
const route = {connectionId:'local',profile:'default',targetProfile:'default'}
const state = {connectionId:{get:()=> 'local'},profile:{get:()=> 'default'}}
test('same gateway uses owning primary runtime, not a newly pooled backend; queues busy sends', async()=>{
 const calls=[]
 const m=await load({state,profileRoutes:async()=>[route],requestProfile:async()=>{throw Error('SESSION_NOT_OWNED')},request:async(method,p)=>{
  calls.push([method,p]);
  if(method==='session.resume') return {session_id:'owner-live',running:true}
  return {status:'queued',user_row_id:41}
 }})
 const result=await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test')
 assert.equal(result.status,'queued'); assert.equal(result.delivered,false); assert.equal(result.queued,true)
 assert.equal(calls[1][1].queued,true)
 assert.equal(calls[0][1].profile,'default')
})
test('null connection atom resolves only the registry primary',async()=>{
 const m=await load({state:{...state,connectionId:{get:()=>null}},connections:async()=>[{id:'local',primary:true}],request:async(method)=>method==='session.resume'?{session_id:'live'}:{status:'streaming',user_row_id:12},requestProfile:async()=>{throw Error('wrong runtime')},profileRoutes:async()=>[route]})
 assert.equal((await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test')).ok,true)
})
test('does not fall back to another profile', async()=>{
 const m=await load({state,profileRoutes:async()=>[{...route,connectionId:'remote',profile:'other',targetProfile:'other'}],requestProfile:async()=>{throw Error('WRONG PROFILE CALLED')}})
 await assert.rejects(()=>m.deliver({connectionId:'remote',profile:'missing',sessionId:'stored'},'test'),/no exact route/)
})
test('unknown submit status is never reported successful',async()=>{
 const m=await load({state,request:async(method)=>method==='session.resume'?{session_id:'live'}:{status:'rejected'}})
 const r=await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test')
 assert.equal(r.ok,false)
})
test('missing user row cannot return an old reply',async()=>{
 const m=await load({state,request:async(method)=>method==='session.resume'?{session_id:'live'}:{status:'queued'}})
 const r=await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test',1)
 assert.equal(r.reply,null); assert.match(r.note,/correlat/)
})
test('lost submit response is unknown and is not retried',async()=>{
 let submitted=0
 const m=await load({state,request:async(method)=>{
  if(method==='session.resume')return {session_id:'live'}
  submitted++;throw Error('socket timeout')
 }})
 const r=await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test')
 assert.equal(submitted,1); assert.equal(r.status,'unknown'); assert.equal(r.retry_safe,false)
})

// ── core's ownership refusal: classified by reason or wording, retried inside the request's TTL ──────────────
// 2026-11-23: core reworded the refusal ("open in another" -> "active in another Hermes window"), the regex
// missed it, and a send into a busy chat came back `unknown, retry_safe: false`, so the sender gave up.
const NOW_WORDING = 'This chat is active in another Hermes window/terminal. Use Take over here (session.take_over), or wait for that turn to finish.\nDetails: session s1 opened by desktop. Writer: pid=1:platform=desktop'
const OLD_WORDING = 'This chat is open in another Hermes window/terminal. Use it there, or start a new chat here.'
// What host.request rejects with: core's JsonRpcGatewayError (apps/shared/src/json-rpc-channel.ts).
const rpcError = (message, code, reason) => Object.assign(new Error(message), { name: 'JsonRpcGatewayError', code, data: reason ? { reason } : undefined })
const tgt = { connectionId: 'local', profile: 'default', sessionId: 'stored' }
const flush = () => new Promise(r => setImmediate(r))
/** Run `promise` to its end on fake time, 1 s per step; real time is only microtask flushes. */
async function onFakeTime(t, promise, maxSeconds = 900) {
  let done = false
  promise.then(() => (done = true), () => (done = true))
  for (let s = 0; s < maxSeconds; s++) {
    await flush()
    if (done) break
    t.mock.timers.tick(1000)
  }
  await flush()
  assert.equal(done, true, 'delivery did not finish on fake time')
  return promise
}
/** A primary-socket host whose n-th prompt.submit is answered by `answer(n)` (throw = rejection). */
function submitHost(answer, at = []) {
  return { state, connections: async () => [{ id: 'local', label: 'This device', key: 'this-device', kind: 'local', primary: true }],
    request: async method => {
      if (method === 'session.resume') return { session_id: 'live' }
      at.push(Date.now())
      return answer(at.length)
    } }
}

test('owner refusal is recognised by reason, by the current wording and by the old wording', async () => {
  const m = await load({ state })
  assert.equal(m.isOwnerRefusal(new Error(NOW_WORDING)), true)
  assert.equal(m.isOwnerRefusal(new Error(OLD_WORDING)), true)
  assert.equal(m.isOwnerRefusal(NOW_WORDING), true)
  assert.equal(m.isOwnerRefusal(rpcError('reworded again', 4090, 'SESSION_NOT_OWNED')), true)
  assert.equal(m.isOwnerRefusal(rpcError('The gateway is running a turn in this chat and an image cannot join its queue. Nothing was sent.', 4090, 'SESSION_OWNER_BUSY')), true)
  // 4090 alone is not the refusal: this one may already be queued, whatever its text says.
  assert.equal(m.isOwnerRefusal(rpcError(NOW_WORDING, 4090, 'SESSION_OWNER_UNCONFIRMED')), false)
  assert.equal(m.isOwnerRefusal(rpcError('Too many turns', 4090)), false)
  assert.equal(m.isOwnerRefusal(new Error('socket timeout')), false)
})

for (const [name, make] of [
  ['current core wording', () => new Error(NOW_WORDING)],
  ['old core wording', () => new Error(OLD_WORDING)],
  ['structured reason only', () => rpcError('reworded again', 4090, 'SESSION_NOT_OWNED')]
]) {
  test(`${name}: one refused submit with no retry window is refused, safe to retry`, async () => {
    const at = []
    const m = await load(submitHost(() => { throw make() }, at))
    const r = await m.deliver(tgt, 'test')
    assert.equal(at.length, 1)
    assert.equal(r.ok, false); assert.equal(r.status, 'refused'); assert.equal(r.delivered, false); assert.equal(r.retry_safe, true)
  })
}

test('refused twice then accepted: exactly one submit lands, after 5 s and 10 s waits', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_000_000 })
  const at = []
  let landed = 0
  const m = await load(submitHost(n => {
    if (n <= 2) throw rpcError(NOW_WORDING, 4090, 'SESSION_NOT_OWNED')
    landed++
    return { status: 'queued', user_row_id: 77 }
  }, at))
  const r = await onFakeTime(t, m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + 600_000 }))
  assert.deepEqual(at.map(x => x - 1_000_000), [0, 5_000, 15_000])
  assert.equal(landed, 1)
  assert.equal(r.ok, true); assert.equal(r.status, 'queued'); assert.equal(r.queued, true); assert.equal(r.user_row_id, 77)
  assert.equal(r.retry_safe, false)
  t.mock.timers.tick(120_000); await flush()
  assert.equal(at.length, 3, 'no submit after the accepted one')
})

test('refused until the TTL runs out: refused, safe to retry, backoff capped at 30 s, nothing after the deadline', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_000_000 })
  const at = []
  const m = await load(submitHost(() => { throw new Error(NOW_WORDING) }, at))
  const r = await onFakeTime(t, m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + 600_000 }))
  assert.equal(r.ok, false); assert.equal(r.status, 'refused'); assert.equal(r.delivered, false); assert.equal(r.retry_safe, true)
  const rel = at.map(x => x - 1_000_000)
  assert.deepEqual(rel.slice(0, 5), [0, 5_000, 15_000, 35_000, 65_000])
  assert.equal(Math.max(...rel.slice(1).map((x, i) => x - rel[i])), 30_000)
  assert.equal(rel.at(-1), 600_000) // the last try is at the deadline, not past it
  assert.equal(r.attempts, at.length); assert.equal(at.length, 23)
  assert.match(r.note, /600 s retry window/)
  t.mock.timers.tick(120_000); await flush()
  assert.equal(at.length, 23)
})

test('an unrelated submit error is unknown and is not retried, even inside a retry window', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_000_000 })
  for (const error of [new Error('socket timeout'), rpcError('did not confirm your message in time', 4090, 'SESSION_OWNER_UNCONFIRMED')]) {
    const at = []
    const m = await load(submitHost(() => { throw error }, at))
    const r = await onFakeTime(t, m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + 600_000 }))
    assert.equal(r.status, 'unknown'); assert.equal(r.retry_safe, false); assert.equal(r.delivered, null)
    t.mock.timers.tick(120_000); await flush()
    assert.equal(at.length, 1)
  }
})

test('an unrelated error after a refusal stops the retries as unknown', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_000_000 })
  const at = []
  const m = await load(submitHost(n => { throw n === 1 ? new Error(NOW_WORDING) : new Error('socket timeout') }, at))
  const r = await onFakeTime(t, m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + 600_000 }))
  assert.equal(at.length, 2); assert.equal(r.status, 'unknown'); assert.equal(r.retry_safe, false)
})

test('retry window is the request TTL from created_at; forwarded or unstamped requests get one try', async () => {
  const m = await load({ state })
  const now = 2_000_000_000_000
  assert.equal(m.sendRetryUntil({ created_at: now / 1000 - 100, ttl: 600 }, now), now + 500_000)
  assert.equal(m.sendRetryUntil({ created_at: now / 1000 - 700, ttl: 600 }, now) <= now, true) // already expired
  assert.equal(m.sendRetryUntil({ created_at: now / 1000 + 5000, ttl: 600 }, now), now + 600_000) // gateway clock ahead
  assert.equal(m.sendRetryUntil({ created_at: now / 1000, ttl: 9999 }, now), now + 600_000)
  assert.equal(m.sendRetryUntil({ created_at: now / 1000, ttl: 600, forwarded: true }, now), 0)
  assert.equal(m.sendRetryUntil({ ttl: 600 }, now), 0)
  assert.equal(m.sendRetryUntil({ created_at: now / 1000 }, now), 0)
})

test('a relayed send (courier path) retries a busy chat inside its TTL and lands once', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_000_000 })
  globalThis.window = { hermesDesktop: { api: async () => ({}) } }
  const at = []
  const host = submitHost(n => { if (n <= 2) throw rpcError(NOW_WORDING, 4090, 'SESSION_NOT_OWNED'); return { status: 'queued', user_row_id: 5 } }, at)
  const m = await load(host)
  const conns = await host.connections()
  const req = { id: 'r1', action: 'send', prewrapped: true, args: { target: '@session:default/abc', message: 'hi' }, created_at: Date.now() / 1000, ttl: 600 }
  const res = await onFakeTime(t, m.performRequest(req, 'local', conns))
  assert.equal(at.length, 3); assert.equal(res.ok, true); assert.equal(res.status, 'queued')
})

test('a forwarded send (home courier path) answers the first refusal at once', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_000_000 })
  globalThis.window = { hermesDesktop: { api: async () => ({}) } }
  const at = []
  const host = submitHost(() => { throw rpcError(NOW_WORDING, 4090, 'SESSION_NOT_OWNED') }, at)
  const m = await load(host)
  const req = { id: 'fwd-1', action: 'send', prewrapped: true, forwarded: true, args: { target: '@session:default/abc', message: 'hi' }, created_at: Date.now() / 1000, ttl: 600 }
  const res = await m.performRequest(req, 'local', await host.connections())
  assert.equal(at.length, 1); assert.equal(res.status, 'refused'); assert.equal(res.retry_safe, true)
  assert.equal(Date.now(), 1_000_000)
})
