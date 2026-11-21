import { test, afterEach, after } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve } from 'node:path'
import { homedir } from 'node:os'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.test' })
globalThis.window = dom.window; globalThis.document = dom.window.document
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const atom = value => ({ value, get() { return this.value } })
const drafts = []
const host = { state: { focusedSessionId: atom('R1'), focusedStoredSessionId: atom('S1'), busy: atom(false), connectionId: atom('C1'), profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' }) }, composer: { setDraft: (s, t) => drafts.push([s, t]), focus() {} } }
globalThis.panelHarness = { React, J, host }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.panelHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.panelHarness.J;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':'export const host=globalThis.panelHarness.host,Codicon=({name})=>null,DisclosureCaret=({open})=>null,COMPOSER_AREAS={top:"composer.top"},useValue=a=>a?.get()'};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const m = await import('../plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))
const show = async node => React.act(async () => { root.render(node); await flush() })
// A waiting row's line as `title | reason | age` (separate cells on screen).
const lineText = e => ['[data-wait-title]', '[data-wait-reason]', '[data-wait-age]'].map(q => e.querySelector(q)?.textContent || '').join(' | ')
// Every mounted Panel starts polling. Clean up even when an assertion fails or
// a new rendered test is appended, otherwise node --test never exits.
afterEach(async () => { await show(null) })
after(async () => {
  await React.act(async () => { root.unmount() })
  dom.window.close()
})

test('person dependency is linked and visible even without card work or with panel collapsed', async () => {
  host.state.focusedStoredSessionId.value = 'person-chat'
  window.localStorage.setItem('hermes.sessionPanel.collapsed.v1', '[]')
  window.__userSteps = { openAskCount: () => 0 }
  window.__sessionTasks = { snapshot: { stored: 'person-chat', chips: [] }, lineageIds: async s => new Set([s]) }
  const record = { id: 'person:example', session: 'person-chat', state: 'waiting', contact: 'Slack @ExampleOperator',
    thread: 'https://example.com/thread/123', response: 'deploy example', next_step: 'verify example staging' }
  const paths = []
  window.hermesDesktop = { api: async args => {
    paths.push(args)
    return args.path.includes('/person-waits?') ? { records: [record] } : { includes: ['done'], tasks: [] }
  } }
  // A visible window (jsdom without pretendToBeVisual reports document.hidden; the poll pauses while hidden).
  Object.defineProperty(document, 'hidden', { value: false, configurable: true })
  try {
    await show(J.jsx(m.Panel, {}))
    await React.act(async () => { await flush() })
  } finally { delete document.hidden }
  const link = el().querySelector('[data-person-wait] a')
  assert.ok(link, 'actual person link must render')
  assert.equal(link.href, record.thread)
  assert.equal(link.textContent, 'Slack @ExampleOperator')
  assert.match(el().textContent, /Waiting on Slack @ExampleOperator/)
  assert.match(el().textContent, /verify example staging/)
  assert.ok(paths.some(p => p.path.includes('/api/plugins/session-outcome/person-waits?') && p.connectionId === 'C1' && p.profile === 'default'))
  host.state.focusedStoredSessionId.value = 'other-chat'
  window.__sessionTasks.snapshot.stored = 'other-chat'
  window.hermesDesktop.api = async () => ({ includes: ['done'], tasks: [], records: [] })
  await show(J.jsx(m.Panel, {}))
  assert.ok(el().querySelector('[data-person-wait]') === null, 'other chat cannot inherit a wait')
  host.state.focusedStoredSessionId.value = 'S1'
})

test('person reply renders verification rather than completion and unsafe links are inert', async () => {
  const record = { id: 'person:example', state: 'verifying', contact: 'Slack @ExampleOperator',
    thread: 'https://example.com/reply', response: 'deploy example', next_step: 'verify staging' }
  await show(J.jsx(m.PersonWaitRows, { records: [record] }))
  assert.match(el().textContent, /Verifying reply from Slack @ExampleOperator/)
  assert.equal(el().querySelector('a').href, record.thread)
  await show(J.jsx(m.PersonWaitRows, { records: [{ ...record, thread: 'javascript:alert(1)' }] }))
  assert.ok(el().querySelector('a') === null)
  await show(J.jsx(m.PersonWaitRows, { records: [] }))
  assert.ok(el().querySelector('[data-person-wait]') === null)
})

test('finished disclosure combines done and archived and remembers expansion only per chat', async () => {
  window.localStorage.clear()
  m.resetDoneCacheForTests()
  host.state.focusedStoredSessionId.value = 'finished-A'
  window.__userSteps = { openAskCount: () => 0 }
  window.__sessionTasks = {
    snapshot: { stored: 'finished-A', chips: [card('open', 'review')] },
    lineageIds: async s => new Set([s])
  }
  window.hermesDesktop = { api: async () => ({ includes: ['done', 'archived'], tasks: [
    { id: 'done-one', title: 'Completed row', status: 'done' },
    { id: 'archived-one', title: 'Archived row', status: 'archived' }
  ] }) }
  await React.act(async () => { await m.refreshDone() })
  await show(J.jsx(m.Panel, {}))
  const toggle = () => el().querySelector('[data-finished-toggle]')
  assert.match(el().textContent, /Card open/)
  assert.equal(toggle()?.textContent, '2 finished')
  assert.equal(toggle().getAttribute('aria-expanded'), 'false')
  assert.doesNotMatch(el().textContent, /Completed row|Archived row/)
  const summary = el().querySelector('[data-status-summary]').textContent
  assert.match(summary, /1 done/)
  assert.doesNotMatch(summary, /2 done/)
  await React.act(async () => toggle().click())
  assert.match(el().textContent, /Completed row/)
  assert.match(el().textContent, /Archived row/)
  assert.equal(el().querySelector('[data-status-summary]').textContent, summary)
  host.state.focusedStoredSessionId.value = 'finished-B'
  window.__sessionTasks.snapshot.stored = 'finished-B'
  await show(J.jsx(m.Panel, {}))
  assert.equal(toggle()?.getAttribute('aria-expanded'), 'false')
  host.state.focusedStoredSessionId.value = 'finished-A'
  window.__sessionTasks.snapshot.stored = 'finished-A'
  await show(J.jsx(m.Panel, {}))
  assert.equal(toggle()?.getAttribute('aria-expanded'), 'true')
  await show(null)
  await show(J.jsx(m.Panel, {}))
  assert.equal(toggle()?.getAttribute('aria-expanded'), 'false', 'not persisted outside renderer state')
  host.state.focusedStoredSessionId.value = 'S1'
})

const NOW = Date.parse('2026-10-29T15:00:00Z'), S = NOW / 1000
const card = (id, state, extra = {}) => ({ key: `kb:${id}`, kind: 'kanban', id, state, label: `Card ${id}`, connectionId: 'C1', profile: 'default', board: 'default', gateway: 'G1', since: S - 600, activityAt: S - 60, ...extra })

test('blocked upstream is a distinct open bucket with precedence controls', () => {
  const blockedUpstream = { id: 'parent', status: 'triage' }
  const out = m.classify([
    card('held', 'todo', { blockedUpstream, runMachine: 'hub' }),
    card('plain', 'todo'),
    card('ask', 'blocked', { blockedUpstream, look: 'needs-you' }),
    card('stale', 'todo', { blockedUpstream, stale: true }),
    card('done', 'done', { blockedUpstream }),
    card('stall', 'scheduled', { blockedUpstream, look: 'stalled' })
  ], { nowMs: NOW })
  assert.deepEqual(out.blockedUpstream.map(i => i.id), ['held'])
  assert.equal(out.blockedUpstream[0].detail, 'waiting on blocked parent card parent (triage)')
  assert.deepEqual(out.queued.map(i => i.id), ['plain'])
  assert.deepEqual(out.unasked.map(i => i.id), ['ask'])
  assert.deepEqual(out.stuck.map(i => i.id), ['stall'])
  assert.deepEqual(out.waiting.map(i => i.id), ['stale'])
  // Shown as a waiting card whose chain ends on its parent's cause: counted as waiting, never pink.
  assert.deepEqual([m.counts(out).blockedUpstream, m.counts(out).waiting], [0, 2])
  assert.deepEqual([m.hubCounts(out).blockedUpstream, m.hubCounts(out).waiting], [0, 1])
  const only = m.classify([card('held', 'todo', { blockedUpstream })])
  assert.equal(m.hasOpenWork(only), true)
  m.annotateEvidence(only, NOW)
  assert.ok(only.blockedUpstream[0].evidenceText)
})

test('a blocked-upstream card is a waiting card whose chain shows its blocked parent (no pink section)', async () => {
  window.localStorage.clear()
  m.resetDoneCacheForTests()
  host.state.focusedStoredSessionId.value = 'pink'
  window.__userSteps = { openAskCount: () => 0 }
  window.hermesDesktop = { api: async a => (/tasks\/t_bb00000001/.test(a.path || '') ? { task: { id: 't_bb00000001', title: 'Parent work', status: 'blocked', block_kind: 'needs_input', human_request: { action: 'Approve the plan', why_human: 'only David decides', interaction: 'approval', scope: 'the plan', evidence: 'draft attached' }, session_id: '20261120_101010_abcdef' }, events: [], links: { parents: [] } } : { tasks: [] }) }
  window.__sessionTasks = { snapshot: { stored: 'pink', chips: [card('t_held00001', 'todo', {
    blockedUpstream: { id: 't_bb00000001', status: 'blocked' },
    boardStatus: { state: 'todo', readAt: Date.now(), source: 'owner' }
  })] }, openTaskChip() {} }
  try {
    await show(J.jsx(m.Panel, {}))
    await React.act(async () => { await flush(); await flush() })
    assert.equal(el().querySelector('[data-pip="blockedUpstream"]'), null)
    assert.equal(el().querySelector('[data-section="blocked-upstream"]'), null)
    assert.match(el().querySelector('[data-status-summary]').textContent, /1 waiting/)
    assert.equal(el().querySelector('[data-section="waiting"]').textContent, 'Waiting · 1')
    const line = el().querySelector('[data-folded-waiting] [data-wait-line]')
    assert.equal(line.querySelector('[data-root-cause]').getAttribute('data-root-cause'), 'you')
    assert.match(lineText(line), /^Card t_held00001 \| you· Approve the plan · via 1 card \| /)
    await React.act(async () => el().querySelector('[data-waiting-toggle]').click())
    await React.act(async () => { await flush(); await flush() })
    assert.ok(el().querySelector('[data-chain-card="t_bb00000001"]'))
    assert.equal(el().querySelector('[data-chain-leaf]').getAttribute('data-chain-leaf'), 'you')
  } finally { await show(null); host.state.focusedStoredSessionId.value = 'S1' }
})

