// Real registered session panel + user-steps: the panel hosts the decision companion in its own bounded frame, and
// the companion omits an ask's options only when that same render drew that exact owner/request/revision.
// Registration, a cached model or `covers()` alone is not coverage. Structure only (jsdom); real layout is
// session-panel/frame.browser.test.mjs.
import './fixtures/cache-hooks.mjs'
import { test, after, beforeEach } from 'node:test'
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
const host = { state: { focusedStoredSessionId: atom('S1'), connectionId: atom('C1'), profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' }) } }
globalThis.presentHarness = { React, J, host }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.presentHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef,useMemo=R.useMemo,useCallback=R.useCallback','react/jsx-runtime':'const J=globalThis.presentHarness.J;export const jsx=J.jsx,jsxs=J.jsxs,Fragment=J.Fragment','@hermes/plugin-sdk':'export const host=globalThis.presentHarness.host,COMPOSER_AREAS={top:"composer.top"},TRANSCRIPT_DIRECTIVE_AREA="transcript.directives",useValue=a=>a?.get(),MessageTextContent=({text})=>text'};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const steps = await import('../plugin.js')
const panel = await import('../../session-panel/plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))
const show = async node => React.act(async () => { root.render(node); await flush(); await flush() })
const settle = async () => React.act(async () => { for (let i = 0; i < 4; i++) await flush() })

const owner = { gatewayId: 'G1', profile: 'default', storedSessionId: 'S1' }
let serial = 0
const ask = extra => ({ schemaVersion: 2, owner, requestId: `00000000-0000-4000-8000-${String(++serial).padStart(12, '0')}`, revision: 1, kind: 'choice', title: `Which color ${serial}?`,
  scope: { complete: true, markdown: 'Pick a color.', sourceRefs: [] }, limits: [], exclusions: [], outcomes: { answer: 'Record answer' },
  choices: [{ id: 'c0', label: 'Red', effect: 'answer' }, { id: 'c1', label: 'Green', effect: 'answer' }], aliases: [], state: 'open', ...extra })
const askRow = (r, { gatewayId = r.owner.gatewayId, revision = r.revision } = {}) => ({ key: `ask:${gatewayId}:${r.requestId}`, kind: 'ask', source: 'question', id: '', title: r.title, detail: '', details: [],
  options: [{ letter: 'A', label: 'Red', choice_id: 'c0', detail: '' }, { letter: 'B', label: 'Green', choice_id: 'c1', detail: '' }],
  actions: [{ kind: 'decide', owner: { ...r.owner, gatewayId }, request_id: r.requestId, revision }], request_id: r.requestId, request_revision: revision })
const lineRow = key => ({ key, kind: 'card', id: key, title: `Card ${key}`, detail: 'running', details: [], actions: [], options: [] })
const SECTIONS = ['needs_you', 'stuck', 'running', 'waiting', 'ready', 'done']
let modelRev = 0
const model = (rows = {}) => {
  const sections = Object.fromEntries(SECTIONS.map(s => [s, rows[s] || []]))
  const counts = Object.fromEntries(SECTIONS.map(s => [s, sections[s].length]))
  return { version: 1, revision: `m${++modelRev}`, header: { state: counts.needs_you ? 'needs_you' : 'running', label: counts.needs_you ? 'Needs you' : 'Running', source: 'derived', status: null, counts,
    pips: SECTIONS.filter(s => counts[s]).map(s => ({ section: s, label: s, count: counts[s] })) }, sections }
}

// Backend: user-steps requests per session, feed bundles per session, owner features.
const backend = { requests: { S1: [], S2: [] }, feed: 'ok', bundles: {}, features: ['withdraw'] }
window.hermesDesktop = { api: async ({ path, body }) => {
  if (path.startsWith('/api/plugins/session-panel/')) {
    if (backend.feed === '404') throw Error('404: Not Found')
    const sessions = body?.sessions || []
    return { feed: 'hub:1', profile: 'default', epoch: 'E1', rev: ++modelRev, bundles: Object.fromEntries(sessions.filter(s => backend.bundles[s]).map(s => [s, backend.bundles[s]])) }
  }
  if (path.endsWith('/ping')) return { features: backend.features }
  if (path.includes('/requests')) { const s = new URL(path, 'https://x').searchParams.get('session') || host.state.focusedStoredSessionId.get(); return { requests: backend.requests[s] || [] } }
  return {}
} }
const seed = (session, m) => { backend.bundles[session] = { session, model: m }; panel.ingestBundle('C1', 'default', { session, model: m }) }

// Register both plugins the way Desktop does and render the composer-top contributions in order.
const contributions = []
const ctx = { register: a => contributions.push(a), onEvent: () => () => {} }
let disposePanel = null, disposeSteps = null
const registerPanel = () => { disposePanel = panel.default.register(ctx) }
const registerSteps = () => { disposeSteps = steps.default.register(ctx) }
const composerTop = () => React.createElement(React.Fragment, null, ...contributions.filter(a => a.area === 'composer.top').sort((a, b) => a.order - b.order).map(a => React.createElement(React.Fragment, { key: a.id }, a.render())))
const coverageOf = () => [...el().querySelectorAll('[data-ask-coverage]')].map(n => n.getAttribute('data-ask-coverage'))
const focus = session => { host.state.focusedStoredSessionId.value = session }

beforeEach(async () => {
  await show(null)
  disposePanel?.(); disposeSteps?.(); disposePanel = disposeSteps = null
  contributions.length = 0
  panel.resetFeedForTests(); steps.resetCanonicalCacheForTests(); steps.clearFeatureCache()
  backend.requests = { S1: [], S2: [] }; backend.feed = 'ok'; backend.bundles = {}; backend.features = ['withdraw']
  focus('S1'); window.localStorage.clear()
  registerPanel(); registerSteps()
})
after(async () => { await React.act(async () => root.unmount()); disposePanel?.(); disposeSteps?.(); dom.window.close() })

test('open plain-choice ask drawn by the panel: one frame hosts rows and the companion; no title-only row', async () => {
  const r = ask()
  backend.requests.S1 = [r]; backend.features = []
  seed('S1', model({ needs_you: [askRow(r)] }))
  await show(composerTop()); await settle()
  const frame = el().querySelectorAll('[data-session-panel]')
  assert.equal(frame.length, 1, 'one session-status frame')
  assert.equal(el().querySelectorAll('[data-action-queue] article').length, 0, 'nothing only the companion can do: no companion row')
  assert.ok(el().querySelector(`[data-ask-binding]`), 'panel drew the bound ask')
  assert.deepEqual(coverageOf(), [])
  assert.ok(el().querySelector('[data-action-request]') === null)
  assert.equal(panel.hostsQueue({ connectionId: 'C1', profile: 'default', session: 'S1' }), true)
  assert.ok(el().querySelector('[data-user-steps-panel-missing]') === null)
})

test('withdraw supported: covered ask keeps only Not needed, inside the panel body', async () => {
  const r = ask()
  backend.requests.S1 = [r]
  seed('S1', model({ needs_you: [askRow(r)] }))
  await show(composerTop()); await settle()
  const row = el().querySelector('[data-action-request]')
  assert.ok(row, 'withdraw-only companion row')
  assert.ok(row.closest('[data-panel-body]'), 'companion lives in the panel body, not a second composer block')
  assert.deepEqual([...row.querySelectorAll('button')].map(b => b.textContent), ['Not needed'])
  assert.deepEqual(coverageOf(), [], 'covered: no explanation line')
  assert.equal(el().querySelectorAll('[data-action-queue]').length, 1)
})

test('omitted request in a nonempty model explains itself instead of a title + Not needed row', async () => {
  const r = ask()
  backend.requests.S1 = [r]
  seed('S1', model({ running: [lineRow('t1')] }))
  await show(composerTop()); await settle()
  assert.deepEqual(coverageOf(), ['omitted'])
  assert.match(el().querySelector('[data-ask-coverage]').textContent, /hasn.t shown this ask yet.*Nothing has been decided/)
})

test('empty model: bounded bare frame still hosts the companion and says why options are absent', async () => {
  const r = ask()
  backend.requests.S1 = [r]
  seed('S1', model())
  await show(composerTop()); await settle()
  assert.equal(el().querySelector('[data-panel-frame]')?.getAttribute('data-panel-frame'), 'bare')
  assert.deepEqual(coverageOf(), ['empty'])
})

test('cold load: pending model plus explicit loading state, never actionable title-only rows', async () => {
  const r = ask()
  backend.requests.S1 = [r]
  await show(composerTop()); await settle()
  assert.ok(el().querySelector('[data-hub-model-pending]'))
  assert.deepEqual(coverageOf(), ['loading'])
  // The model arrives: exactly one ask, drawn by the panel, and the explanation clears.
  seed('S1', model({ needs_you: [askRow(r)] }))
  await settle()
  assert.equal(el().querySelectorAll('[data-ask-binding]').length, 1)
  assert.deepEqual(coverageOf(), [])
})

test('unsupported backend/profile: alert plus explicit unavailable state; recovery to exactly one ask', async () => {
  const r = ask()
  backend.requests.S1 = [r]; backend.feed = '404'
  await show(composerTop()); await settle()
  await panel.feedCatchUp({ connectionId: 'C1', profile: 'default', sessions: ['S1'] }); await settle()
  assert.ok(el().querySelector('[data-session-panel-error][role=alert]'))
  assert.deepEqual(coverageOf(), ['unsupported'])
  assert.ok(el().querySelector('[data-session-panel-error]').closest('[data-panel-body]'), 'error in the bounded body too')
  panel.resetFeedForTests(); backend.feed = 'ok'
  seed('S1', model({ needs_you: [askRow(r)] }))
  await settle()
  assert.equal(el().querySelectorAll('[data-ask-binding]').length, 1, 'recovered: exactly one ask')
  assert.deepEqual(coverageOf(), [])
  assert.ok(el().querySelectorAll('[data-action-queue]').length <= 1)
})

test('unmounted panel contribution: no queue anywhere and an explicit error, not a silent title row', async () => {
  const r = ask()
  backend.requests.S1 = [r]
  seed('S1', model({ needs_you: [askRow(r)] }))
  await steps.refreshRequests()
  const strip = contributions.find(a => a.id === 'strip')
  const wait = steps.panelWait.ms
  try {
    steps.panelWait.ms = 0
    await show(strip.render()); await settle()
    assert.equal(panel.hostsQueue({ connectionId: 'C1', profile: 'default', session: 'S1' }), false)
    assert.equal(el().querySelector('[data-user-steps-panel-missing]')?.getAttribute('data-user-steps-panel-missing'), 'unmounted')
    assert.match(el().textContent, /1 decision is waiting/)
    assert.ok(el().querySelector('[data-action-request]') === null)
    // Mount the panel: the error clears and the ask is the panel's.
    await show(composerTop()); await settle()
    assert.ok(el().querySelector('[data-user-steps-panel-missing]') === null)
    assert.equal(el().querySelectorAll('[data-ask-binding]').length, 1)
  } finally { steps.panelWait.ms = wait }
})

test('owner qualification: the same request id drawn for another gateway is not coverage', async () => {
  const r = ask()
  backend.requests.S1 = [r]
  seed('S1', model({ needs_you: [askRow(r, { gatewayId: 'G2' })] }))
  await show(composerTop()); await settle()
  assert.deepEqual(coverageOf(), ['omitted'])
})

test('session switch: coverage is per exact route, never carried to another chat', async () => {
  const a = ask(), b = ask({ owner: { ...owner, storedSessionId: 'S2' } })
  backend.requests.S1 = [a]; backend.requests.S2 = [b]
  seed('S1', model({ needs_you: [askRow(a)] }))
  await show(composerTop()); await settle()
  assert.deepEqual(coverageOf(), [])
  focus('S2')
  await show(composerTop()); await settle()
  assert.deepEqual(coverageOf(), ['loading'], 'S2 has no model yet')
  assert.equal(panel.hostsQueue({ connectionId: 'C1', profile: 'default', session: 'S1' }), false)
  assert.equal(panel.hostsQueue({ connectionId: 'C1', profile: 'default', session: 'S2' }), true)
  seed('S2', model({ needs_you: [askRow(b)] }))
  await settle()
  assert.deepEqual(coverageOf(), [])
})

test('newer revision: an older drawn revision is not coverage until the model catches up', async () => {
  const r = ask({ revision: 2 })
  backend.requests.S1 = [r]
  seed('S1', model({ needs_you: [askRow(r, { revision: 1 })] }))
  await show(composerTop()); await settle()
  assert.deepEqual(coverageOf(), ['omitted'])
  seed('S1', model({ needs_you: [askRow(r)] }))
  await settle()
  assert.deepEqual(coverageOf(), [])
})

const hiddenNode = n => !!n?.closest('[hidden]')
const collapse = async () => { await React.act(async () => { el().querySelector('[data-panel-toggle]').click(); await flush() }); await settle() }

test('collapse removes rows and counted asks; the header toggle stays; expanding restores both', async () => {
  const r = ask(), stray = ask()
  backend.requests.S1 = [r, stray]
  seed('S1', model({ needs_you: [askRow(r), askRow(stray)] }))
  await show(composerTop()); await settle()
  assert.equal(el().querySelectorAll('[data-action-request]').length, 2, 'withdraw rows while expanded')
  await collapse()
  const body = el().querySelector('[data-panel-body]')
  assert.ok(body.hasAttribute('data-body-collapsed'))
  assert.equal(el().querySelectorAll('[data-panel-row]').length, 0, 'no canonical rows while collapsed')
  assert.ok(el().querySelector('[data-panel-toggle]'), 'toggle reachable')
  assert.ok(el().querySelector('[data-panel-toggle]').closest('[data-panel-body]') === null, 'toggle is outside the scrolling body')
  assert.equal(el().querySelectorAll('[data-action-request]').length, 0, 'counted asks draw nothing under a collapsed panel')
  assert.equal(el().querySelectorAll('[data-action-queue]').length, 1, 'companion stays mounted for recovery')
  await collapse()
  assert.equal(el().querySelector('[data-panel-body]').hasAttribute('data-body-collapsed'), false)
  assert.equal(el().querySelectorAll('[data-action-request]').length, 2)
  assert.equal(el().querySelectorAll('[data-panel-row]').length, 2)
})

// What the header does not count must never vanish behind a collapsed panel.
test('collapsed panel keeps recorded-delivery Check status / Retry visible', async () => {
  const r = ask({ resolution: { operationId: 'op-collapsed', choiceId: 'c0', revision: 1 } })
  backend.requests.S1 = [r]
  const api = window.hermesDesktop.api
  window.hermesDesktop.api = async req => req.path.includes('/operations/') ? { operation: { operationId: 'op-collapsed', dispatchState: 'pending' }, request: r } : api(req)
  try {
    seed('S1', model({ running: [lineRow('t1')] }))
    await show(composerTop()); await settle()
    await collapse(); await settle()
    assert.ok(el().querySelector('[data-session-panel]').hasAttribute('data-collapsed'))
    assert.equal(el().querySelectorAll('[data-panel-row]').length, 0, 'rows are gone')
    const buttons = [...el().querySelectorAll('[data-action-request] button')]
    const labels = buttons.map(b => b.textContent)
    assert.ok(labels.includes('Check status') && labels.includes('Retry'), `recovery controls kept: ${labels}`)
    assert.deepEqual(buttons.map(hiddenNode), buttons.map(() => false), 'none under a hidden ancestor')
    assert.ok(buttons[0].closest('[data-panel-body]'), 'still inside the one bounded body')
  } finally { window.hermesDesktop.api = api }
})

test('collapsed panel keeps an open ask the model omits visible, with its explanation', async () => {
  const counted = ask()
  backend.requests.S1 = [counted]
  seed('S1', model({ needs_you: [askRow(counted)], running: [lineRow('t1')] }))
  await show(composerTop()); await settle()
  await collapse()
  assert.equal(el().querySelectorAll('[data-action-request]').length, 0)
  const omitted = ask(); backend.requests.S1 = [counted, omitted]
  await React.act(async () => { await steps.refreshRequests() }); await settle()
  const rows = [...el().querySelectorAll('[data-action-request]')]
  assert.equal(rows.length, 1, 'only the uncounted ask is drawn')
  assert.ok(rows[0].textContent.includes(omitted.title))
  assert.equal(hiddenNode(rows[0]), false)
  assert.deepEqual(coverageOf(), ['omitted'])
  assert.ok(el().querySelector('[data-session-panel]').hasAttribute('data-collapsed'), 'the panel stays collapsed')
})

test('collapsed panel keeps a queue read error visible', async () => {
  seed('S1', model({ running: [lineRow('t1')] }))
  await show(composerTop()); await settle()
  await collapse()
  const api = window.hermesDesktop.api
  window.hermesDesktop.api = async req => { if (req.path.includes('/requests')) throw Error('503: backend down'); return api(req) }
  try {
    await React.act(async () => { await steps.refreshRequests().catch(() => {}) }); await settle()
    const alert = el().querySelector('[data-action-queue] [role="alert"]')
    assert.ok(alert, 'the error is drawn')
    assert.equal(hiddenNode(alert), false)
  } finally { window.hermesDesktop.api = api }
})

test('collapsed panel keeps a counted ask\'s failed-withdraw error visible (not quiet, so not hidden)', async () => {
  const r = ask()
  backend.requests.S1 = [r]
  const api = window.hermesDesktop.api
  window.hermesDesktop.api = async req => { if (req.path.endsWith('/withdraw')) throw Error('503: backend down'); return api(req) }
  try {
    seed('S1', model({ needs_you: [askRow(r)] }))
    await show(composerTop()); await settle()
    await React.act(async () => { el().querySelector('[data-withdraw-request]').click(); await flush() }); await settle()
    assert.ok(el().querySelector('[data-withdraw-error]'), 'withdraw failed while expanded')
    await collapse(); await settle()
    assert.ok(el().querySelector('[data-session-panel]').hasAttribute('data-collapsed'))
    assert.equal(el().querySelectorAll('[data-panel-row]').length, 0, 'the counted ask row is gone with the canonical rows')
    const error = el().querySelector('[data-action-request] [data-withdraw-error]')
    assert.ok(error, 'the counted ask keeps its unresolved withdraw error while collapsed')
    assert.equal(hiddenNode(error), false)
  } finally { window.hermesDesktop.api = api }
})

test('idle chat with a queue read error paints a real frame with the error, not the idle frame', async () => {
  seed('S1', model())
  const api = window.hermesDesktop.api
  window.hermesDesktop.api = async req => { if (req.path.includes('/requests')) throw Error('503: backend down'); return api(req) }
  try {
    await show(composerTop())
    await React.act(async () => { await steps.refreshRequests().catch(() => {}) }); await settle()
    assert.equal(el().querySelector('[data-session-panel]').getAttribute('data-panel-frame'), 'bare', 'error is visible work')
    assert.ok(el().querySelector('[data-action-queue] [role="alert"]'), 'the error is drawn')
  } finally { window.hermesDesktop.api = api }
})

test('idle frame is not a named landmark', async () => {
  seed('S1', model())
  await show(composerTop()); await settle()
  const frame = el().querySelector('[data-panel-frame="idle"]')
  assert.ok(frame)
  assert.notEqual(frame.tagName, 'SECTION')
  assert.equal(frame.hasAttribute('aria-label'), false)
})

test('idle chat: an empty model and no requests paint nothing, and a later ask still appears', async () => {
  seed('S1', model())
  await show(composerTop()); await settle()
  const frame = el().querySelector('[data-session-panel]')
  assert.equal(frame.getAttribute('data-panel-frame'), 'idle')
  assert.equal(frame.textContent, '', 'no content')
  assert.equal(frame.style.padding, '0px')
  assert.ok(!/1px/.test(frame.style.borderBottom), `no border: ${frame.style.borderBottom}`)
  assert.equal(frame.querySelectorAll('[data-action-queue]').length, 0)
  // The companion is still mounted (polling): a new ask shows up in a real frame.
  const r = ask(); backend.requests.S1 = [r]
  await React.act(async () => { await steps.refreshRequests() }); await settle()
  assert.equal(el().querySelector('[data-session-panel]').getAttribute('data-panel-frame'), 'bare')
  assert.deepEqual(coverageOf(), ['empty'])
})

test('mixed-version: an older panel without hostsQueue keeps its quiet queue, no false "not showing" line', async () => {
  const r = ask(); backend.requests.S1 = [r]; backend.features = []
  const real = window.__sessionPanel
  window.__sessionPanel = { active: true, version: 'old', covers: () => true, showsDecision: () => false }
  try {
    await steps.refreshRequests()
    await show(contributions.find(a => a.id === 'strip').render()); await settle()
    assert.deepEqual(coverageOf(), [])
    assert.equal(el().querySelectorAll('[data-action-request]').length, 0, 'the old panel draws the ask; nothing duplicated here')
    assert.ok(el().querySelector('[data-user-steps-panel-missing]') === null)
  } finally { window.__sessionPanel = real }
})

test('hostsQueue follows the panel\'s own connection, so a differing active-connection source cannot disagree', async () => {
  seed('S1', model({ running: [lineRow('t1')] }))
  await show(composerTop()); await settle()
  assert.equal(panel.hostsQueue({ connectionId: 'OTHER', profile: 'default', session: 'S1' }), true)
  assert.equal(panel.hostsQueue({ connectionId: 'C1', profile: 'default', session: 'S2' }), false)
  assert.equal(panel.hostsQueue({ connectionId: 'C1', profile: 'work', session: 'S1' }), false)
})

test('recorded-delivery recovery keeps Check status / Retry in the hosted companion', async () => {
  const r = ask({ resolution: { operationId: 'op-hosted', choiceId: 'c0', revision: 1 } })
  backend.requests.S1 = [r]
  const api = window.hermesDesktop.api
  window.hermesDesktop.api = async req => req.path.includes('/operations/') ? { operation: { operationId: 'op-hosted', dispatchState: 'pending' }, request: r } : api(req)
  try {
    seed('S1', model({ running: [lineRow('t1')] }))
    await show(composerTop()); await settle()
    const row = el().querySelector('[data-action-request]')
    assert.ok(row?.closest('[data-panel-body]'), 'recorded decision shown in the panel body')
    await settle()
    const labels = [...el().querySelectorAll('[data-action-request] button')].map(b => b.textContent)
    assert.ok(labels.includes('Check status') && labels.includes('Retry'), `recovery controls kept: ${labels}`)
    assert.deepEqual(coverageOf(), [], 'a recorded decision is not an uncovered open ask')
  } finally { window.hermesDesktop.api = api }
})

test('late registration and hot reload attach exactly one companion', async () => {
  disposeSteps(); disposeSteps = null
  contributions.splice(0, contributions.length, ...contributions.filter(a => a.id === 'panel'))
  const r = ask()
  backend.requests.S1 = [r]
  seed('S1', model({ needs_you: [askRow(r)] }))
  await show(composerTop()); await settle()
  assert.equal(el().querySelectorAll('[data-action-queue]').length, 0, 'no user-steps yet')
  await React.act(async () => { registerSteps(); await flush() })
  await show(composerTop()); await settle()
  assert.equal(el().querySelectorAll('[data-action-queue]').length, 1, 'late user-steps attached into the frame')
  assert.ok(el().querySelector('[data-action-queue]').closest('[data-panel-body]'), 'attached inside the panel body')
  // Hot reload: dispose and register again.
  await React.act(async () => { disposeSteps(); contributions.splice(0, contributions.length, ...contributions.filter(a => a.id === 'panel')); registerSteps(); await flush() })
  await show(composerTop()); await settle()
  assert.equal(el().querySelectorAll('[data-action-queue]').length, 1, 'exactly one queue after reload')
  assert.equal(el().querySelectorAll('[data-session-panel]').length, 1)
})
