// node --test: process / sub-agent rows follow the runtime's own gateway events (with a 30 s fallback poll), the
// compute-rentals inventory is shared instead of read twice, and the raw kanban rows are offered to blocked-panel
// while current.
import { test, beforeEach, afterEach } from 'node:test'
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
  return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k) }
}
globalThis.localStorage = memoryStorage()
const m = await import('../plugin.js')
const { host } = await import('@hermes/plugin-sdk')

let focus, rpc, apiCalls, listeners, reports, begun, dispose, clock, epoch
function gatewayAtom(value) {
  const subs = new Set()
  return { get: () => value, set(v) { value = v; subs.forEach(fn => fn(v)) }, listen: fn => (subs.add(fn), () => subs.delete(fn)) }
}
const realNow = Date.now
const settle = async (n = 5) => { for (let i = 0; i < n; i++) await new Promise(r => setTimeout(r, 0)) }
const rpcCount = () => rpc.length
beforeEach(() => {
  m.resetRpcForTests(); m.resetRowsForTests()
  clock = realNow()
  Date.now = () => clock
  focus = { runtime: 'rt-1', stored: `chat-${Math.random().toString(36).slice(2)}`, owner: { connectionId: 'conn', profile: 'default' } }
  host.state = {
    focusedSessionId: { get: () => focus.runtime },
    focusedStoredSessionId: { get: () => focus.stored },
    focusedSessionOwner: { get: () => focus.owner },
    connectionId: { get: () => 'conn' },
    profile: { get: () => 'default' },
    gateway: gatewayAtom('open')
  }
  host.activeConnectionId = () => 'conn'
  rpc = []; apiCalls = []; reports = []; begun = []
  host.request = async (method, params) => { rpc.push({ method, params }); return method === 'process.list' ? { processes: [] } : { subagents: [] } }
  epoch = 1
  host.sessions = { beginWorkSourceRead: r => { const t = { ...r, authorityEpoch: epoch }; begun.push(t); return t }, reportWorkSourceRead: r => reports.push(r) }
  globalThis.document = { hidden: false, getElementById: () => ({ remove() {} }), addEventListener() {}, removeEventListener() {} }
  globalThis.window = { hermesDesktop: { api: async req => {
    apiCalls.push(req)
    if (req.path.startsWith('/api/sessions/')) return { id: focus.stored, parent_session_id: null }
    if (req.path.startsWith('/api/session-kanban-tasks')) return { tasks: [{ id: 't_blocked1', status: 'blocked', board: 'default', title: 'Needs you' }] }
    if (req.path.startsWith('/api/plugins/compute-rentals/inventory')) return { gateway_id: 'gw', machines: [] }
    return null
  } } }
  listeners = new Map()
  dispose = m.default.register({ register() {}, onEvent: (type, fn) => { listeners.set(type, fn); return () => listeners.delete(type) } })
})
afterEach(() => { dispose(); Date.now = realNow; delete globalThis.window; delete host.request; delete host.sessions })
const fire = (type, payload = {}, session_id = focus.runtime) => listeners.get(type)?.({ type, session_id, payload })

