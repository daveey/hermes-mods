// With the hub session panel loaded it draws every ask's options and posts plain-option decisions. This plugin keeps
// only what the panel cannot: free-text decisions (revise / Other), pending-delivery Check/Retry readback recovery,
// the plan-less ask (composer draft) and "Not needed" withdraw. These tests pin that retained behaviour.
import { test, after } from 'node:test'
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
const host = { state: { focusedStoredSessionId: atom('S1'), connectionId: atom('C1'), profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' }) }, composer: { submit() { assert.fail("no composer submission") }, setDraft(s, t) { drafts.push([s, t]) }, focus() {} } }
globalThis.queueHarness = { React, J, host }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.queueHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.queueHarness.J;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':'export const host=globalThis.queueHarness.host,COMPOSER_AREAS={top:"composer.top"},TRANSCRIPT_DIRECTIVE_AREA="transcript.directives",useValue=a=>a?.get(),MessageTextContent=({text})=>text'};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const m = await import('../plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))
const show = async node => React.act(async () => { root.render(node); await flush() })
const owner = { gatewayId: 'G1', profile: 'default', storedSessionId: 'S1' }
const route = { connectionId: 'C1', profile: 'default', session: 'S1' }
const btn = label => [...el().querySelectorAll('button')].find(b => b.textContent === label)
const click = async label => React.act(async () => { assert.ok(btn(label), label); btn(label).click(); await flush() })
const labels = () => [...el().querySelectorAll('button')].map(b => b.textContent)
const type = async text => { const input = el().querySelector('textarea'); await React.act(async () => { Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(input, text); input.dispatchEvent(new dom.window.Event('input', { bubbles: true })); await flush() }) }
let serial = 0
const approval = extra => ({ schemaVersion: 2, owner, requestId: `00000000-0000-4000-8000-${String(++serial).padStart(12, '0')}`, revision: 1, kind: 'approval', title: 'Ship stage A', scope: { complete: true, markdown: 'EXACT-PLAN-TEXT files and tests.', sourceRefs: [] }, limits: [{ key: 'CPU', value: 'LIMIT-4-HOURS' }], exclusions: ['EXCLUDED-SPEND'], outcomes: { approve: 'Stage A only', revise: 'Work stays blocked', decline: 'No execution' }, choices: [{ id: 'yes', label: 'Approve stage A', effect: 'approve' }, { id: 'edit', label: 'Revise', effect: 'revise' }, { id: 'no', label: 'Decline', effect: 'decline' }], aliases: [], state: 'open', ...extra })
const question = extra => approval({ kind: 'choice', title: 'Which color?', outcomes: { answer: 'Record answer' }, choices: [{ id: 'c0', label: 'Red', effect: 'answer' }, { id: 'c1', label: 'Green', effect: 'answer' }, { id: 'answer-text', label: 'Other', effect: 'answer' }], presentation: { kind: 'kanban-question', question: 'Which color?', details: 'CARD-DETAILS', other: true }, ...extra })
after(async () => { await React.act(async () => root.unmount()); dom.window.close() })
const panelOn = () => { window.__sessionPanel = { active: true } }
const panelOff = () => { delete window.__sessionPanel }

test('companion row draws no plan, options or card tiles, only the text-needing choice', async () => {
 const r = approval()
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call: async () => ({}), companion: true }))
 const text = el().textContent
 for (const s of ['EXACT-PLAN-TEXT', 'LIMIT-4-HOURS', 'EXCLUDED-SPEND', 'Stage A only']) assert.ok(!text.includes(s), s)
 assert.deepEqual(labels(), ['Request changes'])
 for (const sel of ['[data-decision-option]', '[data-clarify-row]', '[data-choice-letter]', '[data-action-scope]']) assert.equal(el().querySelector(sel), null, sel)
})

test('companion: revise text is edited and posted unchanged', async () => {
 const r = approval(), calls = []
 const call = async (path, opts = {}) => { calls.push([path, opts]); return { request: r, operation: { operationId: 'op-rev', dispatchState: 'pending' } } }
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call, companion: true }))
 await click('Request changes')
 assert.equal(document.activeElement, el().querySelector('textarea'))
 assert.equal(btn('Send revision').disabled, true, 'empty text cannot be sent')
 await type('Add a rollback step')
 await click('Send revision')
 const post = calls.find(([, o]) => o.method === 'POST')
 assert.equal(post[0], `/api/plugins/user-steps/requests/${r.requestId}/decisions`)
 const { idempotencyKey, ...payload } = post[1].body
 assert.ok(idempotencyKey)
 assert.deepEqual(payload, { session: 'S1', expectedRevision: 1, choiceId: 'edit', revisionText: 'Add a rollback step' })
})

test('companion: a card question keeps Other (type your answer) and posts the typed text; plain options are the panel\'s', async () => {
 const r = question(), calls = []
 const call = async (path, opts = {}) => { calls.push([path, opts]); return { request: r, operation: { operationId: 'op-other', dispatchState: 'pending' } } }
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call, companion: true }))
 assert.deepEqual(labels(), ['Other'])
 assert.ok(!el().textContent.includes('CARD-DETAILS'))
 await click('Other'); await type('Use purple'); await click('Send answer')
 const post = calls.find(([, o]) => o.method === 'POST')
 assert.equal(post[1].body.choiceId, 'answer-text'); assert.equal(post[1].body.revisionText, 'Use purple'); assert.equal(post[1].body.expectedRevision, 1)
})

