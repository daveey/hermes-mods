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

// ── core's ownership refusal: classified by reason or wording; a cross_session send retries a busy chat ──────
// 2026-11-23: core reworded the refusal ("open in another" -> "active in another Hermes window"), the regex
// missed it, and a send into a busy chat came back `unknown, retry_safe: false`, so the sender gave up.
const NOW_WORDING = 'This chat is active in another Hermes window/terminal. Use Take over here (session.take_over), or wait for that turn to finish.\nDetails: session s1 opened by desktop. Writer: pid=1:platform=desktop'
const OLD_WORDING = 'This chat is open in another Hermes window/terminal. Use it there, or start a new chat here.'
// What host.request rejects with: core's JsonRpcGatewayError (apps/shared/src/json-rpc-channel.ts).
const rpcError = (message, code, reason) => Object.assign(new Error(message), { name: 'JsonRpcGatewayError', code, data: reason ? { reason } : undefined })
const busy = () => rpcError(NOW_WORDING, 4090, 'SESSION_NOT_OWNED')
const tgt = { connectionId: 'local', profile: 'default', sessionId: 'stored' }
const T0 = 1_000_000
const WINDOW_MS = 300_000
const localConns = [{ id: 'local', label: 'This device', key: 'this-device', kind: 'local', primary: true }]
const fakeTime = t => t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: T0 })
const flush = () => new Promise(r => setImmediate(r))
/** Step fake time 1 s at a time until `isDone()`; real time is only microtask flushes. */
async function until(t, isDone, maxSeconds = 900) {
  for (let s = 0; s < maxSeconds; s++) {
    await flush()
    if (isDone()) break
    t.mock.timers.tick(1000)
  }
  await flush()
  assert.equal(isDone(), true, 'did not finish on fake time')
}
async function onFakeTime(t, promise, maxSeconds) {
  let done = false
  promise.then(() => (done = true), () => (done = true))
  await until(t, () => done, maxSeconds)
  return promise
}
/** A primary-socket host whose n-th prompt.submit is answered by `answer(n)` (throw = rejection). */
function submitHost(answer, at = [], extra = {}) {
  return { state, connections: async () => localConns,
    request: async method => {
      if (method === 'session.resume') return { session_id: 'live' }
      at.push(Date.now())
      return answer(at.length)
    }, ...extra }
}
/** The Desktop bridge: the request's own gateway answers GET /status/<id> with `status()`; POSTed results are kept. */
function bridge(status = () => ({ state: 'claimed', age: 1 }), posted = [], outbox = []) {
  globalThis.window = { hermesDesktop: { api: async req => {
    if (req.path.includes('/status/')) return status(req)
    if (req.path.endsWith('/result')) { posted.push(req.body); return { ok: true } }
    if (req.path.includes('/outbox')) return { requests: outbox.splice(0) }
    return {}
  } } }
  return posted
}
/** What the cross_session tool writes for a send (plugins/session-mentions/__init__.py `_relay`). */
const toolSend = (extra = {}) => ({ id: 'r1', action: 'send', prewrapped: true, args: { target: '@session:default/abc', message: 'hi' }, created_at: T0 / 1000, ttl: 600, retry_busy: true, retry_busy_seconds: 300, ...extra })
/** register() against a fake plugin context: the middleware handler and the dispose callbacks it registered. */
function registerPlugin(m) {
  const disposers = []
  const parts = []
  m.default.register({ onDispose: fn => disposers.push(fn), onEvent: () => () => {}, register: part => parts.push(part) })
  return { dispose: () => disposers.forEach(fn => fn()), middleware: parts.find(p => p.id === 'session-mention-middleware').data.handler }
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
  fakeTime(t)
  const at = []
  let landed = 0
  const m = await load(submitHost(n => {
    if (n <= 2) throw busy()
    landed++
    return { status: 'queued', user_row_id: 77 }
  }, at))
  const r = await onFakeTime(t, m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + WINDOW_MS }))
  assert.deepEqual(at.map(x => x - T0), [0, 5_000, 15_000])
  assert.equal(landed, 1)
  assert.equal(r.ok, true); assert.equal(r.status, 'queued'); assert.equal(r.queued, true); assert.equal(r.user_row_id, 77)
  assert.equal(r.retry_safe, false)
  t.mock.timers.tick(120_000); await flush()
  assert.equal(at.length, 3, 'no submit after the accepted one')
})

