// node --test: pure projections only (the SDK import is stubbed by the loader below).
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')

// Successful reader fixtures need a resolved route; undefined now means unknown.
beforeEach(async () => {
  const { host } = await import('@hermes/plugin-sdk')
  host.state = { connectionId: { get: () => 'fixture-connection' } }
})

test('local read captures A/work route and board through later focus changes', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  let owner = { connectionId: 'A', profile: 'work' }
  host.state = { connectionId: { get: () => 'B' }, focusedSessionOwner: { get: () => owner } }
  const calls = []
  const open = (...args) => calls.push(args)
  open.supportsQualifiedOwners = true
  globalThis.window = { __hermesKanbanOpenTask: open, __taskMentionsOpen: () => assert.fail('bare route'), hermesDesktop: { api: async ({ path, connectionId, profile }) => {
    assert.equal(connectionId, 'A'); assert.equal(profile, 'work')
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [{ id: 't_collision', status: 'running', board: 'special' }] }
    return { available: false }
  } } }
  try {
    const [chip] = await m.loadKanban(new Set(['local-route']))
    assert.equal(chip.connectionId, 'A')
    assert.equal(chip.profile, 'work')
    owner = { connectionId: 'B', profile: 'default' }
    m.openTaskChip(chip)
    assert.deepEqual(calls, [['t_collision', 'special', 'A', 'work']])
  } finally { delete globalThis.window }
})

test('authoritative release wins over replica and omitted profile is never default', async () => {
  const unknown = m.hubLinkChips([{ id: 't_old', gateway: 'remote', status: 'running' }])[0]
  assert.equal(unknown.profile, null)
  globalThis.window = { __taskMentionsOpen: () => assert.fail('bare route'), hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [] }
    return { tasks: [{ id: 't_old', gateway: 'remote', status: 'running' }] }
  } } }
  try {
    assert.equal(m.openTaskChip(unknown), false)
    assert.deepEqual(await m.loadKanban(new Set(['released'])), [])
  } finally { delete globalThis.window }
})

test('fallback qualifies the origin profile; old replica rows stay nonclickable; outages retain uncertainty', async () => {
  let qualified = true
  let available = true
  const route = { connectionId: 'fallback-A', profile: 'work' }
  globalThis.localStorage = { getItem: () => JSON.stringify({ R: 'remote', S: 'second' }) }
  globalThis.window = { hermesDesktop: { api: async ({ path, connectionId, profile }) => {
    assert.equal(connectionId, route.connectionId); assert.equal(profile, route.profile)
    if (!path.includes('session-links')) throw new Error('unavailable authoritative/board')
    assert.match(path, /origin_profile=work/)
    if (!available) return { available: false }
    return { qualified_origins: qualified, origin_profile: 'work', tasks: [
      { id: 'same', gateway: 'remote', profile: 'work', board: 'bb', status: 'running' },
      { id: 'same', gateway: 'remote', profile: 'default', board: 'bb', status: 'running' },
      { id: 'same', gateway: 'second', profile: 'work', board: 'bb', status: 'running' },
      { id: 'legacy', gateway: 'remote', board: 'bb', status: 'unknown' }
    ] }
  } } }
  try {
    const ids = new Set(['fallback-alias', 'fallback-tip'])
    const chips = (await m.loadKanban(ids, route)).filter(c => c.kind === 'kanban')
    assert.equal(chips.length, 4)
    assert.equal(new Set(chips.map(c => c.key)).size, 4)
    assert.deepEqual(chips.slice(0, 3).map(c => c.connectionId), ['R', 'R', 'S'])
    assert.equal(m.openTaskChip(chips[3]), false)
    available = false
    const stale = (await m.loadKanban(ids, route)).filter(c => c.kind === 'kanban')
    assert.equal(stale.length, 4); assert.ok(stale.every(c => c.stale))
    route.connectionId = 'fallback-B'
    assert.equal((await m.loadKanban(ids, route)).filter(c => c.kind === 'kanban').length, 0)
    available = true; qualified = false
    const legacy = (await m.loadKanban(new Set(['legacy-api']), route)).filter(c => c.kind === 'kanban')
    assert.ok(legacy.every(c => c.profile === null && c.state === 'unknown'))
  } finally { delete globalThis.window; delete globalThis.localStorage }
})

