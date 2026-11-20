// node --test: classify/waitKind labels without a Desktop React install.
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
const NOW = Date.parse('2026-10-29T15:00:00Z'), S = NOW / 1000
const card = (id, state, extra = {}) => ({ key: `kb:${id}`, kind: 'kanban', id, state, label: `Card ${id}`, connectionId: 'C1', profile: 'default', board: 'default', gateway: 'G1', since: S - 600, activityAt: S - 60, ...extra })

test('contained wait kind and blocked-upstream / on-parent labels', () => {
  const contained = { episode: 'ep-1', authority: { id: 't_auth', status: 'done' } }
  const held = m.classify([card('held', 'todo', {
    blockedUpstream: { id: 't_leaf', status: 'triage', contained },
  })], { nowMs: NOW })
  assert.equal(held.blockedUpstream[0].detail, 'waiting on contained card t_leaf · release by card t_auth (done)')
  const waiting = m.classify([
    card('t_held', 'triage', { look: 'waiting', waitingOn: 'triage', contained }),
    card('t_card', 'scheduled', { look: 'waiting', waitingOn: 'card t_x on hub', upstream: { id: 't_run', status: 'running' } }),
  ], { nowMs: NOW })
  assert.equal(m.waitKind(waiting.waiting.find(i => i.id === 't_held')), 'contained')
  assert.equal(waiting.waiting.find(i => i.id === 't_card').detail, 'waiting on card t_x on hub → t_run running')
  assert.equal(m.waitBreakdown(waiting.waiting).text, '1 on parent · 1 contained')
})

test('unreadable containment authority stays explicit', () => {
  for (const authority of [{ id: 't_gone', status: '' }, {}]) {
    const result = m.classify([card('held', 'todo', {
      blockedUpstream: { id: 't_leaf', status: 'triage', contained: { episode: 'ep', authority } },
    })], { nowMs: NOW })
    assert.equal(result.blockedUpstream[0].detail,
      `waiting on contained card t_leaf · release by ${authority.id ? `card ${authority.id}` : 'unknown'} (unknown)`)
  }
})

for (const [status, reason] of [['blocked', 'Awaiting human approval'], ['triage', 'spend budget'], ['scheduled', 'Awaiting CI']]) {
  test(`producer to panel preserves ${status} reason`, () => {
    const parent = { id: 't_parent', status, reason }
    const chips = tasks.sessionTaskChips([{
      id: 't_child', title: 'Child', status: 'todo', board: 'default',
      ...(status === 'scheduled' ? { upstream: parent } : { blocked_upstream: parent }),
    }], { connectionId: 'C1', profile: 'default' })
    const result = m.classify(chips, { evidence: c => tasks.chipObservation(c, Date.now() / 1000) })
    const rows = status === 'scheduled' ? result.waiting : result.blockedUpstream
    assert.equal(rows.length, 1)
    assert.ok(rows[0].detail.endsWith(` · ${reason}${status === 'scheduled' ? ' · board status' : ''}`), rows[0].detail)
    assert.ok(rows[0].detail.includes('t_parent'))
    assert.equal(result.unasked.length, 0)
  })
}

for (const status of ['todo', 'scheduled']) {
  test(`${status} card wait retains reason without duplicating the named parent`, () => {
    const chips = tasks.sessionTaskChips([{
      id: 't_child', title: 'Child', status, board: 'default',
      upstream: { id: 't_run', status: 'scheduled', reason: 'Awaiting CI' },
      wait: { wait_state: 'waiting', waiting_on: 'card t_run', waiting_since: Date.now() / 1000 - 60 },
    }], { connectionId: 'C1', profile: 'default' })
    const result = m.classify(chips, { evidence: c => tasks.chipObservation(c, Date.now() / 1000) })
    assert.equal(result.waiting[0].detail, 'waiting on card t_run for 1m · Awaiting CI · board status')
    assert.equal(result.unasked.length, 0)
  })
  test(`real ${status} projection names upstream on the board-status path`, () => {
    const chips = tasks.sessionTaskChips([{
      id: 't_child', title: 'Child', status, board: 'default',
      upstream: { id: 't_run', status: 'running' },
      wait: { wait_state: 'waiting', waiting_on: 'card t_run', waiting_since: Date.now() / 1000 - 60 },
    }], { connectionId: 'C1', profile: 'default' })
    const result = m.classify(chips, { evidence: c => tasks.chipObservation(c, Date.now() / 1000) })
    assert.equal(result.waiting.length, 1)
    assert.match(result.waiting[0].detail, /^waiting on card t_run for 1m · board status$/)
  })
}