test('refused for the whole window: refused, safe to retry, backoff capped at 30 s, nothing after the deadline', async t => {
  fakeTime(t)
  const at = []
  const m = await load(submitHost(() => { throw new Error(NOW_WORDING) }, at))
  const r = await onFakeTime(t, m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + WINDOW_MS }))
  assert.equal(r.ok, false); assert.equal(r.status, 'refused'); assert.equal(r.delivered, false); assert.equal(r.retry_safe, true)
  const rel = at.map(x => x - T0)
  assert.deepEqual(rel.slice(0, 5), [0, 5_000, 15_000, 35_000, 65_000])
  assert.equal(Math.max(...rel.slice(1).map((x, i) => x - rel[i])), 30_000)
  assert.equal(rel.at(-1), WINDOW_MS) // the last try is at the deadline, not past it
  assert.equal(r.attempts, at.length); assert.equal(at.length, 13)
  assert.match(r.note, /whole retry window \(300 s/)
  t.mock.timers.tick(120_000); await flush()
  assert.equal(at.length, 13)
})

test('a late timer never re-submits past the window (the clock jumped while the retry slept)', async t => {
  fakeTime(t)
  const at = []
  const m = await load(submitHost(() => { throw busy() }, at))
  let r = null
  void m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + WINDOW_MS }).then(x => (r = x))
  await flush(); await flush()
  assert.equal(at.length, 1); assert.equal(r, null) // refused once, now sleeping 5 s
  t.mock.timers.setTime(T0 + 3 * 3600_000) // the Mac slept: hours pass, the timer has not fired yet
  await flush()
  assert.equal(r, null)
  t.mock.timers.tick(5_000) // ...and now it fires
  await flush(); await flush()
  assert.equal(at.length, 1, 'no second submit')
  assert.equal(r.status, 'refused'); assert.equal(r.retry_safe, true); assert.equal(r.delivered, false)
})

test('an unrelated submit error is unknown and is not retried, even inside a retry window', async t => {
  fakeTime(t)
  for (const error of [new Error('socket timeout'), rpcError('did not confirm your message in time', 4090, 'SESSION_OWNER_UNCONFIRMED')]) {
    const at = []
    const m = await load(submitHost(() => { throw error }, at))
    const r = await onFakeTime(t, m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + WINDOW_MS }))
    assert.equal(r.status, 'unknown'); assert.equal(r.retry_safe, false); assert.equal(r.delivered, null)
    t.mock.timers.tick(120_000); await flush()
    assert.equal(at.length, 1)
  }
})

test('an unrelated error after a refusal stops the retries as unknown', async t => {
  fakeTime(t)
  const at = []
  const m = await load(submitHost(n => { throw n === 1 ? new Error(NOW_WORDING) : new Error('socket timeout') }, at))
  const r = await onFakeTime(t, m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + WINDOW_MS }))
  assert.equal(at.length, 2); assert.equal(r.status, 'unknown'); assert.equal(r.retry_safe, false)
})

test('a gateway or profile switch caught before the submit left is refused (safe to retry), not unknown', async () => {
  for (const when of ['before resume', 'before submit']) {
    let here = 'local'
    const sent = []
    const m = await load({ state: { ...state, connectionId: { get: () => here } }, connections: async () => localConns,
      request: async method => {
        sent.push(method)
        if (method === 'session.resume') { here = 'other'; return { session_id: 'live' } }
        return { status: 'queued', user_row_id: 1 }
      } })
    const starting = m.deliver(tgt, 'test', 0, { retryUntil: Date.now() + WINDOW_MS })
    if (when === 'before resume') here = 'other' // deliver has read its primary; the user switches before the first RPC
    const r = await starting
    assert.deepEqual(sent, when === 'before resume' ? [] : ['session.resume'], when)
    assert.equal(r.status, 'refused', when); assert.equal(r.retry_safe, true); assert.equal(r.delivered, false); assert.equal(r.ok, false)
  }
})