test('classify sorts cards, sub-agents and processes into sections', () => {
  const memory = new Map()
  const sub = { key: 'sub:a', kind: 'sub', label: 'Drain queue', toolCount: 3, lastTool: 'terminal', since: S - 3600 }
  m.trackSubagents([sub], NOW - m.STUCK_SUB_MS - 1, memory) // first seen 30 min ago, no tool call since
  const silence = m.trackSubagents([sub], NOW, memory)
  const chips = [
    card('t_run', 'running'),
    card('t_old', 'running', { activityAt: S - m.STUCK_CARD_S - 5, since: S - m.STUCK_CARD_S - 100, activity: 'heartbeat' }),
    card('t_stall', 'blocked', { look: 'stalled', waitingOn: 'card t_x', waitingSince: S - 3 * 3600 }),
    card('t_park', 'scheduled', { look: 'waiting', waitingOn: 'a timer', waitingSince: S - 300 }),
    card('t_asked', 'blocked', { look: 'needs-you' }),
    card('t_unasked', 'blocked', { look: 'needs-you', waitingOn: 'you' }),
    card('t_rev', 'review'),
    card('t_todo', 'todo'),
    sub,
    { key: 'bg:1', kind: 'bg', state: 'running', label: 'npm test', since: S - 120 }
  ]
  const out = m.classify(chips, { nowMs: NOW, silence, isAsked: c => c.id === 't_asked' })
  const ids = list => list.map(i => i.key)
  assert.deepEqual(ids(out.active), ['kb:t_run', 'kb:t_rev'])
  assert.deepEqual(ids(out.stuck), ['kb:t_old', 'kb:t_stall'])
  assert.deepEqual(ids(out.waiting), ['kb:t_park', 'sub:a', 'bg:1'])
  assert.deepEqual(ids(out.queued), ['kb:t_todo'])
  assert.deepEqual(ids(out.unasked), ['kb:t_unasked'], 'a card an ask already covers is not listed twice')
  assert.match(out.stuck[0].detail, /^running, no progress since \d\d:\d\d \(2 h ago\) · last event: heartbeat$/)
  assert.match(out.waiting[0].detail, /^waiting on a timer for 5m$/)
  assert.match(out.waiting[1].detail, /unknown \(sdk projector unavailable\)/)
  assert.deepEqual(m.counts(out, { asks: 1, done: 4 }), { done: 4, running: 2, needsYou: 2, stuck: 2, blockedUpstream: 0, waiting: 3, ready: 0, queued: 1 },
    'sub-agents and processes count like the rows listed under each section')
})

test('a legacy sub-agent without evidence shows one waiting pip, never running', async () => {
  const out = m.classify([{ key: 'sub:solo', kind: 'sub', label: 'Fix it', toolCount: 70, lastTool: 'terminal' }], { nowMs: NOW })
  const n = m.counts(out, { done: 4 })
  assert.equal(n.running, 0)
  assert.equal(n.waiting, 1)
  await show(React.createElement(m.Pips, { n }))
  assert.equal(el().querySelectorAll('[data-pip="running"]').length, 0)
  assert.equal(el().querySelectorAll('[data-pip="waiting"]').length, 1)
  assert.equal(el().querySelectorAll('[data-pip="done"]').length, 4)
})

test('a running card just under the threshold, or with fresh activity, is not stuck', () => {
  const out = m.classify([
    card('t_a', 'running', { activityAt: S - m.STUCK_CARD_S + 30, since: S - 5 * 3600 }),
    card('t_b', 'running', { activityAt: 0, since: S - 60 })
  ], { nowMs: NOW })
  assert.deepEqual(out.stuck, [])
  assert.equal(out.active.length, 2)
})

test('a running card with no known last event is never stuck, however old its first start (kanban-f241d1f60b)', () => {
  // A hub card claimed 53 s earlier arrived with no event time and no running run; the panel used the first claim a
  // week earlier and drafted "no progress since Fri 23 23:05". Only a known progress time (last event, else the
  // current run's start) can make a card Stuck; `since` (first start / creation) is display only.
  const week = 7 * 86400
  const out = m.classify([
    card('t_hub', 'running', { activityAt: 0, since: S - week }),
    card('t_run', 'running', { activityAt: 0, progressAt: S - 3 * 3600, since: S - week })
  ], { nowMs: NOW })
  assert.deepEqual(out.stuck.map(i => i.key), ['kb:t_run'], 'a run 3 h old with no newer event is stuck')
  assert.deepEqual(out.active.map(i => i.key), ['kb:t_hub'])
  assert.match(out.active[0].detail, /last event unknown/)
  assert.equal(out.stuck[0].lastAt, S - 3 * 3600)
  assert.match(m.nudgeText(out.stuck[0], NOW), /no progress since \d\d:\d\d/)
})

test('a stale (last-known) card is never stuck or needs-you: its state is unknown, not evidence', () => {
  // closeout-notice-stale-decision: the owner read failed after the user approved a decision card, so session-tasks
  // kept the last-known "running" chip (stale) for hours after the card re-blocked and was archived; the panel
  // called it "no progress since 21:48" and drafted a nudge about a question already answered.
  const old = { activityAt: S - m.STUCK_CARD_S - 5, since: S - m.STUCK_CARD_S - 100, activity: 'heartbeat', stale: true }
  const sorted = m.classify([
    card('t_gone', 'running', old),
    card('t_ask', 'blocked', { look: 'needs-you', stale: true }),
    card('t_live', 'running', { ...old, stale: false })
  ], { nowMs: NOW })
  assert.deepEqual(sorted.stuck.map(i => i.id), ['t_live'])
  assert.deepEqual(sorted.unasked, [])
  assert.deepEqual(sorted.waiting.map(i => i.id), ['t_gone', 't_ask'])
  assert.match(sorted.waiting[0].detail, /status unknown · last known: running/)
  assert.doesNotMatch(sorted.waiting[0].detail, /no progress/)
})

test('trackSubagents: first sight counts from now, a new tool call resets, finished ones are forgotten', () => {
  const memory = new Map(), sub = n => ({ key: 'sub:x', kind: 'sub', toolCount: n })
  assert.equal(m.trackSubagents([sub(1)], NOW, memory).get('sub:x'), 0)
  assert.equal(m.trackSubagents([sub(1)], NOW + 40 * 60_000, memory).get('sub:x'), 40 * 60_000)
  assert.equal(m.trackSubagents([sub(2)], NOW + 41 * 60_000, memory).get('sub:x'), 0)
  m.trackSubagents([], NOW + 42 * 60_000, memory)
  assert.equal(memory.size, 0)
})

test('unblock steps and the nudge draft name what is stuck and since when', () => {
  const out = m.classify([
    card('t_old', 'running', { activityAt: S - 3 * 3600, since: S - 4 * 3600, activity: 'ssh timeout' }),
    card('t_stall', 'blocked', { look: 'stalled', waitingOn: 'card t_x on hub', waitingSince: S - 3 * 3600 })
  ], { nowMs: NOW })
  const [old, stall] = out.stuck
  const steps = m.unblockSteps(old, NOW)
  assert.equal(steps.length, 3)
  assert.match(steps[0], /^No events on this card since \d\d:\d\d \(last: ssh timeout\)\.$/)
  assert.match(steps[2], /comes back under Needs you/)
  assert.match(m.unblockSteps(stall, NOW)[0], /^It is waiting on card t_x on hub since \d\d:\d\d, and nothing is producing that\.$/)
  assert.match(m.nudgeText(old, NOW), /^Card t_old \("Card t_old"\) has had no progress since \d\d:\d\d \(last event: ssh timeout\)\. Check it, then either get it moving or tell me exactly what you need from me\.\n$/)
  assert.match(m.nudgeText(stall, NOW), /has been waiting on card t_x on hub since \d\d:\d\d with nothing producing it/)
  const sub = { key: 'sub:a', kind: 'sub', label: 'Drain', toolCount: 0 }
  // Test wording only: the classifier no longer infers a stall from tool-count silence.
  const s = { ...sub, quietMin: 45 }
  assert.equal(m.nudgeText(s, NOW), 'Sub-agent "Drain" has made no tool call for 45 min. Check on it: let it finish, restart it, or stop it, and tell me if you need anything from me.\n')
})

test('doneRows keeps finished cards once each, newest first', () => {
  const rows = m.doneRows([
    { id: 't_1', status: 'done', title: 'One', activity_at: 10 },
    { id: 't_1', status: 'done', title: 'One again', activity_at: 10 },
    { id: 't_1', status: 'done', board: 'ops', title: 'Other board', activity_at: 30 },
    { id: 't_2', status: 'running', title: 'Live' },
    { id: 't_3', status: 'done', title: 'Three', activity_at: 20 }
  ])
  assert.deepEqual(rows.map(r => r.label), ['Other board', 'Three', 'One'])
})

// ── rendered panel against fake session-tasks / user-steps bridges ───────────

function bridges({ chips, asks = 0, asked = () => false, cards = [] } = {}) {
  const taskSubs = new Set(), askSubs = new Set(), opened = []
  const snapshot = { runtime: 'R1', stored: 'S1', chips }
  window.__sessionTasks = {
    snapshot, subscribe: fn => (taskSubs.add(fn), () => taskSubs.delete(fn)), refresh() {}, openTaskChip: c => opened.push(c.id),
    lineageIds: async id => new Set([id, 'S0'])
  }
  window.__userSteps = {
    subscribe: fn => (askSubs.add(fn), () => askSubs.delete(fn)), openAskCount: () => asks, requestForSource: s => asked(s),
    snapshot: { ledger: { steps: [] }, issues: [] },
    ActionQueue: ({ embedded }) => J.jsx('div', { 'data-fake-queue': embedded ? 'embedded' : 'standalone' }),
    Strip: () => null
  }
  const calls = []
  window.hermesDesktop = { api: async args => { calls.push(args); return { tasks: cards, includes: ['done'] } } }
  return { opened, calls, push: next => taskSubs.forEach(fn => fn({ ...snapshot, chips: next })) }
}

test('panel: status line, progress bar, needs you, stuck with steps, in progress, folded done', async () => {
  window.localStorage.clear()
  const realNow = Date.now, s = realNow() / 1000
  const b = bridges({ asks: 1, chips: [
    card('t_run', 'running', { activityAt: s - 120, since: s - 600 }),
    card('t_old', 'running', { activityAt: s - 3 * 3600, since: s - 4 * 3600, activity: 'ssh timeout' }),
    card('t_todo', 'todo')
  ], cards: [{ id: 't_d1', status: 'done', title: 'Shipped A', activity_at: s - 600 }, { id: 't_d2', status: 'done', title: 'Shipped B', activity_at: s - 60 }, { id: 't_run', status: 'running' }] })
  await show(React.createElement(m.Panel, { key: 'full' }))
  await React.act(async () => { await m.refreshDone(); await flush() })
  const cardCall = b.calls.find(c => c.path.startsWith('/api/plugins/user-steps/session-cards?'))
  assert.equal(cardCall.path, '/api/plugins/user-steps/session-cards?ids=S1%2CS0')
  assert.deepEqual([cardCall.connectionId, cardCall.profile], ['C1', 'default'])
  assert.equal(el().querySelector('[data-session-status]').textContent, 'Idle · last activity 2 min ago')
  assert.deepEqual(el().querySelector('[data-status-summary]').textContent.split(' · '), ['1 needs you', '1 stuck', '1 running', '1 not started', '2 done'])
  // one box per counted item, in the header, no full-width bar
  assert.deepEqual([...el().querySelectorAll('[data-pip]')].map(x => x.dataset.pip), ['needsYou', 'stuck', 'running', 'queued', 'done', 'done'])
  assert.equal(el().querySelector('[data-progress-bar]'), null)
  assert.equal(el().querySelector('[data-progress-pips]').getAttribute('aria-label'), '1 needs you, 1 stuck, 1 running, 1 not started, 2 done')
  assert.deepEqual([...el().querySelectorAll('[data-section]')].map(x => x.textContent), ['Needs you · 1', 'Stuck · 1', 'In progress · 1'])
  assert.equal(el().querySelector('[data-fake-queue]').getAttribute('data-fake-queue'), 'embedded')
  const stuck = el().querySelector('[data-stuck="kb:t_old"]')
  assert.equal(stuck.querySelectorAll('[data-unblock-steps] li').length, 3)
  await React.act(async () => { stuck.querySelector('[data-nudge]').click(); await flush() })
  assert.equal(drafts.at(-1)[0], 'S1')
  assert.match(drafts.at(-1)[1], /^Card t_old \("Card t_old"\) has had no progress since/)
  await React.act(async () => { stuck.querySelector('[data-open-card]').click(); await flush() })
  assert.deepEqual(b.opened, ['t_old'])
  const folds = [...el().querySelectorAll('[data-fold]')].map(f => f.querySelector('summary').textContent)
  assert.deepEqual(folds, ['Not started · 1'])
  assert.equal(el().querySelector('[data-fold]').open, true, 'open queued work is visible by default')
  assert.equal(el().querySelector('[data-finished-toggle]').textContent, '2 finished')
  assert.doesNotMatch(el().textContent, /Shipped B|Shipped A/)
  await React.act(async () => el().querySelector('[data-finished-toggle]').click())
  assert.deepEqual([...el().querySelectorAll('[data-finished-cards] [data-panel-row]')].map(r => r.firstChild.nextSibling.firstChild.textContent), ['Shipped B', 'Shipped A'])
})

