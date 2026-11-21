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
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const useRef=v=>({current:v});export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
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
    [waitFor('review'), 'waiting for a reviewer (none running) · board status', 'review'],
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
  assert.equal(m.waitKind({ kind: 'kanban', cardStatus: true, detail: 'G1 / default / default / t_x · blocked · request dismissed', chip: { state: 'blocked' } }), 'blocked')
  assert.equal(m.waitKind({ kind: "kanban", detail: "paused", chip: { state: "paused" } }), "paused")
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

test('a review card names that no reviewer is running and its newest event', () => {
  const NOW = Date.parse('2026-11-20T21:54:00Z')
  assert.equal(m.reviewWaitText({ activity: 'review_route_deferred', activityAt: NOW / 1000 - 600 }, NOW), 'waiting for a reviewer (none running) · last: review route deferred 10m ago')
  assert.equal(m.reviewWaitText({}, NOW), 'waiting for a reviewer (none running)')
})

test('a review card through the real projection: no reviewer running, newest event and its age on the classify clock', () => {
  const NOW = Date.parse('2026-11-20T12:00:00Z')
  const out = parked([waitFor('review', { activity_kind: 'review_route_deferred', activity_at: NOW / 1000 - 600 })], {}, NOW)
  assert.equal(out.waiting[0].detail, 'waiting for a reviewer (none running) · last: review route deferred 10m ago · board status')
  // With the session-tasks bridge, the event reads in the task rows' words.
  globalThis.window = { __sessionTasks: { activityLabel: tasks.activityLabel } }
  try {
    const cont = parked([waitFor('review', { activity_kind: 'continued', activity_at: NOW / 1000 - 60 })], {}, NOW)
    assert.equal(cont.waiting[0].detail, 'waiting for a reviewer (none running) · last: checkpointed 1m ago · board status')
  } finally { delete globalThis.window }
})

