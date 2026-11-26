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

test('waiting rows are one line: no Details disclosure, detail right of the title, the word CI is the CI link', async () => {
  const opened = []
  const ci = { kind: 'open_url', label: 'Open CI', url: 'https://github.com/o/r/actions/runs/1', inline: 'CI' }
  const parent = { kind: 'open_card', label: 'Open parent card', target: { id: 'p' } }
  const waiting = row('a', { detail: 'waiting on CI \u00b7 PR #4126 \u00b7 7m on hub', details: ['Waiting on: ci https://github.com/o/r/actions/runs/1'], actions: [ci, parent] })
  await paint(m.renderModel(model({ waiting: [waiting] }), { onAction: (r, a) => opened.push(a.label) }))
  const el = document.querySelector('[data-panel-row]')
  assert.equal(el.querySelector('details'), null, 'no Details disclosure on a line row')
  const titleLine = el.querySelector('[data-ask-title]').parentElement
  assert.equal(titleLine.querySelector('[data-row-detail]').textContent, 'waiting on CI \u00b7 PR #4126 \u00b7 7m on hub', 'detail on the title line')
  assert.match(el.querySelector('[data-ask-title]').title, /Waiting on: ci https/, 'details stay reachable on hover')
  const links = [...el.querySelectorAll('[data-open-url]')]
  assert.deepEqual(links.map(b => [b.textContent, b.hasAttribute('data-inline-link')]), [['CI', true]], 'no separate Open CI button')
  assert.ok(![...el.querySelectorAll('button')].some(b => b.textContent === 'Open CI'))
  await React.act(async () => links[0].click())
  assert.deepEqual(opened, ['Open CI'])
  assert.ok(![...el.querySelectorAll('[data-open-card]')].some(b => b.textContent === 'Open parent card'), 'the parent card opens from the row menu, not a button')
  // Older model (no `inline`): the Open CI button stays.
  await paint(m.renderModel(model({ waiting: [row('a', { ...waiting, actions: [{ ...ci, inline: undefined }] })] })))
  assert.deepEqual([...document.querySelectorAll('[data-open-url]')].map(b => b.textContent), ['Open CI'])
  // Stuck keeps its reason on its own line.
  await paint(m.renderModel(model({ stuck: [row('s', { detail: 'no progress 2h' })] })))
  assert.equal(document.querySelector('[data-ask-title]').parentElement.querySelector('[data-row-detail]'), null)
})

test('an answered ask retains its one status line while a legacy header stays unknown', async () => {
  const decision = { request_id: 'req-9', text: 'Done \u00b7 delivers when this turn ends' }
  await paint(m.renderModel(model({ waiting: [row('a', { decision })] }, { state: 'unresolved', label: 'decision recorded' })))
  assert.equal(document.querySelector('[data-decision-status]').textContent, decision.text)
  assert.equal(document.querySelector('[data-session-status]').textContent, 'unknown')
  assert.equal(document.querySelector('[data-panel-status-stripe]').getAttribute('data-panel-status-stripe'), 'attention')
  assert.doesNotMatch(text(), /unresolved|Delivering|Recorded choice|Check status|Retry/)
})

test('a canonical answered-ask header preserves the backend label and row status line', async () => {
  const decision = { request_id: 'req-canonical', text: 'Done \u00b7 delivers when this turn ends' }
  const status = { status_version: 1, kind: 'waiting', revision: 7, generation: 3,
    identity: { gateway: 'gateway', profile: 'default', session_id: 'session', owner_epoch: 'epoch' },
    coverage: { state: 'complete', reasons: [] }, presentation: { stripe: 'waiting', reason_codes: ['test_waiting'] } }
  await paint(m.renderModel(model({ waiting: [row('a', { decision })] }, { state: 'waiting', label: 'decision recorded', status })))
  assert.equal(document.querySelector('[data-decision-status]').textContent, decision.text)
  assert.equal(document.querySelector('[data-session-status]').textContent, 'decision recorded')
  assert.equal(document.querySelector('[data-panel-status-stripe]').getAttribute('data-panel-status-stripe'), 'waiting')
  assert.doesNotMatch(text(), /unresolved|Delivering|Recorded choice|Check status|Retry/)
})

