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
    status: 'review', title: 'Fix the flaky wake test', meta: 'worker · review requested 5m ago',
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
