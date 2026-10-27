// node --test: linked cards paint instantly (per-chat cache, independent sources, saved ancestry) and read as rows.
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))

function memoryStorage() {
  const data = new Map()
  return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k), data }
}

globalThis.localStorage = memoryStorage()
const m = await import('../plugin.js')
const { host } = await import('@hermes/plugin-sdk')

let focus
beforeEach(() => {
  focus = { runtime: 'rt-1', stored: 'chat-1', owner: { connectionId: 'conn', profile: 'default' } }
  host.state = {
    focusedSessionId: { get: () => focus.runtime },
    focusedStoredSessionId: { get: () => focus.stored },
    focusedSessionOwner: { get: () => focus.owner }
  }
  globalThis.document = { hidden: false, getElementById: () => ({ remove() {} }), addEventListener() {}, removeEventListener() {} }
})

const scopeOf = stored => JSON.stringify(['conn', 'default', stored])
const card = (id, status = 'running', extra = {}) => ({ id, status, title: `card ${id}`, board: 'default', ...extra })
const deferred = () => {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}

test('a revisited chat paints its last-known cards before any network answer', async () => {
  const chips = m.sessionTaskChips([card('t_cached')], { connectionId: 'conn', profile: 'default' })
  m.rememberChips(scopeOf('chat-cached'), chips, [])
  focus.stored = 'chat-cached'
  const gate = deferred()
  host.request = () => gate.promise
  globalThis.window = { hermesDesktop: { api: () => gate.promise } } // nothing answers until released
  const dispose = m.default.register({ register() {} })
  try {
    const round = m.refresh()
    await new Promise(r => setTimeout(r, 0))
    const snap = window.__sessionTasks.snapshot
    assert.equal(snap.scope, scopeOf('chat-cached'))
    assert.deepEqual(snap.chips.map(c => c.id), ['t_cached'], 'cached card painted while every request is still pending')
    gate.resolve({})
    await round
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('kanban rows publish while the process and subagent RPCs are still pending', async () => {
  focus.stored = 'chat-rpc'
  const rpc = deferred()
  host.request = () => rpc.promise
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/sessions/')) return { id: 'chat-rpc', parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [card('t_fast', 'review', { note: 'waiting on review' })] }
    return { machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    const round = m.refresh()
    for (let i = 0; i < 20 && !(window.__sessionTasks.snapshot.chips || []).some(c => c.id === 't_fast'); i++)
      await new Promise(r => setTimeout(r, 5))
    assert.ok(window.__sessionTasks.snapshot.chips.some(c => c.id === 't_fast'), 'kanban did not wait on process.list')
    rpc.resolve({ processes: [], subagents: [] })
    await round
    assert.equal(m.cachedChips(scopeOf('chat-rpc')).kanban[0].id, 't_fast', 'the fresh answer is remembered for next time')
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('switching chats during an in-flight round paints the new chat at once', async () => {
  m.rememberChips(scopeOf('chat-next'), m.sessionTaskChips([card('t_next', 'blocked')], { connectionId: 'conn', profile: 'default' }), [])
  focus.stored = 'chat-slow'
  const slow = deferred()
  host.request = async () => ({})
  globalThis.window = { hermesDesktop: { api: () => slow.promise } }
  const dispose = m.default.register({ register() {} })
  try {
    const round = m.refresh() // stuck on the slow chat's ancestry read
    await new Promise(r => setTimeout(r, 0))
    focus.stored = 'chat-next'
    await m.refresh() // a round is running: this must paint, not wait for it
    assert.equal(window.__sessionTasks.snapshot.scope, scopeOf('chat-next'))
    assert.deepEqual(window.__sessionTasks.snapshot.chips.map(c => c.id), ['t_next'])
    slow.resolve({ id: 'chat-slow' })
    await round
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('a chat ancestry walked once is reused from storage without requests', async () => {
  focus.stored = 'chat-child'
  host.request = async () => ({})
  let ancestry = 0
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path === '/api/sessions/chat-child') return (ancestry++, { id: 'chat-child', parent_session_id: 'chat-parent' })
    if (path === '/api/sessions/chat-parent') return (ancestry++, { id: 'chat-parent', parent_session_id: null })
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [] }
    return { machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    await m.refresh()
    assert.equal(ancestry, 2)
    const saved = JSON.parse(localStorage.getItem('hermes.sessionTasks.lineage.v1'))
    assert.deepEqual(saved[scopeOf('chat-child')].sort(), ['chat-child', 'chat-parent'])
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('a card row reads as status, title, who, what happened last, note and PR', () => {
  const now = Date.UTC(2026, 9, 27, 9, 0, 0)
  const [chip] = m.sessionTaskChips([card('t_row', 'review', {
    title: 'Fix the flaky wake test', assignee: 'worker', activity_kind: 'review_requested', activity_at: now / 1000 - 300,
    note: 'Opened the PR; CI green', pr_url: 'https://github.com/o/r/pull/42'
  })], { connectionId: 'conn', profile: 'default' })
  assert.deepEqual(m.cardRowModel(chip, now), {
    status: 'review', look: null, wait: '', title: 'Fix the flaky wake test', meta: 'worker · review requested 5m ago',
    note: 'Opened the PR; CI green', prLabel: 'PR #42', prUrl: 'https://github.com/o/r/pull/42'
  })
  const [unsafe] = m.sessionTaskChips([card('t_x', 'running', { pr_url: 'javascript:alert(1)' })], { connectionId: 'conn', profile: 'default' })
  assert.equal(m.cardRowModel(unsafe, now).prUrl, null, 'only GitHub links render as links')
})

test('the collapsed strip names the most urgent card', () => {
  const chips = m.sessionTaskChips([card('t_a', 'ready'), card('t_b', 'running', { title: 'Ship the sync client' })], { connectionId: 'conn', profile: 'default' })
  assert.equal(m.progressSummary(chips), 'Agent progress (2) — no decision required · running: Ship the sync client')
  assert.equal(m.progressSummary([{ kind: 'bg', label: 'npm test' }]), 'Agent progress (1) — no decision required')
})

// [daveey] Each parked card says what it waits on and since when; an agent-owned wait over 2 h is amber "stalled",
// distinct from grey waiting and red needs-you (backend `wait` from core kanban_wait_info).
test('a parked card shows waiting on <producer> since <time>, amber when stalled', () => {
  const now = Date.UTC(2026, 9, 27, 9, 0, 0)
  const since = now / 1000 - 5 * 3600 - 600
  const [stalled, waiting, human] = m.sessionTaskChips([
    card('t_159f0063', 'blocked', { title: 'Self-hosted release route', wait: {
      wait_state: 'stalled', waiting_on: 'an agent to split the work (continuation budget exhausted)',
      waiting_since: since, agent_owned: true } }),
    card('t_ci', 'scheduled', { wait: { wait_state: 'waiting', waiting_on: 'ci run 40577358595', waiting_since: now / 1000 - 600 } }),
    card('t_ask', 'blocked', { wait: { wait_state: 'human', waiting_on: 'you', waiting_since: since } })
  ], { connectionId: 'conn', profile: 'default' })
  assert.equal(stalled.id, 't_159f0063', 'stalled ranks first')
  assert.equal(stalled.look, 'stalled')
  const row = m.cardRowModel(stalled, now)
  assert.equal(row.status, 'stalled')
  assert.equal(row.wait, `stalled: waiting on an agent to split the work (continuation budget exhausted) since ${m.sinceClock(since, now)} (5h10 ago)`)
  assert.equal(m.chipColor(stalled.look), '#d29922', 'amber')
  assert.match(stalled.title, /STALLED .* waiting on an agent to split the work/)
  const w = [waiting, human].find(c => c.id === 't_ci')
  assert.equal(w.look, 'waiting')
  assert.equal(m.cardRowModel(w, now).wait, `waiting on ci run 40577358595 since ${m.sinceClock(now / 1000 - 600, now)} (10m ago)`)
  assert.equal(m.chipColor(w.look), '#8b949e', 'grey')
  const h = [waiting, human].find(c => c.id === 't_ask')
  assert.equal(h.look, 'needs-you')
  assert.equal(m.chipColor(h.look), '#f85149', 'red')
  assert.notEqual(m.chipColor('stalled'), m.chipColor('waiting'))
  assert.notEqual(m.chipColor('stalled'), m.chipColor('needs-you'))
  assert.deepEqual(m.chipFrame('stalled').border.includes('#d29922'), true, 'the stalled pill is tinted')
  assert.equal(m.progressSummary([stalled, w]), 'Agent progress (2) — no decision required · 1 stalled · stalled: Self-hosted release route')
})

test('older backends (no wait) and stale rows render as before', () => {
  const [plain] = m.sessionTaskChips([card('t_old', 'scheduled')], { connectionId: 'conn', profile: 'default' })
  assert.equal(plain.look, null)
  assert.equal(m.cardRowModel(plain).wait, '')
  const [stale] = m.sessionTaskChips([card('t_stale', 'blocked', { stale: true, wait: { wait_state: 'stalled', waiting_on: 'x', waiting_since: 1 } })], { connectionId: 'conn', profile: 'default' })
  assert.equal(stale.look, null, 'a last-known row proves nothing about a stall')
})

test('sinceClock: HH:MM today, weekday + date otherwise', () => {
  const now = new Date(2026, 9, 27, 9, 0, 0).getTime()
  assert.equal(m.sinceClock(new Date(2026, 9, 27, 7, 5).getTime() / 1000, now), '07:05')
  assert.equal(m.sinceClock(new Date(2026, 9, 26, 20, 43).getTime() / 1000, now), 'Mon 26 20:43')
  assert.equal(m.sinceClock(0, now), '')
})

test('grey waiting cards rank with parked work, below live and review cards', () => {
  const chips = m.sessionTaskChips([
    card('t_wait', 'scheduled', { wait: { wait_state: 'waiting', waiting_on: 'card t_x', waiting_since: 1 } }),
    card('t_review', 'review'),
    card('t_run', 'running')
  ], { connectionId: 'conn', profile: 'default' })
  assert.deepEqual(chips.map(c => c.id), ['t_run', 't_review', 't_wait'])
})

test('a blocked or triage card keeps its status colour under a grey wait, but still says what it waits on', () => {
  const chips = m.sessionTaskChips([
    card('t_dep', 'blocked', { wait: { wait_state: 'waiting', waiting_on: 'a parent card', waiting_since: 1 } }),
    card('t_ask', 'blocked', { wait: { wait_state: 'waiting', waiting_on: 'an unvalidated ask (needs_input)', waiting_since: 1 } })
  ], { connectionId: 'conn', profile: 'default' })
  for (const c of chips) {
    assert.equal(c.look, null)
    assert.equal(m.chipColor(c.look || c.state), m.chipColor('blocked'))
    assert.match(m.waitLine(c), /^waiting on /)
  }
})