test('the plugin listens to the runtime events that say processes or sub-agents changed', () => {
  assert.deepEqual([...listeners.keys()].sort(), [...m.RPC_EVENTS].sort())
  assert.ok(m.RPC_EVENTS.includes('message.complete') && m.RPC_EVENTS.includes('gateway.ready'))
  assert.deepEqual([...m.PROCESS_TOOLS].sort(), ['process_manage', 'terminal'])
  assert.equal(m.RPC_FALLBACK_MS, 15_000)
  for (const t of ['subagent.start', 'subagent.complete']) assert.equal(m.runtimeWorkChanged({ type: t, session_id: 'rt' }, 'rt'), true, t)
  assert.equal(m.runtimeWorkChanged({ type: 'tool.complete', session_id: 'rt', payload: { name: 'terminal' } }, 'rt'), true)
  assert.equal(m.runtimeWorkChanged({ type: 'tool.complete', session_id: 'rt', payload: { name: 'process_manage' } }, 'rt'), true, "core's process tool")
  assert.equal(m.runtimeWorkChanged({ type: 'tool.complete', session_id: 'rt', payload: { name: 'process' } }, 'rt'), false, 'no such core tool')
  assert.equal(m.runtimeWorkChanged({ type: 'message.complete', session_id: 'rt' }, 'rt'), true, 'turn end (tool progress off)')
  assert.equal(m.runtimeWorkChanged({ type: 'message.complete', session_id: 'other' }, 'rt'), false)
  assert.equal(m.runtimeWorkChanged({ type: 'gateway.ready', connectionId: 'conn' }, 'rt', 'conn'), true, 'the chat gateway reconnected')
  assert.equal(m.runtimeWorkChanged({ type: 'gateway.ready', connectionId: 'elsewhere' }, 'rt', 'conn'), false, 'another gateway')
  assert.equal(m.runtimeWorkChanged({ type: 'gateway.ready' }, 'rt', 'conn'), true, 'unnamed connection')
  assert.equal(m.runtimeWorkChanged({ type: 'tool.complete', session_id: 'rt', payload: { name: 'read_file' } }, 'rt'), false)
  assert.equal(m.runtimeWorkChanged({ type: 'status.update', session_id: 'rt', payload: { kind: 'process' } }, 'rt'), true)
  assert.equal(m.runtimeWorkChanged({ type: 'status.update', session_id: 'rt', payload: { kind: 'status' } }, 'rt'), false)
  assert.equal(m.runtimeWorkChanged({ type: 'subagent.start', session_id: 'other' }, 'rt'), false, 'another runtime')
  assert.equal(m.runtimeWorkChanged({ type: 'subagent.start' }, 'rt'), false, 'session-less')
})

test('process/sub-agent rows are read on focus, then only on events or the 30 s fallback; reads are begun and reported', async () => {
  await m.refresh()
  assert.equal(rpcCount(), 2, 'first round reads both')
  assert.deepEqual(begun.filter(r => ['process', 'subagent'].includes(r.source)).map(r => r.source).sort(), ['process', 'subagent'])
  assert.deepEqual(reports.filter(r => ['process', 'subagent'].includes(r.source)).map(r => r.available), [true, true])
  clock += 5_000
  await m.refresh()
  clock += 5_000
  await m.refresh()
  assert.equal(rpcCount(), 2, 'plain ticks within the fallback read nothing')
  assert.equal(begun.filter(r => r.source === 'process').length, 1, 'no read begun, none reported, when nothing is read')
  assert.equal(reports.filter(r => r.source === 'process').length, 1)
  assert.ok(!window.__sessionTasks.snapshot.chips.some(c => c.key === 'process-status'), 'last answer still stands (not unknown)')

  // An event for another runtime, or a tool that does not touch processes: nothing.
  fire('subagent.start', {}, 'someone-else')
  fire('tool.complete', { name: 'read_file' })
  await new Promise(r => setTimeout(r, m.RPC_EVENT_DEBOUNCE_MS + 30))
  assert.equal(rpcCount(), 2)

  // A burst of events for the focused runtime: one coalesced re-read of both.
  fire('subagent.start'); fire('subagent.complete'); fire('tool.complete', { name: 'process_manage' })
  await new Promise(r => setTimeout(r, m.RPC_EVENT_DEBOUNCE_MS + 30)); await settle()
  assert.equal(rpcCount(), 4, 'one re-read after the burst')
  assert.deepEqual(rpc.slice(2).map(c => c.method).sort(), ['process.list', 'subagent.list'])
  assert.equal(reports.filter(r => r.source === 'subagent').at(-1).available, true)

  fire('status.update', { kind: 'process', text: 'process finished' })
  await new Promise(r => setTimeout(r, m.RPC_EVENT_DEBOUNCE_MS + 30)); await settle()
  assert.equal(rpcCount(), 6, 'a process notice re-reads')

  // Fallback: nothing happened for 15 s.
  clock += m.RPC_FALLBACK_MS - 1
  await m.refresh()
  assert.equal(rpcCount(), 6)
  clock += 2
  await m.refresh()
  assert.equal(rpcCount(), 8, 'the 15 s fallback reads both')
})

