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
// Every mounted Panel starts polling. Clean up even when an assertion fails or
// a new rendered test is appended, otherwise node --test never exits.
afterEach(async () => { await show(null) })
after(async () => {
  await React.act(async () => { root.unmount() })
  dom.window.close()
})

const NOW = Date.parse('2026-10-29T15:00:00Z'), S = NOW / 1000
const card = (id, state, extra = {}) => ({ key: `kb:${id}`, kind: 'kanban', id, state, label: `Card ${id}`, connectionId: 'C1', profile: 'default', board: 'default', gateway: 'G1', since: S - 600, activityAt: S - 60, ...extra })

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
  assert.deepEqual(ids(out.active), ['kb:t_run', 'kb:t_rev', 'bg:1'])
  assert.deepEqual(ids(out.stuck), ['kb:t_old', 'kb:t_stall', 'sub:a'])
  assert.deepEqual(ids(out.waiting), ['kb:t_park'])
  assert.deepEqual(ids(out.queued), ['kb:t_todo'])
  assert.deepEqual(ids(out.unasked), ['kb:t_unasked'], 'a card an ask already covers is not listed twice')
  assert.match(out.stuck[0].detail, /^running, no progress since \d\d:\d\d \(2 h ago\) · last event: heartbeat$/)
  assert.match(out.waiting[0].detail, /^waiting on a timer since \d\d:\d\d$/)
  assert.match(out.stuck[2].detail, /^sub-agent · no new tool call for 30 min \(3 tool calls, last terminal\)$/)
  assert.deepEqual(m.counts(out, { asks: 1, done: 4 }), { done: 4, running: 2, needsYou: 2, stuck: 2, waiting: 1, queued: 1 })
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
  const s = m.classify([sub], { nowMs: NOW, silence: new Map([['sub:a', 45 * 60_000]]) }).stuck[0]
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
  assert.equal(b.calls[0].path, '/api/plugins/user-steps/session-cards?ids=S1%2CS0')
  assert.deepEqual([b.calls[0].connectionId, b.calls[0].profile], ['C1', 'default'])
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
  assert.deepEqual(folds, ['Not started · 1', 'Done · 2'])
  assert.deepEqual([...el().querySelectorAll('[data-fold^="Done"] [data-panel-row]')].map(r => r.firstChild.nextSibling.firstChild.textContent), ['Shipped B', 'Shipped A'])
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
  const lines = [...box.querySelectorAll('[data-waiting-line]')].map(x => x.textContent)
  assert.equal(lines.length, 3, 'at most three lines')
  assert.match(lines[0], /Slack follower loop.*waiting on deploy abc123/)
  assert.match(lines[1], /Mini link fix.*waiting on ci https:\/\/github.com\/o\/r\/pull\/1/)
  assert.match(lines[2], /Old park.*scheduled/)
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
  assert.equal(local.querySelector('[data-ask-step]').textContent, 'Do this: Sign in to the Telnyx portal through the agent browser when opened.')
  assert.equal(local.querySelector('[data-ask-why]').textContent, 'Why you: Primary portal authentication is absent from the vault.')
  assert.match(local.querySelector('[data-ask-next]').textContent, /^Next: When done, open the card and press Unblock \(continue staged/)
  for (const r of rows) assert.equal(r.querySelector('[data-ask-step]').textContent.includes('settled'), false, 'title/status never shown as the ask')
  assert.equal(el().textContent.includes('answer in chat or open the card'), false)
  // safe rendering: markup in the request is text, not elements
  assert.equal(remote.querySelector('img'), null)
  assert.equal(remote.querySelector('[data-ask-step]').textContent, `Do this: ${xss}`)
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

test('panel: a chat whose cards are all done shows no panel; new open work brings it back', async () => {
  window.localStorage.clear()
  const s = Date.now() / 1000
  const b = bridges({ chips: [card('t_done', 'done')], cards: [{ id: 't_done', status: 'done', title: 'Shipped', activity_at: s - 60 }] })
  try {
    await show(React.createElement(m.Panel, { key: 'all-green' }))
    await React.act(async () => { await m.refreshDone(); await flush() })
    assert.equal(el().querySelector('[data-session-status]'), null, 'all green: no panel')
    assert.equal(el().textContent, '')
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
  assert.deepEqual(pips, [['running', true, 'H'], ['running', false, ''], ['waiting', true, 'H'], ['queued', true, 'H'], ['queued', false, '']])
  assert.equal(el().querySelector('[data-progress-pips]').getAttribute('aria-label'), '2 running (1 on hub), 1 waiting (1 on hub), 2 not started (1 on hub)')
  assert.deepEqual(m.hubCounts(m.classify(window.__sessionTasks.snapshot.chips)), { running: 1, stuck: 0, waiting: 1, queued: 1 })
})
