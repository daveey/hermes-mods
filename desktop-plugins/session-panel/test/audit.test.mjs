import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve } from 'node:path'
import { homedir } from 'node:os'
import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime'), { JSDOM } = require('jsdom')
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.test' })
globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
let current = { connectionId: 'conn', profile: 'default', session: 'chat' }
const atom = get => ({ get })
const drafts = [], requests = []
const host = {
  state: { focusedStoredSessionId: atom(() => current.session), focusedSessionId: atom(() => current.session),
    focusedSessionOwner: atom(() => ({ connectionId: current.connectionId, profile: current.profile })), connectionId: atom(() => current.connectionId), profile: atom(() => current.profile) },
  composer: { setDraft: async (...args) => drafts.push(args), focus: () => {} },
  connections: async () => [{ id: 'foreign' }]
}
globalThis.hubHarness = { React, J, host }
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(s,c,n) {
 const m = {
 'react': 'const R=globalThis.hubHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef',
 'react/jsx-runtime': 'const J=globalThis.hubHarness.J;export const jsx=J.jsx,jsxs=J.jsxs',
 '@hermes/plugin-sdk': 'export const host=globalThis.hubHarness.host,Codicon=()=>null,DisclosureCaret=()=>null,COMPOSER_AREAS={top:"composer.top"},useValue=a=>a?.get?.()'
 }; return m[s]?{url:'data:text/javascript,'+encodeURIComponent(m[s]),shortCircuit:true}:n(s,c)
}
export async function load(u,c,n) {
 if(u.endsWith('/plugin.js')) { const fs=await import('node:fs/promises');return {format:'module',source:await fs.readFile(new URL(u),'utf8'),shortCircuit:true} }
 return n(u,c)
}`))
const m = await import('../plugin.js'), { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const paint = tree => React.act(async () => root.render(tree))
const row = (key, extra = {}) => ({ key, kind: 'card', source: 'card', id: key, title: `Card ${key}`, detail: '', details: [], actions: [], request_id: null, ...extra })
const model = (sections = {}, header = {}) => {
  const all = { needs_you: [], stuck: [], running: [], waiting: [], ready: [], done: [], ...sections }
  const counts = Object.fromEntries(Object.entries(all).map(([k, v]) => [k, v.length]))
  return { version: 1, revision: 'r'.repeat(64), header: { state: 'working', counts, pips: [], ...header }, sections: all }
}
const text = () => document.getElementById('root').textContent

test('defect 5: a session with neither open work nor history renders nothing', async () => {
  await paint(m.renderModel(model()))
  assert.equal(document.querySelector('[data-session-status]'), null)
  assert.equal(text(), '')
})

test('defect 6: Done rows do not repeat "done"', async () => {
  await paint(m.renderModel(model({ done: [row('a', { detail: 'done' }), row('b', { detail: 'Merged as abc' })] })))
  const details = [...document.querySelectorAll('[data-panel-row] [data-row-detail]')].map(e => e.textContent)
  assert.deepEqual(details, ['Merged as abc'])
})

test('defect 8: emoji glyphs are stripped from titles even from an older model', async () => {
  await paint(m.renderModel(model({ waiting: [row('a', { title: '\u26d4 Hermes bug \u2705 ok \u26a0' })] })))
  assert.equal(document.querySelector('[data-ask-title]').textContent, 'Hermes bug ok \u26a0')
})

test('defect 4: CI waits show the short phrase and an Open action that opens the URL', async () => {
  const opened = []
  const action = { kind: 'open_url', label: 'Open CI', url: 'https://github.com/o/r/actions/runs/1' }
  await paint(m.renderModel(model({ waiting: [row('a', { detail: 'waiting on CI \u00b7 PR #3881 \u00b7 2m', actions: [action] })] }), { onAction: (r, a) => opened.push(a.url) }))
  assert.match(text(), /waiting on CI \u00b7 PR #3881 \u00b7 2m/)
  assert.doesNotMatch(document.querySelector('[data-panel-row]').querySelector('[data-row-detail]').textContent, /http/)
  await React.act(async () => document.querySelector('[data-open-url]').click())
  assert.deepEqual(opened, [action.url])
  await paint(m.renderModel(model({ waiting: [row('a', { actions: [{ ...action, url: 'javascript:alert(1)' }] })] })))
  assert.equal(document.querySelector('[data-open-url]'), null) // only http(s) links get a button
})

test('defect 1: an answered ask is one status line on its row; the header never says unresolved', async () => {
  const decision = { request_id: 'req-9', text: 'Done \u00b7 delivers when this turn ends' }
  await paint(m.renderModel(model({ waiting: [row('a', { decision })] }, { state: 'unresolved', label: 'decision recorded' })))
  assert.equal(document.querySelector('[data-decision-status]').textContent, decision.text)
  assert.equal(document.querySelector('[data-session-status]').textContent, 'decision recorded')
  assert.doesNotMatch(text(), /unresolved|Delivering|Recorded choice|Check status|Retry/)
})

test('defect 1: showsDecision tells the decision queue the panel already carries the answered ask', async () => {
  assert.equal(typeof m.showsDecision, 'function')
  assert.equal(m.showsDecision('nope'), false)
})

test.after(async () => { await React.act(async () => root.unmount()); dom.window.close() })