test('row line: a short reason per kind of wait, tone and age; the long form stays in `detail`', () => {
  const NOW = Date.parse('2026-11-20T22:00:00Z'), S = NOW / 1000
  const row = (state, chip = {}, extra = {}) => ({ key: `kb:${state}`, kind: 'kanban', label: 'T', detail: 'long form', chip: { state, connectionId: 'C1', profile: 'default', ...chip }, ...extra })
  const line = v => v.reason.map(p => (typeof p === 'string' ? p : `[${p.ref.id}${p.ref.status ? `:${p.ref.status}` : ''}]`)).join('')
  const cases = [
    [row('scheduled', { waitingOn: 'the clock until 2026-11-21 08:00 UTC', waitingSince: S - 7200 }), '10h left', '', '2h', 'Sat 00:00 PST'],
    [row('scheduled', { waitingOn: 'the clock until 2026-11-20 21:50 UTC', waitingSince: S - 3600 }), '10m overdue', '', '1h', 'due Fri 13:50 PST'],
    [row('scheduled', { waitingOn: 'the clock until 2026-11-20 18:00 UTC', waitingSince: S - 86_400 }), '⚠ 4h overdue', 'warn', '1d', 'due Fri 10:00 PST'],
    [row('scheduled', { waitingOn: 'card t_01a1fa96bc4ff0f5 on hub', waitingSince: S - 600 }), 'on [t_01a1fa96bc4ff0f5]', '', '10m', ''],
    [row('todo', { upstream: { id: 't_01a197166756bd53', status: 'scheduled' } }), 'on [t_01a197166756bd53:scheduled]', '', '', ''],
    [row('triage', { waitingOn: 'release by card t_01a1e1778ac09234 (running)', contained: {} }), 'released by [t_01a1e1778ac09234:running]', '', '', ''],
    [row('scheduled', { waitingOn: 'ci https://github.com/o/r/actions/runs/44819160264' }), 'on CI run 44819160264', '', '', ''],
    [row('scheduled', {}), 'no wake published', 'note', '', ''],
    [row('scheduled', { waitingOn: 'a producer the owner did not publish' }), 'no wake published', 'note', '', ''],
    [row('blocked', {}), 'blocked · no reason given', 'note', '', ''],
    [row('triage', { waitingOn: 'triage', waitingSince: S - 5400 }), 'needs triage', '', '1h 30m', ''],
    [row('review', { activity: 'review_route_deferred', activityAt: S - 900 }), 'no reviewer running · review route deferred 15m ago', '', '', ''],
    [row('running', { stale: true }, { detail: 'status unknown · last known: running' }), 'status unknown · could not be re-read', '', '', ''],
    [row('running', {}, { detail: 'status unknown (source expired) · last known: running' }), 'status unknown · source expired', '', '', ''],
  ]
  for (const [i, reason, tone, age, sub] of cases) {
    const v = m.rowWait(i, NOW)
    assert.deepEqual([line(v), v.tone, v.age, v.sub], [reason, tone, age, sub], JSON.stringify(i.chip))
  }
  // Blocked upstream and a retired ask.
  assert.equal(line(m.rowWait({ kind: 'kanban', blockedUpstreamRow: true, chip: { blockedUpstream: { id: 't_par', status: 'blocked' } } }, NOW)), 'parent blocked: [t_par:blocked]')
  // A retired ask's owner: its own status text on the line.
  const ask = { kind: 'kanban', cardStatus: true, detail: 'G1 / default / default / t_0123abcd · blocked · request dismissed', chip: {} }
  assert.deepEqual([line(m.rowWait(ask, NOW)), m.rowWait(ask, NOW).tone], ['blocked · request dismissed · t_0123abcd', 'warn'], 'the status first, then the id')
  // Contained blocked upstream: released by its authority.
  assert.equal(line(m.rowWait({ kind: 'kanban', blockedUpstreamRow: true, chip: { blockedUpstream: { id: 't_leaf', status: 'triage', contained: { authority: { id: 't_auth', status: 'running' } } } } }, NOW)), 'released by [t_auth:running]')
  // A sub-agent or process row (not a card) keeps its own detail line.
  assert.equal(line(m.rowWait({ kind: 'sub', detail: 'unknown (no observation) · progress unknown', chip: {} }, NOW)), 'unknown (no observation) · progress unknown')
  // "waits on deploy" after a card id is not a gateway: same parse as the long form.
  const notGw = m.rowWait(row('scheduled', { waitingOn: 'card t_01a1fa96bc4ff0f5 (running) waits on deploy' }), NOW).reason.find(p => p.ref)
  assert.equal(notGw.ref.status, 'running')
  assert.equal(m.waitSegments(notGw.ref.text, { kind: 'kanban', connectionId: 'C1', profile: 'default', gateway: 'hub' }).find(x => x.id).linkable, true, 'and still linked')
  // A wait start in the future (clock skew) shows no age rather than a negative one.
  assert.equal(m.rowWait(row('triage', { waitingOn: 'triage', waitingSince: S + 60 }), NOW).age, '')
})

test('shortWait: URLs never reach the line', () => {
  assert.equal(m.shortWait('ci https://github.com/o/r/actions/runs/123'), 'CI run 123')
  assert.equal(m.shortWait('ci https://github.com/o/r/pull/7'), 'CI on PR #7')
  assert.equal(m.shortWait('deploy https://example.com/a/very/long/path/that/goes/on/and/on/forever'), 'deploy example.com/a/very/long/path/that/goes/…')
  assert.equal(m.shortWait('deploy abc123'), 'deploy abc123')
})

test('pacificShort: weekday within six days, month and day beyond; PDT in daylight time', () => {
  const NOW = Date.parse('2026-11-20T22:00:00Z')
  assert.equal(m.pacificShort(Date.parse('2026-11-21T08:00:00Z') / 1000, NOW), 'Sat 00:00 PST')
  assert.equal(m.pacificShort(Date.parse('2026-12-03T06:20:00Z') / 1000, NOW), 'Dec 2 22:20 PST')
  assert.equal(m.pacificShort(Date.parse('2026-07-01T07:00:00Z') / 1000, Date.parse('2026-06-30T00:00:00Z')), 'Wed 00:00 PDT')
})

