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
const host = { state: { focusedStoredSessionId: atom('S1'), connectionId: atom('C1'), profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' }) }, composer: { submit() { assert.fail('no composer submission') }, focus() {} } }
const timings = []
const timing = { start: (...args) => { const token = Symbol(); timings.push(['start', ...args, token]); return token }, complete: (...args) => timings.push(['complete', ...args]), cancel: (...args) => timings.push(['cancel', ...args]) }
globalThis.queueHarness = { React, J, host, timing }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.queueHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.queueHarness.J;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':'export const host=globalThis.queueHarness.host,COMPOSER_AREAS={top:"composer.top"},TRANSCRIPT_DIRECTIVE_AREA="transcript.directives",useValue=a=>a?.get(),MessageTextContent=({text})=>text;export const startUiLatency=(...a)=>globalThis.queueHarness.timing.start(...a),completeUiLatency=(...a)=>globalThis.queueHarness.timing.complete(...a),cancelUiLatency=(...a)=>globalThis.queueHarness.timing.cancel(...a)'};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const m = await import('../plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))
const show = async node => React.act(async () => { root.render(node); await flush() })
const click = async label => React.act(async () => { const b = [...el().querySelectorAll('button')].find(x => x.textContent === label); assert.ok(b, label); b.click(); await flush() })
const answerOther = async () => {
 await click('Other')
 const input = el().querySelector('textarea')
 await React.act(async () => {
  Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(input, 'Green')
  input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
  await flush()
 })
 await click('Send answer')
}
const owner = { gatewayId: 'G1', profile: 'default', storedSessionId: 'S1' }
const route = { connectionId: 'C1', profile: 'default', session: 'S1' }
let serial = 0
const request = extra => ({ schemaVersion: 2, owner, requestId: `00000000-0000-4000-8000-${String(++serial).padStart(12, '0')}`, revision: 1, kind: 'choice', title: 'Raw card title t_example', scope: { complete: true, markdown: 'Unblock = continue; archive = abandon. No commands run from this panel.', sourceRefs: [] }, limits: [], exclusions: [], outcomes: { answer: 'Record answer' }, choices: [...['Red','Green','Blue'].map((label, i) => ({ id: `card-${i}`, label, effect: 'answer' })), { id: 'answer-text', label: 'Other', effect: 'answer' }], aliases: [], state: 'open', presentation: { kind: 'kanban-question', question: 'Which color should we use?', details: 'Original context and evidence. Unblock = continue; archive = abandon.', recommendedChoiceId: 'card-1', other: true }, ...extra })
after(async () => { await React.act(async () => root.unmount()); dom.window.close() })


// The session panel renders every card ask (question, options, Other, Skip). This plugin only keeps the decision POST.
test('card-question asks draw no card UI here; the choice posts the unchanged payload', async () => {
 const r = request(), calls = []
 const call = async (path, opts = {}) => { calls.push([path, opts]); return { request: r, operation: { operationId: 'op-fixed', dispatchState: 'pending' } } }
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call }))
 for (const sel of ['[data-clarify-question]', '[data-clarify-row]', '[data-choice-letter]', '[data-ask-situation]', '[data-ask-only-you]', '[data-recommended-row]'])
  assert.equal(el().querySelector(sel), null, sel)
 await answerOther()
 const post = calls.find(([,o]) => o.method === 'POST')
 assert.equal(post[0], `/api/plugins/user-steps/requests/${r.requestId}/decisions`)
 const { idempotencyKey, ...payload } = post[1].body
 assert.ok(idempotencyKey)
 assert.deepEqual(payload, { session: 'S1', expectedRevision: 1, choiceId: 'answer-text', revisionText: 'Green' })
 const start = timings.find(e => e[0] === 'start')
 assert.deepEqual(start.slice(0, 3), ['start', 'ui.decision_submit', { gateway: 'G1', profile: 'default', session_id: 'S1' }])
 assert.equal(timings.find(e => e[0] === 'complete')[3], start[3], 'exact attempt completed')
 assert.ok(!JSON.stringify(timings).includes('Green'), 'no answer text in timing')
})

