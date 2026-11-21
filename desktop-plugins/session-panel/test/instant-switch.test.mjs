// node --test: a revisited chat's chains, titles, wakes and chat names paint from the last answer, across a relaunch.
// [daveey 2026-11-20] "session changes must be instant": card reads used to live in memory only, so after a launch or
// plugin reload every waiting chain read "reading…" for the 1-5 s of reads (desktop.log [panel-timing]).
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const Codicon=()=>null;export const DisclosureCaret=()=>null;export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useRef=v=>({current:v});export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))

function memoryStorage() {
  const data = new Map()
  return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k), data }
}
globalThis.window = { localStorage: memoryStorage() }
const m = await import('../plugin.js')

const chip = { key: 'kb:t_root', kind: 'kanban', id: 't_root', state: 'blocked', connectionId: 'C1', profile: 'default', board: 'default' }
const detail = (id, status, extra = {}) => ({ task: { id, title: `Title ${id}`, status, session_id: 'S_owner', ...extra }, events: [] })
const never = () => new Promise(() => {}) // a read that has not answered yet
const settle = () => new Promise(r => setTimeout(r, 0))
const relaunch = () => { m.writeReads(); m.resetReadsForTests() } // memory gone, localStorage kept

beforeEach(() => { window.localStorage = memoryStorage(); m.resetReadsForTests() })

test('a card read survives a relaunch: shown at once from the last answer, re-read in the background', async () => {
  assert.equal(m.cardInfo(chip, 't_up', 1_000, async () => ({ detail: detail('t_up', 'running'), board: 'default' })).state, 'loading', 'first read ever')
  await settle()
  relaunch()
  let reread = 0
  const r = m.cardInfo(chip, 't_up', 2_000, () => { reread++; return never() })
  assert.equal(r.state, 'ok', 'not "reading…" after a relaunch')
  assert.equal(r.info.title, 'Title t_up')
  assert.equal(r.info.status, 'running')
  assert.equal(reread, 1, 'a restored answer is due: it is re-read once in the background')
})

test('a wake (park) read survives a relaunch, and so does a read that found no park', async () => {
  const parked = { task: { id: 't_root', title: 'x', status: 'scheduled', wake_check: null }, events: [{ id: 1, kind: 'scheduled', created_at: 1_000, payload: { wake_at: 5_000 } }] }
  m.parkFor(chip, 1_000, async () => ({ park: m.parkFromDetail(parked) }))
  const other = { ...chip, id: 't_none', key: 'kb:t_none' }
  m.parkFor(other, 1_000, async () => ({ park: null }))
  await settle()
  relaunch()
  const p = m.parkFor(chip, 2_000, never)
  assert.equal(p.state, 'ok')
  assert.equal(p.park.wakeAt, 5_000)
  assert.equal(m.parkFor(other, 2_000, never).state, 'ok', 'a known "no park" is not "reading wake…"')
})

test('card and chat titles survive a relaunch', async () => {
  m.cardRef(chip, 't_named', 1_000, async () => ({ title: 'Ship the thing', board: 'default' }))
  const route = { connectionId: 'C1', profile: 'default' }
  m.sessionTitle(route, 'S_x', 1_000, async () => 'Chat about the thing')
  await settle()
  relaunch()
  assert.equal(m.cardRef(chip, 't_named', 2_000, never).title, 'Ship the thing')
  assert.equal(m.sessionTitle(route, 'S_x', 2_000, never), 'Chat about the thing')
})

test('a failed re-read keeps the last answer marked stale, and pending reads are never saved as answers', async () => {
  m.cardInfo(chip, 't_up', 1_000, async () => ({ detail: detail('t_up', 'review'), board: 'default' }))
  m.cardInfo(chip, 't_pending', 1_000, never)
  await settle()
  relaunch()
  assert.equal(m.cardInfo(chip, 't_pending', 2_000, never).state, 'loading', 'nothing was known: still a first read')
  m.cardInfo(chip, 't_up', 2_000, async () => null) // the background re-read fails
  await settle()
  const r = m.cardInfo(chip, 't_up', 2_500, never)
  assert.equal(r.state, 'stale')
  assert.equal(r.info.status, 'review')
})

test('corrupt storage starts empty instead of throwing', () => {
  window.localStorage.setItem('hermes.sessionPanel.reads.v1', '{not json')
  m.resetReadsForTests()
  assert.equal(m.cardInfo(chip, 't_up', 1_000, never).state, 'loading')
})