test('released authoritative set survives outage and intervening owner switch', async () => {
  let online = true
  const row = { id: 'released-task', gateway: 'remote', profile: 'work', board: 'bb', status: 'running' }
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/session-kanban-tasks') && online) return { tasks: [] }
    if (path.includes('session-links')) return { tasks: [row] }
    if (path.includes('/board')) return { columns: [{ tasks: [{ ...row, session_id: 'release-outage' }] }] }
    throw new Error('offline')
  } } }
  try {
    const ids = new Set(['release-outage'])
    const route = { connectionId: 'release-A', profile: 'work' }
    assert.deepEqual(await m.loadKanban(ids, route), [])
    await m.loadKanban(ids, { connectionId: 'release-B', profile: 'default' })
    online = false
    const chips = await m.loadKanban(ids, route)
    assert.deepEqual(chips.map(c => c.kind), ['kanban-status'])
  } finally { delete globalThis.window }
})

test('local colliding IDs remain distinct by captured connection/profile/board', () => {
  const row = { id: 'same', status: 'running', board: 'bb' }
  const a = m.sessionTaskChips([row], { connectionId: 'A', profile: 'work' })[0]
  const b = m.sessionTaskChips([row], { connectionId: 'B', profile: 'default' })[0]
  const d = m.sessionTaskChips([row], { connectionId: 'A', profile: 'default' })[0]
  assert.equal(new Set([a.key, b.key, d.key]).size, 3)
  const unknown = m.sessionTaskChips([{ ...row, profile: null }], { connectionId: 'A', profile: 'work' })[0]
  assert.equal(unknown.profile, null)
  assert.equal(unknown.connectionId, null)
  assert.equal(m.openTaskChip(unknown), false)
})

test('processChips keeps only live processes, first command line', () => {
  const chips = m.processChips([
    { session_id: 'p1', command: 'npm run dev\n--watch', status: 'running', uptime_seconds: 90 },
    { session_id: 'p2', command: 'make', status: 'exited', exit_code: 0 }
  ])
  assert.equal(chips.length, 1)
  assert.equal(chips[0].label, 'npm run dev')
  assert.equal(m.elapsed(chips[0].since), '1m')
})

test('subagentChips drops terminal statuses', () => {
  const chips = m.subagentChips([
    { subagent_id: 'a', goal: 'Research X', status: 'running' },
    { subagent_id: 'b', goal: 'Done', status: 'completed' },
    { subagent_id: 'c', goal: 'Bad', status: 'failed' }
  ])
  assert.deepEqual(chips.map(c => c.key), ['sub:a'])
})

test('kanbanChips filters by session lineage and active status, running first', () => {
  const board = { columns: [
    { name: 'ready', tasks: [{ id: 't_1', title: 'queued', status: 'ready', session_id: 's_root' }] },
    { name: 'running', tasks: [
      { id: 't_2', title: 'mine', status: 'running', session_id: 's_tip' },
      { id: 't_3', title: 'other session', status: 'running', session_id: 's_other' }
    ] },
    { name: 'done', tasks: [{ id: 't_4', title: 'finished', status: 'done', session_id: 's_tip' }] }
  ] }
  const chips = m.kanbanChips(board, new Set(['s_tip', 's_root']))
  assert.deepEqual(chips.map(c => c.id), ['t_2', 't_1'])
})

test('triage remains visible locally, through lineage fallback, and on remote gateways', () => {
  for (const block_kind of ['transient', null, 'needs_input', 'capability']) {
    const task = { id: 't_triage', status: 'triage', block_kind, session_id: 's_root' }
    const local = m.sessionTaskChips([task])
    assert.equal(local.length, 1)
    assert.equal(local[0].state, 'triage')
    assert.match(local[0].title, /triage/)
    assert.doesNotMatch(local[0].title, /needs input|waiting on you/i)
    assert.equal(m.kanbanChips({ columns: [{ name: 'triage', tasks: [task] }] }, new Set(['s_leaf', 's_root'])).length, 1)
    assert.equal(m.hubLinkChips([{ ...task, gateway: 'remote', local: false }]).length, 1)
  }
  assert.equal(m.chipColor('triage'), '#f85149')
})

