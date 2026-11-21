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
const publicCases = JSON.parse(execFileSync(process.env.HERMES_MODS_PYTHON || 'python3', [fileURLToPath(new URL('./hub-model-fixtures.py', import.meta.url))], { encoding: 'utf8' }))
const privateCases = process.env.HERMES_PANEL_GOLDENS ? JSON.parse(readFileSync(process.env.HERMES_PANEL_GOLDENS, 'utf8')) : {}
const cases = { ...publicCases, ...privateCases }
for (const [name, fixture] of Object.entries(cases)) {
 test(`hub model golden: ${name}`, async () => {
  const model = fixture.model || fixture.expected
  assert.ok(model, 'golden contains the P1 output model')
  const before = JSON.stringify(model)
  await paint(m.renderModel(model))
  assert.equal(document.querySelector('[data-session-status]').textContent, model.header.state)
  assert.equal(document.querySelector('[data-hub-model]').getAttribute('data-hub-model'), model.revision)
  for (const [section, rows] of Object.entries(model.sections)) {
   assert.equal(document.querySelector(`[data-model-count="${section}"]`).textContent, String(model.header.counts[section]))
   const block = document.querySelector(`[data-model-section="${section}"]`)
   assert.equal(!!block, rows.length > 0)
   if (!block) continue
   const articles = [...block.querySelectorAll('[data-panel-row]')]
   assert.equal(articles.length, rows.length)
   for (const [i, row] of rows.entries()) {
    const article = articles[i]
    assert.equal(article.querySelector('[data-ask-title]').textContent, row.title)
    if (row.detail) assert.ok(article.textContent.includes(row.detail))
    if (row.details.length) assert.deepEqual([...article.querySelectorAll('details > div')].map(e => e.textContent), row.details)
    if (section !== 'needs_you') continue
    if (row.situation) {
     const el = article.querySelector('[data-ask-situation]')
     assert.equal(el.textContent, row.situation); assert.equal(el.style.webkitLineClamp, '3')
    }
    if (row.only_you) assert.equal(article.querySelector('[data-ask-only-you]').textContent, row.only_you)
    const options = [...article.querySelectorAll('[data-choice-letter]')]
    assert.deepEqual(options.map(e => e.dataset.choiceLetter), row.options.map(o => o.letter))
    for (const [j, option] of row.options.entries()) {
     assert.equal(options[j].querySelector('strong[data-option-label]').textContent, option.name)
     assert.equal(options[j].querySelector('[data-option-detail]').textContent, option.detail)
     assert.equal(options[j].querySelector('[data-option-detail]').style.whiteSpace, 'nowrap')
     assert.equal(options[j].hasAttribute('data-recommended-row'), option.recommended)
     if (option.recommended && option.reason) assert.equal(options[j].querySelector('[data-option-reason]').textContent, `Why: ${option.reason}`)
    }
   }
  }
  assert.equal(JSON.stringify(model), before, 'pure renderer never mutates input')
 })
}
test('header counts/pips remain server values, not recomputed row totals', async () => {
 const model = structuredClone(publicCases.all_sections.model)
 model.header.counts.running = 78; model.header.pips = [{ section: 'running', count: 91, label: 'hub value' }]
 await paint(m.renderModel(model))
 assert.equal(document.querySelector('[data-model-pip]').textContent, '91 hub value')
 assert.equal(document.querySelector('[data-model-section="running"] h3').textContent, 'In progress · 78')
})
test('default-off setting registers a Desktop page; opt-in switches mount cached models with no legacy fetch', async () => {
 assert.equal(m.hubModelEnabled(), false)
 let page
 const timers = [globalThis.setTimeout, globalThis.setInterval]
 globalThis.setTimeout = () => 0; globalThis.setInterval = () => 0
 const dispose = m.default.register({ register: () => {}, registerSettingsPage: p => { page = p } })
 ;[globalThis.setTimeout, globalThis.setInterval] = timers
 assert.equal(page.title, 'Session Panel')
 await paint(page.render())
 assert.equal(document.querySelector('input').checked, false)
 await React.act(async () => document.querySelector('input').click())
 assert.equal(window.localStorage.getItem('sessionPanel.hubModel'), 'true')
 const a = publicCases.own_letters.model, b = publicCases.external_watch.model
 m.ingestBundle('conn', 'default', { session: 'chat', model: a })
 m.ingestBundle('conn', 'default', { session: 'other', model: b })
 window.hermesDesktop = { api: async args => { requests.push(args); throw Error('render must not read') } }
 // Already watched scopes ensure feedFocus need not register on these cached switches.
 await paint(J.jsx(m.Panel, {}))
 assert.equal(document.querySelector('[data-hub-model]').getAttribute('data-hub-model'), a.revision)
 current = { ...current, session: 'other' }; await paint(J.jsx(m.Panel, {}))
 assert.equal(document.querySelector('[data-hub-model]').getAttribute('data-hub-model'), b.revision)
 current = { ...current, profile: 'different' }; await paint(J.jsx(m.Panel, {}))
 assert.ok(document.querySelector('[data-hub-model-pending]'))
 assert.ok(requests.every(r => r.path === '/api/plugins/session-panel/changes'), 'only the existing feed registration, no classification/card/brief/person-wait reads')
 await paint(null)
 current = { connectionId: 'conn', profile: 'default', session: 'chat' }
 dispose(); m.setHubModelEnabled(false)
})
test('card option drafts only; question uses choice_id, verified owner route and exact revision with readback', async () => {
 const row = structuredClone(publicCases.remote_card.model.sections.needs_you[0])
 await m.chooseModelOption(row, row.options[0], current)
 assert.equal(drafts.at(-1)[1], m.chooseText(row, row.options[0]))
 const q = structuredClone(publicCases.own_letters.model.sections.needs_you[0])
 const posts = [], calls = []
 const action = q.actions.find(a => a.kind === 'decide')
 const request = { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner, scope: { complete: true, markdown: 'Full scope' }, choices: q.options.map(o => ({ id: o.choice_id, label: o.name, effect: 'answer' })) }
 const call = async (path, opts) => {
  calls.push([path, opts])
  if (opts.connectionId !== 'foreign') return { request: { ...request, owner: { ...action.owner, gatewayId: 'wrong' } } }
  if (opts.method === 'POST') { posts.push(opts); return { operation: { operationId: 'op' } } }
  if (path.includes('/operations/op')) return { operation: { operationId: 'op', dispatchState: 'delivered' }, request: { ...request, resolution: { operationId: 'op' } } }
  if (path.includes('/operations?')) return { operation: { operationId: 'op' } }
  return { request }
 }
 await m.chooseModelOption(q, q.options[0], current, { call })
 assert.equal(posts.length, 1); assert.equal(posts[0].connectionId, 'foreign')
 assert.equal(posts[0].profile, action.owner.profile)
 assert.equal(posts[0].body.choiceId, 'card-0'); assert.equal(posts[0].body.expectedRevision, q.request_revision)
 assert.equal(posts[0].body.session, action.owner.storedSessionId)
 assert.ok(posts[0].body.idempotencyKey)
 assert.equal(m.modelDecisionStatus(q).text, 'Decision delivered.')
 await m.chooseModelOption(q, q.options[1], current, { call }); assert.equal(posts.length, 1)
 assert.ok(calls.some(([p]) => p.includes('/operations/op')))
})
test('lost POST remains locked across alternate choices and check queries the retained key, never reposts', async () => {
 const q = structuredClone(publicCases.own_letters.model.sections.needs_you[0]); q.request_revision += 2
 q.actions.find(a => a.kind === 'decide').revision = q.request_revision
 const action = q.actions.find(a => a.kind === 'decide'), posts = [], keys = []
 const call = async (path, opts) => {
  if (opts.method === 'POST') { posts.push(opts.body); throw Error('network lost') }
  if (path.includes('/operations?')) { keys.push(new URL('https://example.test'+path).searchParams.get('idempotencyKey')); return { operation: null } }
  return { request: { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner, scope: { complete: true, markdown: 'scope' }, choices: [{ id: 'card-0', label: 'Pick', effect: 'answer' }] } }
 }
 await m.chooseModelOption(q, q.options[0], current, { call }); await m.chooseModelOption(q, q.options[1], current, { call })
 assert.equal(posts.length, 1); assert.ok(m.modelDecisionStatus(q).locked)
 await m.checkModelDecision(q, { call }); assert.deepEqual(keys, [posts[0].idempotencyKey]); assert.equal(posts.length, 1)
})
test('stale revision and foreign owner fail closed before POST', async () => {
 for (const invalid of ['revision', 'owner']) {
  const q = structuredClone(publicCases.own_letters.model.sections.needs_you[0]); q.request_revision += invalid === 'revision' ? 10 : 11
  const action = q.actions.find(a => a.kind === 'decide'); action.revision = q.request_revision
  let posts = 0
  await m.chooseModelOption(q, q.options[0], current, { call: async (path, opts) => {
   if (opts.method === 'POST') posts++
   return { request: { requestId: q.request_id, revision: invalid === 'revision' ? 1 : q.request_revision, owner: invalid === 'owner' ? {} : action.owner, state: 'open' } }
  } })
  assert.equal(posts, 0)
 }
})
test.after(async () => { await React.act(async () => root.unmount()); dom.window.close() })