test('CI waits paint light blue; hub-deploy waits paint purple: row box, waiting pip and header dot (David 2026-11-24)', async () => {
  const status = reason => ({ status_version: 1, kind: 'waiting', revision: 7, generation: 3,
    identity: { gateway: 'gateway', profile: 'default', session_id: 'session', owner_epoch: 'epoch' },
    coverage: { state: 'complete', reasons: [] }, presentation: { stripe: 'waiting', reason_codes: reason } })
  const blue = /rgb\(56, 189, 248\)|#38bdf8/i
  const purple = /rgb\(163, 113, 247\)|#a371f7/i
  const pips = [{ section: 'waiting', count: 1, label: 'waiting' }]
  await paint(m.renderModel(model({ waiting: [row('c', { wait_kind: 'ci', detail: 'waiting on ci' })] },
    { state: 'waiting', status: status(['linked_work_open']), wait_kind: 'ci', pips: [{ ...pips[0], wait_kind: 'ci' }] })))
  assert.match(document.querySelector('[data-row-pip]').style.border, blue)
  assert.equal(document.querySelector('[data-model-pip="waiting"] [data-card-colour]').dataset.cardColour, 'blue')
  assert.match(document.querySelector('[data-model-pip="waiting"] [data-card-colour]').style.border, blue)
  assert.match(document.querySelector('[data-panel-status-stripe]').style.background, blue)
  await paint(m.renderModel(model({ waiting: [row('a', { wait_kind: 'deploy', detail: 'waiting on deploy' })] },
    { state: 'waiting', status: status(['linked_work_open']), wait_kind: 'deploy', pips: [{ ...pips[0], wait_kind: 'deploy' }] })))
  assert.match(document.querySelector('[data-row-pip]').style.border, purple)
  assert.equal(document.querySelector('[data-row-pip]').dataset.waitKind, 'deploy')
  assert.equal(document.querySelector('[data-model-pip="waiting"] [data-card-colour]').dataset.cardColour, 'violet')
  assert.match(document.querySelector('[data-panel-status-stripe]').style.background, purple)
  // An ordinary park keeps the amber outline and the grey header dot.
  await paint(m.renderModel(model({ waiting: [row('b', { detail: 'waiting on parent card' })] },
    { state: 'waiting', status: status(['linked_work_open']), pips })))
  assert.doesNotMatch(document.querySelector('[data-row-pip]').style.border, blue)
  assert.equal(document.querySelector('[data-model-pip="waiting"] [data-card-colour]').dataset.cardColour, 'amber')
  assert.doesNotMatch(document.querySelector('[data-panel-status-stripe]').style.background, blue)
  assert.doesNotMatch(document.querySelector('[data-row-pip]').style.border, purple)
})

test('defect 1: showsDecision tells the decision queue the panel already carries the answered ask', async () => {
  assert.equal(typeof m.showsDecision, 'function')
  assert.equal(m.showsDecision('nope'), false)
})

test('showsDecision reads the actual cached model and isolates owner, profile, session and unknown requests', async () => {
  await paint(null)
  m.resetFeedForTests()
  const saved = { ...current }
  const decision = { request_id: 'req-cached', text: 'Decision recorded' }
  m.ingestBundle('conn', 'default', { session: 'chat', model: model({ waiting: [row('answered', { decision })] }) })
  try {
    assert.ok(m.cachedModel(current), 'the fixture must populate the real model cache')
    assert.equal(m.showsDecision(decision.request_id), true)
    assert.equal(m.showsDecision('unknown-request'), false)
    assert.equal(m.showsDecision(''), false)
    for (const different of [{ connectionId: 'other-owner' }, { profile: 'other-profile' }, { session: 'other-chat' }]) {
      current = { ...saved, ...different }
      assert.equal(m.showsDecision(decision.request_id), false, JSON.stringify(different))
    }
  } finally { current = saved; m.resetFeedForTests() }
})

test('real Panel open_url dispatch uses registered OS handler, browser fallback and refuses non-http URLs', async () => {
  await paint(null)
  m.resetFeedForTests()
  const osCalls = [], browserCalls = [], areas = []
  let osResult = true
  const oldOpen = window.open
  window.open = (...args) => { browserCalls.push(args); return {} }
  const dispose = m.default.register({ register: area => areas.push(area), os: { openExternal: async url => { osCalls.push(url); return osResult } } })
  const action = { kind: 'open_url', label: 'Open CI', url: 'https://example.test/ci/run/1' }
  const seed = async url => {
    await React.act(async () => {
      m.ingestBundle('conn', 'default', { session: 'chat', model: model({ waiting: [row('ci', { actions: [{ ...action, url }] })] }) })
    })
  }
  const clickOpen = async () => React.act(async () => {
    const button = document.querySelector('[data-open-url]')
    assert.ok(button, 'mounted Panel must expose the actual Open action')
    button.click()
    await new Promise(resolve => setTimeout(resolve, 0))
  })
  try {
    await seed(action.url)
    await paint(areas.find(a => a.id === 'panel').render())
    await clickOpen()
    assert.deepEqual(osCalls, [action.url])
    assert.deepEqual(browserCalls, [], 'successful OS handler must not also open a browser window')
    osResult = false
    await seed('http://example.test/ci/run/2')
    await clickOpen()
    assert.deepEqual(osCalls, [action.url, 'http://example.test/ci/run/2'])
    assert.deepEqual(browserCalls, [['http://example.test/ci/run/2', '_blank', 'noopener']])
    for (const url of ['javascript:alert(1)', 'file:///private/example.txt']) {
      await seed(url)
      assert.ok(document.querySelector('[data-open-url]') === null, 'non-http URL has no action')
      assert.equal(await m.openUrl(url), false, 'URL opener itself must also refuse non-http input')
    }
    assert.equal(osCalls.length, 2)
    assert.equal(browserCalls.length, 1)
  } finally {
    await paint(null)
    dispose(); window.open = oldOpen; m.resetFeedForTests()
  }
})

