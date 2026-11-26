// Real panel/queue integration: hidden cached decisions must not suppress their companion status.
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
const atom = value => ({ get: () => value })
const route = { connectionId: 'C1', profile: 'default', session: 'S1' }
const host = { state: { focusedStoredSessionId: atom('S1'), connectionId: atom('C1'), profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' }) } }
globalThis.coverageHarness = { React, J, host }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.coverageHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.coverageHarness.J;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':'export const host=globalThis.coverageHarness.host,COMPOSER_AREAS={top:"composer.top"},TRANSCRIPT_DIRECTIVE_AREA="transcript.directives",useValue=a=>a?.get(),MessageTextContent=({text})=>text'};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const queue = await import('../plugin.js')
const panel = await import('../../session-panel/plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const show = async node => React.act(async () => { root.render(node); await new Promise(r => setTimeout(r, 0)) })
after(async () => { await React.act(async () => root.unmount()); dom.window.close() })

test('unsupported panel retains companion decision status; healthy and recovered cache suppress duplication', async () => {
 panel.resetFeedForTests()
 const request = { schemaVersion: 2, owner: { gatewayId: 'G1', profile: 'default', storedSessionId: 'S1' },
  requestId: '00000000-0000-4000-8000-000000000001', revision: 1, kind: 'approval', title: 'Retained decision status',
  scope: { complete: true, markdown: 'Stage A', sourceRefs: [] }, limits: [], exclusions: [],
  outcomes: { approve: 'Stage A only', revise: 'Work stays blocked', decline: 'No execution' }, choices: [{ id: 'yes', label: 'Approve', effect: 'approve' }, { id: 'edit', label: 'Revise', effect: 'revise' }, { id: 'no', label: 'Decline', effect: 'decline' }],
  aliases: [], state: 'open', resolution: { operationId: 'op-coverage', choiceId: 'yes', revision: 1 } }
 const call = async path => {
  if (path.includes('/session-panel/')) throw Error('404: Not Found')
  if (path.endsWith('/ping')) return { features: [] }
  if (path.includes('/operations/')) return { operation: { operationId: 'op-coverage', dispatchState: 'pending' }, request }
  return { requests: [request] }
 }
 window.hermesDesktop = { api: ({ path }) => call(path) }
 window.__sessionPanel = { active: true, showsDecision: panel.showsDecision, covers: panel.covers }
 const sections = Object.fromEntries(['needs_you', 'stuck', 'running', 'waiting', 'ready', 'done'].map(s => [s, []]))
 sections.waiting = [{ key: 'answered', title: request.title, detail: '', actions: [], decision: { request_id: request.requestId, text: 'Decision recorded' } }]
 const seed = () => panel.ingestBundle('C1', 'default', { session: 'S1', model: { version: 1, revision: 'r1', header: { counts: {}, pips: [] }, sections } })
 const companion = key => React.createElement(queue.ActionQueue, { key, embedded: true, companion: true })
 try {
  seed()
  assert.equal(panel.showsDecision(request.requestId), true, 'healthy cached control')
  await queue.refreshRequests(call)
  await show(companion('healthy'))
  assert.equal(el().querySelector('[data-action-request]'), null, 'healthy panel owns cached status')
  await show(null)
  await panel.feedCatchUp({ connectionId: 'C1', profile: 'default', sessions: ['S1'] })
  await show(React.createElement(React.Fragment, null, React.createElement(panel.Panel), companion('unsupported')))
  assert.ok(el().querySelector('[data-session-panel-error][role=alert]'), 'actual panel renders unsupported alert')
  assert.equal(panel.covers(route), false)
  assert.ok(el().querySelector('[data-action-request]'), 'recorded decision remains visible when panel cannot display it')
  assert.equal(panel.showsDecision(request.requestId), false)
  await show(null)
  panel.resetFeedForTests(); seed()
  assert.equal(panel.showsDecision(request.requestId), true, 'supported cache recovers')
  await show(companion('recovered'))
  assert.equal(el().querySelector('[data-action-request]'), null, 'recovery avoids duplicate status')
 } finally { await show(null); panel.resetFeedForTests(); delete window.__sessionPanel; delete window.hermesDesktop }
})
