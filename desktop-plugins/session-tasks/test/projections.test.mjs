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