test('the retry window: opt-in only, 300 s from this Desktop\'s claim, never past the TTL, not for forwarded requests', async () => {
  const m = await load({ state })
  const claimed = 2_000_000_000_000
  assert.equal(m.OWNER_RETRY_WINDOW_S, 300)
  assert.equal(m.sendRetryUntil(toolSend(), claimed), claimed + 300_000)
  // The gateway's clock (created_at) plays no part: hours behind or ahead, the window is the same.
  assert.equal(m.sendRetryUntil(toolSend({ created_at: claimed / 1000 - 7200 }), claimed), claimed + 300_000)
  assert.equal(m.sendRetryUntil(toolSend({ created_at: claimed / 1000 + 7200 }), claimed), claimed + 300_000)
  assert.equal(m.sendRetryUntil(toolSend({ retry_busy_seconds: 9999 }), claimed), claimed + 300_000)
  assert.equal(m.sendRetryUntil(toolSend({ retry_busy_seconds: 120 }), claimed), claimed + 120_000)
  assert.equal(m.sendRetryUntil(toolSend({ ttl: 60 }), claimed), claimed + 60_000)
  assert.equal(m.sendRetryUntil(toolSend({ forwarded: true }), claimed), 0)
  assert.equal(m.sendRetryUntil(toolSend({ retry_busy: undefined }), claimed), 0)
  assert.equal(m.sendRetryUntil(toolSend({ retry_busy: 'yes' }), claimed), 0)
})

test('a cross_session send (retry_busy) retries a busy chat and lands once', async t => {
  fakeTime(t)
  const asked = []
  bridge(req => (asked.push(req.path), { state: 'claimed', age: 3 }))
  const at = []
  const host = submitHost(n => { if (n <= 2) throw busy(); return { status: 'queued', user_row_id: 5 } }, at)
  const m = await load(host)
  const res = await onFakeTime(t, m.performRequest(toolSend(), 'local', localConns))
  assert.equal(at.length, 3); assert.equal(res.ok, true); assert.equal(res.status, 'queued')
  assert.deepEqual(asked, ['/api/plugins/session-mentions/status/r1', '/api/plugins/session-mentions/status/r1']) // once before each re-submit
})

test('a send that did not opt in (Slack follower, close-out) gets the first refusal back at once', async t => {
  fakeTime(t)
  bridge()
  const at = []
  const m = await load(submitHost(() => { throw busy() }, at))
  // What plugins/slack-workspace/follow.py writes: created_at and ttl, no retry_busy.
  const res = await m.performRequest(toolSend({ retry_busy: undefined, retry_busy_seconds: undefined }), 'local', localConns)
  assert.equal(at.length, 1); assert.equal(res.status, 'refused'); assert.equal(res.retry_safe, true); assert.equal(res.delivered, false)
  assert.equal(Date.now(), T0)
})

test('a forwarded send (home courier path) answers the first refusal at once', async t => {
  fakeTime(t)
  bridge()
  const at = []
  const m = await load(submitHost(() => { throw busy() }, at))
  const res = await m.performRequest(toolSend({ id: 'fwd-1', forwarded: true }), 'local', localConns)
  assert.equal(at.length, 1); assert.equal(res.status, 'refused'); assert.equal(res.retry_safe, true)
  assert.equal(Date.now(), T0)
})

test('the sender stopped waiting (request cancelled, gone, or past its window on the gateway clock): no further submit', async t => {
  for (const [name, gone] of [
    ['cancelled', { state: 'claimed', cancelled: true, age: 20 }],
    ['no longer held', { state: 'unknown' }],
    ['past the window on the gateway clock', { state: 'claimed', age: 301 }]
  ]) {
    t.mock.timers.reset(); fakeTime(t)
    let checks = 0
    bridge(() => (++checks === 1 ? { state: 'claimed', age: 5 } : gone))
    const at = []
    const m = await load(submitHost(() => { throw busy() }, at))
    const res = await onFakeTime(t, m.performRequest(toolSend(), 'local', localConns))
    assert.equal(at.length, 2, name) // the first check allowed one re-submit, the second ended it
    assert.equal(res.status, 'refused', name); assert.equal(res.retry_safe, true, name); assert.match(res.note, /sender stopped waiting/, name)
    assert.equal(Date.now() - T0, 15_000, name)
    t.mock.timers.tick(600_000); await flush()
    assert.equal(at.length, 2, name)
  }
})