test('a failed read is retried on the next tick; a new runtime or chat reads at once', async () => {
  let fail = true
  host.request = async method => { rpc.push({ method }); if (fail) throw new Error('timeout'); return method === 'process.list' ? { processes: [] } : { subagents: [] } }
  await m.refresh()
  assert.equal(reports.filter(r => r.source === 'process').at(-1).available, false)
  assert.ok(window.__sessionTasks.snapshot.chips.some(c => c.key === 'process-status'))
  fail = false
  clock += 5_000
  await m.refresh()
  assert.equal(rpcCount(), 4, 'retried on the next tick')
  assert.equal(reports.filter(r => r.source === 'process').at(-1).available, true)
  focus.runtime = 'rt-2'
  await m.refresh()
  assert.equal(rpcCount(), 6, 'new runtime')
  assert.equal(rpc.at(-1).method === 'subagent.list' || rpc.at(-1).method === 'process.list', true)
  focus.stored = 'another-chat'
  await m.refresh()
  assert.equal(rpcCount(), 8, 'another chat (core records reads per stored chat)')
})

test('machine chips use compute-rentals\' shared inventory while younger than 30 s, else read it', async () => {
  const inventoryReads = () => apiCalls.filter(c => c.path.startsWith('/api/plugins/compute-rentals/inventory')).length
  window.__computeRentals = { inventory: { connectionId: 'conn', profile: 'default', at: clock - 1_000, data: { gateway_id: 'gw', machines: [] } } }
  await m.refresh()
  assert.equal(inventoryReads(), 0, 'fresh shared answer: no read')
  assert.equal(m.sharedInventory('conn', 'default', clock)?.gateway_id, 'gw')
  assert.equal(m.sharedInventory('other', 'default', clock), null, 'another connection')
  assert.equal(m.sharedInventory('conn', 'work', clock), null, 'another profile')
  window.__computeRentals.inventory.at = clock - 31_000
  clock += 30_001 // machines due again
  await m.refresh()
  assert.equal(inventoryReads(), 1, 'stale shared answer: read it ourselves')
  delete window.__computeRentals
  clock += 31_000
  await m.refresh()
  assert.equal(inventoryReads(), 2, 'no compute-rentals: read it ourselves')
})

test('kanbanRows offers the raw rows of a current read (15 s) or a feed-kept chat, else null', async () => {
  await m.refresh()
  const ids = await window.__sessionTasks.lineageIds(focus.stored, 'conn', 'default')
  const route = { connectionId: 'conn', profile: 'default', session: focus.stored }
  const r = window.__sessionTasks.kanbanRows(route, ids, clock)
  assert.deepEqual(r.rows.map(t => t.id), ['t_blocked1'])
  r.rows[0].status = 'mutated'
  assert.equal(m.kanbanRows(route, ids, clock).rows[0].status, 'blocked', 'callers get copies')
  assert.equal(m.kanbanRows(route, ids, clock + m.ROWS_FRESH_MS + 1), null, 'aged out')
  assert.equal(m.kanbanRows({ ...route, connectionId: 'other' }, ids, clock), null)
  // A trusted feed bundle keeps the chat current while its pushes are re-stamped.
  m.ingestFeed({ connectionId: 'conn', profile: 'default', session: 'feed-chat', lineage: ['feed-chat'], tasks: [{ id: 't_feed', status: 'blocked' }], trusted: true })
  const feedRoute = { connectionId: 'conn', profile: 'default', session: 'feed-chat' }
  assert.deepEqual(m.kanbanRows(feedRoute, ['feed-chat'], clock + 50_000).rows.map(t => t.id), ['t_feed'])
  assert.equal(m.kanbanRows(feedRoute, ['feed-chat'], clock + 50_000).feed, true)
  assert.equal(m.kanbanRows(feedRoute, ['feed-chat'], clock + 1_000).feed, false, 'within 15 s it is a plain read age')
  m.touchFeed('conn', 'default', ['feed-chat'], clock + 50_000)
  const pushed = m.kanbanRows(feedRoute, ['feed-chat'], clock + 100_000)
  assert.ok(pushed, 're-stamped by a push')
  assert.equal(pushed.at, clock + 50_000, 'at = the push that confirmed it')
  assert.equal(m.kanbanRows(feedRoute, ['feed-chat'], clock + 50_000 + m.FEED_STALE_MS + 1), null, 'pushes stopped')
  m.ingestFeed({ connectionId: 'conn', profile: 'default', session: 'untrusted', lineage: ['untrusted'], tasks: [{ id: 't_u', status: 'blocked' }], trusted: false })
  assert.equal(m.kanbanRows({ connectionId: 'conn', profile: 'default', session: 'untrusted' }, ['untrusted'], clock), null, 'an untrusted bundle is not offered')
})

