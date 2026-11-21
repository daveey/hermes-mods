// node --test: a ready card says how long it has waited for a worker, on each path the panel shows it.
// [daveey 2026-11-21] "Waiting · 1 blocked on worker slot … waiting for a worker" and the Ready row "ready · waiting
// for a worker to claim it" gave no age, so a card six hours in the queue read the same as one just promoted.
// The backend now carries the Board's tasks.ready_since (ms) on linked-card rows and card reads.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const Codicon=()=>null;export const DisclosureCaret=()=>null;export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const useRef=v=>({current:v});export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const tasks = await import('../../session-tasks/plugin.js')

const NOW = Date.parse('2026-11-21T12:00:00Z')
const SIX_H_MS = NOW - 6 * 3600_000 - 5_000

const readyRows = (row, nowMs = NOW) => {
  const chips = tasks.sessionTaskChips([{ board: 'default', id: 't_ready', title: 'Review', status: 'ready', ...row }], { connectionId: 'C1', profile: 'default' })
  for (const c of chips) if (c.boardStatus) c.boardStatus.readAt = nowMs
  return m.classify(chips, { nowMs, evidence: () => ({ state: 'unknown', sourced: true, reason: 'missing_qualified_identity' }) }).ready
}

test('Ready row: the session-tasks row ready_since (ms) becomes the wait age', () => {
  const out = readyRows({ ready_since: SIX_H_MS })
  assert.equal(out[0].detail, 'ready · waiting 6 h for a worker to claim it · board status')
  // An older backend (no field) keeps the old words; a future stamp (clock skew) never shows a negative age.
  assert.equal(readyRows({})[0].detail, 'ready · waiting for a worker to claim it · board status')
  assert.equal(readyRows({ ready_since: NOW + 60_000 })[0].detail, 'ready · waiting for a worker to claim it · board status')
})

test('chain leaf from a card read: task.ready_since gives the age, beside the queue note', () => {
  const d = (task, extra = {}) => ({ task: { id: 't_x', title: 'X', status: 'ready', ...task }, events: [], links: { parents: [] }, ...extra })
  assert.deepEqual(m.nextHop(d({ ready_since: NOW - 25 * 60_000 }), NOW).leaf, { kind: 'slot', text: 'waiting 25 min for a worker' })
  assert.deepEqual(m.nextHop(d({ ready_since: SIX_H_MS }, { queue_note: 'current: 3 cards ahead on worker-a1; admissible capacity unknown' }), NOW).leaf,
    { kind: 'slot', text: 'waiting 6 h for a worker: 3 cards ahead on worker-a1' })
  assert.deepEqual(m.nextHop(d({}), NOW).leaf, { kind: 'slot', text: 'waiting for a worker' })
})

test('chain through a ready card of this chat: its own row gives the age', () => {
  const ready = { id: 't_a0a0a0', state: 'ready', readySince: (NOW - 2 * 3600_000) / 1000 }
  const local = new Map([['t_a0a0a0', { item: { chip: ready }, waiting: false, label: 'Mine', chip: ready }]])
  const root = { key: 'kb:t_r', kind: 'kanban', label: 't_r', chip: { id: 't_r', state: 'scheduled', look: 'waiting', waitingOn: 'card t_a0a0a0', connectionId: 'C1', profile: 'default' } }
  const ch = m.chainFor(root, NOW, { info: () => { throw new Error('no remote read') }, local })
  assert.deepEqual(ch.leaf, { kind: 'slot', text: 'waiting 2 h for a worker' })
})

test('readyWait: seconds or ms, unknown or future is empty', () => {
  const s = NOW / 1000
  assert.equal(m.readyWait(s - 90, s), ' 1 min')
  assert.equal(m.readyWait((s - 90) * 1000, s), ' 1 min')
  assert.equal(m.readyWait(0, s), '')
  assert.equal(m.readyWait(null, s), '')
  assert.equal(m.readyWait(s + 10, s), '')
})
