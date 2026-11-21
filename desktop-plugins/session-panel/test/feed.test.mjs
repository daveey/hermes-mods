// node --test: the session-panel-feed client. Pushed bundles fill every cache the panel renders from (no reads while
// the feed is live), the revision chain is followed (gaps and new epochs catch up), and a gateway without the feed
// keeps today's reads. [daveey 2026-11-20] "precompute the status panel data on the hub ... kept in sync without polling".
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

const slim = (id, status, extra = {}) => ({ detail: { task: { id, title: `Title ${id}`, status, session_id: 'S1', ...extra.task }, events: [], links: { parents: extra.parents || [], children: [] }, link_tasks: extra.linkTasks || [], slim: true }, board: 'default' })
const bundle = (session = 'S1') => ({
  session, lineage: [session, 'S0'],
  tasks: [{ id: 't_aaaaaa1', status: 'blocked', board: 'default', session_id: session }],
  done: [{ id: 't_dddddd4', status: 'done', title: 'Shipped', activity_at: 10 }],
  cards: { t_aaaaaa1: slim('t_aaaaaa1', 'blocked', { task: { block_kind: 'dependency' }, parents: ['t_bbbbbb2'], linkTasks: [{ id: 't_bbbbbb2', status: 'running' }] }), t_bbbbbb2: slim('t_bbbbbb2', 'running') }
})
const settle = () => new Promise(r => setTimeout(r, 0))
const never = () => new Promise(() => {})

let ingested, live, calls
beforeEach(() => {
  window.localStorage = memoryStorage()
  m.resetReadsForTests(); m.resetFeedForTests(); m.resetDoneCacheForTests()
  ingested = []; live = new Map(); calls = []
  window.__sessionTasks = {
    ingestFeed: x => ingested.push(x),
    markFeedLive: (c, p) => live.set(`${c}|${p}`, Date.now()),
    feedLive: (c, p) => live.has(`${c}|${p}`)
  }
  window.hermesDesktop = { api: async req => { calls.push(req); return window.__answer ? window.__answer(req) : null } }
  window.__answer = null
})

test('a bundle fills session-tasks, Done and every chain card: no read while the feed is live', async () => {
  window.__sessionTasks.markFeedLive('C1', 'default')
  assert.equal(m.ingestBundle('C1', 'default', bundle()), true)
  assert.equal(ingested.length, 1)
  assert.deepEqual(ingested[0].lineage, ['S1', 'S0'])
  assert.equal(ingested[0].tasks[0].id, 't_aaaaaa1')
  assert.equal(m.cachedDone(JSON.stringify(['C1', 'default', 'S1'])).length, 1, 'Done rows ready for the first frame')
  const chip = { kind: 'kanban', id: 't_aaaaaa1', connectionId: 'C1', profile: 'default', board: 'default' }
  let reads = 0
  const later = Date.now() + 10 * 60_000 // ten minutes on: a feed-kept answer is current while the feed is live
  const r = m.cardInfo(chip, 't_aaaaaa1', later, () => { reads++; return never() })
  assert.equal(r.state, 'ok')
  assert.equal(r.info.title, 'Title t_aaaaaa1')
  assert.deepEqual(r.info.hop, { ref: { id: 't_bbbbbb2', status: 'running' } })
  assert.equal(m.cardInfo(chip, 't_bbbbbb2', later, () => { reads++; return never() }).info.status, 'running')
  assert.equal(m.parkFor({ ...chip, state: 'scheduled' }, later, () => { reads++; return never() }).state, 'ok')
  assert.equal(m.cardRef(chip, 't_bbbbbb2', later, () => { reads++; return never() }).title, 'Title t_bbbbbb2')
  assert.equal(reads, 0, 'nothing read: the feed answered')
})

test('once the feed goes quiet a feed-kept answer ages like any other and is re-read', () => {
  m.ingestBundle('C1', 'default', bundle())
  const chip = { kind: 'kanban', id: 't_aaaaaa1', connectionId: 'C1', profile: 'default', board: 'default' }
  let reads = 0
  m.cardInfo(chip, 't_aaaaaa1', Date.now() + 10 * 60_000, () => { reads++; return never() })
  assert.equal(reads, 1, 'feed not live: due for a read after CARD_FRESH_MS')
})

