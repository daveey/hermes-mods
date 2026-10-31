// session-panel + user-steps composed in one renderer: each legacy Kanban card is drawn once, matched by exact
// qualified identity (owner connection/gateway, profile, board, id), never by title. Live shape: request
// 0dd02bb1-... carried only its user_step alias while card t_01a18c6adae1fae9 (same decision, unlinked) showed
// both as the panel's unasked row and again under the strip's "Earlier asks".
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
const host = {
  state: { focusedSessionId: atom('R1'), focusedStoredSessionId: atom('S1'), busy: atom(false), connectionId: atom('C1'), profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' }) },
  composer: { setDraft: async () => true, focus() {}, submit: () => true }, notify: () => {}
}
globalThis.composedHarness = { React, J, host }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={
  'react':'const R=globalThis.composedHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef',
  'react/jsx-runtime':'const J=globalThis.composedHarness.J;export const jsx=J.jsx,jsxs=J.jsxs',
  '@hermes/plugin-sdk':'export const host=globalThis.composedHarness.host,COMPOSER_AREAS={top:"composer.top"},TRANSCRIPT_DIRECTIVE_AREA="transcript.directives",useValue=a=>a?.get?.(),MessageTextContent=({text})=>text'
};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const panel = await import('../plugin.js')
const steps = await import('../../user-steps/plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))

const TITLE = 'Decide whether metta keeps the shared runner'
const ask = { action: TITLE, interaction: 'reply', why_human: 'Only the owner can decide this.', scope: TITLE, evidence: 'Checked the card; owner decision required.' }
const S = Date.now() / 1000
// session-tasks chip and blocked-panel item for the SAME card (as each plugin shapes it)
const chip = (id, extra = {}) => ({ key: `kb:G1/${extra.board || 'default'}/${id}`, kind: 'kanban', id, state: 'blocked', look: 'needs-you', label: TITLE, connectionId: 'C1', gateway: 'G1', gatewayId: 'G1', profile: 'default', board: 'default', since: S - 600, activityAt: S - 60, ...extra })
const item = (id, extra = {}) => ({ id, board: 'default', connectionId: 'C1', gateway: 'G1', gatewayId: 'G1', profile: 'default', title: TITLE, text: TITLE, human_request: ask, at: 1, ...extra })
const STEP = { id: 'us_0dd02bb1aa', kind: 'decide', text: TITLE, status: 'open', created: 1, session: 'S1' }
const canonicalRequest = {
  schemaVersion: 2, requestId: '0dd02bb1-bcb2-434b-acb2-ab36816ce2a5', revision: 1, kind: 'choice', title: TITLE, state: 'open',
  owner: { gatewayId: 'G1', profile: 'default', storedSessionId: 'S1' },
  scope: { markdown: '', complete: false, sourceRefs: [] }, aliases: [{ gatewayId: 'G1', profile: 'default', type: 'user_step', id: STEP.id }],
  limits: [], exclusions: [], outcomes: { answer: 'The agent continues with your answer.' },
  choices: [{ id: 'a', label: 'Keep it', effect: 'answer' }, { id: 'b', label: 'Release it', effect: 'answer' }]
}

function wire({ chips, items, requests = [] }) {
  window.localStorage.clear()
  window.__sessionTasks = { snapshot: { runtime: 'R1', stored: 'S1', chips }, subscribe: () => () => {}, refresh() {}, openTaskChip() {}, lineageIds: async id => new Set([id]) }
  window.__blockedPanel = { snapshot: { stored: 'S1', connectionId: 'C1', profile: 'default', items } }
  window.hermesDesktop = {
    api: async ({ path }) => {
      if (path.includes('/session-cards')) return { tasks: [], includes: ['done'] }
      if (path.includes('/requests')) return { requests }
      if (path.includes('/steps')) return { steps: [STEP], card: null }
      if (path.includes('/ping')) return { ok: true }
      return {}
    }
  }
  const dispose = steps.default.register({ register() {} })
  return dispose
}

async function render(key) {
  await React.act(async () => { root.render(React.createElement(panel.Panel, { key })); await flush() })
  await React.act(async () => { await steps.refresh(); await steps.refreshRequests(); await flush(); await flush() })
}

const unaskedRows = id => [...el().querySelectorAll('[data-panel-row]')].filter(r => r.getAttribute('data-panel-row').endsWith(`/${id}`))
const stripCards = id => [...el().querySelectorAll(`[data-user-steps-card="${id}"]`)]

test('composed: the unlinked legacy card renders once; the same-title canonical request stays its own tile', async () => {
  const dispose = wire({ chips: [chip('t_01a18c6adae1fae9')], items: [item('t_01a18c6adae1fae9')], requests: [canonicalRequest] })
  await render('live-shape')
  assert.equal(unaskedRows('t_01a18c6adae1fae9').length, 1, el().innerHTML)
  assert.equal(stripCards('t_01a18c6adae1fae9').length, 0, 'not repeated under Earlier asks')
  // same title, different identity: the canonical request is still drawn and was not folded into the card
  assert.equal(el().querySelectorAll('[data-action-request]').length, 1)
  assert.match(el().querySelector('[data-action-request]').getAttribute('data-action-request'), /0dd02bb1-bcb2-434b-acb2-ab36816ce2a5/)
  dispose()
})

test('composed: same id on another gateway, board or profile is a different card and still shows', async () => {
  const id = 't_aaaa00001111'
  const dispose = wire({
    chips: [chip(id)],
    items: [item(id), item(id, { connectionId: 'C2', gateway: 'G2', gatewayId: 'G2' }), item(id, { board: 'ops' }), item(id, { profile: 'other' })]
  })
  await render('qualified')
  assert.equal(unaskedRows(id).length, 1)
  assert.equal(stripCards(id).length, 3, 'only the exact (C1, default, default) card is deduplicated')
  dispose()
})

test('composed: without session-panel rows the strip keeps every card (nothing hidden by title)', async () => {
  const dispose = wire({ chips: [], items: [item('t_bbbb00002222'), item('t_cccc00003333')] })
  await render('no-panel-rows')
  assert.equal(stripCards('t_bbbb00002222').length + stripCards('t_cccc00003333').length, 2)
  dispose()
})

test('sameCard: exact qualified identity only', () => {
  const a = { id: 't_x', board: 'default', connectionId: 'C1', profile: 'default' }
  assert.equal(steps.sameCard(a, { ...a }), true)
  assert.equal(steps.sameCard(a, { ...a, board: undefined }), true, 'missing board means default')
  assert.equal(steps.sameCard(a, { ...a, connectionId: 'C2' }), false)
  assert.equal(steps.sameCard(a, { ...a, board: 'ops' }), false)
  assert.equal(steps.sameCard(a, { ...a, profile: 'other' }), false)
  assert.equal(steps.sameCard(a, { ...a, id: 't_y' }), false)
  assert.equal(steps.sameCard({ id: 't_x' }, { id: 't_x' }), false, 'no owner on either side never matches')
  assert.equal(steps.sameCard({ id: 't_x', gateway: 'G1' }, { id: 't_x', gatewayId: 'G1' }), true)
  assert.equal(steps.sameCard({ id: 't_x', gateway: 'G1', title: 'A' }, { id: 't_y', gateway: 'G1', title: 'A' }), false, 'title is not identity')
})

test('done', () => { root.unmount() })
