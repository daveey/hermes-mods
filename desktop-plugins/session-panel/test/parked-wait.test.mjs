// node --test: a parked card (fresh board status, no run) says what it waits on; ready / plain todo cards are not
// "waiting"; the folded breakdown never says "other"; and parked rows are not counted as "evidence unknown".
// [daveey 2026-11-20] "1 waiting · 1 evidence unknown / Waiting · 1  1 other" told the user nothing. The live sweep
// that day: 30 chats, every Waiting row a hub card with unknown execution evidence (missing qualified identity);
// the "other" rows were ready cards and triage cards, and scheduled cards read "scheduled · board status".
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const Codicon=()=>null;export const DisclosureCaret=()=>null;export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const tasks = await import('../../session-tasks/plugin.js')

// The hub's projection as the live sweep saw it: a parked card's only record is a kanban.card one whose
// execution state is unknown (no run). Evidence is that unknown verdict, as the panel computes it.
const unknownEv = () => ({ state: 'unknown', sourced: true, reason: 'missing_qualified_identity' })
const parked = (rows, opts = {}) => {
  const chips = tasks.sessionTaskChips(rows.map(r => ({ board: 'default', ...r })), { connectionId: 'C1', profile: 'default' })
  return m.classify(chips, { nowMs: Date.now(), evidence: unknownEv, ...opts })
}
const waitFor = (status, extra = {}) => ({ id: `t_${status}`, title: `${status} card`, status, ...extra })

test('ready card with a fresh board read is Ready (waiting for a worker), not Waiting', () => {
  const out = parked([waitFor('ready')])
  assert.equal(out.waiting.length, 0)
  assert.equal(out.ready.length, 1)
  assert.equal(out.ready[0].detail, 'ready · waiting for a worker to claim it · board status')
})

test('todo with no parent and no published wait is Not started; with a running parent it waits on that card', () => {
  const plain = parked([waitFor('todo')])
  assert.equal(plain.waiting.length, 0)
  assert.equal(plain.queued[0].detail, 'not started · board status')
  const child = parked([waitFor('todo', { upstream: { id: 't_up', status: 'running' } })])
  assert.equal(child.waiting[0].detail, 'waiting on card t_up (running) · board status')
  assert.equal(m.waitKind(child.waiting[0]), 'parent')
})

test('each parked state leads with what it waits on, never a bare state name', () => {
  const cases = [
    [waitFor('triage', { wait: { wait_state: 'waiting', waiting_on: 'triage' } }), 'in triage · waiting to be specified and promoted · board status', 'triage'],
    [waitFor('triage'), 'in triage · waiting to be specified and promoted · board status', 'triage'],
    [waitFor('review'), 'waiting on review · board status', 'review'],
    [waitFor('blocked'), 'blocked · no reason published · board status', 'blocked'],
    [waitFor('scheduled'), 'scheduled · its owner has not published what wakes it · board status', 'schedule'],
    [waitFor('scheduled', { wait: { wait_state: 'waiting', waiting_on: 'a producer the owner did not publish' } }), 'scheduled · its owner has not published what wakes it · board status', 'schedule'],
    [waitFor('scheduled', { upstream: { id: 't_up', status: 'scheduled' } }), 'scheduled · waiting on card t_up (scheduled) · board status', 'schedule'],
  ]
  for (const [row, detail, kind] of cases) {
    const out = parked([row])
    assert.equal(out.waiting.length, 1, row.status)
    assert.equal(out.waiting[0].detail, detail)
    assert.equal(m.waitKind(out.waiting[0]), kind)
  }
  // The owner's placeholder plus an upstream names the upstream once, without a second "→" leaf.
  const remote = parked([waitFor('scheduled', { remote: true, upstream: { id: 't_up', status: 'running' }, wait: { wait_state: 'waiting', waiting_on: 'a producer the owner did not publish' } })])
  assert.equal(remote.waiting[0].detail, 'scheduled · waiting on card t_up (running) · reported board status')
  // Core's wait text for a blocked dependency is "a parent card": that is on parent, not blocked.
  assert.equal(m.waitKind({ kind: 'kanban', detail: 'waiting on a parent card · board status', chip: { state: 'blocked', waitingOn: 'a parent card' } }), 'parent')
  // Built-in names are ordinary states, not Object prototype keys.
  assert.equal(m.waitBreakdown([{ kind: 'kanban', detail: 'x', chip: { state: 'constructor' } }]).text, '1 constructor')
  // A published wait is said as published, with its upstream leaf.
  const timed = parked([waitFor('scheduled', { wait: { wait_state: 'waiting', waiting_on: 'the clock until 2026-11-23 00:00 UTC' } })])
  assert.match(timed.waiting[0].detail, /^waiting on the clock until 2026-11-23 00:00 UTC · board status$/)
})

test('a replica board read is labelled reported, after the reason', () => {
  const out = parked([waitFor('triage', { remote: true, gateway: 'hub' })])
  assert.equal(out.waiting[0].detail, 'in triage · waiting to be specified and promoted · reported board status')
})

test('the breakdown names every bucket: no "other" for triage, review, blocked, retired asks or unknown states', () => {
  const out = parked([waitFor('triage'), waitFor('review'), waitFor('blocked'), waitFor('scheduled')])
  assert.equal(m.waitBreakdown(out.waiting).text, '1 on schedule · 1 in triage · 1 in review · 1 blocked')
  assert.equal(m.waitKind({ kind: 'kanban', cardStatus: true, detail: 'G1 / default / default / t_x · blocked · request dismissed', chip: { state: 'blocked' } }), 'blocked')
  const odd = { kind: 'kanban', detail: 'paused', chip: { state: 'paused' } }
  assert.equal(m.waitKind(odd), 'paused')
  assert.equal(m.waitBreakdown([odd]).text, '1 paused')
  assert.doesNotMatch(m.waitBreakdown(out.waiting).text, /other/)
})

test('parked rows with unknown execution evidence are not counted as "evidence unknown"; unread running ones still are', () => {
  const out = parked([waitFor('triage'), waitFor('scheduled'), waitFor('ready'), waitFor('todo')])
  assert.equal(m.annotateEvidence(out, Date.now()), 0)
  // A running card has a run to observe: its unknown evidence (here an older producer with no records) stays counted.
  const run = tasks.sessionTaskChips([{ id: 't_run', status: 'running', board: 'default' }], { connectionId: 'C1', profile: 'default' })
  const sorted = m.classify(run, { nowMs: Date.now(), evidence: c => m.chipObservation(c, Date.now() / 1000) })
  assert.equal(sorted.waiting.length, 1)
  assert.match(sorted.waiting[0].detail, /^status unknown/)
  assert.equal(m.waitKind(sorted.waiting[0]), 'unknown')
  // A host with the projector (its functions are not reached: the producer sent no records at all).
  const sdk = Object.fromEntries(['projectExecutionObservation', 'selectExecutionObservation', 'summarizeExecutionObservations', 'executionIdentityKey'].map(k => [k, () => null]))
  assert.equal(m.annotateEvidence(sorted, Date.now(), { sdk }), 1)
})

test('a stalled parked card is Stuck even with a fresh board read and unknown run evidence', () => {
  const out = parked([waitFor('scheduled', { wait: { wait_state: 'stalled', waiting_on: 'ci run 42' } })])
  assert.equal(out.waiting.length, 0)
  assert.equal(out.stuck.length, 1)
  assert.match(out.stuck[0].detail, /^stalled: waiting on ci run 42/)
})