test('elapsed formats', () => {
  const now = 1_000_000_000
  assert.equal(m.elapsed(now / 1000 - 5, now), '5s')
  assert.equal(m.elapsed(now / 1000 - 3700, now), '1h01')
  assert.equal(m.elapsed(0, now), '')
})

test('sessionTaskChips: every listed status, shepherded cards marked, running first, deduped', () => {
  const chips = m.sessionTaskChips([
    { id: 't_b', title: 'theirs', status: 'blocked', via: 'subscribed', board: 'default' },
    { id: 't_q', title: 'queued', status: 'todo', via: 'created', board: 'bb' },
    { id: 't_r', title: 'mine', status: 'running', via: 'created', current_run_started_at: 100 },
    { id: 't_r', title: 'mine (dup from another board)', status: 'running' },
    { id: 't_d', title: 'finished', status: 'done' }
  ])
  assert.deepEqual(chips.map(c => `${c.id}:${c.state}`), ['t_r:running', 't_b:blocked', 't_q:todo'])
  assert.match(chips[1].title, /shepherding/)
  assert.match(chips[2].title, /board bb/)
  assert.equal(chips[0].since, 100)
})

test('machineChips keeps machines bound to the session lineage on this gateway/profile', () => {
  const own = (session_id, extra = {}) => [{ gateway_id: 'gw', profile: 'default', session_id, ...extra }]
  const machines = [
    { key: 'k1', provider: 'vast', display_name: 'box-a', status: 'stopped', ownership: own('s_root'), hourly_rate: '0.4', currency: 'USD' },
    { key: 'k2', provider: 'vast', display_name: 'box-b', status: 'running', ownership: own('s_tip'), hourly_rate: '1.234', currency: 'USD', started_at: new Date(Date.now() - 120_000).toISOString(), hardware: '1 × H100' },
    { key: 'k3', provider: 'ec2', display_name: 'other', status: 'running', ownership: own('s_other') },
    { key: 'k4', provider: 'ec2', display_name: 'other-gw', status: 'running', ownership: [{ gateway_id: 'gw2', profile: 'default', session_id: 's_tip' }] },
    { key: 'k5', provider: 'ec2', display_name: 'other-profile', status: 'running', ownership: own('s_tip', { profile: 'work' }) },
    { key: 'k6', provider: 'slurm', display_name: 'node', status: 'running', ownership: [], hourly_rate: null }
  ]
  const chips = m.machineChips(machines, { gatewayId: 'gw', profile: 'default', ids: new Set(['s_tip', 's_root']) })
  assert.deepEqual(chips.map(c => c.id), ['k2', 'k1'])
  assert.equal(chips[0].extra, '$1.23/h')
  assert.equal(chips[0].state, 'running')
  assert.equal(m.elapsed(chips[0].since), '2m')
  assert.match(chips[0].title, /1 × H100/)
  assert.equal(chips[1].state, 'stopped')
  assert.equal(chips[1].since, 0)
  assert.deepEqual(m.machineChips(machines, { gatewayId: '', profile: 'default', ids: ['s_tip'] }), [])
})

test('machineArchiveLines summarizes machine chips for the archive confirm', () => {
  assert.deepEqual(m.machineArchiveLines([]), [])
  assert.deepEqual(m.machineArchiveLines([{ kind: 'kanban', label: 'x' }]), [])
  assert.deepEqual(m.machineArchiveLines([{ kind: 'machine', label: 'vast-4090', state: 'running', extra: '$0.40/h' }]), [
    { label: 'Rented machine', value: 'vast-4090 (running, $0.40/h)' }
  ])
  const many = ['a', 'b', 'c', 'd', 'e'].map(n => ({ kind: 'machine', label: n, state: 'stopped', extra: '' }))
  assert.deepEqual(m.machineArchiveLines(many), [
    { label: 'Rented machines (5)', value: 'a (stopped), b (stopped), c (stopped) +2 more' }
  ])
})