test('dismissRequest still hides one request locally and updates the queue count without a write', async () => {
 await show(null)
 const r = request(), second = request(), calls = []
 window.hermesDesktop = { api: async args => { calls.push(args); return { requests: [r, second] } } }
 await React.act(async () => { await m.refreshRequests(); await flush() })
 assert.equal(m.openAskCount(), 2)
 m.dismissRequest(r)
 assert.equal(m.openAskCount(), 1)
 assert.ok(m.isRequestDismissed(r)); assert.ok(!m.isRequestDismissed(second))
 assert.ok(calls.every(c => !c.method || c.method === 'GET'))
 delete window.hermesDesktop
})

test('card brief / letter helpers are gone (the hub PanelModel owns display text)', () => {
 for (const name of ['card' + 'Brief', 'panel' + 'Letters', 'option' + 'Name']) assert.equal(m[name], undefined, name)
})

test('a recorded decision makes blocked-panel read the cards itself (forced), not from a pre-change copy', async () => {
 const r = request(), forced = []
 window.__blockedPanel = { refresh: force => { forced.push(force) } }
 try {
  const call = async () => ({ request: r, operation: { operationId: 'op-force', dispatchState: 'pending' } })
  await show(React.createElement(m.DecisionRow, { key: `${r.requestId}-force`, request: r, route, call }))
  assert.deepEqual(forced, [], 'nothing before the action')
  await answerOther()
  await React.act(async () => { await flush(); await flush() })
  assert.deepEqual(forced, [true])
  assert.equal(m.refreshBlockedAfterAction(null), false, 'no blocked-panel: nothing to do')
 } finally { delete window.__blockedPanel }
})

test('a recorded decision forces blocked-panel\'s refresh even when the delivery read fails', async () => {
 const r = request(), forced = []
 window.__blockedPanel = { refresh: force => { forced.push(force) } }
 try {
  const call = async (path, opts = {}) => {
   if (opts.method === 'POST') return { request: r, operation: { operationId: 'op-throw', dispatchState: 'pending' } }
   throw new Error('operation read timed out')
  }
  await show(React.createElement(m.DecisionRow, { key: `${r.requestId}-throw`, request: r, route, call }))
  await answerOther()
  await React.act(async () => { await flush(); await flush() })
  assert.deepEqual(forced, [true], 'forced once, after the POST, despite the failed read')
 } finally { delete window.__blockedPanel }
})

test('a decision whose POST fails does not force a refresh (nothing changed)', async () => {
 const r = request(), forced = []
 const before = timings.length
 window.__blockedPanel = { refresh: force => { forced.push(force) } }
 try {
  const call = async () => { throw new Error('offline') }
  await show(React.createElement(m.DecisionRow, { key: `${r.requestId}-fail`, request: r, route, call }))
  await answerOther()
  await React.act(async () => { await flush(); await flush() })
  assert.deepEqual(forced, [])
  assert.deepEqual(timings.slice(before).map(e => e[0]), ['start', 'cancel'], 'unknown outcome emits no success sample')
 } finally { delete window.__blockedPanel }
})

test('an answered POST without a recording receipt is not a successful timing sample', async () => {
 const r = request(), before = timings.length
 const call = async () => ({ request: r })
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call }))
 await answerOther()
 assert.deepEqual(timings.slice(before).map(e => e[0]), ['start', 'cancel'])
})

test('broken telemetry never prevents recording a decision', async () => {
 const saved = timing.start, r = request(), posts = []
 timing.start = () => { throw Error('telemetry unavailable') }
 try {
  const call = async (path, opts = {}) => { if (opts.method === 'POST') posts.push(opts.body); return { request: r, operation: { operationId: 'op-safe', dispatchState: 'pending' } } }
  await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call }))
  await answerOther()
  assert.equal(posts.length, 1)
 } finally { timing.start = saved }
})
