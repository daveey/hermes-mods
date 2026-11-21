// node --test: the session-panel feed client. Pushed bundles fill every cache the panel renders from; trust comes from
// pushes only (a catch-up answer alone never makes data current); chain steps are worked out at render; the revision
// chain is followed (gaps and new epochs catch up, stale answers are dropped); a gateway without the backend half or
// serving another profile keeps today's reads. [daveey 2026-11-20] "precompute the status panel data on the hub ...
// kept in sync without polling".
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};const a=v=>({get:()=>v});export const host={state:{focusedStoredSessionId:a("S1"),focusedSessionOwner:a({connectionId:"C1",profile:"default"}),connectionId:a("C1"),profile:a("default")}};export const Codicon=()=>null;export const DisclosureCaret=()=>null;export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useRef=v=>({current:v});export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))

function memoryStorage() {
  const data = new Map()
  return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k), clear: () => data.clear(), data }
}
globalThis.window = { localStorage: memoryStorage() }
const m = await import('../plugin.js')

const slim = (id, status, extra = {}) => ({ detail: { task: { id, title: `Title ${id}`, status, session_id: 'S1', ...extra.task }, events: extra.events || [], links: { parents: extra.parents || [], children: [] }, link_tasks: extra.linkTasks || [], slim: true }, board: 'default' })
const bundle = (session = 'S1') => ({
  session, lineage: [session, 'S0'],
  tasks: [{ id: 't_aaaaaa1', status: 'blocked', board: 'default', session_id: session }],
  done: [{ id: 't_dddddd4', status: 'done', title: 'Shipped', activity_at: 10 }],
  cards: { t_aaaaaa1: slim('t_aaaaaa1', 'blocked', { task: { block_kind: 'dependency' }, parents: ['t_bbbbbb2'], linkTasks: [{ id: 't_bbbbbb2', status: 'running' }] }), t_bbbbbb2: slim('t_bbbbbb2', 'running') }
})
const settle = () => new Promise(r => setTimeout(r, 0))
const never = () => new Promise(() => {})
const chip = { kind: 'kanban', id: 't_aaaaaa1', connectionId: 'C1', profile: 'default', board: 'default' }
const target = (sessions = ['S1']) => ({ connectionId: 'C1', profile: 'default', sessions })
const answer = (extra = {}) => ({ feed: 'hub:1', profile: 'default', epoch: 'E1', rev: 3, reset: true, bundles: {}, ...extra })
const push = extra => m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', profile: 'default', epoch: 'E1', bundles: {}, ...extra } })

let ingested, touched, calls
beforeEach(() => {
  window.localStorage = memoryStorage()
  m.resetReadsForTests(); m.resetFeedForTests(); m.resetDoneCacheForTests()
  ingested = []; touched = []; calls = []
  window.__sessionTasks = { ingestFeed: x => ingested.push(x), touchFeed: (...a) => touched.push(a) }
  window.hermesDesktop = { api: async req => { calls.push(req); if (window.__answer) return window.__answer(req); throw new Error('no answer') } }
  window.__answer = null
})

test('a catch-up answer alone is never trusted: it prepaints, and the panel still reads for itself', async () => {
  window.__answer = () => answer({ bundles: { S1: bundle() } })
  assert.equal(await m.feedCatchUp(target()), true)
  assert.equal(m.feedIsLive({ connectionId: 'C1', profile: 'default' }), false, 'no push seen yet')
  assert.equal(ingested[0].trusted, false)
  assert.equal(m.cachedDone(JSON.stringify(['C1', 'default', 'S1'])).length, 1, 'Done prepaints')
  let reads = 0
  m.cardInfo(chip, 't_aaaaaa1', Date.now() + 2 * 60_000, () => { reads++; return never() })
  assert.equal(reads, 1, 'untrusted: it ages and is re-read like any read')
})