test('while the gateway cannot say whether the sender still waits, nothing is re-submitted', async t => {
  fakeTime(t)
  bridge(() => { throw new Error('network down') })
  const at = []
  const m = await load(submitHost(() => { throw busy() }, at))
  const res = await onFakeTime(t, m.performRequest(toolSend(), 'local', localConns))
  assert.equal(at.length, 1)
  assert.equal(res.status, 'refused'); assert.equal(res.retry_safe, true)
  assert.equal(Date.now() - T0, WINDOW_MS)
})

test('the courier (drainConnection) claims a busy send, retries it and posts the accepted result once', async t => {
  fakeTime(t)
  const posted = bridge(undefined, [], [toolSend({ id: 'drain-1' })])
  const at = []
  const m = await load(submitHost(n => { if (n <= 2) throw busy(); return { status: 'queued', user_row_id: 9 } }, at))
  await m.drainConnection(localConns[0], localConns)
  await until(t, () => posted.length > 0)
  assert.equal(at.length, 3)
  assert.equal(posted.length, 1); assert.equal(posted[0].id, 'drain-1')
  assert.equal(posted[0].result.ok, true); assert.equal(posted[0].result.status, 'queued'); assert.equal(posted[0].result.user_row_id, 9)
  t.mock.timers.tick(600_000); await flush()
  assert.equal(at.length, 3); assert.equal(posted.length, 1)
})

test('a disposed plugin stops retrying at once and its courier posts refused, safe to retry', async t => {
  fakeTime(t)
  const posted = bridge(undefined, [], [toolSend({ id: 'drain-2' })])
  const at = []
  const m = await load(submitHost(() => { throw busy() }, at, { onEvent: () => () => {} }))
  const plugin = registerPlugin(m)
  await m.drainConnection(localConns[0], localConns)
  await flush(); await flush()
  assert.equal(at.length, 1); assert.equal(posted.length, 0) // refused once, sleeping before the re-submit
  plugin.dispose()
  await flush(); await flush()
  assert.equal(Date.now(), T0, 'no timer had to fire')
  assert.equal(posted.length, 1); assert.equal(posted[0].id, 'drain-2')
  assert.equal(posted[0].result.status, 'refused'); assert.equal(posted[0].result.retry_safe, true)
  assert.match(posted[0].result.note, /reloaded or disabled/)
  t.mock.timers.tick(600_000); await flush()
  assert.equal(at.length, 1)
})

test('/tell (composer middleware) answers the first refusal at once and never retries', async t => {
  fakeTime(t)
  bridge()
  const at = []
  const errors = []
  const m = await load(submitHost(() => { throw busy() }, at, { onEvent: () => () => {}, notifyError: (error, title) => errors.push([title, error.message]) }))
  const plugin = registerPlugin(m)
  const out = await plugin.middleware({ text: '/tell @session:default/abc are you there' })
  assert.equal(out, null) // consumed: never reaches the agent
  assert.equal(at.length, 1)
  assert.equal(errors.length, 1); assert.equal(errors[0][0], 'Could not send'); assert.match(errors[0][1], /active in another Hermes window/); assert.match(errors[0][1], /Safe to retry/)
  assert.equal(Date.now(), T0)
  t.mock.timers.tick(600_000); await flush()
  assert.equal(at.length, 1)
  plugin.dispose()
})

// ── a send into ANOTHER gateway: every retry round asks that gateway's own courier first, then the direct route ──
const twoConns = [...localConns, { id: 'macbook', label: 'macbook', key: 'macbook', kind: 'ssh', primary: false }]
/** `forward(n)` is the home courier's answer to the n-th forwarded send; `direct(n)` answers the n-th direct submit. */
function crossHost(forward, direct, log) {
  globalThis.window = { hermesDesktop: { api: async req => {
    if (req.path.endsWith('/ping')) return { ok: true, home_courier: true, forwarding: true }
    if (req.path.endsWith('/enqueue')) { log.push(['forward', Date.now() - T0]); return { ok: true } }
    if (req.path.includes('/status/fwd-')) return { state: 'done', result: forward(log.filter(x => x[0] === 'forward').length) }
    if (req.path.includes('/status/')) return { state: 'claimed', age: 1 }
    return {}
  } } }
  return { state, connections: async () => twoConns,
    profileRoutes: async () => [{ connectionId: 'macbook', profile: 'default', targetProfile: 'default' }],
    requestProfile: async (_route, method) => {
      if (method === 'session.resume') return { session_id: 'live' }
      log.push(['direct', Date.now() - T0])
      return direct(log.filter(x => x[0] === 'direct').length)
    } }
}
const farSend = (extra = {}) => toolSend({ args: { target: '@session:default@macbook/x', message: 'hi' }, ...extra })
const REFUSED = { ok: false, delivered: false, status: 'refused', retry_safe: true }