test('panelSignature and tracePanel log one line per switch with each distinct step', () => {
  const lines = []
  const sink = l => lines.push(l)
  m.resetTraceForTests()
  const a = { connectionId: 'C1', profile: 'default', session: 'S_a' }, b = { ...a, session: 'S_b' }
  m.tracePanel(a, 'one', 0, sink)
  m.tracePanel(a, 'one', 10, sink)
  m.tracePanel(a, 'two', 40, sink)
  m.tracePanel(b, 'x', 100, sink) // a switch flushes the previous chat's trace
  assert.equal(lines.length, 1)
  assert.deepEqual(JSON.parse(lines[0].replace('[panel-timing] ', '')), { session: 'S_a', connection: 'C1', steps: [[0, 'one'], [40, 'two']] })
  m.resetTraceForTests()
})

// [daveey 2026-11-20] "pre-compute the status panels ... so most switches are instant": the background warm also
// covers the most recently active sidebar chats and pre-reads every waiting card's dependency chain.
test('warmTargets: recently focused chats first, then the most recently active sidebar chats, deduped and capped', () => {
  window.localStorage = memoryStorage()
  m.noteRecent({ connectionId: 'C1', profile: 'default', session: 'S_recent' })
  const rows = [
    { id: 'S_old', connection_id: 'C1', profile: '', last_active: 10 },
    { id: 'S_new', connection_id: 'C2', profile: 'work', last_active: 99 },
    { id: 'S_recent', connection_id: 'C1', profile: 'default', last_active: 50 }, // already listed as recent
    { id: 'S_arch', connection_id: 'C1', last_active: 100, archived: true },
    { id: 'S_noconn', last_active: 100 }
  ]
  assert.deepEqual(m.warmTargets(rows).map(r => `${r.connectionId}/${r.profile}/${r.session}`), ['C1/default/S_recent', 'C2/work/S_new', 'C1/default/S_old'])
  const many = Array.from({ length: 40 }, (_, i) => ({ id: `S${i}`, connection_id: 'C1', last_active: i }))
  const out = m.warmTargets(many)
  assert.equal(out.length, 1 + m.WARM_SIDEBAR, 'sidebar contributes at most WARM_SIDEBAR')
  assert.ok(out.length <= m.WARM_MAX)
  assert.equal(out[1].session, 'S39', 'most recently active first')
})

test('warmChains walks every waiting card\'s chain to its leaf, so a switch shows no "reading…"', async () => {
  const route = { connectionId: 'C1', profile: 'default', session: 'S_w' }
  const waiting = { key: 'kb:t_aaaaaa1', kind: 'kanban', id: 't_aaaaaa1', state: 'blocked', label: 'Waiting card', waitingOn: 'card t_bbbbbb2 (blocked)', connectionId: 'C1', profile: 'default', board: 'default' }
  const reads = []
  const details = {
    t_aaaaaa1: { task: { id: 't_aaaaaa1', title: 'Waiting card', status: 'blocked' }, events: [] },
    t_bbbbbb2: { task: { id: 't_bbbbbb2', title: 'Middle', status: 'todo', parents: ['t_ccccccc3'] }, parents: [{ id: 't_ccccccc3', status: 'running' }], events: [] },
    t_ccccccc3: { task: { id: 't_ccccccc3', title: 'Leaf', status: 'running' }, events: [] }
  }
  window.hermesDesktop = { api: async ({ path }) => { const id = /tasks\/([^?]+)/.exec(path)?.[1]; reads.push(id); return details[id] || {} } }
  try {
    const local = m.chainLocal([{ key: waiting.key, kind: 'kanban', label: waiting.label, chip: waiting }], [])
    const item = { key: waiting.key, kind: 'kanban', label: waiting.label, id: waiting.id, chip: waiting }
    assert.equal(m.chainFor(item, Date.now(), { local, fallback: () => route }).loading, true, 'cold: the chain would read "reading…"')
    m.resetReadsForTests()
    const walked = await m.warmChains(route, { snapshot: { stored: 'S_w', chips: [waiting] } })
    assert.equal(walked, 1)
    const ch = m.chainFor(item, Date.now(), { local, fallback: () => route })
    assert.ok(!ch.loading, 'warm: the whole chain is known')
    assert.ok(reads.includes('t_bbbbbb2'), 'the named card was read')
  } finally { delete window.hermesDesktop }
})

test('warmChains does nothing for a chat with no cached cards or no waiting card', async () => {
  const route = { connectionId: 'C1', profile: 'default', session: 'S_none' }
  assert.equal(await m.warmChains(route, { snapshot: null }), 0)
  assert.equal(await m.warmChains(route, { snapshot: { chips: [{ key: 'm', kind: 'machine', label: 'box' }] } }), 0)
})