test('the first push makes the feed live and fetches everything again, trusted; heartbeats keep it current', async () => {
  window.__answer = () => answer({ rev: 3 })
  await m.feedCatchUp(target())
  window.__answer = () => answer({ rev: 3, reset: false, bundles: { S1: bundle() } })
  push({ heartbeat: true, prev: 3, rev: 3, fresh: ['S1'] })
  await settle(); await settle()
  assert.equal(m.feedIsLive({ connectionId: 'C1', profile: 'default' }), true)
  assert.equal(calls.at(-1).body.since, 0, 'a full catch-up (since 0, same epoch)')
  assert.equal(ingested.at(-1).trusted, true)
  // Ten minutes on, with heartbeats all along: the trusted entries were re-stamped, nothing is read.
  const t0 = Date.now()
  const realNow = Date.now
  try {
    for (let i = 1; i <= 30; i++) { Date.now = () => t0 + i * 20_000; push({ heartbeat: true, prev: 3, rev: 3, fresh: ['S1'] }) }
    let reads = 0
    const now = t0 + 30 * 20_000
    const r = m.cardInfo(chip, 't_aaaaaa1', now, () => { reads++; return never() })
    assert.equal(r.state, 'ok')
    assert.equal(m.parkFor({ ...chip, state: 'scheduled' }, now, () => { reads++; return never() }).state, 'ok')
    assert.equal(reads, 0, 'current while heartbeats arrive')
    assert.ok(touched.length >= 30 && touched.at(-1)[2].includes('S1'), 'session-tasks re-stamped too')
    // Heartbeats stop: after the freshness window the entries are read again.
    const later = now + m.FEED_STALE_MS + 1
    Date.now = () => later
    assert.equal(m.feedIsLive({ connectionId: 'C1', profile: 'default' }), false)
    m.cardInfo(chip, 't_aaaaaa1', later, () => { reads++; return never() })
    assert.equal(reads, 1)
  } finally { Date.now = realNow }
})

test('chain steps from a pushed card are worked out when read: a wait keeps counting', async () => {
  const wakeAt = Math.floor(Date.now() / 1000) + 600
  const b = bundle()
  b.cards.t_aaaaaa1 = slim('t_aaaaaa1', 'scheduled', { events: [{ id: 1, kind: 'scheduled', created_at: wakeAt - 3600, payload: { wake_at: wakeAt } }] })
  m.ingestBundle('C1', 'default', b)
  const sub = now => m.cardInfo(chip, 't_aaaaaa1', now, never).info.hop.leaf.sub
  const soon = sub(Date.now()), late = sub(Date.now() + 20 * 60_000)
  assert.notEqual(soon, late, 'the same pushed record reads differently later (not frozen at ingest)')
  assert.match(late, /overdue/)
})

test('a gateway without the backend half (404) or serving another profile is left to its own reads', async () => {
  window.__answer = () => { throw new Error('404: {"detail":"Not Found"}') }
  assert.equal(await m.feedCatchUp({ connectionId: 'C2', profile: 'default', sessions: ['S9'] }), false)
  assert.equal(await m.feedCatchUp({ connectionId: 'C2', profile: 'default', sessions: ['S9'] }), false)
  assert.equal(calls.length, 1, 'not asked again until the retry window passes')
  window.__answer = () => ({ feed: 'hub:1', profile: 'work', profile_unsupported: true })
  assert.equal(await m.feedCatchUp({ connectionId: 'C3', profile: 'work', sessions: ['S9'] }), false)
  assert.equal(await m.feedCatchUp({ connectionId: 'C3', profile: 'work', sessions: ['S9'] }), false)
  assert.equal(calls.length, 2)
  window.__answer = () => { throw new Error('timeout') }
  assert.equal(await m.feedCatchUp({ connectionId: 'C4', profile: 'default', sessions: ['S9'] }), false)
  assert.equal(await m.feedCatchUp({ connectionId: 'C4', profile: 'default', sessions: ['S9'] }), false)
  assert.equal(calls.length, 4, 'a transient failure is retried')
})