test('owner park: read from the card detail; the line shows the real wake instead of "no wake published"', async () => {
  const NOW = Date.parse('2026-11-20T22:53:00Z'), S = NOW / 1000
  // The exact shape of t_01a1fb17e589fdd8 on 2026-11-20: a `continued` park with a declared CI wait.
  const detail = { task: { status: 'scheduled', wake_check: null }, events: [
    { id: 1, kind: 'created', created_at: S - 9000, payload: {} },
    { id: 2, kind: 'continued', created_at: S - 240, payload: { continuation: 1, status: 'scheduled', wake_at: S + 360, awaiting: { kind: 'ci', ref: 'local core-wt consumer suite PID 4143137', why: 'Full requested consumer regression suite running on worker.' } } }
  ] }
  const pk = m.parkFromDetail(detail)
  assert.deepEqual([pk.wakeAt, pk.awaiting.kind, pk.continued, pk.parkedAt], [S + 360, 'ci', true, S - 240])
  const row = (park, chip = {}) => m.rowWait({ key: 'kb:x', kind: 'kanban', label: 'X', chip: { id: 't_x', state: 'scheduled', connectionId: 'C1', profile: 'default', ...chip } }, NOW, park)
  const line = v => [v.reason.map(p => (typeof p === 'string' ? p : `[${p.ref.id}]`)).join(''), v.sub, v.age, v.tone]
  assert.deepEqual(line(row({ state: 'ok', park: pk })), ['on CI: local core-wt consumer suite PID 4143137', 'by Fri 14:59 PST', '4m', ''])
  const mk = (awaiting, extra = {}) => ({ state: 'ok', park: { wakeAt: S + 3600, awaiting, check: '', continued: false, parkedAt: S - 600, ...extra } })
  assert.deepEqual(line(row(mk({ kind: 'ci', ref: 'https://github.com/o/r/actions/runs/44830930932' }))), ['on CI: run 44830930932', 'by Fri 15:53 PST', '10m', ''])
  assert.deepEqual(line(row(mk({ kind: 'deploy', ref: 'https://github.com/o/r/pull/3824' }))), ['on deploy PR #3824', 'by Fri 15:53 PST', '10m', ''])
  assert.deepEqual(line(row(mk({ kind: 'deploy', ref: 'e4fd7587390a189fa2726896a7809979858d1e2d' }))), ['on deploy e4fd75873', 'by Fri 15:53 PST', '10m', ''])
  assert.deepEqual(line(row(mk({ kind: 'card', ref: 't_01a1eea7e9cf1de6' }))), ['on [t_01a1eea7e9cf1de6]', 'by Fri 15:53 PST', '10m', ''])
  assert.deepEqual(line(row(mk({ kind: 'external', ref: 'natural GitHub REST use/reset' }, { wakeAt: S - 3 * 3600 }))), ['on natural GitHub REST use/reset', '⚠ 3h overdue', '10m', 'warn'])
  assert.deepEqual(line(row(mk({ kind: 'clock', why: 'two-day soak' }, { wakeAt: S + 86_400 }))), ['1d left (two-day soak)', 'Sat 14:53 PST', '10m', ''])
  assert.deepEqual(line(row(mk(null, { check: 'python3 probe_quiet.py --pool x' }))), ['on probe probe_quiet.py', 'by Fri 15:53 PST', '10m', ''])
  assert.deepEqual(line(row(mk(null, { continued: true }))), ['resumes in 1h', 'Fri 15:53 PST', '10m', ''])
  assert.deepEqual(line(row(mk({ kind: 'human', why: 'pick a window' }))), ['waits on a person (pick a window)', '', '10m', 'warn'])
  // Loading, a failed read and an owner with no park.
  assert.deepEqual(line(row({ state: 'loading', park: null })).slice(0, 1), ['reading wake…'])
  assert.deepEqual(line(row({ state: 'failed', park: null })).slice(0, 1), ['no wake published'])
  assert.equal(m.parkFromDetail({ task: { status: 'scheduled' }, events: [] }), null)
  // Only rows whose own wait names nothing read the owner.
  const i = (chip, extra = {}) => ({ kind: 'kanban', chip: { state: 'scheduled', ...chip }, ...extra })
  assert.equal(m.needsPark(i({})), true)
  assert.equal(m.needsPark(i({ waitingOn: 'a producer the owner did not publish' })), true)
  assert.equal(m.needsPark(i({ waitingOn: 'ci run 1' })), false)
  assert.equal(m.needsPark(i({ upstream: { id: 't_u' } })), false)
  assert.equal(m.needsPark(i({ state: 'triage' })), false)
  assert.equal(m.needsPark(i({}, { cardStatus: true })), false)
})

