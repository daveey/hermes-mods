import { test } from 'node:test'
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
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.panelHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.panelHarness.J;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':'export const host=globalThis.panelHarness.host,COMPOSER_AREAS={top:"composer.top"},useValue=a=>a?.get()'};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const m = await import('../plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))
const show = async node => React.act(async () => { root.render(node); await flush() })

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
  assert.deepEqual([...el().querySelectorAll('[data-count]')].map(x => x.textContent.replace('● ', '')), ['2 done', '1 running', '1 needs you', '1 stuck', '1 not started'])
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
  assert.deepEqual([...el().querySelectorAll('[data-count]')].map(x => x.textContent.replace('● ', '')), ['1 running'])
  assert.equal(el().querySelector('[data-fold^="Done"]'), null)
  void b
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
  assert.equal(el().querySelector('[data-session-status]').textContent, '●Agent working now')
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
