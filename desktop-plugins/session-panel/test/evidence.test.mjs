// Rendered session-panel against the SDK projector: expiry on existing ticks only, legacy asks preserved,
// mixed-version rows explicit unknown. Verdicts come from the core's projector (HERMES_CORE_SOURCE) over the shared
// fixture base; without a core the projector-backed tests skip (see ../../session-tasks/test/execution-support.mjs).
import { test, afterEach, after } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve } from 'node:path'
import { homedir } from 'node:os'
import { caseInput, loadFixture, loadProjector, sdkStubSource } from '../../session-tasks/test/execution-support.mjs'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.test' })
globalThis.window = dom.window; globalThis.document = dom.window.document
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const atom = value => ({ value, get() { return this.value } })
const projector = await loadProjector()
globalThis.__obsSdk = projector || undefined
globalThis.__obsReact = React
globalThis.__obsJsx = J
globalThis.__obsHost = { state: { focusedSessionId: atom('R1'), focusedStoredSessionId: atom('S1'), busy: atom(false), connectionId: atom('C1'), profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' }) }, composer: { setDraft() {}, focus() {} } }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.__obsReact;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.__obsJsx;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':${JSON.stringify(sdkStubSource())}};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const m = await import('../plugin.js')
const tasks = await import('../../session-tasks/plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))
const show = async node => React.act(async () => { root.render(node); await flush() })
// Waiting rows are folded by default (README "Waiting says what each card waits on"): open the fold before reading them.
const openWaiting = async () => {
  const t = el().querySelector('[data-waiting-toggle]')
  if (t && t.getAttribute('aria-expanded') !== 'true') await React.act(async () => { t.click(); await flush() })
  // A waiting row is one line; its long form (full wait text, evidence) opens on click.
  for (const r of el().querySelectorAll('[data-wait-toggle][aria-expanded=false]')) await React.act(async () => { r.click(); await flush() })
}
afterEach(async () => { await show(null) })
after(async () => { await React.act(async () => { root.unmount() }); dom.window.close() })

test('fresh board status survives a parked card with no execution generation', { skip: !projector }, async () => {
  at(10)
  const raw = record('t_x', { set: { 'identity.gateway': 'hub', 'identity.profile': 'default', 'identity.board': 'default', state: 'waiting', source: 'kanban.card', observed_at: null, valid_until: null, source_revision: null, evidence_refs: [] }, delete: ['identity.run_id'] })
  const chips = tasks.sessionTaskChips([{ id: 't_x', title: 'Wait for release', status: 'scheduled', board: 'default', execution_observations: [raw] }], { connectionId: 'C1', profile: 'default' })
  const b = bridges({ chips })
  await show(React.createElement(m.Panel, { key: 'board-status' }))
  await openWaiting()
  assert.match(el().querySelector('[data-wait-reason]').textContent, /^no wake published$/)
  assert.equal(el().querySelector('[data-row-detail]').textContent, 'scheduled · its owner has not published what wakes it')
  assert.match(el().querySelector('[data-wait-ident]').textContent, /^t_x · scheduled.* · read from board status$/)
  assert.match(el().querySelector('[data-evidence]').textContent, /hub\/default\/default\/card:t_x/)
  // A parked card has no run: its unknown execution evidence is not a counted gap (README).
  assert.doesNotMatch(el().querySelector('[data-status-summary]').textContent, /evidence unknown/)
  assert.equal(m.chipObservation(chips[0], BASE_AT + 10).state, 'unknown', 'no fake execution generation')
  at(71)
  await React.act(async () => { b.tick(); await flush() })
  assert.match(el().querySelector('[data-panel-row]').textContent, /status unknown/)
  assert.match(el().querySelector('[data-status-summary]').textContent, /1 evidence unknown/, 'once the board read expires the gap counts again')
})

test('a fresh replica labels its board status as reported, and failed reads cannot reuse it', { skip: !projector }, () => {
  at(10)
  const chips = tasks.sessionTaskChips([{ id: 't_x', title: 'Remote wait', gateway: 'worker', profile: 'default', board: 'default', remote: true, status: 'scheduled', execution_observations: [record('t_x', { set: { 'identity.gateway': 'worker', 'identity.profile': 'default', 'identity.board': 'default', state: 'waiting', source: 'kanban.card', observed_at: null, valid_until: null, source_revision: null, evidence_refs: [] }, delete: ['identity.run_id'] })] }])
  const opts = { nowMs: Date.now(), evidence: c => m.chipObservation(c, Date.now() / 1000) }
  assert.match(m.classify(chips, opts).waiting[0].detail, /scheduled · its owner has not published what wakes it · reported board status$/)
  assert.match(m.classify(chips.map(c => ({ ...c, stale: true })), opts).waiting[0].detail, /status unknown/)
})

test('board freshness expires even without execution observations or an SDK verdict', () => {
  at(10)
  for (const observations of [undefined, []]) {
    const chips = tasks.sessionTaskChips([{ id: 't_x', title: 'Parked', status: 'scheduled', execution_observations: observations }])
    for (const evidence of [c => m.chipObservation(c, Date.now() / 1000), () => null]) {
      assert.match(m.classify(chips, { nowMs: Date.now(), evidence }).waiting[0].detail, /^scheduled · its owner has not published what wakes it · board status$/)
      assert.match(m.classify(chips, { nowMs: Date.now() + 60_001, evidence }).waiting[0].detail, /status unknown/)
      for (const boardStatus of [{ ...chips[0].boardStatus, readAt: NaN }, { ...chips[0].boardStatus, state: 'ready' }, { ...chips[0].boardStatus, readAt: Date.now() + 1 }]) {
        assert.match(m.classify([{ ...chips[0], boardStatus }], { nowMs: Date.now(), evidence }).waiting[0].detail, /status unknown/)
      }
    }
  }
})

test('board read timestamps must be numbers, not coercible values', () => {
  // Near epoch zero, booleans and null also pass the relational freshness checks.
  for (const [nowMs, readAt] of [[10_000, '10000'], [10_000, true], [10_000, false], [10_000, null]]) {
    const chips = tasks.sessionTaskChips([{ id: 't_x', title: 'Parked', status: 'scheduled' }])
    const chip = { ...chips[0], boardStatus: { ...chips[0].boardStatus, readAt } }
    const sorted = m.classify([chip], { nowMs })
    assert.equal(sorted.waiting.length, 1)
    assert.match(sorted.waiting[0].detail, /^status unknown/, `readAt=${JSON.stringify(readAt)}`)
  }
})

test('known execution evidence takes precedence over fresh parked board status', () => {
  at(10)
  const chips = tasks.sessionTaskChips([{ id: 't_x', title: 'Review work', status: 'review' }])
  const opts = { nowMs: Date.now(), evidence: () => ({ state: 'running', sourced: true }) }
  const sorted = m.classify(chips, opts)
  assert.equal(sorted.active.length, 1)
  assert.match(sorted.active[0].detail, /^in review/)
  assert.equal(sorted.waiting.length, 0)
})

test('stale or mismatched board stamps cannot hide known execution evidence', () => {
  at(10)
  const chips = tasks.sessionTaskChips([{ id: 't_x', title: 'Running work', status: 'running' }])
  for (const boardStatus of [
    { ...chips[0].boardStatus, readAt: Date.now() - 60_001 },
    { ...chips[0].boardStatus, state: 'scheduled' },
  ]) {
    const sorted = m.classify([{ ...chips[0], boardStatus }], {
      nowMs: Date.now(), evidence: () => ({ state: 'running', sourced: true }),
    })
    assert.equal(sorted.active.length, 1)
    assert.match(sorted.active[0].detail, /^running/)
    assert.equal(sorted.waiting.length, 0)
  }
})

test('a fresh running board read cannot establish execution liveness', () => {
  at(10)
  const chips = tasks.sessionTaskChips([{ id: 't_x', title: 'Running work', status: 'running' }])
  for (const evidence of [() => null, () => ({ state: 'unknown', sourced: true })]) {
    const sorted = m.classify(chips, { nowMs: Date.now(), evidence })
    assert.equal(sorted.active.length, 0)
    assert.equal(sorted.waiting.length, 1)
    assert.match(sorted.waiting[0].detail, /^status unknown/)
    assert.doesNotMatch(sorted.waiting[0].detail, /board status/)
  }
})

test('needs-you shows the plain card title next to the action', async () => {
  bridges({ chips: [card('t_x', 'blocked', { label: 'Choose the release window', look: 'needs-you', humanRequest: { action: 'Choose A or B' } })] })
  await show(React.createElement(m.Panel, { key: 'ask-context' }))
  assert.equal(el().querySelector('[data-ask-title]')?.textContent, 'Choose the release window')
  assert.match(el().querySelector('[data-ask-step]').textContent, /Choose A or B/)
})

const { fixture } = loadFixture()
const skip = projector ? false : 'no core checkout: set HERMES_CORE_SOURCE'
const realNow = Date.now
const BASE_AT = fixture.base.observed_at // source seconds
const at = sec => { Date.now = () => (BASE_AT + sec) * 1000 }
afterEach(() => { Date.now = realNow })
const card = (id, state, extra = {}) => ({ key: `kb:${id}`, kind: 'kanban', id, state, label: `Card ${id}`, connectionId: 'C1', profile: 'work', board: 'tasks', gateway: 'edge', gatewayId: 'edge', since: BASE_AT - 600, activityAt: BASE_AT - 20, observations: undefined, ...extra })
const record = (id, patch = {}) => caseInput(fixture, { set: { 'identity.work_id': id, ...patch.set }, delete: patch.delete })

function bridges({ chips, asks = 0 }) {
  const subs = new Set()
  window.__sessionTasks = { snapshot: { runtime: 'R1', stored: 'S1', chips }, subscribe: fn => (subs.add(fn), () => subs.delete(fn)), refresh() {}, openTaskChip() {}, lineageIds: async id => new Set([id]) }
  window.__userSteps = { subscribe: () => () => {}, openAskCount: () => asks, requestForSource: () => null, snapshot: { ledger: { steps: [] }, issues: [] }, ActionQueue: () => null, Strip: () => null }
  window.hermesDesktop = { api: async () => ({ tasks: [], includes: ['done'] }) }
  return { tick: () => subs.forEach(fn => fn({ runtime: 'R1', stored: 'S1', chips })) }
}

test('a cached running card flips to unknown on the next existing tick with no refetch', { skip }, async () => {
  const chips = [card('task-a', 'running', { observations: [record('task-a')] })]
  const b = bridges({ chips })
  at(100)
  await show(React.createElement(m.Panel, { key: 'a' }))
  assert.equal(el().querySelectorAll('[data-running-row]').length, 1, 'listed as running')
  assert.equal(el().querySelector('[data-section="in-progress"]'), null, 'no heading: the header already counts it')
  assert.equal(el().querySelector('[data-evidence]'), null, 'evidence is a detail, not the default view')
  await React.act(async () => { el().querySelector('[data-running-row] button').click(); await flush() })
  assert.match(el().querySelector('[data-evidence]').textContent, /^evidence: running · observed 1 min ago/)
  at(181) // only the clock moved; session-tasks' existing poll re-emits the SAME cached chips
  await React.act(async () => { b.tick(); await flush() })
  assert.equal(el().querySelectorAll('[data-running-row]').length, 0, 'no longer listed as running')
  await openWaiting()
  assert.equal(el().querySelector('[data-section="waiting"]').textContent, 'Waiting · 1')
  const row = el().querySelector('[data-panel-row]').textContent
  assert.match(row, /status unknown \(source expired\) · last known: running \(observed 3 min ago\)/)
  assert.match(el().querySelector('[data-status-summary]').textContent, /1 evidence unknown/)
})

test('an older producer (no records): a legacy running card is explicit unknown with last known state, never In progress', { skip }, async () => {
  bridges({ chips: [card('task-a', 'running')] })
  at(10)
  await show(React.createElement(m.Panel, { key: 'b' }))
  await openWaiting()
  assert.equal(el().querySelector('[data-section="in-progress"]'), null)
  assert.equal(el().querySelector('[data-section="waiting"]').textContent, 'Waiting · 1')
  assert.match(el().querySelector('[data-panel-row]').textContent, /status unknown \(producer sent no observations\) · last known: running/)
  assert.match(el().querySelector('[data-evidence]').textContent, /^evidence: unknown \(producer sent no observations\) · progress unknown$/)
  assert.match(el().querySelector('[data-status-summary]').textContent, /1 evidence unknown/)
})

test('an older producer keeps a legacy blocked ask in its own section', { skip }, async () => {
  const sorted = m.classify([card('task-b', 'blocked', { look: 'needs-you' })], { nowMs: (BASE_AT + 10) * 1000, evidence: c => m.chipObservation(c, BASE_AT + 10) })
  assert.equal(sorted.unasked.length, 1)
  assert.equal(sorted.waiting.length, 0)
})

test('a missing SDK projector is explicit unknown on every row, and never changes legacy sections', async () => {
  const none = m.annotateEvidence
  const sorted = m.classify([card('task-a', 'running', { observations: [record('task-a')] })], { nowMs: (BASE_AT + 10) * 1000, evidence: c => m.chipObservation(c, 1, { sdk: m.observationSdk({}) }) })
  assert.equal(sorted.active.length, 1, 'no evidence at all: legacy classification stands')
  assert.equal(none(sorted, (BASE_AT + 10) * 1000, { sdk: m.observationSdk({}) }), 0)
  assert.match(sorted.active[0].evidenceText, /^unknown \(sdk projector unavailable\) · progress unknown$/)
})

test('legacy needs-you ask keeps its actionable row even when its evidence expired or is absent', { skip }, async () => {
  const ask = { action: 'Pick one. Unblock = go; archive = stop', why: 'Preference', interaction: 'reply' }
  const chips = [card('task-a', 'blocked', { look: 'needs-you', humanRequest: ask, observations: [record('task-a')] })]
  bridges({ chips })
  at(500) // record long expired
  await show(React.createElement(m.Panel, { key: 'c' }))
  assert.equal(el().querySelector('[data-section="needs-you"]').textContent, 'Needs you · 1')
  assert.match(el().querySelector('[data-ask-step]').textContent, /^Do this: Pick one\./)
  assert.ok(el().querySelector('[data-open-card]'), 'Open card stays')
  assert.match(el().querySelector('[data-evidence]').textContent, /unknown \(source expired\)/)
})

test('a stale cached chip lists as unknown with its last-known state, legacy behaviour intact', { skip }, async () => {
  bridges({ chips: [card('task-a', 'running', { stale: true, observations: [record('task-a')] })] })
  at(10)
  await show(React.createElement(m.Panel, { key: 'd' }))
  await openWaiting()
  assert.match(el().querySelector('[data-panel-row]').textContent, /status unknown · last known: running/)
  assert.match(el().querySelector('[data-evidence]').textContent, /unknown \(cached after failed read\)/)
})

test('a hub-sync replica running card is explicit unknown with the replica reason and its source-activity age', { skip }, async () => {
  bridges({ chips: [card('task-r', 'running', { replica: true, activityAt: BASE_AT - 120 })] })
  at(0)
  await show(React.createElement(m.Panel, { key: 'r' }))
  await openWaiting()
  assert.equal(el().querySelector('[data-section="in-progress"]'), null)
  assert.equal(el().querySelector('[data-section="waiting"]').textContent, 'Waiting · 1')
  assert.match(el().querySelector('[data-panel-row]').textContent, /status unknown \(hub replica not source evidence\) · last known: running \(observed 2 min ago\)/)
  assert.match(el().querySelector('[data-status-summary]').textContent, /1 evidence unknown/)
})

test('a hub-sync replica blocked ask keeps its own section', { skip }, async () => {
  const sorted = m.classify([card('task-s', 'blocked', { look: 'needs-you', replica: true })], { nowMs: (BASE_AT + 10) * 1000, evidence: c => m.chipObservation(c, BASE_AT + 10) })
  assert.equal(sorted.unasked.length, 1)
  assert.equal(sorted.waiting.length, 0)
})