test('companion: pending delivery keeps Check status and Retry (same operation) with the decision locked', async () => {
 await show(null)
 const r = approval({ title: 'Recorded ask' })
 const resolution = { operationId: 'op-pending', choiceId: 'yes', revision: 1 }
 let delivered = false; const seen = []
 const call = async (path, opts = {}) => {
  seen.push([path, opts.method || 'GET'])
  if (path.endsWith('/ping')) return { features: [] }
  if (opts.method === 'POST') delivered = true
  const request = { ...r, resolution, state: delivered ? 'resolved' : 'open' }
  if (path.includes('/operations/')) return { operation: { operationId: resolution.operationId, dispatchState: delivered ? 'delivered' : 'failed' }, request }
  return { requests: [request] }
 }
 window.hermesDesktop = { api: ({ path, ...opts }) => call(path, opts) }
 await m.refreshRequests(call)
 await show(React.createElement(m.ActionQueue, { key: 'pending', embedded: true, companion: true }))
 assert.ok(el().querySelector('[data-action-request]'), 'a recorded-but-unconfirmed decision stays on screen under the panel')
 assert.match(el().textContent, /delivery unconfirmed/i)
 assert.ok(btn('Check status') && btn('Retry'))
 assert.ok(!labels().some(l => /Request changes|Approve/.test(l)), 'no new decision while one is recorded')
 await click('Retry')
 assert.deepEqual(seen.filter(([, mth]) => mth === 'POST').map(([p]) => p.replace(/\?.*/, '')), ['/api/plugins/user-steps/operations/op-pending/retry'])
 assert.equal(el().querySelector('[data-action-request]'), null, 'confirmed delivery clears the row')
 delete window.hermesDesktop
})

test('companion: a plan-less ask still offers Ask agent for the plan (composer draft) and draws no Approve', async () => {
 const r = approval({ title: 'No plan', scope: { complete: false, markdown: '', sourceRefs: [] } })
 drafts.length = 0
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call: async () => ({}), companion: true }))
 assert.ok(!labels().includes('Approve') && !labels().includes('Request changes'))
 await click('Ask agent for the plan')
 assert.equal(drafts.length, 1)
 assert.match(drafts[0][1], /full plan for "No plan"/)
})

test('companion: Not needed withdraw is kept when the owner advertises it', async () => {
 m.clearFeatureCache(); m.resetCanonicalCacheForTests()
 const r = approval({ title: 'Withdraw me' }), calls = []
 const call = async (path, opts = {}) => { calls.push([path, opts]); if (path.endsWith('/ping')) return { features: ['withdraw'] }; return { request: { ...r, state: 'withdrawn' }, requests: [] } }
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call, companion: true }))
 await React.act(async () => { await flush(); await flush() })
 await click('Not needed')
 const w = calls.find(([p]) => p.endsWith('/withdraw'))
 assert.ok(w); assert.deepEqual(w[1].body, { session: 'S1', expectedRevision: 1, reason: 'Dismissed in Desktop as not needed' })
})

test('companion: a plain open ask with nothing only this plugin can do draws no row', async () => {
 m.clearFeatureCache(); m.resetCanonicalCacheForTests()
 await show(null)
 const r = question({ choices: [{ id: 'c0', label: 'Red', effect: 'answer' }, { id: 'c1', label: 'Green', effect: 'answer' }] })
 await m.refreshRequests(async () => ({ requests: [r] }))
 window.hermesDesktop = { api: async ({ path }) => path.endsWith('/ping') ? { features: [] } : { requests: [r] } }
 await show(React.createElement(m.ActionQueue, { key: 'idle', embedded: true, companion: true }))
 await React.act(async () => { await flush(); await flush() })
 assert.equal(el().querySelector('[data-action-request]'), null)
 assert.equal(el().textContent.includes('Which color?'), false)
 delete window.hermesDesktop
})

test('without the panel the full row (plan, options, Approve) is unchanged', async () => {
 const r = approval()
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call: async () => ({}) }))
 assert.ok(el().textContent.includes('EXACT-PLAN-TEXT')); assert.ok(labels().includes('Approve'))
})

test('registered strip: with the panel loaded it keeps the queue but never the legacy card/step tiles', async () => {
 const areas = [], dispose = m.default.register({ register: a => areas.push(a) })
 try {
  const strip = areas.find(a => a.id === 'strip')
  const standalone = strip.render().props.children
  assert.deepEqual(standalone.map(k => k.type), [m.ActionQueue, m.Strip], 'standalone: queue and legacy strip')
  assert.equal(standalone[0].props.companion, false)
  panelOn()
  const withPanel = strip.render().props.children
  assert.equal(withPanel.type, m.ActionQueue, 'panel loaded: only the queue, no legacy Strip')
  assert.equal(withPanel.props.companion, true)
 } finally { panelOff(); dispose() }
})

test('companion queue polls requests itself (the legacy Strip that used to drive the poll is not mounted)', async () => {
 await show(null)
 const r = approval({ title: 'Polled' }); let reads = 0
 window.hermesDesktop = { api: async ({ path }) => { if (path.includes('/requests?')) reads++; return { requests: [r], features: [] } } }
 m.queuePoll.ms = 20
 Object.defineProperty(document, 'hidden', { value: false, configurable: true })
 try {
  await show(React.createElement(m.ActionQueue, { key: 'poll', companion: true }))
  const first = reads
  await React.act(async () => { await new Promise(res => setTimeout(res, 150)) })
  assert.ok(reads >= first + 2, `polled again (${first} -> ${reads})`)
  await show(null)
  const stopped = reads
  await new Promise(res => setTimeout(res, 80))
  assert.equal(reads, stopped, 'unmount stops the poll')
 } finally { m.queuePoll.ms = 8000; delete window.hermesDesktop }
})
