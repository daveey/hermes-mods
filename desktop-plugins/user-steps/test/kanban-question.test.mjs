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
globalThis.queueHarness = { React, J, host }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.queueHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.queueHarness.J;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':'export const host=globalThis.queueHarness.host,COMPOSER_AREAS={top:"composer.top"},TRANSCRIPT_DIRECTIVE_AREA="transcript.directives",useValue=a=>a?.get(),MessageTextContent=({text})=>text'};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const m = await import('../plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))
const show = async node => React.act(async () => { root.render(node); await flush() })
const click = async id => React.act(async () => { const b = el().querySelector(`[data-clarify-row="${id}"]`); assert.ok(b, id); b.click(); await flush() })
const owner = { gatewayId: 'G1', profile: 'default', storedSessionId: 'S1' }
const route = { connectionId: 'C1', profile: 'default', session: 'S1' }
let serial = 0
const request = extra => ({ schemaVersion: 2, owner, requestId: `00000000-0000-4000-8000-${String(++serial).padStart(12, '0')}`, revision: 1, kind: 'choice', title: 'Raw card title t_example', scope: { complete: true, markdown: 'Unblock = continue; archive = abandon. No commands run from this panel.', sourceRefs: [] }, limits: [], exclusions: [], outcomes: { answer: 'Record answer' }, choices: ['Red','Green','Blue'].map((label, i) => ({ id: `card-${i}`, label, effect: 'answer' })), aliases: [], state: 'open', presentation: { kind: 'kanban-question', question: 'Which color should we use?', details: 'Original context and evidence. Unblock = continue; archive = abandon.', recommendedChoiceId: 'card-1', other: true }, ...extra })
after(async () => { await React.act(async () => root.unmount()); dom.window.close() })

// Compare the visible compact face, not hidden Details textContent.
test('card question has lettered ordered rows, hidden details and unchanged choice payload', async () => {
 const r = request(), calls = []
 const call = async (path, opts = {}) => { calls.push([path, opts]); return { request: r, operation: { operationId: 'op-fixed', dispatchState: 'pending' } } }
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call, position: 2, total: 3 }))
 assert.ok(el().querySelector('h3') === null, 'compact question must not show a card-title heading')
 assert.equal(el().querySelector('details').open, false)
 const visible = el().cloneNode(true); visible.querySelectorAll('details').forEach(n => n.remove())
 assert.doesNotMatch(visible.textContent, /Unblock =|archive =|No commands run from this panel|t_example/)
 assert.match(visible.textContent, /2\/3 — Which color should we use\?/)
 assert.match(visible.textContent, /Pick one/)
 assert.deepEqual([...el().querySelectorAll('[data-choice-letter]')].map(n => n.textContent), ['A','B','C','D','E'])
 assert.deepEqual([...el().querySelectorAll('[data-option-label]')].map(n => n.textContent), ['Green (recommended)','Red','Blue','Skip','Other (type your answer)'])
 await click('card-1')
 const post = calls.find(([,o]) => o.method === 'POST')
 assert.equal(post[0], `/api/plugins/user-steps/requests/${r.requestId}/decisions`)
 const { idempotencyKey, ...payload } = post[1].body
 assert.ok(idempotencyKey)
 assert.deepEqual(payload, { session: 'S1', expectedRevision: 1, choiceId: 'card-1', revisionText: 'Green' })
})

test('Other sends typed text using existing decision payload', async () => {
 const r = request(), calls = []
 const call = async (path, opts = {}) => { calls.push([path,opts]); return { request: r, operation: { operationId: 'op-other', dispatchState: 'pending' } } }
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call }))
 await click('answer-text')
 const input = el().querySelector('textarea'); assert.equal(document.activeElement, input)
 await React.act(async () => { Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(input,'Use purple'); input.dispatchEvent(new dom.window.Event('input',{bubbles:true})); await flush() })
 await React.act(async () => { [...el().querySelectorAll('button')].find(b => b.textContent === 'Send answer').click(); await flush() })
 const post = calls.find(([,o]) => o.method === 'POST')
 assert.equal(post[1].body.choiceId, 'answer-text'); assert.equal(post[1].body.revisionText, 'Use purple')
})

test('Skip hides only this request locally and updates queue count without a write', async () => {
 await show(null)
 const r = request(), second = request(), calls = []
 window.hermesDesktop = { api: async args => { calls.push(args); return { requests: [r, second] } } }
 await show(React.createElement(m.ActionQueue, { embedded: true }))
 await React.act(async () => { await m.refreshRequests(); await flush() })
 assert.equal(m.openAskCount(), 2)
 assert.match(el().textContent, /1\/2/); assert.match(el().textContent, /2\/2/)
 await click('skip')
 assert.equal(m.openAskCount(), 1)
 assert.equal(el().querySelectorAll('[data-action-request]').length, 1)
 assert.ok(m.isRequestDismissed(r)); assert.ok(!m.isRequestDismissed(second))
 assert.ok(calls.every(c => !c.method || c.method === 'GET'))
 await show(null); delete window.hermesDesktop
})

test('fallback Unblock and Archive remain answer rows', async () => {
 const r = request({ choices: [{ id: 'card-0', label: 'Unblock', effect: 'answer' }, { id: 'card-1', label: 'Archive', effect: 'answer' }], presentation: { kind: 'kanban-question', question: 'Ready to continue?', details: 'Evidence', recommendedChoiceId: null, other: true } })
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call: async () => ({}) }))
 assert.deepEqual([...el().querySelectorAll('[data-option-label]')].map(n => n.textContent), ['Unblock','Archive','Skip','Other (type your answer)'])
})
