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
    const next = m.refresh() // a round is running: this must paint, not wait for it
    assert.equal(window.__sessionTasks.snapshot.scope, scopeOf('chat-next'))
    assert.deepEqual(window.__sessionTasks.snapshot.chips.map(c => c.id), ['t_next'])
    slow.resolve({ id: 'chat-slow' })
    await round
    await next
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('a switch reads the new chat at once instead of queueing behind the old chat\'s slow round', async () => {
  focus.stored = 'chat-old'
  const slow = deferred(), paths = []
  host.request = async () => ({ processes: [], subagents: [] })
  globalThis.window = { hermesDesktop: { api: ({ path }) => {
    paths.push(path)
    if (path.includes('chat-old')) return slow.promise // the old chat's reads hang
    if (path.startsWith('/api/sessions/')) return Promise.resolve({ id: 'chat-new', parent_session_id: null })
    if (path.startsWith('/api/session-kanban-tasks')) return Promise.resolve({ tasks: [card('t_new', 'running')] })
    return Promise.resolve({ machines: [] })
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    const old = m.refresh()
    await new Promise(r => setTimeout(r, 0))
    focus.stored = 'chat-new'
    focus.runtime = 'rt-new'
    await m.refresh()
    assert.ok(paths.some(p => p.startsWith('/api/session-kanban-tasks') && p.includes('chat-new')), 'the new chat was read while the old round hung')
    assert.equal(window.__sessionTasks.snapshot.stored, 'chat-new')
    assert.deepEqual(window.__sessionTasks.snapshot.chips.map(c => c.id), ['t_new'])
    slow.resolve({ id: 'chat-old', parent_session_id: null })
    await old
    assert.equal(window.__sessionTasks.snapshot.stored, 'chat-new', 'the old round never overwrites the new chat')
    assert.deepEqual(window.__sessionTasks.snapshot.chips.map(c => c.id), ['t_new'])
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('the runtime id landing mid-round (resume) keeps the round\'s kanban answer', async () => {
  focus.stored = 'chat-resume'
  focus.runtime = null // not resumed yet
  const kb = deferred()
  let kanbanReads = 0
  host.request = async () => ({ processes: [], subagents: [] })
  globalThis.window = { hermesDesktop: { api: ({ path }) => {
    if (path.startsWith('/api/sessions/')) return Promise.resolve({ id: 'chat-resume', parent_session_id: null })
    if (path.startsWith('/api/session-kanban-tasks')) return (kanbanReads++, kb.promise)
    return Promise.resolve({ machines: [] })
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    const round = m.refresh()
    await new Promise(r => setTimeout(r, 0))
    focus.runtime = 'rt-resume' // resume finished while the kanban read is in flight
    void m.refresh() // the strip's runtime effect: same chat, queued behind this round
    kb.resolve({ tasks: [card('t_keep', 'review')] })
    await round
    for (let i = 0; i < 20 && window.__sessionTasks.snapshot.runtime !== 'rt-resume'; i++) await new Promise(r => setTimeout(r, 5))
    assert.deepEqual(window.__sessionTasks.snapshot.chips.filter(c => c.kind === 'kanban').map(c => c.id), ['t_keep'])
    assert.equal(window.__sessionTasks.snapshot.stored, 'chat-resume')
    assert.equal(kanbanReads, 1, 'the answer was used, not thrown away and read again')
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('a first visit never publishes the previous chat\'s cards under the new chat', async () => {
  m.rememberChips(scopeOf('chat-prev'), m.sessionTaskChips([card('t_prev', 'running')], { connectionId: 'conn', profile: 'default' }), [])
  focus.stored = 'chat-prev'
  const never = deferred()
  host.request = async () => ({ processes: [], subagents: [] })
  globalThis.window = { hermesDesktop: { api: () => never.promise } }
  const dispose = m.default.register({ register() {} })
  try {
    void m.refresh()
    await new Promise(r => setTimeout(r, 0))
    assert.deepEqual(window.__sessionTasks.snapshot.chips.map(c => c.id), ['t_prev'])
    focus.stored = 'chat-unseen'
    void m.refresh()
    for (let i = 0; i < 10; i++) await new Promise(r => setTimeout(r, 2))
    const snap = window.__sessionTasks.snapshot
    assert.equal(snap.stored, 'chat-unseen', 'the process/sub-agent answer published for the new chat')
    assert.deepEqual(snap.chips.filter(c => c.kind === 'kanban'), [], 'no leftover cards from chat-prev')
  } finally { never.resolve(null); dispose(); delete globalThis.window; delete host.request }
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
    note: 'Opened the PR; CI green', pr_url: 'https://github.com/o/r/pull/42', execution_observations: []
  })], { connectionId: 'conn', profile: 'default' })
  assert.deepEqual(m.cardRowModel(chip, now), {
    status: 'review', onHub: false, look: null, wait: '', title: 'Fix the flaky wake test', meta: 'worker · review requested 5m ago',
    note: 'Opened the PR; CI green', evidence: 'unknown (sdk projector unavailable) · progress unknown', prLabel: 'PR #42', prUrl: 'https://github.com/o/r/pull/42'
  })
  // An older producer (no execution_observations at all) cannot prove the review is current.
  const [legacy] = m.sessionTaskChips([card('t_row', 'review')], { connectionId: 'conn', profile: 'default' })
  assert.equal(m.cardRowModel(legacy, now).status, 'unknown')
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
  // A CI/deploy wait is light blue, neither grey nor amber (David 2026-11-23).
  assert.equal(w.look, 'delivery')
  assert.equal(m.cardRowModel(w, now).wait, `waiting on ci run 40577358595 since ${m.sinceClock(now / 1000 - 600, now)} (10m ago)`)
  assert.equal(m.chipColor(w.look), '#38bdf8', 'light blue')
  // A hub-deploy wait is purple, apart from CI/timer light blue (David 2026-11-24).
  assert.equal(m.waitLook({ wait_state: 'external_running', waiting_on: 'deploy hermes-hub mods-pull' }), 'deploy')
  assert.equal(m.waitLook({ wait_state: 'waiting', wait_kind: 'deploy', waiting_on: 'hub mods-pull' }), 'deploy')
  assert.equal(m.chipColor('deploy'), '#a371f7', 'purple')
  assert.equal(m.waitLook({ wait_state: 'stalled', waiting_on: 'deploy hermes-hub' }), 'stalled', 'a stalled deploy wait stays amber')
  assert.equal(m.waitLook({ wait_state: 'waiting', waiting_on: 'producer card t_abc (running)' }), 'waiting')
  assert.equal(m.waitLook({ wait_state: 'stalled', waiting_on: 'ci run 1' }), 'stalled', 'a stalled CI wait stays amber')
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

test('a hub-scheduled card shows [H] in its status box; a worker card does not', () => {
  const chips = m.sessionTaskChips([
    card('t_hub', 'running', { run_machine: 'hub' }), card('t_w', 'ready', { run_machine: 'worker-1' }),
    card('t_pool', 'todo', { run_machine: 'worker-pool' }), card('t_none', 'scheduled')
  ], { connectionId: 'conn', profile: 'default' })
  const by = Object.fromEntries(chips.map(c => [c.id, c]))
  assert.deepEqual(Object.fromEntries(chips.map(c => [c.id, [m.onHub(c), m.cardRowModel(c).onHub]])),
    { t_hub: [true, true], t_w: [false, false], t_pool: [false, false], t_none: [false, false] })
  assert.match(by.t_hub.title, /^t_hub · running · runs on hub/)
  assert.match(by.t_w.title, /^t_w · ready · runs on worker-1/)
  assert.match(by.t_pool.title, /runs on a pool worker/)
  assert.doesNotMatch(by.t_none.title, /runs on/)
})

test('a card carries its run model from the backend; none reads null', () => {
  const chips = m.sessionTaskChips([card('t_a', 'running', { run_model: 'class:deep' }), card('t_b', 'running')],
    { connectionId: 'conn', profile: 'default' })
  assert.deepEqual(chips.map(c => c.runModel), ['class:deep', null])
})


test('warm caches another chat\'s cards without touching the focused snapshot; a failed read caches nothing', async () => {
  focus.stored = 'chat-focused'
  host.request = async () => ({})
  let fail = false
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/sessions/')) return { id: 'chat-bg', parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) { if (fail) throw new Error('504'); return { tasks: [card('t_bg', 'running')] } }
    return null
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    const before = window.__sessionTasks.snapshot
    assert.equal(await window.__sessionTasks.warm({ connectionId: 'conn', profile: 'default', session: 'chat-bg' }), true)
    assert.equal(window.__sessionTasks.snapshot, before, 'focused snapshot untouched')
    assert.deepEqual(m.cachedChips(scopeOf('chat-bg')).kanban.map(c => c.id), ['t_bg'])
    fail = true
    assert.equal(await m.warm({ connectionId: 'conn', profile: 'default', session: 'chat-bg' }), false)
    assert.deepEqual(m.cachedChips(scopeOf('chat-bg')).kanban.map(c => c.id), ['t_bg'], 'last good copy kept, no warning chip cached')
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('a hub-sync replica card is explicit unknown with a replica reason, aged from its source activity, never synced_at', () => {
  const now = Date.now() / 1000
  // Exact hubview.session_links row shape: a current producer that carries no execution_observations.
  const row = { id: 't_remote', title: 'remote', status: 'running', reported_status: 'running', profile: 'default',
    ownership_state: 'qualified', board: 'default', session_id: 's', via: 'created', gateway: 'hub-ec2', local: false,
    stale: false, synced_at: now - 5, activity_at: now - 300 }
  const [chip] = m.hubLinkChips([row])
  assert.equal(chip.replica, true)
  assert.equal(chip.observations, null)
  assert.equal(m.cardRowModel(chip).status, 'unknown')
  const v = m.unobservedView(chip, now)
  assert.deepEqual([v.state, v.reason, v.lastKnown, v.sourced], ['unknown', m.REPLICA_REASON, 'running', false])
  assert.ok(Math.abs(v.ageSec - 300) < 1, 'age from activity_at (source clock), not synced_at (copy time)')
  assert.equal(m.unobservedView({ ...chip, activityAt: 0 }, now).ageSec, null, 'no source clock: no age')
  // A live-route row from an older producer keeps the legacy reason.
  const [legacy] = m.sessionTaskChips([{ ...row, local: true, gateway: '' }])
  assert.equal(legacy.replica, undefined)
  assert.equal(m.unobservedView(legacy, now).reason, 'producer_sent_no_observations')
})

// Exercise the actual refresh/RPC/REST seam, not a fabricated outcome or task count.
test('idle source failures publish qualified completion vetoes and authoritative recovery clears each one', async () => {
  focus.stored = 'chat-failclosed'
  const reports = []
  host.sessions = { reportWorkSourceRead: r => reports.push(r) }
  let failed = true
  host.request = async () => {
    if (failed) throw new Error('request timed out')
    return { processes: [], subagents: [] }
  }
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/sessions/')) return { id: focus.stored, parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) {
      if (failed) throw new Error('authoritative tasks unavailable')
      return { tasks: [] }
    }
    return null
  } } }
  const dispose = m.default.register({ register() {} })
  const clock = Date.now
  try {
    await m.refresh()
    for (const source of ['tasks', 'process', 'subagent']) {
      assert.ok(reports.some(r => r.source === source && r.available === false), `${source} failure reaches core`)
    }
    assert.ok(reports.every(r => r.connectionId === 'conn' && r.profile === 'default' && r.storedSessionId === 'chat-failclosed'))
    assert.ok(window.__sessionTasks.snapshot.chips.some(c => c.label === 'Task status unavailable'))
    failed = false
    Date.now = () => clock() + 11000
    await m.refresh()
    for (const source of ['tasks', 'process', 'subagent', 'lineage']) {
      assert.equal(reports.filter(r => r.source === source).at(-1)?.available, true, `${source} recovers only after a successful read`)
    }
    assert.deepEqual(window.__sessionTasks.snapshot.chips, [])
  } finally { Date.now = clock; dispose(); delete host.sessions; delete host.request; delete globalThis.window }
})

test('a malformed RPC response is unknown, never an authoritative empty source', async () => {
  focus.stored = 'chat-malformed-rpc'
  const reports = []
  host.sessions = { reportWorkSourceRead: r => reports.push(r) }
  host.request = async () => ({})
  globalThis.window = { hermesDesktop: { api: async ({ path }) => path.startsWith('/api/sessions/')
    ? { id: focus.stored, parent_session_id: null } : { tasks: [], machines: [] } } }
  const dispose = m.default.register({ register() {} })
  try {
    await m.refresh()
    assert.equal(reports.find(r => r.source === 'process')?.available, false)
    assert.equal(reports.find(r => r.source === 'subagent')?.available, false)
    assert.ok(window.__sessionTasks.snapshot.chips.some(c => c.label === 'Process status unknown'))
  } finally { dispose(); delete host.sessions; delete host.request; delete globalThis.window }
})

// [daveey 2026-11-20] the panel judges the switch's cached cards as of `cachedAt` (when they were read) until this
// chat's kanban read answers: snapshots carry it only while the cards on screen are that cached copy.
test('a switch carries cachedAt (the kanban read time) until the fresh kanban read lands', async () => {
  const chips = m.sessionTaskChips([card('t_c')], { connectionId: 'conn', profile: 'default' })
  m.rememberChips(scopeOf('chat-at'), chips, [])
  const readAt = m.cachedChips(scopeOf('chat-at')).kanbanAt
  assert.ok(readAt > 0)
  focus.stored = 'chat-at'
  const kanban = deferred()
  host.request = async () => ({ processes: [], subagents: [] })
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/sessions/')) return { id: 'chat-at', parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) return kanban.promise
    return { machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    const round = m.refresh()
    for (let i = 0; i < 20 && !window.__sessionTasks.snapshot.fresh; i++) await new Promise(r => setTimeout(r, 5))
    assert.equal(window.__sessionTasks.snapshot.fresh, true, 'process/sub-agent answers published')
    assert.equal(window.__sessionTasks.snapshot.cachedAt, readAt, 'kanban cards are still the cached copy')
    const tagged = window.__sessionTasks.snapshot.chips.find(c => c.id === 't_c')
    assert.equal(tagged.cachedAt, readAt, 'each cached kanban card carries its read time')
    assert.ok(tagged.paintedAt >= readAt)
    assert.ok(window.__sessionTasks.snapshot.chips.filter(c => c.kind !== 'kanban').every(c => !('cachedAt' in c)), 'live chips are never tagged')
    assert.deepEqual(window.__sessionTasks.cachedSnapshot(scopeOf('chat-at'), 'chat-at').chips.map(c => c.id), ['t_c'])
    kanban.resolve({ tasks: [card('t_c')] })
    await round
    assert.equal(window.__sessionTasks.snapshot.cachedAt, undefined, 'the fresh read is judged at now')
    assert.ok(window.__sessionTasks.snapshot.chips.every(c => !('cachedAt' in c)), 'fresh cards are untagged')
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('a failed kanban read drops cachedAt (the cached cards are then judged at now: unknown)', async () => {
  m.rememberChips(scopeOf('chat-fail'), m.sessionTaskChips([card('t_f')], { connectionId: 'conn', profile: 'default' }), [])
  focus.stored = 'chat-fail'
  host.request = async () => ({ processes: [], subagents: [] })
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/sessions/')) return { id: 'chat-fail', parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) throw new Error('hub down')
    return { machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    await m.refresh()
    assert.equal(window.__sessionTasks.snapshot.cachedAt, undefined)
    const kept = window.__sessionTasks.snapshot.chips.filter(c => c.kind === 'kanban')
    assert.ok(kept.every(c => !('cachedAt' in c)), 'the kept cached cards are untagged: judged at now')
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('a machines-only refresh does not make old kanban evidence look newly read', async () => {
  const realNow = Date.now
  try {
    Date.now = () => 1_000_000
    m.rememberChips(scopeOf('chat-m'), [card('t_m')], [])
    Date.now = () => 2_000_000
    m.rememberChips(scopeOf('chat-m'), undefined, [])
    assert.equal(m.cachedChips(scopeOf('chat-m')).kanbanAt, 1_000_000)
  } finally { Date.now = realNow }
})

// [daveey 2026-11-20] session-panel-feed: the gateway pushes each watched chat's cards; session-tasks takes them as
// that chat's current answer and, while the feed is live, does not read the kanban list on a switch.
test('a pushed bundle becomes the chat\'s feed-kept copy and its ancestry', () => {
  m.ingestFeed({ connectionId: 'conn', profile: 'default', session: 'chat-feed', lineage: ['chat-feed', 'chat-old'], tasks: [card('t_p1')] })
  const c = m.cachedChips(scopeOf('chat-feed'))
  assert.deepEqual(c.kanban.map(x => x.id), ['t_p1'])
  assert.ok(c.feedAt > 0, 'marked as from the feed')
  m.rememberChips(scopeOf('chat-feed'), undefined, [])
  assert.ok(m.cachedChips(scopeOf('chat-feed')).feedAt, 'a machines-only refresh keeps it')
  m.rememberChips(scopeOf('chat-feed'), [card('t_p1')], undefined)
  assert.equal(m.cachedChips(scopeOf('chat-feed')).feedAt, undefined, 'a kanban read of our own replaces it')
})

test('a switch to a feed-kept chat paints it untagged and reads no kanban list while the feed is live', async () => {
  m.ingestFeed({ connectionId: 'conn', profile: 'default', session: 'chat-live', lineage: ['chat-live'], tasks: [card('t_l1')] })
  assert.equal(m.feedFresh(scopeOf('chat-live')), true)
  focus.stored = 'chat-live'
  const paths = []
  host.request = async () => ({ processes: [], subagents: [] })
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    paths.push(path)
    if (path.startsWith('/api/sessions/')) return { id: 'chat-live', parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [card('t_l1')] }
    return { machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    await m.refresh()
    const snap = window.__sessionTasks.snapshot
    assert.deepEqual(snap.chips.filter(c => c.kind === 'kanban').map(c => c.id), ['t_l1'])
    assert.equal(snap.cachedAt, undefined, 'current, not a cached copy: no "updating…"')
    assert.ok(snap.chips.every(c => !('cachedAt' in c)))
    assert.ok(!paths.some(p => p.startsWith('/api/session-kanban-tasks')), 'no kanban read: the feed keeps it')
    // A push for the focused chat republishes at once.
    m.ingestFeed({ connectionId: 'conn', profile: 'default', session: 'chat-live', lineage: ['chat-live'], tasks: [card('t_l1'), card('t_l2', 'review')] })
    assert.deepEqual(window.__sessionTasks.snapshot.chips.filter(c => c.kind === 'kanban').map(c => c.id), ['t_l1', 't_l2'])
    assert.equal(window.__sessionTasks.snapshot.fresh, true)
  } finally { dispose(); delete globalThis.window; delete host.request }
})

test('when the feed is not live a feed-kept chat is read as before', async () => {
  const realNow = Date.now
  Date.now = () => realNow() - m.FEED_STALE_MS - 1
  try { m.ingestFeed({ connectionId: 'conn', profile: 'default', session: 'chat-dead', lineage: ['chat-dead'], tasks: [card('t_d1')] }) } finally { Date.now = realNow }
  assert.equal(m.feedFresh(scopeOf('chat-dead')), false, 'no push re-stamped it')
  focus.stored = 'chat-dead'
  const paths = []
  host.request = async () => ({ processes: [], subagents: [] })
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    paths.push(path)
    if (path.startsWith('/api/sessions/')) return { id: 'chat-dead', parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [card('t_d1')] }
    return { machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    await m.refresh()
    assert.ok(paths.some(p => p.startsWith('/api/session-kanban-tasks')), 'stale feed: read it ourselves')
  } finally { dispose(); delete globalThis.window; delete host.request }
})


test('pushes re-stamp feed-kept chats; an untrusted bundle is an ordinary cached copy', () => {
  const realNow = Date.now
  try {
    Date.now = () => 1_000_000
    m.ingestFeed({ connectionId: 'conn', profile: 'default', session: 'chat-t', lineage: ['chat-t'], tasks: [card('t_t1')] })
    m.ingestFeed({ connectionId: 'conn', profile: 'default', session: 'chat-u', lineage: ['chat-u'], tasks: [card('t_u1')], trusted: false })
    Date.now = () => 1_000_000 + m.FEED_STALE_MS + 10
    assert.equal(m.feedFresh(scopeOf('chat-t')), false)
    assert.equal(m.touchFeed('conn', 'default', ['chat-t', 'chat-u'], Date.now()), 1, 'only the feed-kept chat is re-stamped')
    assert.equal(m.feedFresh(scopeOf('chat-t')), true)
    assert.equal(m.feedFresh(scopeOf('chat-u')), false, 'untrusted: never current')
    assert.deepEqual(m.cachedChips(scopeOf('chat-u')).kanban.map(c => c.id), ['t_u1'], 'still a prepaint copy')
  } finally { Date.now = realNow }
})


test('the 60 s safety-net read of a feed-kept chat keeps it feed-kept', async () => {
  m.ingestFeed({ connectionId: 'conn', profile: 'default', session: 'chat-net', lineage: ['chat-net'], tasks: [card('t_n1')] })
  focus.stored = 'chat-net'
  host.request = async () => ({ processes: [], subagents: [] })
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/sessions/')) return { id: 'chat-net', parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [card('t_n1')] }
    return { machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  const realNow = Date.now
  try {
    await m.refresh()                       // switch: no kanban read (feed-kept)
    const t0 = realNow()
    m.touchFeed('conn', 'default', ['chat-net'], t0 + m.FEED_KANBAN_POLL_MS + 5)
    Date.now = () => t0 + m.FEED_KANBAN_POLL_MS + 10
    await m.refresh()                       // the safety-net read runs now
    assert.ok(m.cachedChips(scopeOf('chat-net')).feedAt, 'still feed-kept after its own read')
    assert.equal(m.feedFresh(scopeOf('chat-net')), true)
  } finally { Date.now = realNow; dispose(); delete globalThis.window; delete host.request }
})