test('machineChips + machineArchiveLines match ownership across the lineage only', () => {
  const machines = [
    { key: 'k1', display_name: 'box-1', status: 'running', currency: 'USD', hourly_rate: '0.4', ownership: [{ gateway_id: 'g', profile: 'default', session_id: 's_root' }] },
    { key: 'k2', display_name: 'box-2', status: 'running', ownership: [{ gateway_id: 'g', profile: 'default', session_id: 's_other' }] }
  ]
  const chips = m.machineChips(machines, { gatewayId: 'g', profile: 'default', ids: new Set(['s_tip', 's_root']) })
  assert.deepEqual(m.machineArchiveLines(chips), [{ label: 'Rented machine', value: 'box-1 (running, $0.40/h)' }])
})

test('scheduled (timer-blocked) chips are yellow, pill included', () => {
  assert.equal(m.chipColor('scheduled'), '#e3b341')
  const f = m.chipFrame('scheduled')
  assert.match(f.border, /#e3b341/)
  assert.match(f.background, /#e3b341/)
  // other states keep the neutral frame
  assert.doesNotMatch(m.chipFrame('todo').border, /#e3b341/)
  assert.equal(m.chipColor('blocked'), '#f85149')
})

test('hubLinkChips: other gateways only, deduped by gateway+board+id, gateway + sync age in the title', () => {
  const now = Date.now() / 1000
  const chips = m.hubLinkChips(
    [
      { id: 't_here', title: 'Mine', status: 'running', gateway: 'laptop', local: true, synced_at: now },
      { id: 't_live', title: 'Dup', status: 'running', gateway: 'mini', board: 'bb', local: false, synced_at: now },
      { id: 't_live', title: 'Same id, other gateway', status: 'running', gateway: 'nas', local: false, synced_at: now },
      { id: 't_mini', title: 'Remote', status: 'blocked', gateway: 'mini', local: false, synced_at: now - 300, via: 'subscribed' },
      { id: 't_mini', title: 'Remote dup', status: 'blocked', gateway: 'mini', board: 'default', local: false, synced_at: now },
      { id: 't_done', title: 'Old', status: 'done', gateway: 'mini', local: false, synced_at: now }
    ],
    new Set([m.taskKey('mini', 'bb', 't_live')])
  )
  assert.deepEqual(chips.map(c => c.key), ['kb:nas:?:default:t_live', 'kb:mini:?:default:t_mini'])
  const mini = chips[1]
  assert.equal(mini.id, 't_mini')
  assert.equal(mini.gateway, 'mini')
  assert.equal(mini.state, 'blocked')
  assert.match(mini.title, /on mini/)
  assert.match(mini.title, /synced 5m ago/)
  assert.match(mini.title, /shepherding/)
})

test('staleChips keeps last-known remote rows, marked stale (hub down never means no tasks)', () => {
  const out = m.staleChips([{ key: 'kb:mini:default:t1', title: 'x' }])
  assert.equal(out.length, 1)
  assert.equal(out[0].stale, true)
  assert.match(out[0].title, /last known/)
  assert.equal(m.staleChips(out)[0].title, out[0].title)
})

test('hubLinkChips tolerates a missing/unavailable hub answer', () => {
  assert.deepEqual(m.hubLinkChips(null, new Set()), [])
  assert.deepEqual(m.hubLinkChips(undefined), [])
})

test('sessionTaskChips preserves qualified owners and stale state without collapsing ids', () => {
  const task = { id: 't_same', title: 'Work', status: 'running', gateway: 'remote', profile: 'default', board: 'default', stale: true }
  const chips = m.sessionTaskChips([
    task, { ...task }, { ...task, gateway: 'other' },
    { ...task, board: 'other' }, { ...task, profile: 'work' }
  ])
  assert.equal(chips.length, 4)
  assert.equal(new Set(chips.map(c => c.key)).size, 4)
  assert.deepEqual(chips.map(c => [c.gateway, c.profile, c.board, c.stale]), [
    ['remote', 'default', 'default', true], ['other', 'default', 'default', true],
    ['remote', 'default', 'other', true], ['remote', 'work', 'default', true]
  ])
  assert.match(chips[0].title, /stale/)
})

test('hub-only unknown obligations remain visible with unknown owner profile', () => {
  const chips = m.hubLinkChips([{ id: 't_legacy', gateway: 'remote', profile: null, status: 'unknown', stale: true }])
  assert.equal(chips.length, 1)
  assert.equal(chips[0].state, 'unknown')
  assert.equal(chips[0].profile, null)
})

test('unknown ledger obligations stay visible, including unreleased terminal reports', () => {
  const rows = [
    { id: 't_unknown', status: 'unknown', unfinished: true, stale: true },
    { id: 't_unverified', status: 'done', unfinished: true },
    { id: 't_released', status: 'done', unfinished: false }
  ]
  assert.deepEqual(m.sessionTaskChips(rows).map(c => c.id), ['t_unknown', 't_unverified'])
  assert.equal(m.sessionTaskChips(rows)[1].state, 'unknown')
})

test('unknown profile is not silently assigned to default ownership', () => {
  const row = { id: 't_same', status: 'unknown', gateway: 'remote', profile: null, board: 'default' }
  const chips = m.sessionTaskChips([row, { ...row, profile: 'default' }])
  assert.equal(chips.length, 2)
  assert.equal(chips[0].profile, null)
})

test('loadKanban preserves authoritative owners without adding replica rows', async () => {
  const row = { id: 't_same', status: 'running', gateway: 'remote', profile: 'work', board: 'default' }
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [row, row, { ...row, profile: 'default' }] }
    if (path.includes('session-links')) return { gateway: 'origin', tasks: [row, { ...row, profile: 'other' }] }
    throw new Error('unexpected route')
  } } }
  try {
    const chips = await m.loadKanban(new Set(['s_one']))
    assert.equal(chips.length, 2)
    assert.equal(new Set(chips.map(c => c.key)).size, 2)
  } finally { delete globalThis.window }
})