test('live ask renders its older recorded decision from the real reducer and cache', async () => {
  const script = `import importlib.util, json
from pathlib import Path
p = Path('plugins/session-panel/model.py')
s = importlib.util.spec_from_file_location('panel_fixture', p)
f = importlib.util.module_from_spec(s); s.loader.exec_module(f)
ref = {'type':'kanban','gatewayId':'remote','profile':'default','board':'default','id':'t_abc123'}
old = {'requestId':'req-old','revision':1,'state':'open','title':'Older ask','owner':{'gatewayId':'remote','profile':'default','storedSessionId':'chat'},'scope':{'sourceRefs':[ref]},'choices':[{'id':'yes','label':'Done'}],'resolution':{'choiceId':'yes','operationId':'op-1','revision':1}}
live = {**old, 'requestId':'req-live','resolution':None,'title':'New ask'}
card = {'id':'t_abc123','gateway':'remote','profile':'default','board':'default','status':'blocked','human_request':{'action':'Pick','interaction':'approval'},'wait':{'wait_state':'human'}}
print(json.dumps(f.reduce_panel({'tasks':[card],'requests':[old,live]}, now=1795281000)))`
  const real = JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c', script], { cwd: fileURLToPath(new URL('../../../', import.meta.url)), encoding: 'utf8' }))
  assert.equal(real.sections.needs_you[0].request_id, 'req-live')
  assert.equal(real.sections.needs_you[0].decision.request_id, 'req-old')
  m.ingestBundle('conn', 'default', { session: 'chat', model: real })
  await paint(React.createElement(m.Panel))
  assert.equal(m.showsDecision('req-old'), true)
  // Cached task asks expire, but their independently recorded delivery receipt
  // remains visible; companion suppression must not swallow the sole status.
  assert.ok(document.querySelector('[data-ask-row]') === null)
  assert.equal(document.querySelector('[data-decision-status="req-old"]')?.textContent, real.sections.needs_you[0].decision.text)
  await paint(null); m.resetFeedForTests()
})

test.after(async () => { await React.act(async () => root.unmount()); dom.window.close() })

test('collapse: the caret minimizes to the header line and a new ask reopens it', async () => {
  const toggles = []
  const mdl = model({ waiting: [row('w')], needs_you: [row('a1', { options: [] })] })
  await paint(m.renderModel(mdl, { collapsed: false, onToggle: () => toggles.push(1) }))
  const btn = document.querySelector('[data-panel-toggle]')
  assert.equal(btn.getAttribute('aria-expanded'), 'true')
  assert.ok(text().includes('Card w'))
  await React.act(async () => btn.click())
  assert.equal(toggles.length, 1)
  await paint(m.renderModel(mdl, { collapsed: true, onToggle: () => {} }))
  assert.equal(document.querySelector('[data-panel-toggle]').getAttribute('aria-expanded'), 'false')
  assert.ok(!text().includes('Card w') && !text().includes('Card a1'), 'collapsed shows rows')
  assert.ok(text().includes('This session'))
  // Persistence: collapsed while the same asks remain; a new ask reopens.
  m.writeCollapsed('scope', m.attentionKeys(mdl))
  assert.equal(m.isCollapsed(mdl, m.readCollapsed('scope')), true)
  const more = model({ waiting: [row('w')], needs_you: [row('a1', { options: [] }), row('a2', { options: [] })] })
  assert.equal(m.isCollapsed(more, m.readCollapsed('scope')), false)
  m.writeCollapsed('scope', null)
  assert.equal(m.readCollapsed('scope'), null)
})