test('owner park: one cached read per card on its owner route; failures retry after 2 minutes', async () => {
  const reads = []
  const read = (route, id) => { reads.push([route.connectionId, route.profile, route.board, id]); return Promise.resolve(id === 't_bad' ? { failed: true } : { park: { wakeAt: 1, awaiting: null, check: '', continued: false, parkedAt: null } }) }
  const chip = id => ({ id, connectionId: 'C1', profile: 'work', board: 'ops' })
  const B = Date.now()
  assert.equal(m.parkFor(chip('t_ok'), B, read).state, 'loading')
  await new Promise(r => setTimeout(r, 0))
  assert.equal(m.parkFor(chip('t_ok'), B + 1_000, read).state, 'ok')
  assert.deepEqual(reads, [['C1', 'work', 'ops', 't_ok']])
  m.parkFor(chip('t_ok'), Date.now() + 60_001, read)
  assert.equal(reads.length, 2, 'a park is re-read after a minute')
  m.parkFor(chip('t_bad'), B, read); await new Promise(r => setTimeout(r, 0))
  assert.equal(m.parkFor(chip('t_bad'), Date.now() + 50_000, read).state, 'failed')
  assert.equal(reads.filter(r => r[3] === 't_bad').length, 1)
  m.parkFor(chip('t_bad'), Date.now() + 2 * 60_000 + 1, read)
  assert.equal(reads.filter(r => r[3] === 't_bad').length, 2)
  assert.equal(m.parkFor({ id: 't_none', connectionId: null }, 1, read, () => null).state, 'failed', 'no route: no read')
})