test('pushes continuing the chain apply; a duplicate is ignored; a gap, an oversized batch or a new epoch catches up', async () => {
  window.__answer = () => answer({ rev: 3 })
  await m.feedCatchUp(target())
  calls.length = 0
  push({ prev: 3, rev: 4, bundles: { S1: bundle() }, fresh: ['S1'] })
  assert.equal(ingested.length, 1, 'applied in order')
  assert.equal(ingested[0].trusted, true)
  await settle()
  calls.length = 0
  push({ prev: 3, rev: 4, bundles: { S1: bundle() }, fresh: ['S1'] })
  assert.equal(ingested.length, 1 + 0, 'already have it')
  window.__answer = () => answer({ rev: 7, reset: false })
  push({ prev: 6, rev: 7, bundles: { S1: bundle() } })
  await settle()
  assert.equal(calls.at(-1).body.since, 4, 'a gap: catch up from the last revision applied')
  window.__answer = () => answer({ rev: 8, reset: false })
  push({ prev: 7, rev: 8, sessions: ['S1'] })
  await settle()
  assert.equal(calls.at(-1).body.since, 7, 'sent by name only: fetched')
  window.__answer = () => answer({ epoch: 'E2', rev: 1 })
  push({ epoch: 'E2', prev: 0, rev: 1 })
  await settle()
  assert.equal(calls.at(-1).body.epoch, null, 'a restarted gateway: full reset')
  const before = ingested.length
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'other:9', profile: 'default', epoch: 'X', prev: 0, rev: 1, bundles: { S1: bundle() } } })
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', profile: 'work', epoch: 'E2', prev: 1, rev: 2, bundles: { S1: bundle() } } })
  assert.equal(ingested.length, before, 'another feed or another profile is ignored')
})

test('a catch-up answer older than a push that landed meanwhile is dropped', async () => {
  window.__answer = () => answer({ rev: 3 })
  await m.feedCatchUp(target())
  let release
  window.__answer = () => new Promise(r => { release = () => r(answer({ rev: 3, reset: false, bundles: { S1: { ...bundle(), tasks: [] } } })) })
  const slow = m.feedCatchUp(target(['S1', 'S2']))
  await settle()
  push({ prev: 3, rev: 4, bundles: { S1: bundle() }, fresh: ['S1'] })
  const n = ingested.length
  release()
  assert.equal(await slow, true)
  assert.equal(ingested.length, n, 'the older answer did not overwrite the newer push')
})

test('one catch-up in flight per connection', async () => {
  let release
  window.__answer = () => new Promise(r => { release = () => r(answer()) })
  const a = m.feedCatchUp(target()), b = m.feedCatchUp(target())
  await settle()
  assert.equal(calls.length, 1, 'the second waits for the first')
  release()
  await a
  await settle()
  release()
  await b
  assert.equal(calls.length, 2)
})

test('feedTargets groups the focused chat and warm targets by connection', () => {
  const rows = [{ id: 'S7', connection_id: 'C2', profile: 'work', last_active: 5 }, { id: 'S8', connection_id: 'C1', last_active: 4 }]
  const t = m.feedTargets(rows)
  const c1 = t.find(x => x.connectionId === 'C1'), c2 = t.find(x => x.connectionId === 'C2')
  assert.equal(c1.sessions[0], 'S1', 'the focused chat first')
  assert.ok(c1.sessions.includes('S8'))
  assert.deepEqual(c2, { connectionId: 'C2', profile: 'work', sessions: ['S7'] })
})


test('a push re-stamps only the chats it vouches for; the gateway\'s own profile name is what pushes carry', async () => {
  window.__answer = () => answer({ rev: 3, profile: 'custom' }) // the hub calls its launch profile 'custom'
  await m.feedCatchUp(target(['S1', 'S2']))
  window.__answer = () => answer({ rev: 3, profile: 'custom', reset: false, bundles: { S1: bundle('S1'), S2: bundle('S2') } })
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', profile: 'custom', epoch: 'E1', prev: 3, rev: 3, heartbeat: true, bundles: {}, fresh: ['S1', 'S2'] } })
  await settle(); await settle()
  assert.equal(m.feedIsLive({ connectionId: 'C1', profile: 'default' }), true, 'matched on the hub\'s name for its profile')
  touched.length = 0
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', profile: 'custom', epoch: 'E1', prev: 3, rev: 3, heartbeat: true, bundles: {}, fresh: ['S1'] } })
  assert.deepEqual(touched.at(-1)[2], ['S1'], 'S2 (dropped or failing on the gateway) is left to age')
})

test('three queued catch-ups for one connection run one after another, never two at once', async () => {
  let inFlight = 0, most = 0
  const releases = []
  window.__answer = () => { inFlight++; most = Math.max(most, inFlight); return new Promise(r => releases.push(() => { inFlight--; r(answer()) })) }
  const all = [m.feedCatchUp(target()), m.feedCatchUp(target()), m.feedCatchUp(target())]
  for (let i = 0; i < 3; i++) { await settle(); await settle(); releases.shift()?.() }
  await Promise.all(all)
  assert.equal(calls.length, 3)
  assert.equal(most, 1)
})
