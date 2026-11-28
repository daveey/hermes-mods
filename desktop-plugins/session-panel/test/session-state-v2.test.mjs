// Shared core scenarios -> real Feed/model -> mounted Panel, Chip and bound ask tiles.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve } from 'node:path'
import { homedir } from 'node:os'
import { readFileSync, existsSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const mods = fileURLToPath(new URL('../../../', import.meta.url))
const core = process.env.HERMES_CORE_SOURCE || resolve(mods, '../core')
const available = existsSync(resolve(core, 'tests/fixtures/session-state-v2/scenarios.json'))
const baseline = JSON.parse(readFileSync(resolve(mods, 'plugins/session-panel/tests/session_state_v2_goldens.json'), 'utf8'))
const actual = available ? JSON.parse(execFileSync(process.env.HERMES_MODS_PYTHON || 'python3',
  [resolve(mods, 'plugins/session-panel/tests/session_state_v2_fixtures.py')],
  { encoding: 'utf8', env: { ...process.env, HERMES_CORE_SOURCE: core } })) : null

test('shared manifest exactly covers every golden scenario', { skip: !available }, () => {
 const shared = JSON.parse(readFileSync(resolve(core, 'tests/fixtures/session-state-v2/scenarios.json')))
 assert.deepEqual(actual.coverage, shared.scenarios.map(c => c.id))
 assert.deepEqual(actual, baseline)
})

if (available) {
 const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
 const React = require('react'), J = require('react/jsx-runtime'), { JSDOM } = require('jsdom')
 const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.test' })
 globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
 Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
 const atom = get => ({ get })
 const route = { connectionId: 'fixture-connection', profile: 'default', session: 'fixture-chat' }
 const host = { state: { focusedStoredSessionId: atom(() => route.session), focusedSessionId: atom(() => route.session),
   focusedSessionOwner: atom(() => ({ connectionId: route.connectionId, profile: route.profile })),
   connectionId: atom(() => route.connectionId), profile: atom(() => route.profile) },
   connections: async () => [], composer: { setDraft: async () => {}, focus: () => {} } }
 globalThis.sessionV2Harness = { React, J, host }
 register('data:text/javascript,' + encodeURIComponent(`
 export async function resolve(s,c,n) {
 const m={
 'react':'const R=globalThis.sessionV2Harness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef',
 'react/jsx-runtime':'const J=globalThis.sessionV2Harness.J;export const jsx=J.jsx,jsxs=J.jsxs',
 '@hermes/plugin-sdk':'const H=globalThis.sessionV2Harness,R=H.React,F=p=>R.createElement(R.Fragment,null,p.children);export const host=H.host,Codicon=()=>null,DisclosureCaret=()=>null,COMPOSER_AREAS={top:"composer.top"},useValue=a=>a?.get?.(),ContextMenu=F,ContextMenuTrigger=F,ContextMenuContent=()=>null,ContextMenuItem=()=>null'
 };return m[s]?{url:'data:text/javascript,'+encodeURIComponent(m[s]),shortCircuit:true}:n(s,c)
 }
 export async function load(u,c,n) {if(u.endsWith('/plugin.js')){const fs=await import('node:fs/promises');return {format:'module',source:await fs.readFile(new URL(u),'utf8'),shortCircuit:true}}return n(u,c)}
 `))
 const panel = await import('../plugin.js'), tasks = await import('../../session-tasks/plugin.js')
 const { createRoot } = require('react-dom/client')
 const root = createRoot(document.getElementById('root'))
 const paint = tree => React.act(async () => root.render(tree))
 const realNow = Date.now
 Date.now = () => actual.now * 1000
 for (const id of actual.coverage) test(`shared ${id}: mounted Panel + Chip + exact choice binding`, async () => {
   const { model, requests } = actual.cases[id]
   const expected = baseline.cases[id].model
   panel.resetFeedForTests(); window.localStorage.clear()
   const posts = []
   window.hermesDesktop = { api: async req => {
     if (req.path.endsWith('/decisions')) { posts.push(req); return { operation: { operationId: 'fixture-receipt', state: 'queued' } } }
     if (req.body?.subscriber) return {}
     return { feed: 'fixture-feed', epoch: 'fixture-epoch', profile: 'default', rev: 1,
       bundles: { 'fixture-chat': { session: 'fixture-chat', model } } }
   } }
   try {
     await panel.feedCatchUp({ ...route, sessions: [route.session] })
     await React.act(async () => panel.onFeedEvent({ payload: { feed: 'fixture-feed', epoch: 'fixture-epoch',
       profile: 'default', rev: 1, heartbeat: true, fresh: [route.session] } }))
     await paint(React.createElement(panel.Panel))
     const expectedRows = Object.entries(expected.sections).flatMap(([section, rows]) => rows.map(row => ({ section, row })))
     if (!expectedRows.length) {
       // Actual Panel intentionally hides a rowless failed-turn model: record, do not fabricate a tile.
       assert.equal(document.querySelector('[data-session-panel]'), null)
       return
     }
     assert.ok(document.querySelector('[data-session-panel]'), id)
     assert.equal(document.querySelector('[data-session-status]').textContent, expected.header.label || expected.header.state)
     assert.equal(document.querySelectorAll('[data-panel-row]').length, expectedRows.length)
     for (const { section, row } of expectedRows) {
       const node = [...document.querySelectorAll('[data-panel-row]')].find(n => n.getAttribute('data-panel-row') === row.key)
       assert.ok(node, `${id} ${row.key}`)
       assert.ok(node.textContent.includes(row.title))
       const painted = panel.clockRow(row, actual.now)
       if (painted.detail) assert.ok(node.textContent.includes(painted.detail), `${id}: ${painted.detail}`)
       const detailText = node.querySelector('[data-ask-title]')?.title || node.textContent
       for (const detail of painted.details || []) if (typeof detail === 'string') assert.ok(detailText.includes(detail) || node.textContent.includes(detail), detail)
       const box = node.querySelector('[data-row-pip]')
       if (box) {
         const kind = row.wait_kind || row.last_wait_kind
         const palette = { stuck: '#f0883e', running: '#3b82f6', ready: '#3fb950', done: '#8b949e' }
         const color = row.attention === 'triage_overdue' ? '#f85149' : section === 'waiting'
           ? ({ agent: '#3b82f6', deploy: '#a371f7', ci: '#38bdf8', clock: '#38bdf8', card: '#38bdf8' }[kind] || '#d29922') : palette[section]
         const probe = document.createElement('span'); probe.style.color = color
         assert.equal(section === 'waiting' && row.attention !== 'triage_overdue' ? box.style.borderColor : box.style.backgroundColor, probe.style.color)
       }
       if (section === 'needs_you') {
         assert.equal(node.style.borderLeftColor, 'rgb(248, 81, 73)')
         if (row.situation) assert.equal(node.querySelector('[data-ask-situation]').textContent, row.situation)
         if (row.only_you) assert.equal(node.querySelector('[data-ask-only-you]').textContent, row.only_you)
         const request = requests.find(r => r.requestId === row.request_id)
         const action = row.actions.find(a => a.kind === 'decide')
         assert.deepEqual(action.owner, request.owner)
         assert.equal(action.revision, request.revision)
         assert.equal(node.getAttribute('data-ask-binding'), JSON.stringify([request.owner.gatewayId, request.owner.profile, request.requestId]))
         const buttons = [...node.querySelectorAll('[data-choice-letter]')]
         assert.deepEqual(buttons.map(b => b.getAttribute('data-choice-letter')), row.options.map(o => o.letter))
         for (let i=0; i<buttons.length; i++) {
           assert.equal(buttons[i].disabled, false)
           assert.equal(buttons[i].querySelector('[data-option-label]').textContent, row.options[i].name)
           assert.equal(buttons[i].querySelector('[data-option-detail]').textContent, row.options[i].detail)
         }
         // Exercise the actual mounted Panel callback, with only the transport substituted.
         await React.act(async () => buttons[0].click())
         assert.equal(posts.length, 1)
         assert.ok(posts[0].path.includes(encodeURIComponent(request.requestId)))
         assert.equal(posts[0].body.expectedRevision, request.revision)
         assert.equal(posts[0].body.choiceId, row.options[0].choice_id)
         assert.equal(posts[0].connectionId, route.connectionId)
         assert.equal(posts[0].profile, request.owner.profile)
         assert.equal(posts[0].body.session, request.owner.storedSessionId)
       }
     }
     await paint(null)
     window.__sessionPanel = { clockRow: panel.clockRow }
     const sourceCase = actual.cases[id]
     const chipInputs = tasks.sessionTaskChips(sourceCase.tasks.map(t => ({ ...t, gateway: t.gateway || 'fixture-gateway', profile: t.profile || 'default' })), { connectionId: route.connectionId, profile: route.profile })
     // Runtime source evidence is not upgraded to running merely because the scenario has that name.
     for (const row of expectedRows.map(e => e.row).filter(r => ['subagent', 'process'].includes(r.kind))) {
       chipInputs.push({ id: row.id, workId: row.id, kind: row.kind === 'process' ? 'bg' : 'sub',
         state: 'unknown', title: row.title, label: row.title, observations: sourceCase.runtime_evidence.filter(o => o.identity?.work_id === row.id) })
     }
     const chips = tasks.withModelDisplay(chipInputs, model, actual.now)
     // Terminal history has a panel row but the active-task strip correctly has no chip.
     assert.equal(chips.length, expectedRows.filter(e => e.section !== 'done').length)
     for (const chip of chips) {
       await paint(React.createElement(tasks.Chip, { chip, now: actual.now * 1000 }))
       const node = document.querySelector('[data-session-task]')
       assert.ok(node)
       if (chip.display) {
         const row = expectedRows.find(e => e.row.id === chip.id).row
         assert.equal(chip.display.detail, panel.clockRow(row, actual.now).detail)
         assert.equal(node.title, `${chip.display.title}\n${chip.display.detail}`)
       }
       const pip = node.querySelector('[data-chip-pip]')
       const eta = node.querySelector('[data-wait-eta]')
       assert.ok(pip || eta)
       if (chip.kind === 'kanban' && pip) {
         const colors = { unknown: '#8b949e', running: 'var(--accent, #3b82f6)', ready: '#8b949e', todo: '#8b949e',
           scheduled: '#e3b341', blocked: '#f85149', triage: '#f85149', 'agent-wait': '#38bdf8',
           waiting: '#8b949e', delivery: '#38bdf8', deploy: '#a371f7', stalled: '#d29922', 'needs-you': '#f85149' }
         const probe = document.createElement('span'); probe.style.background = colors[chip.look || chip.state]
         assert.equal(pip.style.background, probe.style.background)
       }
     }
   } finally { await paint(null); delete window.__sessionPanel; panel.resetFeedForTests() }
 })
 test.after(async () => { Date.now = realNow; await React.act(async () => root.unmount()); dom.window.close() })
}
