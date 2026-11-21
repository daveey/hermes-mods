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