test('cross-gateway retry alternates forward then direct each round; the forward route lands it once', async t => {
  fakeTime(t)
  const log = []
  const m = await load(crossHost(n => (n <= 2 ? REFUSED : { ok: true, status: 'queued', queued: true, user_row_id: 4 }), () => { throw busy() }, log))
  const res = await onFakeTime(t, m.performRequest(farSend(), 'local', twoConns))
  assert.deepEqual(log.map(x => x[0]), ['forward', 'direct', 'forward', 'direct', 'forward'])
  assert.equal(res.ok, true); assert.equal(res.status, 'queued'); assert.equal(res.forwarded, true)
  t.mock.timers.tick(600_000); await flush()
  assert.equal(log.length, 5, 'nothing after the accepted send')
})

test('cross-gateway retry: the direct route lands it once when the forward route keeps refusing', async t => {
  fakeTime(t)
  const log = []
  let landed = 0
  const m = await load(crossHost(() => REFUSED, n => { if (n <= 2) throw busy(); landed++; return { status: 'streaming', user_row_id: 8 } }, log))
  const res = await onFakeTime(t, m.performRequest(farSend(), 'local', twoConns))
  assert.deepEqual(log.map(x => x[0]), ['forward', 'direct', 'forward', 'direct', 'forward', 'direct'])
  assert.equal(landed, 1); assert.equal(res.ok, true); assert.equal(res.status, 'streaming')
  t.mock.timers.tick(600_000); await flush()
  assert.equal(log.length, 6)
})

test('cross-gateway retry stops at an uncertain forward answer: no direct submit after it', async t => {
  fakeTime(t)
  const log = []
  const m = await load(crossHost(n => (n === 1 ? REFUSED : { ok: false, delivered: null, status: 'unknown', retry_safe: false }), () => { throw busy() }, log))
  const res = await onFakeTime(t, m.performRequest(farSend(), 'local', twoConns))
  assert.deepEqual(log.map(x => x[0]), ['forward', 'direct', 'forward'])
  assert.equal(res.status, 'unknown'); assert.equal(res.retry_safe, false)
  t.mock.timers.tick(600_000); await flush()
  assert.equal(log.length, 3)
})

test('cross-gateway retry refused on both routes for the whole window: refused, nothing past the window', async t => {
  fakeTime(t)
  const log = []
  const m = await load(crossHost(() => REFUSED, () => { throw busy() }, log))
  const res = await onFakeTime(t, m.performRequest(farSend(), 'local', twoConns))
  assert.equal(res.status, 'refused'); assert.equal(res.retry_safe, true); assert.equal(res.delivered, false)
  assert.match(res.note, /whole retry window/)
  assert.equal(Math.max(...log.map(x => x[1])) <= WINDOW_MS, true)
  // Late in the window a forward round could outlast the sender's wait: only the direct route is tried then.
  const late = log.filter(x => x[1] > WINDOW_MS - 65_000)
  assert.equal(late.length > 0 && late.every(x => x[0] === 'direct'), true)
  const n = log.length
  t.mock.timers.tick(600_000); await flush()
  assert.equal(log.length, n)
})

test('cross-gateway send without retry_busy keeps the single forward-then-direct try', async t => {
  fakeTime(t)
  const log = []
  const m = await load(crossHost(() => REFUSED, () => { throw busy() }, log))
  const res = await onFakeTime(t, m.performRequest(farSend({ retry_busy: undefined }), 'local', twoConns))
  assert.deepEqual(log.map(x => x[0]), ['forward', 'direct'])
  assert.equal(res.status, 'refused'); assert.equal(res.retry_safe, true)
})