test('panel: an unmarked session-cards answer leaves done unknown instead of zeroing work', async () => {
  window.localStorage.clear()
  const b = bridges({ chips: [card('t_run', 'running', { activityAt: Date.now() / 1000 - 30 })] })
  window.hermesDesktop = { api: async () => ({ tasks: [{ id: 't_x', status: 'done' }] }) } // older owner / other route
  await show(React.createElement(m.Panel, { key: 'unmarked' }))
  await React.act(async () => { await m.refreshDone(); await flush() })
  assert.deepEqual(el().querySelector('[data-status-summary]').textContent.split(' · '), ['1 running'])
  assert.equal(el().querySelector('[data-fold^="Done"]'), null)
  void b
})

test('panel: the caret folds the whole panel to its header, like the task list; asks keep their colour', async () => {
  window.localStorage.clear()
  const s = Date.now() / 1000
  bridges({ chips: [card('t_ask', 'blocked', { look: 'needs-you' }), card('t_run', 'running', { activityAt: s - 30 })] })
  await show(React.createElement(m.Panel, { key: 'collapse-ask' }))
  const toggle = () => el().querySelector('[data-panel-toggle]')
  assert.equal(toggle().getAttribute('aria-expanded'), 'true')
  assert.ok(toggle().classList.contains('status-section-trigger'), 'same disclosure row as the composer status stack')
  assert.match(toggle().textContent, /This session/)
  await React.act(async () => { toggle().click(); await flush() })
  assert.equal(toggle().getAttribute('aria-expanded'), 'false')
  assert.equal(el().querySelector('[data-panel-body]'), null, 'body is folded')
  assert.equal(el().querySelector('[data-section="needs-you"]'), null, 'asks fold too: the toggle minimizes the panel')
  assert.equal(el().querySelector('[data-panel-row]'), null)
  const summary = el().querySelector('[data-collapsed-summary]')
  assert.match(summary.textContent, /1 needs you/)
  const ask = [...summary.querySelectorAll('span')].find(x => x.textContent === '1 needs you')
  assert.ok(ask && ask.style.color, 'the folded ask count keeps the needs-you colour, never muted')
  await React.act(async () => { toggle().click(); await flush() })
  assert.ok(el().querySelector('[data-section="needs-you"]'), 'expanding shows the asks again')
})

test('panel: canonical ask identity reopens the same-count queue and persists deliberate folding', async () => {
  window.localStorage.clear()
  bridges({ asks: 2, chips: [] })
  let keys = ['["gateway-a","default","ask-a",1]', '["gateway-a","default","ask-b",1]']
  window.__userSteps.openAskKeys = () => keys
  const mount = async () => show(J.jsx(m.Panel, { key: 'canonical-fold' }))
  const expanded = () => el().querySelector('[data-panel-toggle]').getAttribute('aria-expanded')
  await mount()
  await React.act(async () => el().querySelector('[data-panel-toggle]').click())
  assert.equal(expanded(), 'false', 'the user can deliberately fold the current asks')
  keys = [...keys].reverse()
  await mount()
  assert.equal(expanded(), 'false', 'polling/order changes do not reopen the same set')
  await show(null)
  await mount()
  assert.equal(expanded(), 'false', 'folding the same identities survives remount')
  keys = [keys[0], '["gateway-a","default","ask-c",1]']
  await mount()
  assert.equal(expanded(), 'true', 'a replacement ask reopens even though the count stays two')
  await React.act(async () => el().querySelector('[data-panel-toggle]').click())
  await show(null)
  await mount()
  assert.equal(expanded(), 'false', 'the replacement set can be deliberately folded too')
  keys = [keys[0], '["gateway-a","default","ask-c",2]']
  await mount()
  assert.equal(expanded(), 'true', 'a new request revision requires attention')
})

test('panel: legacy count-only collapse cannot hide current canonical asks', async () => {
  window.localStorage.setItem('hermes.sessionPanel.collapsed.v1', JSON.stringify(['asks:2']))
  bridges({ asks: 2, chips: [] })
  window.__userSteps.openAskKeys = () => ['owner-qualified-ask-a', 'owner-qualified-ask-b']
  await show(J.jsx(m.Panel, { key: 'legacy-count-fold' }))
  assert.equal(el().querySelector('[data-panel-toggle]').getAttribute('aria-expanded'), 'true')
})

test('panel: legacy ask fold does not collapse zero-attention running work', async () => {
  window.localStorage.setItem('hermes.sessionPanel.collapsed.v1', JSON.stringify(['asks:2']))
  bridges({ asks: 0, chips: [card('t_run', 'running', { activityAt: Date.now() / 1000 })] })
  await show(J.jsx(m.Panel, { key: 'legacy-empty-attention' }))
  assert.equal(el().querySelector('[data-panel-toggle]').getAttribute('aria-expanded'), 'true')
})

test('panel: answering the last folded ask reopens remaining running work', async () => {
  window.localStorage.clear()
  bridges({ asks: 1, chips: [card('t_run', 'running', { activityAt: Date.now() / 1000 })] })
  window.__userSteps.openAskKeys = () => ['owner-qualified-ask-a']
  const mount = () => show(J.jsx(m.Panel, { key: 'answered-last-ask' }))
  const expanded = () => el().querySelector('[data-panel-toggle]').getAttribute('aria-expanded')
  await mount()
  await React.act(async () => el().querySelector('[data-panel-toggle]').click())
  assert.equal(expanded(), 'false')
  window.__userSteps.openAskCount = () => 0
  window.__userSteps.openAskKeys = () => []
  await mount()
  assert.equal(expanded(), 'true', 'a fold made for an ask must not hide unrelated running work')
})

test('panel: deliberate zero-attention fold persists across remount', async () => {
  window.localStorage.clear()
  bridges({ asks: 0, chips: [card('t_run', 'running', { activityAt: Date.now() / 1000 })] })
  const mount = () => show(J.jsx(m.Panel, { key: 'deliberate-empty-fold' }))
  const expanded = () => el().querySelector('[data-panel-toggle]').getAttribute('aria-expanded')
  await mount()
  assert.equal(expanded(), 'true')
  await React.act(async () => el().querySelector('[data-panel-toggle]').click())
  assert.equal(expanded(), 'false')
  await show(null)
  await mount()
  assert.equal(expanded(), 'false', 'an explicit empty-attention fold is still remembered')
})

test('panel: a count-only bridge fails open rather than reusing a saved fold', async () => {
  window.localStorage.setItem('hermes.sessionPanel.collapsed.v1', JSON.stringify(['asks:2']))
  bridges({ asks: 2, chips: [] })
  await show(React.createElement(m.Panel, { key: 'identity-unavailable' }))
  assert.equal(el().querySelector('[data-panel-toggle]').getAttribute('aria-expanded'), 'true')
})

test('panel: a new ask reopens a folded panel', async () => {
  window.localStorage.clear()
  const s = Date.now() / 1000
  const b = bridges({ chips: [card('t_run', 'running', { activityAt: s - 30 })] })
  await show(React.createElement(m.Panel, { key: 'collapse-new-ask' }))
  await React.act(async () => { el().querySelector('[data-panel-toggle]').click(); await flush() })
  assert.equal(el().querySelector('[data-panel-body]'), null, 'collapsed')
  await React.act(async () => { b.push([card('t_run', 'running', { activityAt: s - 30 }), card('t_ask', 'blocked', { look: 'needs-you' })]); await flush() })
  assert.ok(el().querySelector('[data-panel-body]'), 'a new needs-you card reopens the panel')
})

