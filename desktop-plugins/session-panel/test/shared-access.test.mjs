// node --test: the person-waits poll pauses while the window is hidden (one refresh on becoming visible), and the
// read-only card-cache door other plugins (blocked-panel) use never starts a read.
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
  return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k), clear: () => data.clear() }
}
globalThis.window = { localStorage: memoryStorage() }
const m = await import('../plugin.js')
const blocked = await import('../../blocked-panel/plugin.js')

function fakeDoc(hidden = false) {
  const handlers = new Set()
  return {
    hidden,
    addEventListener: (type, fn) => type === 'visibilitychange' && handlers.add(fn),
    removeEventListener: (type, fn) => handlers.delete(fn),
    show() { this.hidden = false; handlers.forEach(fn => fn()) },
    hide() { this.hidden = true; handlers.forEach(fn => fn()) },
    get listeners() { return handlers.size }
  }
}

test('person-waits poll: reads every 8 s while visible, never while hidden, once on becoming visible', t => {
  t.mock.timers.enable({ apis: ['setInterval'] })
  const doc = fakeDoc(false)
  let reads = 0
  const stop = m.pollWhileVisible(() => { reads++ }, m.PERSON_WAITS_POLL_MS, doc)
  assert.equal(reads, 1, 'visible at mount: read now')
  t.mock.timers.tick(m.PERSON_WAITS_POLL_MS)
  assert.equal(reads, 2)
  doc.hide()
  assert.equal(reads, 2, 'hiding does not read')
  t.mock.timers.tick(m.PERSON_WAITS_POLL_MS * 10)
  assert.equal(reads, 2, 'no reads while hidden')
  doc.show()
  assert.equal(reads, 3, 'one read on becoming visible')
  t.mock.timers.tick(m.PERSON_WAITS_POLL_MS)
  assert.equal(reads, 4, 'and the poll resumes')
  stop()
  assert.equal(doc.listeners, 0)
  t.mock.timers.tick(m.PERSON_WAITS_POLL_MS * 3)
  doc.show()
  assert.equal(reads, 4, 'stopped')
})

test('person-waits poll mounted in a hidden window waits for it to be shown', t => {
  t.mock.timers.enable({ apis: ['setInterval'] })
  const doc = fakeDoc(true)
  let reads = 0
  const stop = m.pollWhileVisible(() => { reads++ }, m.PERSON_WAITS_POLL_MS, doc)
  t.mock.timers.tick(m.PERSON_WAITS_POLL_MS * 3)
  assert.equal(reads, 0)
  doc.show()
  assert.equal(reads, 1)
  stop()
})

const slim = (id, status, extra = {}) => ({ detail: { task: { id, title: `Title ${id}`, status, session_id: 'S1', block_kind: 'needs_input', human_request: { action: 'Reply.' }, ...extra.task }, events: [], links: { parents: [], children: [] }, link_tasks: [], slim: true }, board: extra.board || 'default' })

beforeEach(() => {
  window.localStorage = memoryStorage()
  m.resetReadsForTests(); m.resetFeedForTests()
  window.__sessionTasks = { ingestFeed() {}, touchFeed() {} }
})

test('cachedCard returns the pushed record (detail included) and never starts a read', () => {
  const calls = []
  window.hermesDesktop = { api: async req => { calls.push(req); throw new Error('no network expected') } }
  const chip = { connectionId: 'C1', profile: 'default', board: 'ops' }
  assert.equal(m.cachedCard(chip, 't_aaaaaa1'), null, 'nothing cached: null, and no read started')
  const now = Date.now()
  m.ingestBundle('C1', 'default', { session: 'S1', lineage: ['S1'], tasks: [], cards: { t_aaaaaa1: slim('t_aaaaaa1', 'blocked', { board: 'ops' }) } }, { nowMs: now })
  const rec = m.cachedCard(chip, 't_aaaaaa1', now + 1_000)
  assert.equal(rec.detail.task.status, 'blocked')
  assert.equal(rec.detail.slim, true)
  assert.equal(rec.board, 'ops')
  assert.equal(rec.fresh, true)
  assert.equal(rec.at, now)
  assert.equal(m.cachedCard(chip, 't_aaaaaa1', now + 61_000).fresh, false, 'ages like any read')
  assert.equal(m.cachedCard({ connectionId: 'C1', profile: 'work' }, 't_aaaaaa1'), null, 'another profile')
  assert.equal(m.cachedCard({ profile: 'default' }, 't_aaaaaa1'), null, 'no connection: never the focused fallback')
  assert.equal(calls.length, 0)
})

test('window.__sessionPanel exposes the read-only doors', () => {
  const dispose = m.default.register({ register() {}, onEvent: () => () => {} })
  try {
    for (const k of ['cachedCard', 'feedVouches', 'feedIsLive']) assert.equal(typeof window.__sessionPanel[k], 'function', k)
    assert.equal(window.__sessionPanel.feedVouches({ connectionId: 'C1', profile: 'default', session: 'S1' }), false, 'no push seen')
  } finally { dispose() }
})