const wait = ms => new Promise(r => setTimeout(r, ms))

test('an event during an in-flight read is not lost: the rows are read again after it', async () => {
  const gates = []
  host.request = method => { rpc.push({ method }); return new Promise(res => gates.push(() => res(method === 'process.list' ? { processes: [] } : { subagents: [] }))) }
  const round = m.refresh()
  await settle()
  assert.equal(rpcCount(), 2, 'first read in flight')
  fire('subagent.start') // arrives while that read is out
  await wait(m.RPC_EVENT_DEBOUNCE_MS + 30)
  assert.equal(rpcCount(), 2, 'no second read while the first is out')
  gates.splice(0).forEach(g => g())
  await round
  await settle(10)
  assert.equal(rpcCount(), 4, 'read again after the in-flight one answered')
  gates.splice(0).forEach(g => g())
  await settle(10)
  assert.equal(reports.filter(r => r.source === 'subagent').at(-1).available, true)
})

test('A to B to A: each visit reads and reports for that chat, never reusing the other chat\'s answer', async () => {
  const a = focus.stored
  await m.refresh()
  focus.stored = 'chat-B'
  await m.refresh()
  focus.stored = a
  await m.refresh()
  assert.equal(rpcCount(), 6)
  const procReports = reports.filter(r => r.source === 'process')
  assert.deepEqual(procReports.map(r => [r.storedSessionId, r.available]), [[a, true], ['chat-B', true], [a, true]])
  clock += 5_000
  await m.refresh()
  assert.equal(rpcCount(), 6, 'then A is settled again')
})

test('a moved work-source epoch with the same runtime re-reads the rows at once (reconnect)', async () => {
  await m.refresh()
  assert.equal(rpcCount(), 2)
  // The socket reconnected: core re-epochs the chat. The runtime id is unchanged.
  epoch = 2
  clock += 10_000 // the kanban list comes due (its begun read carries the new epoch); the rows are not due by time
  await m.refresh()
  await wait(20); await settle(10)
  assert.equal(rpcCount(), 4, 'the epoch change made the rows due at once')
  const last = begun.filter(r => r.source === 'process').at(-1)
  assert.equal(last.authorityEpoch, 2, 'and they were begun under the new epoch')
  assert.equal(reports.filter(r => r.source === 'process').at(-1).authorityEpoch, 2)
  // The same epoch again does nothing.
  clock += 10_000
  await m.refresh(); await wait(20); await settle(10)
  assert.equal(rpcCount(), 4)
})

test('gateway.ready, a gateway state change and message.complete each re-read the rows', async () => {
  await m.refresh()
  fire('gateway.ready', {}, undefined)
  await wait(m.RPC_EVENT_DEBOUNCE_MS + 30); await settle()
  assert.equal(rpcCount(), 4, 'gateway.ready')
  host.state.gateway.set('connecting'); host.state.gateway.set('open')
  await wait(m.RPC_EVENT_DEBOUNCE_MS + 30); await settle()
  assert.equal(rpcCount(), 6, 'gateway state (coalesced)')
  fire('message.complete', { text: 'done' })
  await wait(m.RPC_EVENT_DEBOUNCE_MS + 30); await settle()
  assert.equal(rpcCount(), 8, 'message.complete')
})
