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
const parked = (rows, opts = {}, nowMs = Date.parse('2026-11-20T12:00:00Z')) => {
  const chips = tasks.sessionTaskChips(rows.map(r => ({ board: 'default', ...r })), { connectionId: 'C1', profile: 'default' })
  for (const c of chips) if (c.boardStatus) c.boardStatus.readAt = nowMs // a fixed clock: wake times never age into the past
  return m.classify(chips, { nowMs, evidence: unknownEv, ...opts })
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
  assert.equal(timed.waiting[0].detail, '2d 12h left (until 2026-11-22 16:00 PST) · board status')
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

test('readable waits: how long it has waited, how long is left, how late, and a flag when well overdue', () => {
  const NOW = Date.parse('2026-11-22T23:50:00Z')
  const c = (w, waitedS = 600) => ({ waitingOn: w, waitingSince: NOW / 1000 - waitedS })
  assert.equal(m.waitPhrase(c('the clock until 2026-11-23 08:00 UTC'), NOW), 'waiting 10m · 8h 10m left (until 2026-11-23 00:00 PST)')
  assert.equal(m.waitPhrase(c('the clock until 2026-11-23 08:00 UTC (Explicitly requested two-day window)', 2 * 86_400), NOW), 'waiting 2d · 8h 10m left (until 2026-11-23 00:00 PST)')
  assert.equal(m.waitPhrase(c('the clock until 2026-11-22 23:40 UTC', 3600), NOW), 'waiting 1h · overdue by 10m (was due 2026-11-22 15:40 PST)')
  assert.equal(m.waitPhrase(c('the clock until 2026-11-22 23:21 UTC', 3600), NOW), 'waiting 1h · overdue by 29m (was due 2026-11-22 15:21 PST)', 'under 30 min: not flagged')
  assert.equal(m.waitPhrase(c('the clock until 2026-11-22 23:20 UTC', 3600), NOW), 'waiting 1h · ⚠ overdue by 30m (was due 2026-11-22 15:20 PST)', 'from 30 min: flagged')
  assert.equal(m.waitPhrase(c('the clock until 2026-11-22 20:50 UTC', 86_400), NOW), 'waiting 1d · ⚠ overdue by 3h (was due 2026-11-22 12:50 PST)')
  assert.equal(m.waitPhrase({ waitingOn: 'the clock until 2026-11-23 08:00 UTC' }, NOW), '8h 10m left (until 2026-11-23 00:00 PST)', 'no start time: no waited part')
  assert.equal(m.waitPhrase(c('ci run 42'), NOW), 'waiting on ci run 42 for 10m')
  assert.equal(m.waitPhrase({ waitingOn: 'ci run 42' }, NOW), 'waiting on ci run 42')
  assert.equal(m.waitPhrase({}, NOW), '')
  assert.equal(m.pacific(Date.parse('2026-07-01T07:00:00Z') / 1000), '2026-07-01 00:00 PDT', 'Pacific wall clock in daylight time')
  for (const [s, x] of [[5, '5s'], [600, '10m'], [3600, '1h'], [12_000, '3h 20m'], [86_400 * 2 + 3600 * 4, '2d 4h'], [86_400, '1d']]) assert.equal(m.shortDur(s), x)
})

test('card references read "Title" (t_x): this chat\'s cards, else a read on the owner (or chat) route, board then default', async () => {
  const chip = { kind: 'kanban', id: 't_child', connectionId: 'C9', profile: 'default', board: 'ops' }
  m.noteTitles([{ kind: 'kanban', id: 't_aaaaaaaa', label: 'Sibling card', connectionId: 'C9', profile: 'default', board: 'default' }])
  assert.equal(m.waitLabel('waiting on card t_aaaaaaaa for 1m', chip), 'waiting on card "Sibling card" (t_aaaaaaaa) for 1m')
  const reads = []
  const read = (r, id) => { reads.push([r.connectionId, r.profile, r.board, id]); return Promise.resolve(id === 't_bbbbbbbb' ? { title: 'Fetched title', board: 'default' } : { title: '' }) }
  assert.equal(m.waitLabel('waiting on card t_bbbbbbbb', chip, Date.now(), read), 'waiting on card t_bbbbbbbb', 'first paint: id only')
  await new Promise(r => setTimeout(r, 0))
  const seg = m.waitSegments('waiting on card t_bbbbbbbb', chip, Date.now(), read).find(s => s.id)
  assert.equal(seg.title, 'Fetched title')
  assert.deepEqual(seg.open, { kind: 'kanban', id: 't_bbbbbbbb', board: 'default', connectionId: 'C9', profile: 'default' }, 'opens on the board that answered')
  assert.deepEqual(reads, [['C9', 'default', 'ops', 't_bbbbbbbb']], 'one read, cached')
  // A failed read is retried only after the retry window.
  m.waitLabel('card t_cccccccc', chip, Date.now(), read); await new Promise(r => setTimeout(r, 0))
  m.waitLabel('card t_cccccccc', chip, Date.now(), read)
  assert.equal(reads.filter(r => r[3] === 't_cccccccc').length, 1)
  m.waitLabel('card t_cccccccc', chip, Date.now() + 5 * 60_000 + 1, read)
  assert.equal(reads.filter(r => r[3] === 't_cccccccc').length, 2)
  // A replica row of another gateway (no owner connection) reads and opens through the chat's route.
  const remote = { kind: 'kanban', id: 't_r', connectionId: null, profile: 'default', board: 'default', gateway: 'worker-a1' }
  const viaChat = m.waitSegments('waiting on card t_dddddddd', remote, Date.now(), read, () => ({ connectionId: 'CHAT', profile: 'work' })).find(s => s.id)
  assert.equal(viaChat.linkable, false, 'a replica row is titled through the chat route but not linked (no owner route)')
  assert.deepEqual(reads.at(-1), ['CHAT', 'work', 'default', 't_dddddddd'])
  // No route at all: bare id, not linked, no read.
  const none = m.waitSegments('card t_eeeeeeee', remote, Date.now(), read, () => null).find(s => s.id)
  assert.deepEqual([none.linkable, none.open], [false, null])
  assert.equal(reads.filter(r => r[3] === 't_eeeeeeee').length, 0)
})

test('the real title read: owner route, the waiting card\'s board first, then default; status joins the parens', async () => {
  const calls = []
  globalThis.window = { hermesDesktop: { api: async a => { calls.push(a); if (a.path.includes('board=ops')) throw new Error('404: not found'); return { task: { title: 'Lives on default' } } } } }
  try {
    const chip = { kind: 'kanban', id: 't_k', connectionId: 'C7', profile: 'work', board: 'ops', gateway: 'G7' }
    assert.equal(m.waitLabel('waiting on card t_77777777 (running)', chip), 'waiting on card t_77777777 (running)')
    await new Promise(r => setTimeout(r, 0))
    assert.deepEqual(calls.map(c => [c.path, c.connectionId, c.profile, c.timeoutMs]), [
      ['/api/plugins/kanban/tasks/t_77777777?board=ops', 'C7', 'work', 8000],
      ['/api/plugins/kanban/tasks/t_77777777?board=default', 'C7', 'work', 8000]])
    const seg = m.waitSegments('waiting on card t_77777777 (running)', chip).find(s => s.id)
    assert.equal(m.waitLabel('waiting on card t_77777777 (running)', chip), 'waiting on card "Lives on default" (t_77777777, running)')
    assert.deepEqual(seg.open, { kind: 'kanban', id: 't_77777777', board: 'default', connectionId: 'C7', profile: 'work' })
    // Only a lifecycle status joins the parens; other parenthesised words stay where they are.
    assert.equal(m.waitLabel('card t_77777777 (see notes)', chip), 'card "Lives on default" (t_77777777) (see notes)')
    // A local card (gateway unknown) cannot call another name foreign.
    assert.equal(m.waitSegments('card t_77777777 on hub', { ...chip, gateway: 'local', gatewayId: '' }).find(s => s.id).linkable, true)
    // Named on another gateway: titled (the chat route's replica answers) but not linked; its own gateway is linked.
    assert.equal(m.waitSegments('card t_77777777 on worker-a1', chip).find(s => s.id).linkable, false)
    assert.equal(m.waitSegments('card t_77777777 on G7', chip).find(s => s.id).linkable, true)
    // A read that never answers is retried after 30 s, not never.
    let hangs = 0
    const hang = () => { hangs++; return new Promise(() => {}) }
    m.cardRef(chip, 't_88888888', 1_000, hang); m.cardRef(chip, 't_88888888', 20_000, hang)
    assert.equal(hangs, 1)
    m.cardRef(chip, 't_88888888', 31_001, hang)
    assert.equal(hangs, 2)
  } finally { delete globalThis.window }
})