test('catch-up registers the watched chats, applies bundles, and remembers the revision', async () => {
  window.__answer = req => ({ feed: 'hub:1', epoch: 'E1', rev: 7, reset: true, bundles: { S1: bundle() } })
  assert.equal(await m.feedCatchUp({ connectionId: 'C1', profile: 'default', sessions: ['S1', 'S2'] }), true)
  assert.equal(calls[0].path, '/api/plugins/session-panel-feed/changes')
  assert.equal(calls[0].method, 'POST')
  assert.deepEqual(calls[0].body, { sessions: ['S1', 'S2'], since: 0, epoch: null })
  assert.ok(live.has('C1|default'), 'the feed is live for that connection')
  assert.equal(ingested.length, 1)
  window.__answer = () => ({ feed: 'hub:1', epoch: 'E1', rev: 7, reset: false, bundles: {} })
  await m.feedCatchUp({ connectionId: 'C1', profile: 'default', sessions: ['S1', 'S2'] })
  assert.deepEqual(calls[1].body, { sessions: ['S1', 'S2'], since: 7, epoch: 'E1' }, 'asks only for what is newer')
})

test('a gateway without the feed is remembered and keeps its own reads', async () => {
  window.__answer = () => null // 404: the bridge resolves not-found as null
  assert.equal(await m.feedCatchUp({ connectionId: 'C2', profile: 'default', sessions: ['S9'] }), false)
  assert.equal(await m.feedCatchUp({ connectionId: 'C2', profile: 'default', sessions: ['S9'] }), false)
  assert.equal(calls.length, 1, 'not asked again until the retry window passes')
  assert.equal(m.feedIsLive({ connectionId: 'C2', profile: 'default' }), false)
})

test('pushed batches that continue the chain apply; a gap, an oversized batch or a new epoch catches up', async () => {
  window.__answer = () => ({ feed: 'hub:1', epoch: 'E1', rev: 3, reset: true, bundles: {} })
  await m.feedCatchUp({ connectionId: 'C1', profile: 'default', sessions: ['S1'] })
  calls.length = 0
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', epoch: 'E1', prev: 3, rev: 4, bundles: { S1: bundle() } } })
  assert.equal(ingested.length, 1, 'applied in order')
  assert.equal(calls.length, 0, 'no request')
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', epoch: 'E1', prev: 3, rev: 4, bundles: { S1: bundle() } } })
  assert.equal(ingested.length, 1, 'a batch we already have is ignored')
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', epoch: 'E1', prev: 6, rev: 7, bundles: { S1: bundle() } } })
  await settle()
  assert.equal(calls.length, 1, 'a gap: catch up instead of applying out of order')
  assert.equal(calls[0].body.since, 4, 'from the last revision applied')
  window.__answer = () => ({ feed: 'hub:1', epoch: 'E2', rev: 1, reset: true, bundles: {} })
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', epoch: 'E2', prev: 0, rev: 1, bundles: {} } })
  await settle()
  assert.deepEqual(calls.at(-1).body.epoch, null, 'a restarted gateway: full reset')
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'other:9', epoch: 'X', prev: 0, rev: 1, bundles: { S1: bundle() } } })
  assert.equal(ingested.length, 1, 'a feed this window never registered with is ignored')
})

test('an oversized batch arrives by name only and is fetched', async () => {
  window.__answer = () => ({ feed: 'hub:1', epoch: 'E1', rev: 1, reset: true, bundles: {} })
  await m.feedCatchUp({ connectionId: 'C1', profile: 'default', sessions: ['S1'] })
  calls.length = 0
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', epoch: 'E1', prev: 1, rev: 2, bundles: {}, sessions: ['S1'] } })
  await settle()
  assert.equal(calls.length, 1)
})

test('feedTargets groups the focused chat and warm targets by connection', () => {
  const rows = [{ id: 'S7', connection_id: 'C2', profile: 'work', last_active: 5 }, { id: 'S8', connection_id: 'C1', last_active: 4 }]
  const t = m.feedTargets(rows)
  const c1 = t.find(x => x.connectionId === 'C1'), c2 = t.find(x => x.connectionId === 'C2')
  assert.equal(c1.sessions[0], 'S1', 'the focused chat first')
  assert.ok(c1.sessions.includes('S8'))
  assert.deepEqual(c2, { connectionId: 'C2', profile: 'work', sessions: ['S7'] })
})