test('panel: a collapsed panel names what each waiting card waits on, not just a count', async () => {
  window.localStorage.clear()
  bridges({ chips: [
    card('t_soak', 'scheduled', { look: 'waiting', waitingOn: 'deploy abc123 (gateway restart, then 30-minute soak)', label: 'Slack follower loop' }),
    card('t_pr', 'scheduled', { look: 'waiting', waitingOn: 'ci https://github.com/o/r/pull/1', label: 'Mini link fix' }),
    card('t_old', 'scheduled', { label: 'Old park' }),
    card('t_4', 'scheduled', { look: 'waiting', waitingOn: 'clock 11:00', label: 'Fourth' })
  ] })
  await show(React.createElement(m.Panel, { key: 'collapse-waiting' }))
  await React.act(async () => { el().querySelector('[data-panel-toggle]').click(); await flush() })
  assert.equal(el().querySelector('[data-panel-body]'), null, 'progress is folded')
  const box = el().querySelector('[data-collapsed-waiting]')
  assert.ok(box, 'waiting reasons stay visible while collapsed')
  const lines = [...box.querySelectorAll('[data-waiting-line]')].map(lineText)
  assert.equal(lines.length, 3, 'at most three lines')
  assert.match(lines[0], /^Slack follower loop \| external· deploy abc123/)
  assert.match(lines[1], /^Mini link fix \| external· CI on PR #1 \| /, 'a CI URL is shortened on the line')
  assert.match(lines[2], /^Old park \| unknown· no wake published \| /)
  assert.match(box.textContent, /\+1 more/)
})

test('panel: collapse holds until a new stuck item appears, then reopens', async () => {
  window.localStorage.clear()
  const s = Date.now() / 1000
  const b = bridges({ chips: [card('t_old', 'running', { activityAt: s - 3 * 3600, since: s - 4 * 3600 })] })
  await show(React.createElement(m.Panel, { key: 'collapse' }))
  await React.act(async () => { el().querySelector('[data-panel-toggle]').click(); await flush() })
  assert.equal(el().querySelector('[data-panel-body]'), null, 'collapsed')
  assert.match(el().querySelector('[data-collapsed-summary]').textContent, /1 stuck/)
  await React.act(async () => { b.push([card('t_old', 'running', { activityAt: s - 3 * 3600, since: s - 4 * 3600 }), card('t_new', 'blocked', { look: 'stalled', waitingOn: 'x' })]); await flush() })
  assert.ok(el().querySelector('[data-stuck="kb:t_new"]'), 'a new stuck item reopens the panel')
})

test('panel: busy session says so; nothing to show renders nothing', async () => {
  window.localStorage.clear()
  host.state.busy.value = true
  bridges({ chips: [card('t_run', 'running', { activityAt: Date.now() / 1000 - 30 })] })
  await show(React.createElement(m.Panel, { key: 'busy' }))
  assert.equal(el().querySelector('[data-session-status]').textContent, '●Agent replying in this chat')
  host.state.busy.value = false
  bridges({ chips: [] })
  window.hermesDesktop = { api: async () => ({ tasks: [], includes: ['done'] }) }
  await show(React.createElement(m.Panel, { key: 'empty' }))
  await React.act(async () => { await m.refreshDone(); await flush() })
  assert.equal(el().querySelector('[data-session-panel]'), null)
})

test('register marks the panel active (so the old strips hide) and unregisters cleanly', () => {
  const areas = []
  const dispose = m.default.register({ register: a => areas.push(a) })
  assert.equal(window.__sessionPanel.active, true)
  assert.deepEqual(areas.map(a => [a.id, a.area, a.order]), [['panel', 'composer.top', -30]])
  dispose()
  assert.equal(window.__sessionPanel, undefined)
})

// ── Needs you copy: the exact step, why, next — never the agent's card title or block reason ──────────────
// Regression: card t_b857cd9d showed its title "Phone credentials: Telnyx login for staged rotation; host-access
// choice settled" plus "answer in chat or open the card", while its structured human_request said what to do.
const TECH_TITLE = 'Phone credentials: Telnyx login for staged rotation; host-access choice settled'
const REQ = {
  action: 'Sign in to the Telnyx portal through the agent browser when opened. Unblock = continue staged credential rotation with read-only checks and no calls; archive = leave rotation undone and retain current credentials.',
  why: 'Primary portal authentication is absent from the vault.', interaction: 'external'
}

test('askView: a structured request becomes do-this / why-you / next; the technical title never appears', () => {
  const v = m.askView({ label: TECH_TITLE, humanRequest: REQ })
  assert.equal(v.known, true)
  assert.equal(v.step, 'Sign in to the Telnyx portal through the agent browser when opened.')
  assert.equal(v.why, 'Primary portal authentication is absent from the vault.')
  assert.equal(v.next, 'When done, open the card and press Unblock (continue staged credential rotation with read-only checks and no calls), or Archive if you don’t want it (leave rotation undone and retain current credentials). You can also reply in chat.')
  assert.equal(JSON.stringify(v).includes('host-access choice settled'), false)
  // No outcome clauses: a generic, still concrete next step.
  assert.match(m.askView({ humanRequest: { action: 'Plug the hub into power.' } }).next, /^When done, open the card and press Unblock, or reply in chat\./)
})

test('askView: no structured request → says the step is not shown and offers an agent question, never an invented action', () => {
  for (const hr of [undefined, null, {}, { action: '   ' }, { action: 42 }]) {
    const v = m.askView({ label: TECH_TITLE, humanRequest: hr })
    assert.equal(v.known, false)
    assert.equal(v.step, 'The exact step isn’t shown here.')
    assert.match(v.next, /Ask agent/)
    assert.equal(JSON.stringify(v).includes('Telnyx'), false, 'nothing is guessed from the title')
  }
  assert.equal(m.askAgentText({ id: 't_x' }), 'Card t_x: what exactly do you need me to do, and where? Please answer in one or two plain sentences.\n')
})

test('askView: long copy is clipped inline with an ellipsis; the full step stays available', () => {
  const long = `Open the billing page at https://example.com/billing and ${'confirm each line item carefully '.repeat(40)}`
  const v = m.askView({ humanRequest: { action: long, why: 'x'.repeat(900) } })
  assert.equal(v.step.length, m.ASK_MAX)
  assert.ok(v.step.endsWith('…'))
  assert.equal(v.full, long.trim())
  assert.ok(v.why.length <= 300 && v.why.endsWith('…'))
})

test('classify: archived, done, cancelled or resolved cards never need you, whatever wait they last reported', () => {
  const chips = ['archived', 'done', 'cancelled', 'resolved', 'blocked'].map(st => card(`t_${st}`, st, { look: 'needs-you', humanRequest: REQ }))
  const out = m.classify(chips, { nowMs: NOW })
  assert.deepEqual(out.unasked.map(i => i.id), ['t_blocked'])
})

test('panel: unasked rows show the step for local and remote cards; missing data asks the agent; text renders safely', async () => {
  window.localStorage.clear()
  drafts.length = 0
  const s = Date.now() / 1000
  const xss = '<img src=x onerror="globalThis.__pwned=1"> Reply "yes" in chat'
  const b = bridges({ chips: [
    card('t_local', 'blocked', { look: 'needs-you', label: TECH_TITLE, title: `t_local · blocked\n${TECH_TITLE}`, humanRequest: REQ, activityAt: s - 60 }),
    // a hub-replica card on another gateway: the qualified key carries gateway + profile
    card('t_remote', 'blocked', { key: 'kb:G2:default:default:t_remote', gateway: 'G2', gatewayId: 'G2', connectionId: 'C2', look: 'needs-you', label: 'Infra: settled gate, rotation staged', humanRequest: { action: xss, why: 'Only you can answer.' }, activityAt: s - 60 }),
    card('t_bare', 'blocked', { look: 'needs-you', label: 'Gate retained; architecture settled', activityAt: s - 60 }),
    card('t_canon', 'blocked', { look: 'needs-you', label: 'Covered by a canonical ask', humanRequest: REQ, activityAt: s - 60 })
  ], asks: 1, asked: src => src.id === 't_canon' })
  await show(React.createElement(m.Panel, { key: 'ask-rows' }))
  try {
  const rows = [...el().querySelectorAll('[data-ask-row]')]
  assert.deepEqual(rows.map(r => r.getAttribute('data-panel-row')), ['kb:t_local', 'kb:G2:default:default:t_remote', 'kb:t_bare'], 'canonical-covered card is not repeated')
  assert.equal(el().querySelector('[data-section="needs-you"]').textContent, 'Needs you · 4')
  const [local, remote, bare] = rows
  assert.equal(local.querySelector('[data-ask-step]').textContent, 'Sign in to the Telnyx portal through the agent browser when opened.')
  assert.equal(local.querySelector('[data-ask-why]').textContent, 'Why you: Primary portal authentication is absent from the vault.')
  assert.equal(local.querySelector('[data-ask-next]').textContent, 'Buttons draft your answer in the chat box; send it to decide.')
  assert.match(local.querySelector('[data-ask-details]').textContent, /Card buttons: Unblock = continue staged/, 'button mapping lives under Details')
  await React.act(async () => { local.querySelector('[data-ask-done]').click(); await flush() })
  assert.deepEqual(drafts.at(-1), ['S1', 'Card t_local: done.\n'])
  await React.act(async () => { local.querySelector('[data-ask-skip]').click(); await flush() })
  assert.deepEqual(drafts.at(-1), ['S1', 'Card t_local: skip this one; archive it.\n'])
  for (const r of rows) assert.equal(r.querySelector('[data-ask-step]').textContent.includes('settled'), false, 'title/status never shown as the ask')
  assert.equal(el().textContent.includes('answer in chat or open the card'), false)
  // safe rendering: markup in the request is text, not elements
  assert.equal(remote.querySelector('img'), null)
  assert.equal(remote.querySelector('[data-ask-step]').textContent, xss)
  assert.equal(globalThis.__pwned, undefined)
  // Open card routes the exact (remote) chip
  await React.act(async () => { remote.querySelector('[data-open-card]').click(); await flush() })
  assert.deepEqual(b.opened, ['t_remote'])
  // missing data: honest copy plus Ask agent (draft only), no Do this
  assert.equal(bare.getAttribute('data-ask-row'), 'unknown')
  assert.equal(bare.querySelector('[data-ask-step]').textContent, 'The exact step isn’t shown here.')
  assert.equal(local.querySelector('[data-ask-agent]'), null)
  await React.act(async () => { bare.querySelector('[data-ask-agent]').click(); await flush() })
  assert.deepEqual(drafts.at(-1), ['S1', m.askAgentText({ id: 't_bare' })])
  assert.equal(bare.querySelector('[data-ask-card]').getAttribute('title'), 'Gate retained; architecture settled', 'title only as hover context')
  } finally { await show(null) } // unmount: stops the panel's done poll so the file exits
})

test('hasOpenWork: finished cards alone are not open work; anything unfinished is', () => {
  const empty = { active: [], stuck: [], waiting: [], queued: [], unasked: [] }
  assert.equal(m.hasOpenWork(empty), false)
  assert.equal(m.hasOpenWork({ ...empty, waiting: [{ kind: 'kanban', chip: { state: 'done' } }, { kind: 'kanban', chip: { state: 'archived' } }] }), false)
  assert.equal(m.hasOpenWork(empty, { steps: [{ id: 's1', status: 'done' }] }), false)
  assert.equal(m.hasOpenWork({ ...empty, waiting: [{ kind: 'kanban', chip: { state: 'blocked' } }] }), true)
  assert.equal(m.hasOpenWork({ ...empty, active: [{ kind: 'sub', chip: {} }] }), true)
  assert.equal(m.hasOpenWork(empty, { asks: 1 }), true)
  assert.equal(m.hasOpenWork(empty, { steps: [{ id: 's1' }] }), true)
  assert.equal(m.hasOpenWork(empty, { issues: [{ id: 'i' }] }), true)
})

test('panel: a chat whose cards are all done retains its finished disclosure', async () => {
  window.localStorage.clear()
  const s = Date.now() / 1000
  const b = bridges({ chips: [card('t_done', 'done')], cards: [{ id: 't_done', status: 'done', title: 'Shipped', activity_at: s - 60 }] })
  try {
    await show(React.createElement(m.Panel, { key: 'all-green' }))
    await React.act(async () => { await m.refreshDone(); await flush() })
    assert.equal(el().querySelector('[data-finished-toggle]')?.textContent, '1 finished')
    assert.doesNotMatch(el().textContent, /Shipped/)
    await React.act(async () => el().querySelector('[data-finished-toggle]').click())
    assert.match(el().textContent, /Shipped/)
    await React.act(async () => { b.push([card('t_done', 'done'), card('t_run', 'running', { activityAt: s - 30 })]); await flush() })
    assert.ok(el().querySelector('[data-session-status]'), 'open work shows the panel')
    assert.deepEqual(el().querySelector('[data-status-summary]').textContent.split(' · '), ['1 running', '1 done'])
  } finally { await show(null) }
})

test('hub-scheduled running and waiting cards carry an H in their box; worker cards stay plain', async () => {
  window.localStorage.clear()
  const fresh = Date.now() / 1000 - 60
  bridges({ chips: [
    card('t_hub_run', 'running', { runMachine: 'hub', activityAt: fresh }),
    card('t_w_run', 'running', { runMachine: 'worker-1', activityAt: fresh }),
    card('t_hub_wait', 'scheduled', { runMachine: 'hub' }),
    card('t_hub_todo', 'ready', { runMachine: 'hub' }),
    card('t_pool', 'todo', { runMachine: 'worker-pool' })
  ] })
  await show(React.createElement(m.Panel, { key: 'hub' }))
  const pips = [...el().querySelectorAll('[data-pip]')].map(x => [x.dataset.pip, x.hasAttribute('data-pip-hub'), x.textContent])
  assert.deepEqual(pips, [['running', true, 'H'], ['running', false, ''], ['ready', true, 'H'], ['waiting', true, 'H'], ['queued', false, '']])
  assert.equal(el().querySelector('[data-progress-pips]').getAttribute('aria-label'), '2 running (1 on hub), 1 ready (1 on hub), 1 waiting (1 on hub), 1 not started')
  assert.deepEqual(m.hubCounts(m.classify(window.__sessionTasks.snapshot.chips)), { running: 1, stuck: 0, blockedUpstream: 0, waiting: 1, ready: 1, queued: 0 })
  const colour = k => window.getComputedStyle(el().querySelector(`[data-pip="${k}"]`))
  assert.equal(m.C.ready, '#3fb950', 'green = ready')
  assert.equal(m.C.needsYou, '#f85149', 'red = blocked on me')
  assert.equal(m.C.done, '#8b949e', 'grey = done')
  assert.equal(m.C.waiting, '#d29922', 'yellow = not ready'); assert.equal(m.C.queued, '#d29922')
  assert.ok(/3fb950|63, 185, 80/.test(colour('ready').backgroundColor), 'ready pip is filled green')
  assert.ok(/d29922|210, 153, 34/.test(colour('queued').borderColor || colour('queued').border), 'not-started pip is yellow')
})

test('rows lead with their state box, not a dot; hub cards carry the H there too', async () => {
  window.localStorage.clear()
  const fresh = Date.now() / 1000 - 60
  bridges({ chips: [
    card('t_hub_run', 'running', { runMachine: 'hub', activityAt: fresh }),
    card('t_w_run', 'running', { runMachine: 'worker-1', activityAt: fresh }),
    card('t_hub_wait', 'scheduled', { runMachine: 'hub' }),
    card('t_w_wait', 'scheduled', { runMachine: 'worker-1' })
  ] })
  await show(React.createElement(m.Panel, { key: 'row-pips' }))
  await React.act(async () => el().querySelector('[data-waiting-toggle]').click())
  const rows = [...el().querySelectorAll('[data-panel-row]')]
  assert.ok(rows.length >= 4)
  assert.ok(!rows.some(r => r.textContent.includes('●')), 'no bullet dots left in rows')
  const marks = Object.fromEntries(rows.map(r => { const x = r.querySelector('[data-row-pip]'); return [r.getAttribute('data-panel-row'), [x.dataset.rowPip, x.hasAttribute('data-row-pip-hub'), x.textContent]] }))
  assert.deepEqual(marks['kb:t_hub_run'], ['running', true, 'H'])
  assert.deepEqual(marks['kb:t_w_run'], ['running', false, ''])
  assert.deepEqual(marks['kb:t_hub_wait'], ['waiting', true, 'H'])
  assert.deepEqual(marks['kb:t_w_wait'], ['waiting', false, ''])
  assert.equal(el().querySelectorAll('[data-pip]').length, 4, 'header pips unchanged by row marks')
})


test('waitKind sorts waiting rows into named kinds, never a catch-all "other"', () => {
  const out = m.classify([
    card('t_sched', 'scheduled', { look: 'waiting', waitingOn: 'ci https://github.com/o/r/actions/runs/1' }),
    card('t_bare', 'scheduled'),
    card('t_card', 'scheduled', { look: 'waiting', waitingOn: 'card t_x on hub' }),
    card('t_gone', 'running', { stale: true }),
    card('t_triage', 'triage', { look: 'waiting', waitingOn: 'triage' }),
    { key: 'sub:a', kind: 'sub', label: 'Legacy sub-agent' }
  ], { nowMs: NOW })
  assert.deepEqual(Object.fromEntries(out.waiting.map(i => [i.key, m.waitKind(i)])),
    { 'kb:t_sched': 'schedule', 'kb:t_bare': 'schedule', 'kb:t_card': 'parent', 'kb:t_gone': 'unknown', 'kb:t_triage': 'triage', 'sub:a': 'unknown' })
  // A todo card reaches Waiting only through a fresh board read; it waits on its parents.
  assert.equal(m.waitKind({ kind: 'kanban', detail: 'todo · board status', chip: { state: 'todo' } }), 'parent')
  assert.equal(m.waitKind({ kind: 'kanban', detail: 'status unknown (missing qualified identity) · last known: running', chip: { state: 'running' } }), 'unknown',
    'a status-unknown row is never guessed from its last known state')
})

test('panel: Waiting folds to the root-cause counts plus one line per card, and opens to the chains per chat', async () => {
  window.localStorage.clear()
  bridges({ chips: [
    card('t_w1', 'scheduled', { look: 'waiting', waitingOn: 'the clock until 2099-01-01 00:00 UTC', label: 'Timer card' }),
    card('t_w2', 'scheduled', { look: 'waiting', waitingOn: 'ci https://github.com/o/r/actions/runs/5', label: 'CI card' }),
    card('t_w3', 'running', { stale: true, label: 'Lost card' })
  ] })
  await show(React.createElement(m.Panel, { key: 'waiting-fold' }))
  const toggle = () => el().querySelector('[data-waiting-toggle]')
  assert.equal(el().querySelector('[data-section="waiting"]').textContent, 'Waiting · 3')
  assert.deepEqual([...el().querySelectorAll('[data-waiting-breakdown] [data-cause]')].map(e => e.textContent), ['2 external', '1 unknown'])
  assert.equal(toggle().getAttribute('aria-expanded'), 'false')
  assert.equal(el().querySelectorAll('[data-wait-chain]').length, 0, 'no chains while folded')
  const lines = [...el().querySelectorAll('[data-folded-waiting] [data-wait-line]')].map(lineText)
  assert.match(lines[0], /^Timer card \| external· \d+d( \d+h)? left, \w{3} \d+ \d\d:\d\d P[SD]T \| $/)
  assert.equal(lines[1], 'CI card | external· CI run 5 | ')
  assert.match(lines[2], /^Lost card \| unknown· status unknown/)
  await React.act(async () => toggle().click())
  assert.equal(toggle().getAttribute('aria-expanded'), 'true')
  assert.equal(el().querySelector('[data-folded-waiting]'), null, 'open: chains replace the one-liners')
  assert.equal(el().querySelectorAll('[data-wait-chain]').length, 3)
  await React.act(async () => toggle().click())
  assert.ok(el().querySelector('[data-folded-waiting]'), 'folded again')
})

test('panel: folded Waiting lists 3 one-liners then "+N more"; ready / plain todo render in their own folds; a retired ask renders in full', async () => {
  window.localStorage.clear()
  const fresh = state => ({ boardStatus: { state, readAt: Date.now(), source: 'owner' } })
  bridges({ asked: () => true, chips: [
    card('t_a', 'scheduled', { look: 'waiting', waitingOn: 'clock 11:00', label: 'A' }),
    card('t_b', 'scheduled', { look: 'waiting', waitingOn: 'clock 12:00', label: 'B' }),
    card('t_c', 'scheduled', { look: 'waiting', waitingOn: 'clock 13:00', label: 'C' }),
    card('t_d', 'scheduled', { look: 'waiting', waitingOn: 'clock 14:00', label: 'D' }),
    card('t_ready', 'ready', { label: 'Ready one', ...fresh('ready') }),
    card('t_todo', 'todo', { label: 'Todo one', ...fresh('todo') }),
    card('t_ask', 'blocked', { look: 'needs-you', label: 'Retired ask' })
  ] })
  window.__userSteps.presentationForSource = s => (s.id === 't_ask' ? { representedInQueue: false, dismissed: true } : null)
  await show(React.createElement(m.Panel, { key: 'fold-more' }))
  const folded = el().querySelector('[data-folded-waiting]')
  assert.equal(folded.querySelectorAll('[data-line-pip="waiting"]').length, 3, 'each one-liner carries the yellow waiting box')
  assert.deepEqual([...folded.querySelectorAll('[data-wait-line] [data-wait-title]')].map(e => e.textContent), ['A', 'B', 'C'])
  assert.match(folded.textContent, /\+1 more waiting$/, 'D only: the retired ask is not a one-liner')
  assert.doesNotMatch(folded.textContent, /Retired ask/)
  assert.match(el().querySelector('[data-card-status]').textContent, /t_ask · blocked · request dismissed/, 'retired ask status row renders in full while folded')
  assert.match(el().querySelector('[data-fold="Ready · 1"]').textContent, /Ready one.*ready · waiting for a worker to claim it · board status/)
  assert.match(el().querySelector('[data-fold="Not started · 1"]').textContent, /Todo one.*not started · board status/)
  assert.doesNotMatch(el().querySelector('[data-waiting-breakdown]').textContent, /other/)
})

test('panel: a chain through this chat\'s running card ends there; its card opens from the chain', async () => {
  window.localStorage.clear()
  const b = bridges({ chips: [
    card('t_aaaaaaaa', 'running', { label: 'Parent build', activityAt: Date.now() / 1000 - 30 }),
    card('t_kid', 'scheduled', { look: 'waiting', waitingOn: 'card t_aaaaaaaa', label: 'Child' })
  ] })
  await show(React.createElement(m.Panel, { key: 'wait-link' }))
  const line = el().querySelector('[data-folded-waiting] [data-wait-line]')
  assert.equal(lineText(line), 'Child | running· running on G1 · via 1 card | ')
  await React.act(async () => el().querySelector('[data-waiting-toggle]').click())
  const chain = el().querySelector('[data-wait-chain="kb:t_kid"]')
  assert.equal(chain.querySelector('[data-wait-reason]').textContent, 'waiting on running work · 2 hops')
  assert.equal(chain.querySelector('[data-chain-card="t_aaaaaaaa"] [data-this-chat]').textContent, 'this chat')
  await React.act(async () => { chain.querySelector('[data-wait-card="t_aaaaaaaa"]').click() })
  assert.deepEqual(b.opened, ['t_aaaaaaaa'])
  // The root's title opens its long form; Open card there opens the waiting card itself.
  await React.act(async () => { chain.querySelector('[data-wait-toggle]').click() })
  assert.match(chain.querySelector('[data-row-detail]').textContent, /waiting on card "Parent build" \(t_aaaaaaaa\)/)
  await React.act(async () => { chain.querySelector('[data-open-card]').click() })
  assert.deepEqual(b.opened, ['t_aaaaaaaa', 't_kid'])
})

test('panel: a stuck card\'s stalled wait links its card too', async () => {
  window.localStorage.clear()
  const b = bridges({ chips: [
    card('t_bbbbbbbb', 'running', { label: 'Producer', activityAt: Date.now() / 1000 - 30 }),
    card('t_st', 'scheduled', { look: 'stalled', waitingOn: 'card t_bbbbbbbb', label: 'Stalled one' })
  ] })
  await show(React.createElement(m.Panel, { key: 'stuck-link' }))
  const link = el().querySelector('[data-wait-card="t_bbbbbbbb"]')
  assert.equal(link.textContent, '"Producer" (t_bbbbbbbb)')
  await React.act(async () => { link.click() })
  assert.deepEqual(b.opened, ['t_bbbbbbbb'])
})

test('panel: a well-overdue clock wake says so on its line (external, with the flag)', async () => {
  window.localStorage.clear()
  const now = Date.now() / 1000, due = new Date((now - 3 * 3600) * 1000)
  const utc = `${due.toISOString().slice(0, 10)} ${due.toISOString().slice(11, 16)} UTC`
  bridges({ chips: [card('t_late', 'scheduled', { look: 'waiting', waitingOn: `the clock until ${utc}`, waitingSince: now - 86_400, label: 'Late one' })] })
  await show(React.createElement(m.Panel, { key: 'overdue' }))
  const line = el().querySelector('[data-folded-waiting] [data-wait-line]')
  assert.equal(line.querySelector('[data-root-cause]').getAttribute('data-root-cause'), 'external')
  assert.match(lineText(line), /^Late one \| external· ⚠ 3h( 1m)? overdue, due \w{3} \d\d:\d\d P[SD]T \| 1d$/)
})

test('panel: collapsed, each waiting card keeps its one line; a retired ask keeps its plain status row', async () => {
  window.localStorage.clear()
  bridges({ asked: () => true, chips: [
    card('t_cccccccc', 'running', { label: 'Upstream', activityAt: Date.now() / 1000 - 30 }),
    card('t_kid2', 'scheduled', { look: 'waiting', waitingOn: 'card t_cccccccc', label: 'Kid' }),
    card('t_0123abcd', 'blocked', { look: 'needs-you', label: 'Retired ask' })
  ] })
  window.__userSteps.presentationForSource = s => (s.id === 't_0123abcd' ? { representedInQueue: false, dismissed: true } : null)
  await show(React.createElement(m.Panel, { key: 'collapsed-link' }))
  assert.equal(el().querySelector('[data-card-status] [data-wait-card]'), null)
  assert.match(el().querySelector('[data-card-status]').textContent, /t_0123abcd · blocked · request dismissed/)
  await React.act(async () => el().querySelector('[data-panel-toggle]').click())
  const line = el().querySelector('[data-collapsed-waiting] [data-waiting-line="kb:t_kid2"]')
  assert.equal(lineText(line), 'Kid | running· running on G1 · via 1 card | ')
})

test('panel: one-line rows carry their box (yellow waiting, the hub "H"); a blocked-upstream card is a waiting line', async () => {
  window.localStorage.clear()
  bridges({ chips: [
    card('t_w', 'scheduled', { look: 'waiting', waitingOn: 'clock 11:00', label: 'Waits' }),
    card('t_h', 'scheduled', { look: 'waiting', waitingOn: 'clock 12:00', label: 'Hub waits', runMachine: 'hub' }),
    card('t_bu', 'todo', { label: 'Held', blockedUpstream: { id: 't_par', status: 'blocked' } })
  ] })
  await show(React.createElement(m.Panel, { key: 'line-pips' }))
  const folded = el().querySelector('[data-folded-waiting]')
  assert.equal(folded.querySelector('[data-waiting-line="kb:t_w"] [data-line-pip]').getAttribute('data-line-pip'), 'waiting')
  const hub = folded.querySelector('[data-waiting-line="kb:t_h"] [data-line-pip]')
  assert.equal(hub.hasAttribute('data-line-pip-hub'), true)
  assert.equal(hub.textContent, 'H')
  assert.equal(folded.querySelector('[data-waiting-line="kb:t_bu"] [data-line-pip]').getAttribute('data-line-pip'), 'waiting')
})

test('panel: chains cross chats: each card names the chat that owns it (opens it), ending on the leaf', async () => {
  window.localStorage.clear()
  const s = Date.now() / 1000
  const b = bridges({ chips: [card('t_a1a0000001', 'scheduled', { look: 'waiting', waitingOn: 'card t_c1c0000001', waitingSince: s - 600, label: 'Ship it' })] })
  const opens = []
  host.sessions = { rows: { get: () => [{ id: '20261120_090000_aaaaaa', title: 'BB release planning' }] } }
  host.openSession = async (id, opts) => { opens.push([id, opts?.route?.connectionId]) }
  window.hermesDesktop = { api: async a => {
    if (/tasks\/t_c1c0000001/.test(a.path)) return { task: { id: 't_c1c0000001', title: 'Choose the window', status: 'todo', block_kind: 'dependency', session_id: '20261120_090000_aaaaaa' }, events: [], links: { parents: ['t_e1e0000001'] }, link_tasks: [{ id: 't_e1e0000001', status: 'ready' }] }
    if (/tasks\/t_e1e0000001/.test(a.path)) return { task: { id: 't_e1e0000001', title: 'Build the release', status: 'ready', session_id: 'local-abc' }, events: [], links: { parents: [] }, queue_note: 'current: 3 cards ahead on worker-a1' }
    return { tasks: [], includes: ['done'] } } }
  try {
    await show(React.createElement(m.Panel, { key: 'chains-cross' }))
    for (let n = 0; n < 4; n++) await React.act(async () => { await flush(); await flush() })
    await React.act(async () => el().querySelector('[data-waiting-toggle]').click())
    for (let n = 0; n < 4; n++) await React.act(async () => { await flush(); await flush() })
    const chain = el().querySelector('[data-wait-chain="kb:t_a1a0000001"]')
    assert.equal(chain.querySelector('[data-wait-reason]').textContent, 'waiting on a worker slot · 3 hops')
    assert.deepEqual([...chain.querySelectorAll('[data-chain-card]')].map(e => e.getAttribute('data-chain-card')), ['t_c1c0000001', 't_e1e0000001'])
    const chip = chain.querySelector('[data-chain-card="t_c1c0000001"] [data-open-session]')
    assert.match(chip.textContent, /BB release planning/)
    await React.act(async () => { chip.click() })
    assert.deepEqual(opens, [['20261120_090000_aaaaaa', 'C1']])
    assert.equal(chain.querySelector('[data-chain-card="t_e1e0000001"] [data-open-session]'), null, 'a local-* session is not a chat')
    const leaf = chain.querySelector('[data-chain-leaf]')
    assert.equal(leaf.getAttribute('data-chain-leaf'), 'slot')
    assert.match(leaf.textContent, /waiting for a worker: 3 cards ahead on worker-a1/)
    assert.deepEqual(b.opened, [])
  } finally { delete host.sessions; delete host.openSession }
})

test('panel: a chain that runs into another waiting card of this chat shows "same chain as … above"', async () => {
  window.localStorage.clear()
  bridges({ chips: [
    card('t_a0000001', 'scheduled', { label: 'Card A', look: 'waiting', waitingOn: 'ci https://github.com/o/r/actions/runs/7' }),
    card('t_b0000002', 'scheduled', { label: 'Card B', look: 'waiting', waitingOn: 'card t_a0000001' })
  ] })
  await show(React.createElement(m.Panel, { key: 'chains-same' }))
  await React.act(async () => el().querySelector('[data-waiting-toggle]').click())
  const b = el().querySelector('[data-wait-chain="kb:t_b0000002"]')
  assert.equal(b.querySelector('[data-wait-reason]').textContent, 'waiting on CI run 7 · 2 hops')
  assert.match(b.querySelector('[data-chain-same]').textContent, /^same chain as “Card A” above$/)
  assert.equal(b.querySelector('[data-chain-leaf]'), null)
})

test('panel: the Waiting heading counts cards by root cause', async () => {
  window.localStorage.clear()
  bridges({ chips: [
    card('t_a0000001', 'scheduled', { label: 'A', look: 'waiting', waitingOn: 'the clock until 2099-01-01 00:00 UTC' }),
    card('t_b0000002', 'triage', { label: 'B', look: 'waiting', waitingOn: 'triage' }),
    card('t_c0000003', 'scheduled', { label: 'C', look: 'waiting', waitingOn: 'ci https://github.com/o/r/actions/runs/9' })
  ] })
  await show(React.createElement(m.Panel, { key: 'causes' }))
  assert.deepEqual([...el().querySelectorAll('[data-waiting-breakdown] [data-cause]')].map(e => [e.getAttribute('data-cause'), e.textContent]), [['external', '2 external'], ['slot', '1 worker slot']])
})

test('panel: a scheduled card with no published wait shows its owner\'s wake, read from the card', async () => {
  window.localStorage.clear()
  const s = Date.now() / 1000
  const b = bridges({ chips: [card('t_park0001', 'scheduled', { label: 'Parked one' })] })
  window.hermesDesktop = { api: async a => (a.path.startsWith('/api/plugins/kanban/tasks/t_park0001')
    ? { task: { status: 'scheduled', wake_check: null }, events: [{ id: 9, kind: 'continued', created_at: s - 300, payload: { wake_at: s + 1800, awaiting: { kind: 'ci', ref: 'local suite PID 7', why: 'suite running' } } }] }
    : { tasks: [], includes: ['done'] }) }
  await show(React.createElement(m.Panel, { key: 'owner-park' }))
  await React.act(async () => { await flush(); await flush() })
  const line = el().querySelector('[data-folded-waiting] [data-waiting-line]')
  assert.match(lineText(line), /^Parked one \| external· CI: local suite PID 7, 2\dm left · \w{3} \d\d:\d\d P[SD]T \| 5m$/)
  assert.doesNotMatch(el().textContent, /no wake published/)
  void b
})

test('panel: a retired ask counts as waiting on you and keeps a collapsed line', async () => {
  window.localStorage.clear()
  bridges({ asked: () => true, chips: [
    card('t_a0000001', 'scheduled', { label: 'A', look: 'waiting', waitingOn: 'ci https://github.com/o/r/actions/runs/9' }),
    card('t_0123abcd', 'blocked', { look: 'needs-you', label: 'Retired ask' })
  ] })
  window.__userSteps.presentationForSource = s => (s.id === 't_0123abcd' ? { representedInQueue: false, dismissed: true } : null)
  await show(React.createElement(m.Panel, { key: 'retired-count' }))
  assert.deepEqual([...el().querySelectorAll('[data-waiting-breakdown] [data-cause]')].map(e => e.textContent), ['1 you', '1 external'])
  await React.act(async () => el().querySelector('[data-panel-toggle]').click())
  const line = el().querySelector('[data-collapsed-waiting] [data-waiting-line="kb:t_0123abcd"]')
  assert.equal(lineText(line), 'Retired ask | you· still blocked; its ask was retired | ')
})

test('panel: a card another gateway owns opens its chat on that gateway; with no connection it is named only', async () => {
  window.localStorage.clear()
  const opens = []
  host.sessions = { rows: { get: () => [] } }
  host.openSession = async (id, opts) => { opens.push([id, opts?.route?.connectionId]) }
  const b = bridges({ chips: [card('t_a9a9000001', 'scheduled', { look: 'waiting', waitingOn: 'card t_c9c9000001', label: 'Root' })] })
  window.__sessionTasks.connectionForHubGateway = gw => (gw === 'worker-a1' ? 'CW' : null)
  window.hermesDesktop = { api: async a => {
    if (/tasks\/t_c9c9000001/.test(a.path)) return { source: 'replica', owner: 'worker-a1', task: { id: 't_c9c9000001', title: 'Remote', status: 'running', session_id: '20261120_090000_bbbbbb' }, events: [], links: { parents: [] } }
    if (/\/api\/sessions\//.test(a.path)) return { title: a.connectionId === 'CW' ? 'Worker chat' : 'WRONG' }
    return { tasks: [], includes: ['done'] } } }
  try {
    await show(React.createElement(m.Panel, { key: 'owner-gw' }))
    await React.act(async () => el().querySelector('[data-waiting-toggle]').click())
    for (let n = 0; n < 4; n++) await React.act(async () => { await flush(); await flush() })
    const chip = el().querySelector('[data-chain-card="t_c9c9000001"] [data-open-session]')
    assert.match(chip.textContent, /Worker chat/)
    await React.act(async () => { chip.click() })
    assert.deepEqual(opens, [['20261120_090000_bbbbbb', 'CW']])
    void b
  } finally { delete host.sessions; delete host.openSession }
})

// [daveey] Switching chats shows the panel at once: last-known data paints first, reads replace it.
test('panel: a chat whose runtime id has not landed yet (resume) still shows its cards', async () => {
  window.localStorage.clear()
  const b = bridges({ chips: [card('t_run', 'running', { activityAt: Date.now() / 1000 - 30 })] })
  window.__sessionTasks.snapshot.runtime = null // session-tasks painted the stored chat before resume finished
  await show(React.createElement(m.Panel, { key: 'no-runtime' }))
  assert.deepEqual(el().querySelector('[data-status-summary]').textContent.split(' · '), ['1 running'])
  void b
})

test('panel: a revisited chat shows its last-known Done count before the session-cards read answers', async () => {
  window.localStorage.clear()
  m.resetDoneCacheForTests()
  const s = Date.now() / 1000
  bridges({ chips: [card('t_run', 'running', { activityAt: s - 30 })], cards: [{ id: 't_d1', status: 'done', title: 'Shipped A', activity_at: s - 60 }] })
  await show(React.createElement(m.Panel, { key: 'first-visit' }))
  await React.act(async () => { await m.refreshDone(); await flush() })
  assert.deepEqual(el().querySelector('[data-status-summary]').textContent.split(' · '), ['1 running', '1 done'])
  await show(null)
  // Back to the chat later: the read now hangs, the cached count still paints on the first render.
  m.resetDoneCacheForTests() // a relaunch: memory gone, localStorage kept
  let release
  window.hermesDesktop = { api: () => new Promise(r => { release = r }) }
  await show(React.createElement(m.Panel, { key: 'revisit' }))
  assert.deepEqual(el().querySelector('[data-status-summary]').textContent.split(' · '), ['1 running', '1 done'])
  assert.equal(m.cachedDone(JSON.stringify(['C1', 'default', 'S1'])).length, 1)
  release?.({ tasks: [], includes: ['done'] })
})

test('panel: switching chats kicks user-steps (queue and strip) instead of waiting for its poll', async () => {
  window.localStorage.clear()
  bridges({ chips: [] })
  const kicks = []
  window.__userSteps.refresh = () => kicks.push('refresh')
  window.__userSteps.refreshRequests = () => kicks.push('requests')
  await show(React.createElement(m.Panel, { key: 'kick' }))
  assert.deepEqual(kicks.sort(), ['refresh', 'requests'], 'nothing open yet: the panel is hidden but still asked for this chat\'s asks')
})


const setHidden = v => Object.defineProperty(document, 'hidden', { value: v, configurable: true }) // jsdom reports hidden by default
// [daveey] Recent chats stay warm in the background so the first switch to them paints from a fresh cache.
test('warmRecent: warms recent chats other than the focused one, once per interval, never while hidden', async () => {
  window.localStorage.clear()
  m.resetWarmForTests(); m.resetDoneCacheForTests(); setHidden(false)
  bridges({ chips: [] })
  const warmed = []
  window.__sessionTasks.warm = async r => { warmed.push(['tasks', r.session]); return true }
  window.__userSteps.warm = async r => { warmed.push(['steps', r.session]); return true }
  window.hermesDesktop = { api: async ({ path }) => ({ tasks: [{ id: `t_${path.slice(-2)}`, status: 'done', title: 'Shipped', activity_at: 1 }], includes: ['done'] }) }
  for (const s of ['S3', 'S2', 'S1']) m.noteRecent({ connectionId: 'C1', profile: 'default', session: s }) // S1 = focused, most recent
  assert.deepEqual(m.readRecent().map(r => r.session), ['S1', 'S2', 'S3'])
  assert.deepEqual(await m.warmRecent(), [], 'focus just moved: wait for it to settle')
  const t0 = Date.now() + m.WARM_SETTLE_MS
  assert.deepEqual((await m.warmRecent({ nowMs: t0 })).map(r => r.session), ['S2', 'S3'])
  assert.deepEqual(warmed, [['tasks', 'S2'], ['steps', 'S2'], ['tasks', 'S3'], ['steps', 'S3']], 'one chat at a time, focused chat skipped')
  assert.equal(m.cachedDone(JSON.stringify(['C1', 'default', 'S2'])).length, 1, 'Done rows warmed too')
  assert.deepEqual(await m.warmRecent({ nowMs: t0 + 1000 }), [], 'not again within the interval')
  setHidden(true)
  try { assert.deepEqual(await m.warmRecent({ nowMs: t0 + m.WARM_EVERY_MS + 1 }), [], 'never while the window is hidden') } finally { setHidden(false) }
  assert.deepEqual((await m.warmRecent({ nowMs: t0 + m.WARM_EVERY_MS + 1 })).map(r => r.session), ['S2', 'S3'])
  delete document.hidden
})

test('warmRecent: one failing source never stops the pass; the recent list is capped', async () => {
  window.localStorage.clear()
  m.resetWarmForTests(); setHidden(false)
  bridges({ chips: [] })
  window.__sessionTasks.warm = async () => { throw new Error('hub down') }
  window.__userSteps.warm = async () => true
  window.hermesDesktop = { api: async () => { throw new Error('504') } }
  for (let i = 0; i < m.WARM_RECENT + 3; i++) m.noteRecent({ connectionId: 'C1', profile: 'default', session: `X${i}` })
  assert.equal(m.readRecent().length, m.WARM_RECENT)
  const done = await m.warmRecent({ force: true })
  assert.equal(done.length, m.WARM_RECENT, 'every recent chat attempted despite failures')
  delete document.hidden
})

test('a closed remote card whose owner never confirmed it is listed as reported, not as waiting', () => {
  const rep = (id, reported, extra = {}) => card(id, 'unknown', { gateway: 'worker-5', reported, boardStatus: { state: 'unknown', readAt: NOW, source: 'replica' }, ...extra })
  const chips = [rep('t_d', 'done'), rep('t_a', 'archived'), rep('t_ask', 'done', { look: 'needs-you' }), rep('t_stale', 'done', { stale: true }), card('t_u', 'unknown', { boardStatus: { state: 'unknown', readAt: NOW, source: 'replica' } }), card('t_park', 'scheduled', { look: 'waiting', waitingOn: 'a timer' })]
  const out = m.classify(chips, { nowMs: NOW })
  const ids = list => list.map(i => i.key)
  assert.deepEqual(ids(out.reported), ['kb:t_d', 'kb:t_a', 'kb:t_ask'])
  assert.equal(out.reported[0].detail, 'reported done by worker-5 · not confirmed by its owner')
  assert.equal(out.reported[1].detail, 'reported archived by worker-5 · not confirmed by its owner')
  assert.deepEqual(ids(out.waiting), ['kb:t_stale', 'kb:t_u', 'kb:t_park'], 'a stale read or a card with no report stays status unknown under Waiting')
  assert.deepEqual(out.unasked, [], 'a card its owner reports closed asks nothing')
  assert.equal(m.counts(out).waiting, 3)
  assert.equal(m.counts(out, { done: 0 }).done, 0, 'a reported card is never counted as done')
  assert.equal(m.annotateEvidence(out, NOW), 0, 'reported rows are not counted as evidence unknown')
  assert.equal(m.hasOpenWork({ ...m.classify([rep('t_d', 'done')], { nowMs: NOW }) }), true, 'an unconfirmed card keeps the panel shown')
})

test('reported cards render collapsed under their own count and expand on click', async () => {
  window.localStorage.clear()
  m.resetDoneCacheForTests()
  host.state.focusedStoredSessionId.value = 'reported-A'
  window.__userSteps = { openAskCount: () => 0 }
  const rep = id => card(id, 'unknown', { gateway: 'worker-5', reported: 'done', boardStatus: { state: 'unknown', readAt: Date.now(), source: 'replica' } })
  window.__sessionTasks = { snapshot: { stored: 'reported-A', chips: [rep('t_1'), rep('t_2')] }, lineageIds: async s => new Set([s]) }
  await show(J.jsx(m.Panel, {}))
  assert.equal(el().querySelector('[data-section="waiting"]'), null)
  assert.match(el().querySelector('[data-status-summary]').textContent, /2 reported finished, unconfirmed/)
  assert.doesNotMatch(el().textContent, /status unknown|evidence unknown/)
  const button = el().querySelector('[data-reported-toggle]')
  assert.equal(button.textContent, '2 reported finished, not confirmed')
  assert.equal(el().querySelectorAll('[data-reported-cards] [data-panel-row]').length, 0)
  await React.act(async () => { button.click(); await flush() })
  assert.equal(el().querySelectorAll('[data-reported-cards] [data-panel-row]').length, 2)
  assert.match(el().querySelector('[data-reported-cards]').textContent, /reported done by worker-5 · not confirmed by its owner/)
})

// The screenshot David flagged (2026-11-20): one running hub card took three lines, "running · default · just now"
// and a 230-character evidence line. A running row is now one line: box, title, where + how long.
test('a running card is one line: title, where it runs and for how long; details on click', async () => {
  const realNow = Date.now
  Date.now = () => NOW
  try {
    const title = 'Fix: prompt queued behind core-update hold is stranded when the old backend outlives the update'
    host.state.focusedStoredSessionId.value = 'S1'
    const b = bridges({ chips: [card('t_01a1f6400580d812', 'running', { label: title, runMachine: 'hub', assignee: 'default', gateway: 'hub', since: S - 12 * 60, activityAt: S - 30, activity: 'heartbeat' })] })
    await show(React.createElement(m.Panel, { key: 'running-one-line' }))
    const row = el().querySelector('[data-running-row]')
    assert.equal(row.querySelector('[data-running-title]').textContent, title)
    assert.equal(row.querySelector('[data-running-meta]').textContent, 'hub · 12 min')
    assert.equal(row.textContent, `H${title}hub · 12 min`, 'nothing else on the row: no state word, no assignee, no evidence')
    assert.equal(el().querySelector('[data-section="in-progress"]'), null, 'the header already counts it')
    assert.equal(el().querySelector('[data-evidence]'), null)
    assert.match(row.querySelector('button').title, /running on hub · for 12 min · last event heartbeat just now/)
    await React.act(async () => { row.querySelector('button').click(); await flush() })
    const details = el().querySelector('[data-running-details]').textContent
    assert.match(details, /card t_01a1f6400580d812 · assignee default · on hub/)
    assert.match(details, /started 12 min ago · last event heartbeat just now/)
    assert.match(details, /evidence: /)
    await React.act(async () => { el().querySelector('[data-running-details] [data-open-card]').click(); await flush() })
    assert.deepEqual(b.opened, ['t_01a1f6400580d812'])
    await React.act(async () => { row.querySelector('button').click(); await flush() })
    assert.equal(el().querySelector('[data-running-details]'), null, 'second click folds it again')
  } finally { Date.now = realNow }
})

test('running meta: quiet after 20 min without an event, pool and worker names, review, sub-agents', () => {
  const at = (extra, kind = 'kanban') => ({ kind, chip: card('t', 'running', extra) })
  assert.deepEqual(m.runningMeta(at({ runMachine: 'worker-pool', since: S - 3 * 3600, activityAt: S - 60 }), NOW).text, 'pool · 3 h')
  const quiet = m.runningMeta(at({ runMachine: 'worker-1', since: S - 3600, activityAt: S - 25 * 60 }), NOW)
  assert.deepEqual([quiet.text, quiet.quiet], ['worker-1 · quiet 25 min', true])
  assert.equal(m.runningMeta(at({ runMachine: null, gateway: '', since: 0, activityAt: 0, progressAt: 0 }), NOW).text, '')
  assert.equal(m.runningMeta({ kind: 'kanban', chip: card('t', 'review', { activityAt: S - 7200 }) }, NOW).text, 'in review · 2 h')
  assert.equal(m.runningMeta({ kind: 'sub', chip: { toolCount: 14, lastTool: 'terminal' } }, NOW).text, 'sub-agent · 14 tools')
  assert.equal(m.runningMeta({ kind: 'bg', chip: {} }, NOW).text, 'process')
})

test('running meta shows the run model between worker and runtime', () => {
  const at = extra => ({ kind: 'kanban', chip: card('t', 'running', extra) })
  const deep = m.runningMeta(at({ runMachine: 'worker-a2', runModel: 'class:deep', since: S - 20 * 60, activityAt: S - 60 }), NOW)
  assert.equal(deep.text, 'worker-a2 · deep · 20 min')
  assert.match(deep.tip, /running on worker-a2 · model class deep · for 20 min/)
  const pinned = m.runningMeta(at({ runMachine: 'hub', runModel: 'claude-opus-5-5', since: S - 600, activityAt: S - 60 }), NOW)
  assert.equal(pinned.text, 'hub · opus-5-5 · 10 min')
  assert.match(pinned.tip, /model claude-opus-5-5/)
  assert.equal(m.runningMeta(at({ runMachine: 'hub', runModel: null, since: S - 600, activityAt: S - 60 }), NOW).text, 'hub · 10 min')
  assert.equal(m.runningMeta({ kind: 'kanban', chip: card('t', 'review', { runModel: 'gpt-6-astra', activityAt: S - 7200 }) }, NOW).text, 'in review · gpt-6-astra · 2 h')
  assert.match(m.runningDetails(at({ runModel: 'class:deep', assignee: 'default' }), NOW)[0], /assignee default · model class:deep/)
})

test('a quiet running card renders its meta in the waiting colour, still one line', async () => {
  const realNow = Date.now
  Date.now = () => NOW
  try {
    host.state.focusedStoredSessionId.value = 'S1'
    bridges({ chips: [card('t_q', 'running', { runMachine: 'worker-1', since: S - 3600, activityAt: S - 40 * 60 })] })
    await show(React.createElement(m.Panel, { key: 'running-quiet' }))
    const meta = el().querySelector('[data-running-meta]')
    assert.equal(meta.textContent, 'worker-1 · quiet 40 min')
    assert.ok(meta.hasAttribute('data-quiet'))
  } finally { Date.now = realNow }
})

test('askView: a brief from generate-unblock-card is passed through for the Needs you row', () => {
  const brief = { situation: 'The fix is installed; one check needs a debug run.', options: ['A) One supervised run', 'B) Accept the gap'], recommend: 'A — covers the bug you hit.' }
  const v = m.askView({ humanRequest: { action: 'Choose. Unblock = A. Archive = B.', why: 'Tried: x.', brief } })
  assert.deepEqual(v.brief, brief)
  assert.equal(m.askView({ humanRequest: { action: 'Plug it in.' } }).brief, null)
  assert.equal(m.askView({ humanRequest: { action: 'Plug it in.', brief: { situation: '', options: [] } } }).brief, null)
})

test('askView: "Unblock = A. Archive = B." splits like "Unblock = A; archive = B"', () => {
  for (const action of ['Choose. Unblock = A (one run). Archive = B (accept).', 'Choose. Unblock = A (one run); archive = B (accept).']) {
    assert.equal(m.askView({ humanRequest: { action } }).next, 'When done, open the card and press Unblock (A (one run)), or Archive if you don’t want it (B (accept)). You can also reply in chat.')
  }
})

test('askView parts: option tiles, recommended letter, why split (redesigned decision card)', () => {
  assert.deepEqual(m.optionParts('A) One idle-timed test run: an agent restarts Desktop (~10 min). Reversible.'),
    { letter: 'A', name: 'One idle-timed test run', detail: 'An agent restarts Desktop (~10 min). Reversible.', full: 'A) One idle-timed test run: an agent restarts Desktop (~10 min). Reversible.' })
  assert.deepEqual(m.optionParts('B) Accept with the known gaps — nothing restarts.').name, 'Accept with the known gaps')
  assert.equal(m.optionParts('Option 2: keep it').letter, '2')
  assert.deepEqual(m.recommendParts('A - two short reloads cover the bug'), { letter: 'A', reason: 'two short reloads cover the bug' })
  assert.deepEqual(m.recommendParts('Option B because it is cheaper'), { letter: 'B', reason: 'it is cheaper' })
  assert.deepEqual(m.recommendParts('plug it in at the desk'), { letter: '', reason: 'plug it in at the desk' })
  assert.deepEqual(m.whyParts('Tried: rung a (x). Only you can: allow a restart.'), { line: 'Only you can: allow a restart.', tried: 'rung a (x).' })
  assert.deepEqual(m.whyParts('Spend needs the owner.'), { line: 'Why you: Spend needs the owner.', tried: '' })
  assert.equal(m.chooseText({ id: 't_x' }, { letter: 'A', name: 'One run' }), 'Card t_x: go with option A (One run).\n')
})

test('decision card DOM: Choose drafts the pick; an unmatched recommendation still shows; unlettered options work', async () => {
  window.localStorage.clear()
  drafts.length = 0
  const brief = (options, recommend) => ({ situation: 'The release is staged and one approval is missing before it ships.', options, recommend })
  bridges({ chips: [
    card('t_dec', 'blocked', { look: 'needs-you', label: 'Pick the release path', humanRequest: { action: 'Pick. Unblock = A; archive = B.', why: 'Tried: x. Only you can: approve a release.', brief: brief(['A) Ship now: today; reversible.', 'B) Wait a week: nothing ships.'], 'A - it is ready') } }),
    card('t_free', 'blocked', { look: 'needs-you', label: 'Pick a fix', humanRequest: { action: 'Pick one.', why: 'Only you can: decide.', brief: brief(['Roll back: safe and slow.', 'Hotfix: fast and risky.'], 'Roll back because it is safe') } }),
    card('t_one', 'blocked', { look: 'needs-you', label: 'Plug in the ring', humanRequest: { action: 'Plug the ring charger in.', why: 'Only you can: touch the ring.', interaction: 'physical', brief: brief(['A) Plug it in: 1 minute.'], 'A') } }),
    card('t_none', 'blocked', { look: 'needs-you', label: 'No request' })
  ] })
  await show(React.createElement(m.Panel, { key: 'decision-dom' }))
  try {
    const row = id => el().querySelector(`[data-panel-row="kb:${id}"]`)
    await React.act(async () => { row('t_dec').querySelector('[data-ask-choose="B"]').click(); await flush() })
    assert.deepEqual(drafts.at(-1), ['S1', 'Card t_dec: go with option B (Wait a week).\n'])
    assert.equal(row('t_dec').querySelector('[data-ask-choose="A"]').getAttribute('aria-label'), 'Choose option A: Ship now')
    // unlettered options: tiles without letters, the recommendation shown as its own line
    const free = row('t_free')
    assert.equal(free.getAttribute('data-ask-row'), 'decision')
    assert.deepEqual([...free.querySelectorAll('[data-ask-option-name]')].map(n => n.textContent), ['Roll back', 'Hotfix'])
    assert.equal(free.querySelectorAll('[data-recommended]').length, 0)
    assert.equal(free.querySelector('[data-ask-recommend]').textContent, 'Recommended: Roll back because it is safe')
    await React.act(async () => { free.querySelectorAll('[data-ask-choose]')[0].click(); await flush() })
    assert.deepEqual(drafts.at(-1), ['S1', 'Card t_free: go with this option (Roll back).\n'])
    // full option text is reachable without hover
    assert.match(row('t_dec').querySelector('[data-ask-details]').textContent, /Options in full: A\) Ship now: today; reversible\./)
    // one option: a step card that still shows the option and the recommendation
    const one = row('t_one')
    assert.equal(one.getAttribute('data-ask-row'), 'known')
    assert.equal(one.querySelector('[data-ask-single-option]').textContent, 'A) Plug it in: 1 minute.')
    assert.equal(one.querySelector('[data-ask-recommend]').textContent, 'Recommended: A')
    // no request: the hint is visible, not hidden in an empty Details
    const none = row('t_none')
    assert.match(none.querySelector('[data-ask-hint]').textContent, /Ask agent/)
    const nd = none.querySelector('[data-ask-details]')
    assert.equal(!nd || nd.querySelectorAll('div').length > 0, true, 'Details, when present, is never empty')
    // Open card is a sibling of Details, never inside <summary>
    assert.equal(el().querySelectorAll('summary [data-open-card]').length, 0)
    assert.ok(el().querySelectorAll('[data-open-card]').length > 0)
  } finally { await show(null) }
})

test('situation More: offered for long text, toggles, and is absent for short text', async () => {
  window.localStorage.clear()
  const long = 'The release is staged and one approval is missing before it ships. '.repeat(4).trim()
  bridges({ chips: [
    card('t_long', 'blocked', { look: 'needs-you', label: 'Long', humanRequest: { action: 'Do it.', why: 'Only you can: x.', brief: { situation: long, options: [], recommend: '' } } }),
    card('t_short', 'blocked', { look: 'needs-you', label: 'Short', humanRequest: { action: 'Do it.', why: 'Only you can: x.', brief: { situation: 'Short situation text that fits on a line.', options: [], recommend: '' } } })
  ] })
  await show(React.createElement(m.Panel, { key: 'more' }))
  try {
    const more = el().querySelector('[data-panel-row="kb:t_long"] [data-ask-more]')
    assert.equal(more.textContent, 'More')
    await React.act(async () => { more.click(); await flush() })
    assert.equal(more.textContent, 'Less')
    assert.equal(more.getAttribute('aria-expanded'), 'true')
    assert.equal(el().querySelector('[data-panel-row="kb:t_short"] [data-ask-more]'), null)
  } finally { await show(null) }
})

test('recommendParts / whyParts edge cases', () => {
  assert.deepEqual(m.recommendParts('A, B both ok'), { letter: '', reason: 'A, B both ok' })
  assert.deepEqual(m.recommendParts('B.'), { letter: 'B', reason: '' })
  assert.deepEqual(m.recommendParts('Recommendation: A - cheap'), { letter: 'A', reason: 'cheap' })
  assert.deepEqual(m.recommendParts('I recommend A'), { letter: '', reason: 'I recommend A' })
  assert.deepEqual(m.whyParts('Tried: x, y'), { line: 'Agent tried: x, y', tried: '' })
  assert.deepEqual(m.whyParts('Needs your password; only you can sign in.'), { line: 'Why you: Needs your password; only you can sign in.', tried: '' })
  assert.deepEqual(m.whyParts('Only you can approve the spend.'), { line: 'Only you can: approve the spend.', tried: '' })
})

test('situation More re-measures on resize (ResizeObserver)', async () => {
  window.localStorage.clear()
  const observers = []
  const prevRO = window.ResizeObserver
  window.ResizeObserver = class { constructor(cb) { this.cb = cb; observers.push(this) } observe(el) { this.el = el } disconnect() { this.off = true } }
  bridges({ chips: [card('t_rs', 'blocked', { look: 'needs-you', label: 'Resize', humanRequest: { action: 'Do it.', why: 'Only you can: x.', brief: { situation: 'Short situation text that fits on one line.', options: [], recommend: '' } } })] })
  await show(React.createElement(m.Panel, { key: 'resize' }))
  try {
    const sit = el().querySelector('[data-panel-row="kb:t_rs"] [data-ask-situation]')
    assert.equal(el().querySelector('[data-panel-row="kb:t_rs"] [data-ask-more]'), null)
    const ro = observers.find(o => o.el === sit)
    assert.ok(ro, 'the situation is observed')
    Object.defineProperty(sit, 'clientHeight', { configurable: true, get: () => 40 })
    Object.defineProperty(sit, 'scrollHeight', { configurable: true, get: () => 90 })
    await React.act(async () => { ro.cb(); await flush() })
    assert.equal(el().querySelector('[data-panel-row="kb:t_rs"] [data-ask-more]')?.textContent, 'More')
  } finally { await show(null); window.ResizeObserver = prevRO }
})
