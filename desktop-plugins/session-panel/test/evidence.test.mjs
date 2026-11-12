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
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))
const show = async node => React.act(async () => { root.render(node); await flush() })
afterEach(async () => { await show(null) })
after(async () => { await React.act(async () => { root.unmount() }); dom.window.close() })

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
  assert.equal(el().querySelectorAll('[data-section="in-progress"]').length, 1)
  assert.match(el().querySelector('[data-evidence]').textContent, /^evidence: running · observed 1 min ago/)
  at(181) // only the clock moved; session-tasks' existing poll re-emits the SAME cached chips
  await React.act(async () => { b.tick(); await flush() })
  assert.equal(el().querySelectorAll('[data-section="in-progress"]').length, 0, 'no longer listed as running')
  assert.equal(el().querySelector('[data-section="waiting"]').textContent, 'Waiting · 1')
  const row = el().querySelector('[data-panel-row]').textContent
  assert.match(row, /status unknown \(source expired\) · last known: running \(observed 3 min ago\)/)
  assert.match(el().querySelector('[data-status-summary]').textContent, /1 evidence unknown/)
})

test('an older producer (no records): a legacy running card is explicit unknown with last known state, never In progress', { skip }, async () => {
  bridges({ chips: [card('task-a', 'running')] })
  at(10)
  await show(React.createElement(m.Panel, { key: 'b' }))
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
  assert.match(el().querySelector('[data-panel-row]').textContent, /status unknown · last known: running/)
  assert.match(el().querySelector('[data-evidence]').textContent, /unknown \(cached after failed read\)/)
})