const settle = () => new Promise(r => setTimeout(r, 0))
const feedAnswer = extra => ({ feed: 'hub:1', profile: 'default', epoch: 'E1', rev: 3, bundles: {}, ...extra })
const askBundle = () => ({ session: 'S1', lineage: ['S1'], tasks: [], cards: { t_aaaaaa1: slim('t_aaaaaa1', 'blocked') } })
const row = { id: 't_aaaaaa1', board: 'default', connectionId: 'C1', readProfile: 'default' }
const sp = () => ({ cachedCard: m.cachedCard, feedVouches: m.feedVouches })

test('an untrusted (catch-up only) bundle detail never reaches blocked-panel; a trusted, vouched one does', async () => {
  const calls = []
  window.hermesDesktop = { api: async req => { calls.push(req); return window.__answer(req) } }
  // Catch-up answer before any push: an ordinary cached copy.
  window.__answer = () => feedAnswer({ bundles: { S1: askBundle() } })
  await m.feedCatchUp({ connectionId: 'C1', profile: 'default', sessions: ['S1'] })
  const untrusted = m.cachedCard({ connectionId: 'C1', profile: 'default' }, 't_aaaaaa1')
  assert.ok(untrusted.detail, 'the detail is cached')
  assert.equal(untrusted.trusted, false)
  assert.equal(blocked.sharedDetail(row, 'S1', Date.now(), sp()), null, 'blocked-panel does not take it')
  // Pushes start: the full catch-up is trusted and the chat vouched.
  window.__answer = () => feedAnswer({ bundles: { S1: askBundle() } })
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', profile: 'default', epoch: 'E1', prev: 3, rev: 3, heartbeat: true, fresh: ['S1'] } })
  await settle(); await settle()
  const trusted = m.cachedCard({ connectionId: 'C1', profile: 'default' }, 't_aaaaaa1')
  assert.equal(trusted.trusted, true)
  const taken = blocked.sharedDetail(row, 'S1', Date.now(), sp())
  assert.equal(taken.detail.task.id, 't_aaaaaa1')
  assert.equal(taken.at, trusted.at, 'its own confirmation time')
  // The gateway stops vouching for the chat (a heartbeat without it): no longer trusted.
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', profile: 'default', epoch: 'E1', prev: 3, rev: 3, heartbeat: true, fresh: [] } })
  assert.equal(m.cachedCard({ connectionId: 'C1', profile: 'default' }, 't_aaaaaa1').trusted, false)
  assert.equal(blocked.sharedDetail(row, 'S1', Date.now(), sp()), null)
  // A later untrusted bundle for the chat withdraws the trusted scope.
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', profile: 'default', epoch: 'E1', prev: 3, rev: 3, heartbeat: true, fresh: ['S1'] } })
  assert.equal(m.cachedCard({ connectionId: 'C1', profile: 'default' }, 't_aaaaaa1').trusted, true)
  m.ingestBundle('C1', 'default', askBundle(), { trusted: false })
  assert.equal(m.cachedCard({ connectionId: 'C1', profile: 'default' }, 't_aaaaaa1').trusted, false)
  assert.ok(!calls.some(c => String(c.path).startsWith('/api/plugins/kanban/tasks/')), 'no card read by either')
})

test('an untrusted bundle of another chat cannot replace a current trusted record; it clears trust otherwise', async () => {
  window.hermesDesktop = { api: async req => window.__answer(req) }
  window.__answer = () => feedAnswer({ bundles: { S1: askBundle() } })
  await m.feedCatchUp({ connectionId: 'C1', profile: 'default', sessions: ['S1', 'S2'] })
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', profile: 'default', epoch: 'E1', prev: 3, rev: 3, heartbeat: true, fresh: ['S1'] } })
  await settle(); await settle()
  const before = m.cachedCard({ connectionId: 'C1', profile: 'default' }, 't_aaaaaa1')
  assert.equal(before.trusted, true)
  // Another chat's catch-up-only bundle carries an older copy of the same card.
  const old = { session: 'S2', lineage: ['S2'], tasks: [], cards: { t_aaaaaa1: slim('t_aaaaaa1', 'running') } }
  m.ingestBundle('C1', 'default', old, { trusted: false, nowMs: Date.now() + 1_000 })
  const after = m.cachedCard({ connectionId: 'C1', profile: 'default' }, 't_aaaaaa1')
  assert.equal(after.detail.task.status, 'blocked', 'the trusted content stands')
  assert.equal(after.trusted, true)
  assert.equal(after.at, before.at)
  // Once the trusted record is no longer current, an untrusted copy replaces it and the record is untrusted, even
  // though the key is still in S1's trusted scope.
  m.ingestBundle('C1', 'default', old, { trusted: false, nowMs: before.at + 61_000 })
  const replaced = m.cachedCard({ connectionId: 'C1', profile: 'default' }, 't_aaaaaa1', before.at + 61_000)
  assert.equal(replaced.detail.task.status, 'running')
  assert.equal(replaced.trusted, false, 'the record carries its own trust')
  assert.equal(blocked.sharedDetail({ ...row }, 'S1', before.at + 61_000, sp()), null)
  // A heartbeat vouching for S1 does not re-stamp the untrusted record.
  m.onFeedEvent({ type: m.FEED_EVENT, payload: { feed: 'hub:1', profile: 'default', epoch: 'E1', prev: 3, rev: 3, heartbeat: true, fresh: ['S1'] } })
  assert.equal(m.cachedCard({ connectionId: 'C1', profile: 'default' }, 't_aaaaaa1').at, before.at + 61_000)
})