test('owner park mirrors core: not scheduled, spent parks, implied wakes, a human block, a failed re-read', async () => {
  const NOW = Date.parse('2026-11-20T22:00:00Z'), S = NOW / 1000
  const ev = (id, kind, payload = {}, extra = {}) => ({ id, kind, created_at: S - 10_000 + id * 100, payload, ...extra })
  const park = ev(2, 'scheduled', { wake_at: S - 86_000, awaiting: { kind: 'ci', ref: 'https://github.com/o/r/actions/runs/123' } }, { run_id: 1 })
  // A blocked card: core wakes only scheduled cards, so no park is read (and rows never ask for one).
  assert.equal(m.parkFromDetail({ task: { status: 'blocked' }, events: [park] }), null)
  assert.equal(m.needsPark({ kind: 'kanban', chip: { state: 'blocked' } }), false)
  // Spent: woken, or claimed by another run -> superseded, backstop one hour after the last event.
  for (const later of [ev(3, 'continuation_woken'), ev(3, 'claimed', {}, { run_id: 2 }), ev(3, 'spawned', {}, { run_id: null })]) {
    const pk = m.parkFromDetail({ task: { status: 'scheduled' }, events: [ev(1, 'created'), park, later] })
    assert.deepEqual([pk.superseded, pk.wakeAt, pk.awaiting], [true, later.created_at + 3600, null], later.kind)
  }
  // The parking run's own claim does not spend it.
  assert.equal(m.parkFromDetail({ task: { status: 'scheduled' }, events: [park, ev(3, 'claimed', {}, { run_id: 1 })] }).superseded, false)
  // Events are ordered by id, not by array position.
  assert.equal(m.parkFromDetail({ task: { status: 'scheduled' }, events: [ev(3, 'continuation_woken'), park] }).superseded, true)
  // Implied wake: marker (with offset), else 24 h after the park.
  const legacy = ev(4, 'scheduled', { reason: 'soak SCHEDULED_UNTIL=2026-11-21T08:00:00-08:00.' })
  assert.equal(m.parkFromDetail({ task: { status: 'scheduled' }, events: [legacy] }).wakeAt, Date.parse('2026-11-21T16:00:00Z') / 1000)
  const bare = ev(5, 'scheduled', { reason: 'waiting' })
  assert.equal(m.parkFromDetail({ task: { status: 'scheduled' }, events: [bare] }).wakeAt, bare.created_at + 86_400)
  assert.equal(m.impliedWake({ reason: 'SCHEDULED_UNTIL=2030-01-01T00:00:00Z' }, S), S + 14 * 86_400, 'clamped to 14 days')
  // A human block kind and no timed wake: the dispatcher blocks it for a person.
  assert.equal(m.parkFromDetail({ task: { status: 'scheduled', block_kind: 'needs_input' }, events: [bare] }).forHuman, true)
  // No park event and a person block kind: core blocks it for a person, so no countdown.
  const noPark = m.parkFromDetail({ task: { status: 'scheduled', block_kind: 'needs_input' }, events: [ev(7, 'blocked')] })
  assert.deepEqual([noPark.forHuman, noPark.superseded], [true, false])
  // A date-only marker is local midnight.
  assert.equal(m.impliedWake({ reason: 'SCHEDULED_UNTIL=2026-11-22' }, S), new Date(2026, 10, 22, 0, 0).getTime() / 1000)
  // Lines.
  const row = pi => m.rowWait({ key: 'kb:x', kind: 'kanban', label: 'X', chip: { id: 't_x', state: 'scheduled' } }, NOW, pi)
  const spentPk = m.parkFromDetail({ task: { status: 'scheduled' }, events: [park, ev(3, 'continuation_woken')] })
  assert.deepEqual([row({ state: 'ok', park: spentPk }).reason[0], row({ state: 'ok', park: spentPk }).tone], ['new park not synced yet', ''])
  assert.equal(row({ state: 'ok', park: m.parkFromDetail({ task: { status: 'scheduled' }, events: [bare] }) }).reason[0].endsWith('left'), true, 'a legacy park has its implied deadline')
  assert.equal(row({ state: 'ok', park: m.parkFromDetail({ task: { status: 'scheduled', block_kind: 'needs_input' }, events: [bare] }) }).reason[0], 'no timed wake · will be blocked for a person')
  assert.equal(row({ state: 'ok', park: noPark }).reason[0], 'no timed wake · will be blocked for a person')
  const okPk = m.parkFromDetail({ task: { status: 'scheduled' }, events: [ev(6, 'scheduled', { wake_at: S + 3600, awaiting: { kind: 'deploy', ref: 'x' } })] })
  assert.match(row({ state: 'stale', park: okPk }).sub, / · not re-read$/)
  // parkFor: a failed re-read keeps the old park and marks it stale.
  let fail = false
  const read = () => Promise.resolve(fail ? { failed: true } : { park: okPk })
  const chip = { id: 't_stale', connectionId: 'C1', profile: 'default', board: 'default' }
  m.parkFor(chip, Date.now(), read); await new Promise(r => setTimeout(r, 0))
  fail = true
  m.parkFor(chip, Date.now() + 60_001, read); await new Promise(r => setTimeout(r, 0))
  const st = m.parkFor(chip, Date.now() + 60_002, read)
  assert.deepEqual([st.state, st.park === okPk], ['stale', true])
})
