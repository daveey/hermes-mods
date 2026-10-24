// node --test: pure projections only (the SDK import is stubbed by the loader below).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')

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
  assert.deepEqual(chips.map(c => c.key), ['kb:nas:default:default:t_live', 'kb:mini:default:default:t_mini'])
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

test('loadKanban dedupes by actual owner gateway, profile, board and id', async () => {
  const row = { id: 't_same', status: 'running', gateway: 'remote', profile: 'work', board: 'default' }
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [row] }
    if (path.includes('session-links')) return { gateway: 'origin', tasks: [row, { ...row, profile: 'other' }] }
    throw new Error('unexpected route')
  } } }
  try {
    const chips = await m.loadKanban(new Set(['s_one']))
    assert.equal(chips.length, 2)
    assert.equal(new Set(chips.map(c => c.key)).size, 2)
  } finally { delete globalThis.window }
})

test('hub fallback cache cannot leak between profiles with the same session id', async () => {
  const { host } = await import('@hermes/plugin-sdk')
  let profile = 'work'
  let online = true
  host.state = { profile: { get: () => profile } }
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [] }
    if (online && path.includes('session-links')) return { gateway: 'origin', tasks: [
      { id: 't_work', status: 'running', gateway: 'remote', profile: 'work' }
    ] }
    throw new Error('offline')
  } } }
  try {
    const ids = new Set(['s_collision'])
    assert.equal((await m.loadKanban(ids)).length, 1)
    online = false
    assert.equal((await m.loadKanban(ids))[0].stale, true)
    profile = 'other'
    assert.deepEqual(await m.loadKanban(ids), [])
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
    assert.equal(m.openTaskChip({ id: 't1', gateway: 'remote', profile: 'work', board: 'default' }), true)
    assert.deepEqual(calls, [['t1', 'default', 'conn', 'work']])
  } finally { delete globalThis.window; delete globalThis.localStorage }
})

test('connectionForHubGateway reads the core-learned connection -> hub name map', () => {
  const storage = { getItem: () => JSON.stringify({ local: 'laptop', 'conn-mini': 'mini' }) }
  assert.equal(m.connectionForHubGateway('mini', storage), 'conn-mini')
  assert.equal(m.connectionForHubGateway('nas', storage), null)
  assert.equal(m.connectionForHubGateway('mini', { getItem: () => '{bad' }), null)
})