test('failed authoritative reads dedupe retained remote rows against hub rows', async () => {
  const row = { id: 't_retained_remote', status: 'running', gateway: 'remote', profile: 'work', board: 'default' }
  let online = true
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/session-kanban-tasks')) {
      if (online) return { tasks: [row] }
      throw new Error('offline ledger')
    }
    if (path.includes('session-links')) return { gateway: 'origin', tasks: [row, { ...row, profile: 'other' }] }
    return { columns: [] }
  } } }
  try {
    const ids = new Set(['s_retained_remote'])
    await m.loadKanban(ids)
    online = false
    const chips = await m.loadKanban(ids)
    assert.equal(chips.filter(c => c.id === row.id).length, 1)
    assert.equal(chips.some(c => c.kind === 'kanban-status'), true)
  } finally { delete globalThis.window }
})

test('hub fallback cache cannot leak between profiles with the same session id', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  let profile = 'work'
  let online = true
  host.state = { ...host.state, profile: { get: () => profile } }
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/session-kanban-tasks')) throw new Error('legacy backend')
    if (online && path.includes('session-links')) return { gateway: 'origin', qualified_origins: true, origin_profile: profile, tasks: [
      { id: 't_work', status: 'running', gateway: 'remote', profile: 'work' }
    ] }
    throw new Error('offline')
  } } }
  try {
    const ids = new Set(['s_collision'])
    assert.equal((await m.loadKanban(ids)).filter(c => c.kind === 'kanban').length, 1)
    online = false
    assert.equal((await m.loadKanban(ids))[0].stale, true)
    profile = 'other'
    assert.deepEqual((await m.loadKanban(ids)).filter(c => c.kind === 'kanban'), [])
  } finally { delete globalThis.window; delete host.state }
})

test('failed authoritative task read stays unknown even when legacy board succeeds empty', async () => {
  let online = true
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/session-kanban-tasks')) {
      if (online) return { tasks: [{ id: 't_retained', status: 'running', gateway: 'owner', profile: 'work' }] }
      throw new Error('unreadable ledger')
    }
    if (path.includes('session-links')) return { gateway: 'origin', tasks: [] }
    return { columns: [] }
  } } }
  try {
    const ids = new Set(['s_read_failure'])
    assert.equal((await m.loadKanban(ids)).length, 1)
    online = false
    const warm = await m.loadKanban(ids)
    assert.equal(warm.find(c => c.id === 't_retained')?.stale, true)
    assert.equal(warm.some(c => c.state === 'unknown'), true)
    const cold = await m.loadKanban(new Set(['s_cold_failure']))
    assert.equal(cold.length, 1)
    assert.equal(cold[0].state, 'unknown')
    assert.equal(cold[0].kind, 'kanban-status')
    online = true
    const recovered = await m.loadKanban(ids)
    assert.deepEqual(recovered.map(c => c.id), ['t_retained'])
    assert.equal(recovered.some(c => c.kind === 'kanban-status'), false)
  } finally { delete globalThis.window }
})

test('explicit unresolved task and archive routes never re-resolve the active connection', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  host.state = { connectionId: { get: () => 'unrelated-active' } }
  const calls = []
  globalThis.window = { hermesDesktop: { api: async args => {
    calls.push(args)
    if (args.path.startsWith('/api/sessions/')) return { id: 's_unknown_route' }
    return { tasks: [], machines: [] }
  } } }
  try {
    for (const connectionId of [undefined, null, '']) {
      const chips = await m.loadKanban(new Set(['s_unknown_route']), { connectionId, profile: 'work' })
      assert.ok(chips.some(c => c.state === 'unknown'), 'unresolved tasks must not look empty')
      const warnings = await m.archiveGuard({ sessionId: 's_unknown_route', connectionId, profile: 'work' })
      assert.ok(warnings.some(line => /unavailable/i.test(line.value)))
    }
    assert.deepEqual(calls, [], 'no bridge call may inherit an active route')
  } finally { delete globalThis.window; delete host.state }
})

test('unresolved primary owner stays unknown without querying the active connection', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  let connectionId
  const calls = []
  host.state = {
    focusedSessionId: { get: () => 'primary-runtime' },
    focusedStoredSessionId: { get: () => 'primary-stored' },
    focusedSessionOwner: { get: () => ({ connectionId, profile: 'work' }) },
    connectionId: { get: () => 'unrelated-active' }
  }
  host.request = async (...args) => { calls.push(args); return {} }
  globalThis.document = { hidden: false, getElementById: () => ({ remove() {} }), addEventListener() {}, removeEventListener() {} }
  globalThis.window = { hermesDesktop: { api: async args => {
    calls.push(args)
    if (args.path.startsWith('/api/sessions/')) return { id: 'primary-stored' }
    return { tasks: [], machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    await m.refresh()
    assert.deepEqual(calls, [], 'unresolved primary must not use ambient REST or RPC')
    assert.ok(window.__sessionTasks.snapshot.chips.some(c => c.state === 'unknown'))
    connectionId = 'resolved-primary'
    await m.refresh()
    assert.equal(window.__sessionTasks.snapshot.chips.some(c => c.state === 'unknown'), false)
    assert.ok(calls.some(c => c.connectionId === 'resolved-primary'))
  } finally { dispose(); delete globalThis.window; delete globalThis.document; delete host.state; delete host.request }
})

test('poller reloads the same stored id when its owner profile changes', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  let profile = 'work'
  host.state = {
    focusedSessionId: { get: () => 'runtime-same' },
    focusedStoredSessionId: { get: () => 'stored-same' },
    focusedSessionOwner: { get: () => ({ connectionId: 'owner', profile }) }
  }
  host.request = async () => ({})
  const calls = []
  globalThis.window = { hermesDesktop: { api: async args => {
    calls.push(args)
    if (args.path.startsWith('/api/sessions/')) return { id: 'stored-same' }
    if (args.path.startsWith('/api/session-kanban-tasks')) return { tasks: [] }
    if (args.path.includes('session-links')) return { gateway: 'origin', tasks: [] }
    return { gateway_id: 'owner', machines: [] }
  } } }
  try {
    await m.refresh()
    profile = 'other'
    await m.refresh()
    const taskReads = calls.filter(c => c.path.startsWith('/api/session-kanban-tasks'))
    assert.deepEqual(taskReads.map(c => [c.connectionId, c.profile]), [['owner', 'work'], ['owner', 'other']])
    const lineageReads = calls.filter(c => c.path.startsWith('/api/sessions/'))
    assert.deepEqual(lineageReads.map(c => c.profile), ['work', 'other'])
  } finally { delete globalThis.window; delete host.state; delete host.request }
})

test('poller never publishes an old owner after same-runtime focus changes in flight', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  let profile = 'before'
  host.state = {
    focusedSessionId: { get: () => 'race-runtime' },
    focusedStoredSessionId: { get: () => 'race-stored' },
    focusedSessionOwner: { get: () => ({ connectionId: 'race-owner', profile }) }
  }
  host.request = async () => ({})
  globalThis.document = { hidden: false, getElementById: () => ({ remove() {} }), addEventListener() {}, removeEventListener() {} }
  globalThis.window = { hermesDesktop: { api: async args => {
    if (args.path.startsWith('/api/sessions/')) return { id: 'race-stored' }
    if (args.path.startsWith('/api/session-kanban-tasks')) {
      profile = 'after'
      return { tasks: [{ id: 't_race', status: 'running', title: 'Old owner task', gateway: 'race-owner', profile: 'before', board: 'default' }] }
    }
    return { tasks: [], machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  const before = window.__sessionTasks.snapshot
  try {
    await m.refresh()
    assert.equal(window.__sessionTasks.snapshot, before)
  } finally { dispose(); delete globalThis.window; delete globalThis.document; delete host.state; delete host.request }
})

test('failed lineage stays unknown and retries rather than caching an empty task answer', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  host.state = {
    focusedSessionId: { get: () => 'lineage-runtime' },
    focusedStoredSessionId: { get: () => 'lineage-failure' },
    focusedSessionOwner: { get: () => ({ connectionId: 'owner', profile: 'default' }) }
  }
  host.request = async () => ({})
  let failed = true
  let lineageReads = 0
  globalThis.document = { hidden: false, getElementById: () => ({ remove() {} }), addEventListener() {}, removeEventListener() {} }
  globalThis.window = { hermesDesktop: { api: async args => {
    if (args.path.startsWith('/api/sessions/')) {
      lineageReads++
      if (failed) throw new Error('owner unavailable')
      return { id: 'lineage-failure', parent_session_id: null }
    }
    return { tasks: [], machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    await m.refresh()
    assert.ok(window.__sessionTasks.snapshot.chips.some(c => c.state === 'unknown'), 'incomplete lineage must not look idle')
    failed = false
    await m.refresh()
    assert.equal(lineageReads, 2, 'failed lineage must retry on next poll')
    assert.equal(window.__sessionTasks.snapshot.chips.some(c => c.state === 'unknown'), false)
  } finally { dispose(); delete globalThis.window; delete globalThis.document; delete host.state; delete host.request }
})

test('archive guard warns when session ancestry is unavailable and retries recovery', async () => {
  let failed = true
  let reads = 0
  globalThis.window = { hermesDesktop: { api: async args => {
    if (args.path.startsWith('/api/sessions/')) {
      reads++
      if (failed) throw new Error('owner offline')
      return { id: 's_archive_unknown', parent_session_id: null }
    }
    return { gateway_id: 'owner', machines: [] }
  } } }
  try {
    const target = { sessionId: 's_archive_unknown', connectionId: 'owner-connection', profile: 'work' }
    const warnings = await m.archiveGuard(target)
    assert.ok(warnings.some(line => /ancestry unavailable/i.test(line.value)), 'unknown ancestry must require archive confirmation')
    failed = false
    assert.deepEqual(await m.archiveGuard(target), [])
    assert.equal(reads, 2)
  } finally { delete globalThis.window }
})

test('malformed ancestry cannot certify a root or poison the lineage cache', async () => {
  const invalid = [null, {}, { id: 'wrong', parent_session_id: null }, { id: 's_invalid_3', parent_session_id: {} }]
  let response
  globalThis.window = { hermesDesktop: { api: async args => {
    if (args.path.startsWith('/api/sessions/')) return response
    return { gateway_id: 'owner', machines: [] }
  } } }
  try {
    for (const [index, row] of invalid.entries()) {
      const sessionId = `s_invalid_${index}`
      const target = { sessionId, connectionId: 'owner-connection', profile: 'work' }
      response = row
      assert.ok((await m.archiveGuard(target)).some(line => /ancestry unavailable/i.test(line.value)), `invalid row ${index}`)
      response = { id: sessionId, parent_session_id: null }
      assert.deepEqual(await m.archiveGuard(target), [])
    }
  } finally { delete globalThis.window }
})

test('lineage reads and caches keep identical session ids separate by owner profile', async () => {
  const calls = []
  globalThis.window = { hermesDesktop: { api: async args => {
    calls.push(args)
    if (args.path.startsWith('/api/sessions/')) return { id: 's_same', parent_session_id: null }
    return { gateway_id: 'owner', machines: [] }
  } } }
  try {
    for (const profile of ['work', 'other']) {
      await m.archiveGuard({ sessionId: 's_same', connectionId: 'owner-connection', profile })
    }
    const reads = calls.filter(c => c.path.startsWith('/api/sessions/'))
    assert.equal(reads.length, 2)
    assert.deepEqual(reads.map(c => [c.connectionId, c.profile]), [
      ['owner-connection', 'work'], ['owner-connection', 'other']
    ])
  } finally { delete globalThis.window }
})

test('task reads pin the focused owner throughout a mid-fetch focus switch', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  let owner = { connectionId: 'owner-connection', profile: 'worker' }
  host.state = {
    focusedSessionOwner: { get: () => owner },
    profile: { get: () => 'ambient' }, connectionId: { get: () => 'ambient-connection' }
  }
  const calls = []
  globalThis.window = { hermesDesktop: { api: async args => {
    calls.push(args)
    owner = { connectionId: 'new-connection', profile: 'other' }
    if (args.path.startsWith('/api/session-kanban-tasks')) return { tasks: [] }
    if (args.path.includes('session-links')) return { gateway: 'origin', tasks: [] }
    throw new Error('unexpected route')
  } } }
  try {
    assert.deepEqual(await m.loadKanban(new Set(['s_pinned'])), [])
    assert.equal(calls.length, 1)
    for (const call of calls) {
      assert.equal(call.connectionId, 'owner-connection')
      assert.equal(call.profile, 'worker')
    }
  } finally { delete globalThis.window; delete host.state }
})

test('explicit owner without a profile uses its default, not the ambient profile', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  host.state = {
    focusedSessionOwner: { get: () => ({ connectionId: 'remote-default' }) },
    profile: { get: () => 'ambient-work' }
  }
  const calls = []
  globalThis.window = { hermesDesktop: { api: async args => {
    calls.push(args)
    return { tasks: [], gateway: 'remote' }
  } } }
  try {
    await m.loadKanban(new Set(['s_owner_default']))
    assert.deepEqual(calls.map(c => c.profile), ['default'])
  } finally { delete globalThis.window; delete host.state }
})

test('remote task clicks never fall back to the active gateway', () => {
  const calls = []
  const open = (...args) => { calls.push(args); return true }
  globalThis.window = { __hermesKanbanOpenTask: open, __taskMentionsOpen: () => calls.push('fallback') }
  globalThis.localStorage = { getItem: () => JSON.stringify({ conn: 'remote' }) }
  try {
    assert.equal(m.openTaskChip({ id: 't1', gateway: 'missing', profile: 'default', board: 'default' }), false)
    assert.equal(m.openTaskChip({ id: 't1', gateway: 'remote', profile: null, board: 'default' }), false)
    assert.equal(m.openTaskChip({ id: 't1', gateway: 'remote', profile: 'work', board: 'default' }), false)
    assert.deepEqual(calls, [])
    open.supportsQualifiedOwners = true
    const [chip] = m.sessionTaskChips([{ id: 't1', gateway: 'remote', profile: 'work', board: 'default', status: 'running' }])
    globalThis.localStorage = { getItem: () => JSON.stringify({ wrong: 'remote' }) }
    assert.equal(m.openTaskChip(chip), true)
    assert.deepEqual(calls, [['t1', 'default', 'conn', 'work']])
  } finally { delete globalThis.window; delete globalThis.localStorage }
})

test('connectionForHubGateway reads the core-learned connection -> hub name map', () => {
  const storage = { getItem: () => JSON.stringify({ local: 'laptop', 'conn-mini': 'mini' }) }
  assert.equal(m.connectionForHubGateway('mini', storage), 'conn-mini')
  assert.equal(m.connectionForHubGateway('nas', storage), null)
  assert.equal(m.connectionForHubGateway('mini', { getItem: () => '{bad' }), null)
})
