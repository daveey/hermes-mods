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
 '@hermes/plugin-sdk': 'const H=globalThis.hubHarness,R=H.React,F=p=>R.createElement(R.Fragment,null,p.children);export const Dialog=F,DialogContent=p=>R.createElement("div",{"data-process-view":p["data-process-view"]},p.children),DialogHeader=F,DialogTitle=p=>R.createElement("h2",null,p.children),DialogDescription=p=>R.createElement("p",null,p.children),host=H.host,Codicon=()=>null,DisclosureCaret=()=>null,COMPOSER_AREAS={top:"composer.top"},useValue=a=>a?.get?.(),ContextMenu=F,ContextMenuTrigger=F,ContextMenuContent=p=>H.menus?R.createElement("div",{"data-row-menu":p["data-row-menu"]},p.children):null,ContextMenuItem=p=>R.createElement("button",{type:"button","data-row-menu-item":p["data-row-menu-item"],disabled:p.disabled,onClick:p.onSelect},p.children)'
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
for (const [name, lead] of [['clock_reason', /waiting on Event watch on service owners/], ['clock_unreasoned', /reason unavailable/]]) {
  test(`clock wait ${name} paints the prerequisite before the timer`, async () => {
    const { model, now } = publicCases[name]
    await paint(m.renderModel(model, { now }))
    const detail = document.querySelector('[data-row-detail]').textContent
    assert.match(detail, lead)
    assert.match(detail, /left \(until 2027-01-15 00:01 PST\)/)
    assert.match(document.querySelector('[data-ask-title]').title, /Next actor: unknown \(the park recorded none\)/)
    assert.doesNotMatch(document.querySelector('[data-ask-title]').title, /Next actor: the clock/, 'an older-core label names no actor')
    assert.match(document.querySelector('[data-ask-title]').title, /Next check: the clock at 2027-01-15 00:01 PST/)
    assert.ok(detail.search(lead) < detail.search(/left \(until/), 'prerequisite precedes the next-check time')
  })
}
test('probe watch renders named actors and a complete separate backstop', async () => {
  const { model, now } = publicCases.clock_probe
  await paint(m.renderModel(model, { now }))
  const detail = document.querySelector('[data-row-detail]').textContent
  assert.match(detail, /waiting on Service rollout/)
  assert.match(detail, /watching 2 cards/)
  assert.match(detail, /left \(backstop 2027-01-15 00:01 PST\)/)
  assert.match(detail, /long-worker.example.com/)
  const hover = document.querySelector('[data-ask-title]').title
  assert.match(hover, /Next actor: cards t_0000000000000001, t_0000000000000002 \(event watch\)/)
  assert.match(hover, /Next check: clock backstop at 2027-01-15 00:01 PST/)
  assert.doesNotMatch(hover, /Next actor: the clock/)
})
test('aged parked board data keeps its status and age in the panel and task chip', async () => {
  const { model, now } = publicCases.aged_parked
  const row = model.sections.waiting[0]
  await paint(m.renderModel(model, { now }))
  const detail = document.querySelector('[data-row-detail]').textContent
  assert.match(detail, /waiting on CI/)
  assert.match(detail, /as of .*panel data 2m old/)
  assert.doesNotMatch(detail, /status unknown/)
  assert.equal(row.evidence_state, 'unknown')
  // e44ba1cb: a delivery wait keeps its wait_kind up to HISTORICAL_DELIVERY_S (360 s); this data is 2m old
  assert.equal(row.wait_kind, 'ci')
  const tasks = await import('../../session-tasks/plugin.js')
  window.__sessionPanel = { clockRow: m.clockRow }
  const [chip] = tasks.withModelDisplay([{ kind: 'kanban', id: row.id, gateway: row.gateway,
    profile: row.profile, board: row.board, state: 'scheduled', observations: [], title: 'Card' }], model, now)
  assert.equal(chip.display.detail, detail)
  await paint(React.createElement(tasks.Chip, { chip, now: now * 1000 }))
  assert.match(document.querySelector('[data-session-task]').title, /panel data 2m old/)
  assert.doesNotMatch(document.querySelector('[data-session-task]').title, /status unknown/)
  delete window.__sessionPanel
})

const cases = { ...publicCases, ...privateCases }
for (const age of [599, 600, 601, 6 * 86400]) {
 test(`real CoreReaders -> reducer -> panel/chip: triage age ${age}`, { skip: !cases[`composed_triage_${age}`] }, async () => {
  const { model, now } = cases[`composed_triage_${age}`]
  const red = age > 600
  assert.equal(model.sections.needs_you.length, 0)
  await paint(m.renderModel(model, { now }))
  const row = model.sections[red ? 'stuck' : 'waiting'][0]
  const pip = document.querySelector(`[data-row-pip="${red ? 'stuck' : 'waiting'}"]`)
  assert.ok(pip)
  if (red) {
   assert.equal(window.getComputedStyle(pip).backgroundColor, 'rgb(248, 81, 73)')
   assert.equal(document.querySelector('[data-session-status]').style.color, 'var(--color-red-500)')
   assert.match(document.querySelector('[data-row-detail]').textContent, /overdue triage/)
   assert.equal(model.header.counts.stuck, 1)
  } else {
   assert.notEqual(window.getComputedStyle(pip).backgroundColor, 'rgb(248, 81, 73)')
   assert.equal(model.header.counts.stuck, 0)
  }
  assert.equal(model.header.status.presentation.stripe, 'waiting', 'stored canonical status remains truthful; backend attention wins')
  assert.equal(document.querySelectorAll('[data-choice-id]').length, 0)
  const tasks = await import('../../session-tasks/plugin.js')
  window.__sessionPanel = { clockRow: m.clockRow }
  const [chip] = tasks.withModelDisplay([{ kind: 'kanban', id: row.id, gateway: row.gateway, profile: row.profile,
    board: row.board, state: 'triage', observations: [], title: 'Owned triage' }], model, now)
  assert.equal(chip.look, red ? 'triage' : 'agent-wait')
  await paint(React.createElement(tasks.Chip, { chip, now: now * 1000 }))
  assert.doesNotMatch(document.body.textContent, /Needs you|Needs your decision/)
  delete window.__sessionPanel
 })
}
for (const age of [601, 6 * 86400]) {
 test(`stale snapshot clears overdue triage in rows, header and pips: triage age ${age}`, { skip: !cases[`composed_triage_${age}`] }, async () => {
  const { model, now } = cases[`composed_triage_${age}`]
  const before = JSON.stringify(model)
  assert.equal(model.header.attention, 'triage_overdue')
  const later = now  // the vouch is withdrawn (stale source), not merely aged
  const effective = m.expireNativeWaits(model, later, false)
  assert.equal(effective.header.attention, undefined)
  assert.doesNotMatch(effective.header.label, /^overdue triage/)
  assert.ok(effective.header.pips.every(p => p.attention === undefined && m.pipCategory(p) !== 'red'))
  assert.ok(Object.values(effective.sections).flat().every(row => row.attention !== 'triage_overdue'))
  await paint(m.renderModel(model, { now: later, vouched: false }))
  for (const box of document.querySelectorAll('[data-row-pip]'))
   assert.notEqual(window.getComputedStyle(box).backgroundColor, 'rgb(248, 81, 73)', 'stale DOM claims no fresh operational red')
  assert.notEqual(document.querySelector('[data-session-status]').style.color, 'var(--color-red-500)')
  // Only explicitly historical text may still mention it ("last known: overdue triage · …").
  assert.doesNotMatch(document.body.textContent.replaceAll('last known: overdue triage · ', ''), /overdue triage/)
  assert.equal(JSON.stringify(model), before)
  // Recovery: a fresh snapshot paints the backend's overdue red again.
  await paint(m.renderModel(model, { now }))
  assert.equal(window.getComputedStyle(document.querySelector('[data-row-pip="stuck"]')).backgroundColor, 'rgb(248, 81, 73)')
 })
}
const groupRow = model => (model.groups || []).find(g => g.kind === 'blocker' && g.status === 'triage')
for (const age of [599, 601]) {
 test(`real CoreReaders -> reducer -> panel: shared-blocker group triage age ${age}`, { skip: !cases[`composed_triage_group_${age}`] }, async () => {
  const { model, now } = cases[`composed_triage_group_${age}`]
  const red = age > 600, group = groupRow(model)
  assert.ok(group, 'the triage parent is a shared-blocker group, not a session row')
  assert.equal(group.attention === 'triage_overdue', red)
  assert.equal(model.header.attention === 'triage_overdue', red)
  await paint(m.renderModel(model, { now }))
  const box = document.querySelector(`[data-tree-node="${group.key}"] [data-row-pip]`)
  assert.equal(window.getComputedStyle(box).backgroundColor === 'rgb(248, 81, 73)', red)
  assert.equal(document.querySelector('[data-session-status]').style.color === 'var(--color-red-500)', red)
  assert.doesNotMatch(document.body.textContent, /Needs you|Needs your decision/)
 })
}
test('stale snapshot clears an overdue shared-blocker group in its box, text and header', { skip: !cases.composed_triage_group_601 }, async () => {
  const { model, now } = cases.composed_triage_group_601
  const before = JSON.stringify(model), key = groupRow(model).key
  const effective = m.expireNativeWaits(model, now, false)
  assert.ok((effective.groups || []).every(g => g.attention !== 'triage_overdue'))
  assert.equal(effective.header.attention, undefined)
  await paint(m.renderModel(model, { now, vouched: false }))
  for (const box of document.querySelectorAll('[data-row-pip]'))
   assert.notEqual(window.getComputedStyle(box).backgroundColor, 'rgb(248, 81, 73)', 'stale group claims no fresh operational red')
  const text = document.querySelector(`[data-tree-node="${key}"]`).textContent
  assert.match(text, /status unknown · last known: overdue triage · /)
  assert.doesNotMatch(document.body.textContent.replaceAll('last known: overdue triage · ', ''), /overdue triage/)
  assert.notEqual(document.querySelector('[data-session-status]').style.color, 'var(--color-red-500)')
  assert.equal(JSON.stringify(model), before)
  // Recovery: a fresh snapshot paints the group's backend red again.
  await paint(m.renderModel(model, { now }))
  assert.equal(window.getComputedStyle(document.querySelector(`[data-tree-node="${key}"] [data-row-pip]`)).backgroundColor, 'rgb(248, 81, 73)')
})
test('an expiring dispatcher diagnostic keeps a fresh group-only overdue header red', { skip: !cases.composed_triage_group_601 }, async () => {
  const { model: source, now } = cases.composed_triage_group_601
  const model = structuredClone(source)
  assert.ok(model.sections.stuck.every(row => row.attention !== 'triage_overdue'), 'only the group carries overdue attention')
  // An unrelated ready row whose current dispatcher reason outlives its 180 s window.
  model.sections.ready.push({ key: 'card:diag', id: 't_diag', kind: 'card', title: 'Diagnostic', detail: 'ready', details: [], actions: [],
    non_running_reason: { freshness: 'current', disposition: 'eligible', actor: 'dispatcher', observed_at: now - 500, valid_until: now + 1000 } })
  model.header.counts.ready = (model.header.counts.ready || 0) + 1
  const expired = m.expireDiagnostics(model, now)
  assert.equal(expired.sections.ready.at(-1).non_running_reason.freshness, 'unknown', 'the diagnostic really expired')
  assert.equal(expired.header.attention, 'triage_overdue')
  assert.match(expired.header.label, /^overdue triage · /)
  await paint(m.renderModel(model, { now }))
  assert.equal(document.querySelector('[data-session-status]').style.color, 'var(--color-red-500)')
  assert.equal(window.getComputedStyle(document.querySelector(`[data-tree-node="${groupRow(model).key}"] [data-row-pip]`)).backgroundColor, 'rgb(248, 81, 73)')
})
for (const diagnostic of [false, true]) for (const collapsed of [false, true]) {
 test(`expiry withdraws header attention and wait metadata diagnostic=${diagnostic} collapsed=${collapsed}`, async () => {
  const model = structuredClone(Object.values(cases).find(c => c.model?.sections?.needs_you?.length).model)
  const row = model.sections.needs_you[0]
  for (const key of Object.keys(model.sections)) model.sections[key] = []
  const section = diagnostic ? 'stuck' : 'needs_you'
  model.sections[section] = [row]
  model.header.counts = Object.fromEntries(Object.keys(model.sections).map(k => [k, k === section ? 1 : 0]))
  model.header.pips = [{ section, count: 1, label: section, wait_kind: 'ci' }]
  model.header.wait_kind = 'ci'; model.header.wait_eta = { wait_kind: 'ci', waited_since: 900, expected_s: 50 }
  model.header.source = 'derived'; model.header.status = null; model.header.state = section; model.header.label = section
  model.freshness = { computed_at: diagnostic ? 1000 : 900, ttl_s: 60, trusted: true }
  row.kind = 'card'; row.actions ||= []; row.decision = null
  if (diagnostic) row.non_running_reason = { freshness: 'current', observed_at: 800, valid_until: 980 }
  else delete row.non_running_reason
  const before = JSON.stringify(model)
  await paint(m.renderModel(model, { now: 1000, collapsed }))
  assert.equal(document.querySelector(`[data-model-count="${section}"]`).textContent, '0')
  assert.ok(document.querySelector(`[data-model-pip="${section}"]`) === null, 'expired attention pip withdrawn')
  const destination = diagnostic ? 'ready' : 'waiting'
  assert.equal(document.querySelector(`[data-model-count="${destination}"]`).textContent, '1')
  const effective = m.expireNativeWaits(m.expireDiagnostics(model, 1000), 1000)
  assert.equal(effective.header.wait_kind, null)
  assert.equal(effective.header.wait_eta, null)
  if (diagnostic) {
   assert.equal(effective.header.state, 'unknown')
   assert.equal(document.querySelector('[data-session-status]').textContent, 'execution reason unknown')
  }
  assert.equal(JSON.stringify(model), before)
  // Source recovery restores the untouched canonical classification.
  if (diagnostic) row.non_running_reason = { freshness: 'current', observed_at: 999, valid_until: 1100 }
  else model.freshness.computed_at = 1000
  await paint(m.renderModel(model, { now: 1000, collapsed }))
  assert.equal(document.querySelector(`[data-model-count="${section}"]`).textContent, '1')
 })
}
test('mounted legacy cached running header fails closed before any heartbeat', async () => {
 await paint(null); m.resetFeedForTests()
 current = { connectionId: 'legacy', profile: 'default', session: 'legacy-chat' }
 const base = Object.values(cases).find(c => c.model?.sections?.running?.length)
 const model = { ...base.model, header: { ...base.model.header, source: 'derived', state: 'running', label: 'running', status: null } }
 delete model.freshness
 window.hermesDesktop = { api: async () => ({ feed: 'legacy-feed', epoch: 'legacy-epoch', rev: 1, bundles: {} }) }
 m.ingestBundle(current.connectionId, current.profile, { session: current.session, model })
 try {
  await paint(J.jsx(m.Panel, {}))
  assert.equal(m.feedIsLive(current), false)
  assert.equal(document.querySelector('[data-session-status]').textContent, 'status unknown (stale)')
  assert.equal(document.querySelector('[data-session-status]').title, 'unknown')
  assert.equal(document.querySelector('[data-panel-status-stripe]').dataset.panelStatusStripe, 'none')
 } finally {
  await paint(null); delete window.hermesDesktop; m.resetFeedForTests()
  current = { connectionId: 'conn', profile: 'default', session: 'chat' }
 }
})
for (const [name, trusted, age, vouched] of [
 ['restored', false, 0, null], ['expired', true, 61, true],
 ['excluded-55s', true, 55, false], ['excluded-30m', true, 1800, false],
 ['unvouched', true, 0, false], ['future', true, -3600, true], ['fresh', true, 0, true]
]) {
 test(`running header ${name} uses source freshness, not transport age`, async () => {
  const base = Object.values(cases).find(c => c.model?.sections?.running?.length)
  assert.ok(base)
  const now = 1795281000
  const model = { ...base.model, header: { ...base.model.header, source: 'derived', state: 'running', label: 'running', status: null },
   freshness: { trusted, computed_at: now - age, ttl_s: 60 } }
  const before = JSON.stringify(model)
  await paint(m.renderModel(model, { now, vouched }))
  const stale = name !== 'fresh'
  const text = document.querySelector('[data-session-status]').textContent
  // Vouch exclusion removes current trust, not historical knowledge of the cached state.
  const ageText = m.formatClockText([{ timestamp: model.freshness.computed_at, format: 'elapsed' }], now)
  assert.equal(text, name === 'future' ? 'status unknown (stale)' : stale ? `running · synced ${ageText} ago` : 'running')
  if (stale) {
   assert.equal(document.querySelector('[data-panel-status-stripe]').dataset.panelStatusStripe, 'none')
   assert.equal(document.querySelector('[data-session-status]').title, 'unknown')
   assert.ok(document.querySelector('[data-panel-status-stripe]').style.background.includes('muted-foreground'))
  } else assert.equal(document.querySelector('[data-session-status]').textContent, 'running')
  if (age > 0) assert.ok(text.includes(`synced ${ageText} ago`))
  assert.equal(JSON.stringify(model), before)
 })
}
for (const [name, expected] of Object.entries({ fresh: 'running', stale55: 'running · synced 55s ago',
 stale30m: 'running · synced 30m ago', future: 'status unknown (stale)', no_state: 'status unknown (stale · 55s old)' })) {
 test(`real Python header clock ${name} renders without renewing evidence`, async () => {
  const { model, now, vouched } = publicCases['header_clock_' + name]
  const before = JSON.stringify(model)
  await paint(m.renderModel(model, { now, vouched }))
  assert.equal(document.querySelector('[data-session-status]').textContent, expected)
  assert.equal(document.querySelector('[data-session-status]').title, name === 'fresh' ? 'running' : 'unknown')
  assert.equal(JSON.stringify(model), before)
 })
}
for (const clock of [null, NaN, Infinity, '1795281000', 0]) {
 test(`invalid header clock ${clock} cannot report a sync`, async () => {
  const { model, now } = publicCases.header_clock_stale55
  await paint(m.renderModel({ ...model, freshness: { ...model.freshness, computed_at: clock } }, { now, vouched: false }))
  assert.equal(document.querySelector('[data-session-status]').textContent, 'status unknown (stale)')
 })
}
for (const name of ['agent_held_local', 'agent_held_replica', 'agent_held_spend', 'agent_held_human']) {
 test(`real reducer ${name}: overdue triage is red without inventing a human actor`, async () => {
  const { model, now } = cases[name]
  await paint(m.renderModel(model, { now }))
  const human = name === 'agent_held_human'
  // The shared >600 s policy lives in core; without it the reducer cannot age, but actor wording still holds.
  const aged = !!publicCases.composed_triage_601
  if (human) {
   assert.equal(model.sections.needs_you.length, 1)
   assert.equal(model.sections.waiting.length, 0)
   assert.equal(window.getComputedStyle(document.querySelector('[data-ask-row]')).borderLeftColor, 'rgb(248, 81, 73)')
  } else {
   assert.equal(model.sections.needs_you.length, 0)
   if (aged) {
    const pip = document.querySelector('[data-row-pip="stuck"]')
    assert.equal(window.getComputedStyle(pip).backgroundColor, 'rgb(248, 81, 73)')
    assert.equal(m.pipCategory(model.header.pips.find(p => p.section === 'stuck')), 'red')
   } else {
    const pip = document.querySelector('[data-row-pip="waiting"]')
    assert.equal(window.getComputedStyle(pip).borderTopColor, 'rgb(59, 130, 246)')
   }
   const detail = document.querySelector('[data-row-detail]')
   assert.ok(detail.textContent.includes("waiting on this chat's agent to decide: "))
   // Elapsed triage age needs core's lifecycle-entry derivation; without it the age is unknown, never fabricated.
   if (aged) assert.ok(detail.textContent.includes('in triage · 5h 20m'))
   else assert.doesNotMatch(detail.textContent, /in triage · \d/)
   assert.equal(detail.style.whiteSpace, 'normal')
   assert.equal(detail.style.overflowWrap, 'anywhere')
   assert.equal(detail.style.overflow, 'visible')
   assert.equal(detail.style.flexBasis, '100%')
   await paint(m.renderModel(model, { now: now + 120 }))
   if (aged) assert.ok(document.querySelector('[data-row-detail]').textContent.includes('in triage · 5h 22m'))
   assert.equal(document.querySelectorAll('[data-choice-id]').length, 0)
  }
 })
}
for (const state of ['dismissed', 'answered', 'withdrawn', 'open']) {
 test(`shared human group canonical ${state} controls red attention`, async () => {
  const { model, now } = cases['shared_human_' + state]
  const before = JSON.stringify(model)
  await paint(m.renderModel(model, { now }))
  assert.equal(document.querySelectorAll('[data-panel-row]').length, Object.values(model.sections).flat().length + model.groups.length)
  assert.equal(JSON.stringify(model), before)
  const group = model.groups[0]
  const node = [...document.querySelectorAll('[data-panel-row]')].find(n => n.dataset.panelRow === group.key)
  assert.ok(node)
  const pip = node.querySelector('[data-row-pip]')
  const style = window.getComputedStyle(pip)
  assert.equal(state === 'open' ? style.backgroundColor : style.borderTopColor, state === 'open' ? 'rgb(248, 81, 73)' : 'rgb(59, 130, 246)')
  if (state !== 'open') {
   assert.equal(document.querySelectorAll('[data-choice-id], [data-choice-letter]').length, 0)
   assert.ok(node.textContent.includes(`request ${state}`))
   assert.ok(node.textContent.includes("waiting on this chat's agent"))
  } else {
   assert.ok(document.querySelectorAll('[data-choice-letter]').length > 0)
   assert.ok(node.textContent.includes('waiting on you to answer the current request'))
  }
 })
}
const tasksEta = await import('../../session-tasks/plugin.js')
test('ETA formatter matches both standalone plugins', () => {
 for (const [seconds, text] of [[30, '30s'], [720, '12m'], [12240, '3.4h'], [50400, '14h'], [172800, '2d'], [129600, '1.5d']]) {
  assert.equal(m.formatWaitDuration(seconds), text)
  assert.equal(tasksEta.formatWaitDuration(seconds), text)
 }
})
for (const kind of ['ci', 'deploy', 'clock']) for (const state of ['normal', 'over', 'unknown']) {
 test(`real reducer ETA ${kind}/${state} keeps its coloured pip, ticks and preserves opening`, async () => {
  const { model, now } = cases[`eta_${kind}_${state}`]
  const row = model.sections.waiting[0]
  const actions = []
  await paint(m.renderModel(model, { now, onAction: (...args) => actions.push(args) }))
  const article = document.querySelector('[data-line-row]')
  const pip = article.querySelector('[data-row-pip="waiting"]')
  assert.ok(pip, 'a delivery wait with an ETA still draws its state box')
  assert.equal(pip.dataset.waitKind, kind)
  assert.equal(window.getComputedStyle(pip).borderTopColor, kind === 'deploy' ? 'rgb(163, 113, 247)' : 'rgb(56, 189, 248)')
  const eta = article.querySelector('[data-wait-eta]')
  assert.equal(eta.dataset.waitEta, kind)
  assert.ok(eta.title.includes('fixture history'))
  assert.equal(eta.querySelector('[data-eta-left]').textContent, {normal:'13m',over:'+16m',unknown:'—'}[state])
  if (state === 'over') assert.equal(eta.querySelector('[data-eta-left]').style.color, 'rgb(240, 136, 62)')
  if (state === 'unknown') assert.ok(eta.textContent.includes('/ ?'))
  const etaColor = state === 'over' ? 'rgb(240, 136, 62)' : kind === 'deploy' ? 'rgb(163, 113, 247)' : 'rgb(56, 189, 248)'
  assert.equal(eta.querySelector('[data-eta-left]').style.color, etaColor)
  eta.dispatchEvent(new window.MouseEvent('dblclick', { bubbles: true }))
  assert.equal(actions[0][1].target.id, row.id)
  await paint(m.renderModel(model, { now: now + 60 }))
  if (state === 'normal') assert.equal(document.querySelector('[data-eta-left]').textContent, '12m')
  // An unread watch (core external_unknown: missed poll, job unread) keeps the producer's colour on the chip.
  for (const wait_state of ['external_unknown', 'external_running']) {
   const wait = {...row.wait_eta, wait_kind:kind, wait_state, waiting_on: kind === 'clock' ? 'the clock until 2026-11-24 14:30 UTC' : `${kind} example`}
   const [unread] = tasksEta.sessionTaskChips([{id:row.id, status:'scheduled', wait}])
   assert.equal(unread.look, kind === 'deploy' ? 'deploy' : 'delivery', wait_state)
   assert.equal(tasksEta.chipColor(unread.look), kind === 'deploy' ? '#a371f7' : '#38bdf8', wait_state)
  }
  const wait = {...row.wait_eta, wait_kind:kind, wait_state:'external_running', waiting_on: kind === 'clock' ? 'the clock until 2026-11-24 14:30 UTC' : `${kind} example`}
  const [chip] = tasksEta.sessionTaskChips([{id:row.id, status:'scheduled', wait}])
  assert.equal(chip.look, kind === 'deploy' ? 'deploy' : 'delivery')
  assert.equal(tasksEta.chipColor(chip.look), kind === 'deploy' ? '#a371f7' : '#38bdf8')
  assert.deepEqual(tasksEta.waitReadout(chip.waitEta, now), m.waitReadout({...row.wait_eta,wait_kind:kind}, now))
  await paint(React.createElement(tasksEta.Chip, { chip, now: now * 1000 }))
  assert.ok(document.querySelector('[data-chip-pip]') === null)
  assert.equal(document.querySelector('[data-wait-eta]').dataset.waitEta, kind)
  assert.equal(document.querySelector('[data-eta-left]').style.color, etaColor)
  await paint(React.createElement(tasksEta.CardRow, {chip, now: now * 1000}))
  assert.equal(document.querySelector('[data-wait-eta]').dataset.waitEta, kind)
  assert.equal(document.querySelector('[data-eta-left]').style.color, etaColor)
 })
}
test('an unread hub-deploy watch (external_unknown) paints purple on the panel row, header, chip and card row', async () => {
  const { model, now, wait } = cases.eta_deploy_unread
  const row = model.sections.waiting[0]
  assert.equal(row.wait_kind, 'deploy')
  assert.equal(model.header.wait_kind, 'deploy')
  await paint(m.renderModel(model, { now }))
  const pip = document.querySelector('[data-row-pip="waiting"]')
  assert.equal(pip.dataset.waitKind, 'deploy')
  assert.equal(window.getComputedStyle(pip).borderTopColor, 'rgb(163, 113, 247)')
  assert.equal(document.querySelector('[data-model-pip="waiting"] [data-card-colour]').dataset.cardColour, 'violet')
  assert.ok(document.querySelector('[data-line-row]').textContent.includes('waiting on hub deploy'))
  const [chip] = tasksEta.sessionTaskChips([{ id: row.id, status: 'scheduled', board: 'default', title: 'deploy wait', wait }])
  assert.equal(chip.look, 'deploy')
  assert.equal(chip.waitingOn, wait.waiting_on, 'the chip keeps the named prerequisite')
  await paint(React.createElement(tasksEta.CardRow, { chip, now: now * 1000 }))
  const card = document.querySelector('[data-session-task="kanban-row"]')
  assert.equal(card.dataset.waitLook, 'deploy')
  assert.equal(card.firstChild.style.color, 'rgb(163, 113, 247)')
  assert.ok(card.textContent.includes(wait.waiting_on))
  // Controls: a down watch keeps the status colour; a CI park with an unread watch stays light blue.
  const [down] = tasksEta.sessionTaskChips([{ id: 't_down', status: 'scheduled', wait: { ...wait, wait_state: 'external_down' } }])
  assert.notEqual(down.look, 'deploy')
  const [ci] = tasksEta.sessionTaskChips([{ id: 't_ci', status: 'scheduled', wait: { ...wait, wait_kind: 'ci', waiting_on: 'ci run 1' } }])
  assert.equal(ci.look, 'delivery')
  assert.equal(tasksEta.chipColor(ci.look), '#38bdf8')
})
test('ordinary waits and stale/blocked chip controls keep pips', async () => {
 const model = structuredClone(cases.eta_ci_normal.model)
 delete model.sections.waiting[0].wait_kind
 await paint(m.renderModel(model))
 assert.ok(document.querySelector('[data-row-pip]'))
 assert.ok(document.querySelector('[data-wait-eta]') === null)
 for (const status of ['blocked','running']) {
  const [chip] = tasksEta.sessionTaskChips([{id:'t_control',status,wait:{wait_state:'external_running',waiting_on:'ci example',waited_since:1,expected_s:50}}])
  await paint(React.createElement(tasksEta.Chip,{chip,now:100000}))
  assert.ok(document.querySelector('[data-chip-pip]'))
  assert.ok(document.querySelector('[data-wait-eta]') === null)
 }
 const [stale] = tasksEta.sessionTaskChips([{id:'t_stale',status:'scheduled',stale:true,wait:{wait_state:'external_running',waiting_on:'the clock until tomorrow',waited_since:1,expected_s:50}}])
 // A stale scheduled card keeps its last-known delivery colour (the panel row's `last_wait_kind`), never its ETA.
 assert.equal(stale.look,'delivery')
 assert.equal(stale.waitEta,null)
 assert.equal(stale.waitingOn,'')
 const [staleBlocked] = tasksEta.sessionTaskChips([{id:'t_stale_b',status:'blocked',stale:true,wait:{wait_state:'external_running',waiting_on:'ci example',waited_since:1,expected_s:50}}])
 assert.equal(staleBlocked.look,null, 'a blocked card keeps its own status colour')
 for (const wait_state of ['human','stalled']) assert.notEqual(tasksEta.waitLook({wait_state,wait_kind:'clock',waiting_on:'the clock until tomorrow'}),'delivery')
})
const producerCases = JSON.parse(execFileSync(process.env.HERMES_MODS_PYTHON || 'python3', [fileURLToPath(new URL('./status-producer-fixtures.py', import.meta.url))], { encoding: 'utf8' }))
test('real core status producer available for composed acceptance', { skip: !process.env.HERMES_CORE_SOURCE }, () => {
 assert.equal(Object.keys(producerCases).length, 12)
})
for (const [name, { model, now, status }] of Object.entries(producerCases)) {
 test(`real refresh_outcome through panel and mounted DOM: ${name}`, async () => {
  await paint(m.renderModel(model, { now }))
  assert.equal(model.header.source, 'canonical')
  assert.deepEqual(model.header.status, status)
  const active = name === 'active_local_turn', recovered = name === 'recovered_running', done = name === 'recovered_done', human = name === 'known_human'
  const header = document.querySelector('[data-session-status]')
  if (done) assert.equal(header, null, 'confirmed zero unfinished work hides the panel')
  else assert.equal(header.textContent, model.header.label || model.header.state)
  assert.equal(status.kind, human ? 'decision' : done ? 'done' : active || recovered ? 'running' : ['LinksUnavailable', 'TimeoutError', 'expired_lease'].includes(name) ? 'unresolved' : 'waiting')
  if (human) assert.ok(document.querySelector('[data-choice-letter]'), 'known current human request retains choices')
  if (!active && !recovered && !done && !human) {
   assert.equal(status.coverage.state, 'unknown')
   assert.ok(!['done', 'waiting', 'attention'].includes(status.presentation.stripe))
   assert.equal(model.sections.running.length, 0)
   assert.equal(model.sections.waiting[0].evidence_state, 'unknown')
   assert.equal(model.sections.needs_you.length, 0)
   assert.equal(document.querySelectorAll('[data-choice-letter]').length, 0)
   assert.ok(!document.querySelector('[data-session-status]').textContent.includes('interrupted'))
  }
  if (active) assert.ok(document.querySelector('[data-session-status]').textContent.includes('chat active · task execution shown below'))
  if (recovered || done) assert.equal(status.coverage.state, 'complete')
 })
}
const partialCases = JSON.parse(execFileSync(process.env.HERMES_MODS_PYTHON || 'python3', [fileURLToPath(new URL('./partial-projection-fixtures.py', import.meta.url))], { encoding: 'utf8' }))
for (const kind of ['ci', 'deploy', 'clock']) {
 test(`held canonical ${kind} stays producer ${kind === 'deploy' ? 'purple' : 'light-blue'}`, async () => {
   const { model, now } = cases['producer_' + kind]
   await paint(m.renderModel(model, { now }))
   assert.equal(window.getComputedStyle(document.querySelector('[data-row-pip]')).borderTopColor,
    kind === 'deploy' ? 'rgb(163, 113, 247)' : 'rgb(56, 189, 248)')
  assert.ok(!document.querySelector('[data-row-detail]').textContent.includes('agent to decide'))
 })
}
for (const [name, expected] of [['source_unavailable', "status unknown · couldn't read linked cards"],
 ['own_turn_unknown_remote', 'chat active · task execution shown below'], ['genuine_interruption', 'interrupted']]) {
 test(`shared status evidence ${name}: canonical reason reaches the actual header and unknown row`, async () => {
  const { model, now } = cases[name]
  await paint(m.renderModel(model, { now }))
  assert.equal(model.header.label, expected)
  assert.equal(document.querySelector('[data-session-status]').textContent, expected)
  assert.equal(document.querySelectorAll('[data-panel-row]').length, 1)
  assert.equal(model.sections.running.length, 0)
  assert.equal(model.sections.waiting[0].evidence_state, 'unknown')
  assert.ok(document.body.textContent.includes('waiting for status from remote'))
  assert.equal(document.querySelectorAll('[data-choice-id]').length, 0)
 })
}
for (const [name, { model, now, status }] of Object.entries(partialCases)) {
 test(`real partial projector ${name}: unknown coverage never becomes empty or consent`, async () => {
  assert.equal(model.header.state, 'unknown')
  assert.equal(model.sections.needs_you.length, 0)
  assert.ok(model.diagnostics.length > 0)
  await paint(m.renderModel(model, { now }))
  const rows = [...document.querySelectorAll('[data-panel-row]')]
  assert.equal(rows.length, status ? 1 : 0)
  if (status) {
   assert.ok(document.body.textContent.includes('Known partial work'))
   const view = model.sections.waiting[0]
   const action = view.actions.find(a => a.kind === 'open_card')
   assert.deepEqual(action.target, { id: 't_aaaaaa1', gateway: 'gateway', profile: 'default', board: 'default' })
  }
  assert.equal(document.querySelectorAll('[data-choice-id]').length, 0)
 })
}
for (const name of ['tree_direct', 'tree_multihop', 'tree_cross_section', 'tree_group', 'tree_single', 'tree_cycle', 'tree_depth']) {
 test(`real reducer ${name}: outline order, guides and exactly-once rows`, async () => {
  const { model, now } = cases[name]
  const entries = Object.values(model.sections).flat()
  const all = [...entries, ...model.groups]
  await paint(m.renderModel(model, { now }))
  const drawn = [...document.querySelectorAll('[data-panel-row]')].map(n => n.dataset.panelRow)
  assert.equal(drawn.length, all.length)
  assert.equal(new Set(drawn).size, all.length)
  for (const row of all) {
   const node = [...document.querySelectorAll('[data-tree-node]')].find(n => n.dataset.treeNode === row.key)
   assert.ok(node)
   const article = [...document.querySelectorAll('[data-panel-row]')].find(n => n.dataset.panelRow === row.key)
   assert.equal(article.querySelector('[data-ask-title]').textContent, row.title)
   const expectedDetail = row.parent ? row.nested_detail ?? row.detail : row.detail
   if (expectedDetail) assert.ok(article.textContent.includes(expectedDetail))
   assert.equal(Number(node.dataset.treeDepth), row.depth)
   if (row.parent) {
    assert.equal(node.parentElement.dataset.treeGuide, '')
    assert.equal(node.parentElement.style.paddingLeft, '15px')
    const guideStyle = window.getComputedStyle(node.parentElement)
    assert.equal(guideStyle.borderLeftWidth, '1px', 'mounted guide must have visible width, not only a data attribute')
    assert.equal(guideStyle.borderLeftStyle, 'solid')
    assert.equal(node.parentElement.parentElement.dataset.treeNode, row.parent)
    assert.ok(drawn.indexOf(row.parent) < drawn.indexOf(row.key))
   }
  }
  assert.equal(document.querySelectorAll('[data-tree-guide]').length, new Set(entries.map(r => r.parent).filter(Boolean)).size)
  if (name === 'tree_group') {
   assert.equal(document.querySelectorAll('[data-open-own-card]').length, 4)
   assert.equal(document.querySelector('[data-model-count="waiting"]').textContent, '3')
   for (const n of document.querySelectorAll('[data-row-detail]')) assert.ok(!n.textContent.includes('waiting on:'))
  }
  if (name === 'tree_cross_section') {
   assert.equal(document.querySelector('[data-model-section="running"] [data-tree-depth="1"] [data-row-pip]').dataset.rowPip, 'waiting')
  }
  await paint(m.renderModel(model, { now, collapsed: true }))
  assert.equal(document.querySelectorAll('[data-panel-row]').length, 0)
 })
}

test('old flat models render without parent fields', async () => {
 const model = structuredClone(cases.tree_direct.model)
 delete model.groups
 for (const row of Object.values(model.sections).flat()) { delete row.parent; delete row.depth; delete row.nested_detail }
 await paint(m.renderModel(model))
 assert.equal(document.querySelectorAll('[data-panel-row]').length, 2)
 assert.equal(document.querySelectorAll('[data-tree-guide]').length, 0)
 assert.ok(document.body.textContent.includes('waiting on:'))
})

test('absolute timestamps render 38m and started 38 min ago at the local clock', async () => {
 const { model, now } = cases.relative_clock
 const before = JSON.stringify(model)
 await paint(m.renderModel(model, { now: now + 120 }))
 assert.equal(document.querySelector('[data-run-meta]')?.textContent, 'worker · example-model · 38m')
 // Line rows carry details[] on hover (title tooltip), not in a Details disclosure.
 assert.ok(document.querySelector('[data-line-row] [data-ask-title]').title.includes('started 38 min ago · last event tool 3 min ago'))
 await paint(m.renderModel(model, { now: now + 180 }))
 assert.equal(document.querySelector('[data-run-meta]')?.textContent, 'worker · example-model · 39m')
 assert.equal(JSON.stringify(model), before, 'local repaint never changes the cached wire model')
})

test('running elapsed text is allowed to wrap instead of overflowing the panel', async () => {
 const {model,now}=cases.elapsed_layout
 await paint(m.renderModel(model,{now}))
 const detail=document.querySelector('[data-run-meta]')
 assert.match(detail.textContent,/ · 7m$/)
 assert.equal(detail.style.whiteSpace,'normal')
 assert.equal(detail.style.maxWidth,'100%')
 assert.equal(detail.style.overflowWrap,'anywhere')
 assert.equal(detail.parentElement.style.flexWrap,'wrap')
})

test('mounted feed timing survives local ticks, replacement, session switch and remount without changing evidence', async () => {
 await paint(null); m.resetFeedForTests()
 const originalNow = Date.now, originalInterval = globalThis.setInterval, originalClear = globalThis.clearInterval
 const hiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden')
 const ticks = new Map(); let next = 0, now = cases.elapsed_layout.now
 Date.now = () => now * 1000
 globalThis.setInterval = (fn, ms) => { const id = ++next; ticks.set(id, {fn, ms}); return id }
 globalThis.clearInterval = id => ticks.delete(id)
 Object.defineProperty(document, 'hidden', {value:false, configurable:true})
 window.hermesDesktop = {api:async () => ({feed:'timing',epoch:'timing',rev:1,bundles:{}})}
 try {
  for (const name of ['elapsed_layout', 'eta_ci_normal', 'eta_deploy_normal', 'eta_clock_normal']) {
   now=cases[name].now
   current = {connectionId:'conn',profile:'default',session:`timing-${name}`}
   const model=structuredClone(cases[name].model), original=JSON.stringify(model)
   await React.act(async () => m.ingestBundle(current.connectionId,current.profile,{session:current.session,model}))
   await paint(J.jsx(m.Panel, {}))
   // Cache and HTTP catch-up alone do not attest a live source. Exercise the
   // real heartbeat path; never bypass freshness just to retain a timing label.
   if (name !== 'elapsed_layout') assert.ok(document.querySelector('[data-wait-eta]') === null)
   await React.act(async () => {
    await m.feedCatchUp({...current,sessions:[current.session]})
    m.onFeedEvent({payload:{feed:'timing',profile:'default',epoch:'timing',rev:1,heartbeat:true,fresh:[current.session]}})
   })
   assert.ok(m.feedVouches(current), `${name}: heartbeat must vouch for focused session`)
   const read = () => document.querySelector(name === 'elapsed_layout' ? '[data-row-detail]' : '[data-wait-eta]')?.textContent
   now=cases[name].now
   await React.act(async () => { for(const t of ticks.values()) if(t.ms===1000)t.fn() })
   const before=read(); assert.ok(before, `${name}: ${document.body.textContent}`)
   now+=60
   await React.act(async () => { for(const t of ticks.values()) if(t.ms===1000)t.fn() })
   assert.notEqual(read(),before)
   const after=read()
   // A replacement is the same wire snapshot, not a preformatted JS imitation.
   await React.act(async () => m.ingestBundle(current.connectionId,current.profile,{session:current.session,model:structuredClone(model)}))
   assert.equal(read(),after)
   await paint(null); await paint(J.jsx(m.Panel, {}))
   assert.equal(read(),after)
   // Local repaint cannot keep an ETA alive after the independent source TTL.
   now+=1
   await React.act(async () => {
    // Renew transport independently. Expired source evidence must stay expired
    // even while the owning Feed continues delivering live heartbeats.
    m.onFeedEvent({payload:{feed:'timing',profile:'default',epoch:'timing',rev:1,heartbeat:true,fresh:[current.session]}})
    for(const t of ticks.values()) if(t.ms===1000)t.fn()
   })
   assert.ok(m.feedVouches(current), `${name}: transport is still fresh at source expiry`)
   assert.deepEqual(model.freshness, {trusted:true,computed_at:cases[name].now,ttl_s:60})
   if (name !== 'elapsed_layout') {
    assert.ok(document.querySelector('[data-wait-eta]') === null)
    assert.match(document.querySelector('[data-row-detail]').textContent, /status unknown · last known:/)
   }
   assert.equal(JSON.stringify(model),original)
   // Missing-heartbeat is a distinct negative control, not the source-TTL proof.
   now+=61
   await React.act(async () => { for(const t of ticks.values()) if(t.ms===1000)t.fn() })
   assert.equal(m.feedVouches(current),false)
   assert.ok(document.querySelector('[data-wait-eta]') === null)
   assert.equal(JSON.stringify(model),original)
   await paint(null)
  }
 } finally {
  await paint(null); m.resetFeedForTests(); Date.now=originalNow
  globalThis.setInterval=originalInterval; globalThis.clearInterval=originalClear
  if (hiddenDescriptor) Object.defineProperty(document, 'hidden', hiddenDescriptor); else delete document.hidden
  delete window.hermesDesktop
  current={connectionId:'conn',profile:'default',session:'chat'}
 }
})

test('clock text boundaries, invalid values and legacy strings stay presentation-only', () => {
 const now = 1800000000
 assert.equal(m.formatClockText([{timestamp: now - 59, format: 'ago'}], now), 'just now')
 assert.equal(m.formatClockText([{timestamp: now - 3600, format: 'elapsed'}], now), '1h')
 assert.equal(m.formatClockText([{timestamp: now + 120, format: 'remaining'}], now), '2m')
 assert.equal(m.formatClockText([{timestamp: 'bad', format: 'ago'}], now), '')
 assert.equal(m.clockRow({detail: 'legacy'}, now).detail, 'legacy')
})
test('panel status pip consumes the published stripe and revision, never row-count authority', async () => {
 const model = cases.canonical_done_with_open_rows.model
 assert.ok(model.header.counts.needs_you > 0, 'contradictory row count is intentional')
 await paint(m.renderModel(model))
 const pip = document.querySelector('[data-panel-status-stripe]')
 assert.equal(pip?.getAttribute('data-panel-status-stripe'), 'done')
 assert.equal(pip?.getAttribute('data-panel-status-revision'), '41')
 assert.equal(pip?.style.background, 'var(--color-emerald-500)')
 assert.ok(document.body.textContent.includes('done'))
})

test('panel legacy done yields explicitly derived idle, not a canonical status', async () => {
 const model = cases.derived_idle_with_done_history.model
 assert.equal(model.header.source, 'derived')
 assert.equal(model.header.status, null)
 await paint(m.renderModel(model))
 assert.equal(document.querySelector('[data-session-status]')?.textContent, 'idle')
 const pip = document.querySelector('[data-panel-status-stripe]')
 assert.equal(pip?.getAttribute('data-panel-status-stripe'), 'attention')
 assert.equal(pip?.style.background, 'var(--color-amber-500)')
 assert.ok(!pip?.hasAttribute('data-panel-status-revision'))
})

test('unreadable panel row inputs keep derived unknown', async () => {
 await paint(m.renderModel(cases.derived_unknown_with_done_history.model))
 assert.equal(document.querySelector('[data-session-status]')?.textContent, 'unknown')
})

test('mixed-version backend cannot publish a legacy done label without status', async () => {
 const model = structuredClone(cases.canonical_done_with_open_rows.model)
 delete model.header.status
 model.header.state = 'done'; model.header.label = 'done'
 await paint(m.renderModel(model))
 assert.equal(document.querySelector('[data-session-status]')?.textContent, 'unknown')
 assert.equal(document.querySelector('[data-panel-status-stripe]')?.getAttribute('data-panel-status-stripe'), 'attention')
 assert.equal(document.querySelector('[data-model-count="needs_you"]')?.textContent, String(model.header.counts.needs_you))
})

test('mounted cold panel shows loading, retries the registered gap and removes the placeholder', async () => {
 await paint(null); m.resetFeedForTests()
 current = { connectionId: 'cold', profile: 'default', session: 'cold-chat' }
 let release, calls = 0
 const model = cases.named_parent.model
 window.hermesDesktop = { api: async req => {
   if (req.body?.subscriber) {
     assert.deepEqual(req.body.sessions, [])
     assert.ok(req.body.visible_session === 'cold-chat' || req.body.visible_session === null)
     return {}
   }
   calls++
   assert.deepEqual(req.body.sessions, ['cold-chat'])
   if (calls === 1) return new Promise(r => { release = r })
   return { feed: 'cold-feed', epoch: 'cold-epoch', rev: 1, bundles: { 'cold-chat': { session: 'cold-chat', model } } }
 } }
 try {
   await paint(J.jsx(m.Panel, {}))
   assert.match(document.querySelector('[role="status"]').textContent, /Loading session status/)
   assert.doesNotMatch(document.body.textContent, /not available yet/)
   await React.act(async () => { release({ feed: 'cold-feed', epoch: 'cold-epoch', rev: 0, bundles: {} }); await new Promise(r => setTimeout(r, 300)) })
   assert.equal(calls, 2)
   assert.ok(document.querySelector('[data-hub-model-pending]') === null, 'model arrival removes loading')
   assert.ok(document.querySelector('[data-session-panel]'))
 } finally {
   await paint(null); delete window.hermesDesktop; m.resetFeedForTests()
   current = { connectionId: 'conn', profile: 'default', session: 'chat' }
 }
})

test('mounted cold panel offers explicit Retry after its bounded attempts, then recovers', async () => {
 await paint(null); m.resetFeedForTests()
 current = { connectionId: 'retry', profile: 'default', session: 'retry-chat' }
 let calls = 0
 window.hermesDesktop = { api: async req => {
   if (req.body?.subscriber) return {} // metadata lease is not a model-read attempt
   if (++calls <= 3) throw Error('timeout')
   return { feed: 'retry-feed', epoch: 'retry-epoch', rev: 1, bundles: { 'retry-chat': { session: 'retry-chat', model: cases.named_parent.model } } }
 } }
 try {
   await paint(J.jsx(m.Panel, {}))
   await React.act(async () => { await new Promise(r => setTimeout(r, 1100)) })
   assert.equal(calls, 3)
   assert.match(document.querySelector('[role="status"]').textContent, /taking longer than usual/)
   assert.doesNotMatch(document.body.textContent, /not available yet/)
   await React.act(async () => document.querySelector('[role="status"] button').click())
   assert.equal(calls, 4)
   assert.ok(document.querySelector('[data-hub-model-pending]') === null, 'manual retry hydrates the panel')
 } finally {
   await paint(null); delete window.hermesDesktop; m.resetFeedForTests()
   current = { connectionId: 'conn', profile: 'default', session: 'chat' }
 }
})

test('mounted focused panel paints after two peek timeouts without a Retry click', async () => {
 await paint(null); m.resetFeedForTests()
 current = { connectionId: 'peek-to', profile: 'default', session: 'peek-chat' }
 const model = cases.named_parent.model
 let peeks = 0, catchUps = 0
 window.hermesDesktop = { api: async req => {
   if (req.body?.subscriber) return {}
   if (req.timeoutMs === 5_000) { peeks++; throw Error('timeout') }
   catchUps++
   if (catchUps <= 2) return { feed: 'peek-feed', epoch: 'peek-epoch', rev: catchUps, bundles: {} }
   return { feed: 'peek-feed', epoch: 'peek-epoch', rev: 3, bundles: { 'peek-chat': { session: 'peek-chat', model } } }
 } }
 try {
   await m.feedCatchUp({ connectionId: 'peek-to', profile: 'default', sessions: ['peek-chat'] })
   assert.equal(catchUps, 1)
   await paint(J.jsx(m.Panel, {}))
   await React.act(async () => { await new Promise(r => setTimeout(r, 1100)) })
   assert.equal(peeks, 2)
   assert.equal(catchUps, 3)
   assert.ok(document.querySelector('[data-session-panel]'), 'catch-up after peek timeouts paints the model')
   assert.ok(document.querySelector('[data-hub-model-pending]') === null)
   assert.equal(document.querySelector('[role="status"] button'), null, 'no Retry click required')
 } finally {
   await paint(null); delete window.hermesDesktop; m.resetFeedForTests()
   current = { connectionId: 'conn', profile: 'default', session: 'chat' }
 }
})

test('mounted focused panel keeps retrying after Retry is shown and cancels on focus change', async () => {
 await paint(null); m.resetFeedForTests()
 current = { connectionId: 'persist', profile: 'default', session: 'first-chat' }
 const model = cases.named_parent.model
 const savedRetry = m.FEED_FOCUS ? [...m.FEED_FOCUS.retryMs] : null
 if (m.FEED_FOCUS) m.FEED_FOCUS.retryMs.splice(0, m.FEED_FOCUS.retryMs.length, 40, 40, 40, 40)
 const reads = {}
 window.hermesDesktop = { api: async req => {
   if (req.body?.subscriber) return {}
   const session = req.body?.sessions?.[0]
   if (session) reads[session] = (reads[session] || 0) + 1
   if (session === 'second-chat' && reads[session] >= 4) {
     return { feed: 'persist-feed', epoch: 'persist-epoch', rev: 4, bundles: { 'second-chat': { session: 'second-chat', model } } }
   }
   throw Error('timeout')
 } }
 try {
   await paint(J.jsx(m.Panel, {}))
   await React.act(async () => { await new Promise(r => setTimeout(r, 1100)) })
   assert.ok((reads['first-chat'] || 0) >= 3, `expected the bounded attempts, got ${reads['first-chat']}`)
   assert.match(document.querySelector('[role="status"]').textContent, /taking longer than usual/)
   assert.ok(document.querySelector('[role="status"] button'), 'Retry stays visible')
   const firstAfterRetry = reads['first-chat']
   current = { ...current, session: 'second-chat' }
   await paint(J.jsx(m.Panel, {}))
   await React.act(async () => { await new Promise(r => setTimeout(r, 200)) })
   assert.equal(reads['first-chat'], firstAfterRetry, 'focus change cancels the first chat loop')
   await React.act(async () => { await new Promise(r => setTimeout(r, 1300)) })
   assert.ok(document.querySelector('[data-session-panel]'), 'later auto-retry paints without a click')
   assert.ok(document.querySelector('[data-hub-model-pending]') === null)
 } finally {
   if (m.FEED_FOCUS && savedRetry) m.FEED_FOCUS.retryMs.splice(0, m.FEED_FOCUS.retryMs.length, ...savedRetry)
   await paint(null); delete window.hermesDesktop; m.resetFeedForTests()
   current = { connectionId: 'conn', profile: 'default', session: 'chat' }
 }
})

test('named dependency paints human title; its card opens from the row menu, not a button', async () => {
 const model = cases.named_parent.model
 await paint(m.renderModel(model))
 const row = document.querySelector('[data-panel-row]')
 assert.ok(row.textContent.includes('waiting on: Publish the release on remote · 2m'))
 assert.ok(![...row.querySelectorAll('button')].some(b => /parent card|dependency/i.test(b.textContent)), 'no Open parent card link')
 const dep = m.rowMenuItems(model.sections.waiting[0]).find(i => i.key === 'dependency').action
 assert.equal(dep.role, 'dependency')
 assert.notEqual(dep.target.id, model.sections.waiting[0].id)
 assert.equal(row.querySelector('[data-row-detail]').textContent, 'waiting on: Publish the release on remote · 2m')
})

test('deep leaf wait paints gateway and reason without dropping simultaneous Running', async () => {
 const model = cases.leaf_wait_with_running.model
 assert.equal(model.header.counts.running, 1)
 assert.equal(model.header.counts.waiting, 1)
 await paint(m.renderModel(model))
 const wait = document.querySelector('[data-model-section="waiting"]')
 assert.match(wait.textContent, /Reconcile cutover/)
 assert.match(wait.textContent, /on worker-one \(worker-one-host\)/)
 assert.match(wait.textContent, /asks origin: Choose which premise to retain/)
 const running = document.querySelector('[data-model-section="running"]')
 assert.ok(running, 'a populated Running section must not disappear beside waiting work')
 assert.match(running.textContent, /Active portable work/)
})

test('a line row opens its own card by double-clicking the state square, with no Open card button', async () => {
 const model = structuredClone(cases.named_parent.model)
 const row = model.sections.waiting[0]
 const own = row.actions.find(a => a.kind === 'open_card' && a.target.id === row.id)
 assert.ok(own, 'fixture row carries its own open_card action')
 const opened = []
 await paint(m.renderModel(model, { onAction: (r, a) => opened.push([r.id, a.target.id]) }))
 const el = document.querySelector('[data-line-row]')
 assert.ok(![...el.querySelectorAll('button')].some(b => b.textContent === 'Open card'))
 assert.ok(![...el.querySelectorAll('button')].some(b => b.textContent === 'Open parent card'))
 const pip = el.querySelector('[data-row-pip][data-open-own-card]')
 await React.act(async () => pip.click())
 assert.deepEqual(opened, [], 'a single click does not open')
 await React.act(async () => pip.dispatchEvent(new window.MouseEvent('dblclick', { bubbles: true })))
 assert.deepEqual(opened, [[row.id, row.id]])
})

for (const [name, fixture] of Object.entries(cases).filter(([name]) => name.startsWith('retiring_'))) {
 test(`retiring delivery is one non-actionable line: ${name}`, async () => {
  await paint(m.renderModel(fixture.model))
  const rows = document.querySelectorAll('[data-panel-row]')
  assert.equal(rows.length, 1)
  assert.match(rows[0].textContent, /Delivering…|Delivery to remote not confirmed \(120 min\)/)
  assert.doesNotMatch(rows[0].textContent, /request cancelled|delivers when this turn ends/i)
  assert.equal(rows[0].querySelectorAll('[data-decision-option]').length, 0)
  assert.equal(fixture.model.sections.needs_you.length, 0)
 })
}

for (const [name, fixture] of Object.entries(publicCases).filter(([name]) => name.startsWith('owner_delivery_'))) {
 test(`retiring answer preserves owner detail beside delivery: ${name}`, async () => {
  const state = name.split('_')[2]
  const baseline = publicCases[`owner_baseline_${state}`].model
  const plain = Object.values(baseline.sections).flat()[0]
  assert.ok(plain.detail, 'control carries its own status')
  await paint(m.renderModel(fixture.model))
  const rows = document.querySelectorAll('[data-panel-row]')
  assert.equal(rows.length, 1)
  assert.equal(rows[0].querySelector('[data-row-detail]')?.textContent, plain.detail)
  assert.equal(rows[0].querySelectorAll('[data-decision-status]').length, 1)
  assert.match(rows[0].textContent, /Delivering…|Delivery to remote not confirmed \(120 min\)/)
  assert.doesNotMatch(rows[0].textContent, /request cancelled/i)
  assert.equal(rows[0].querySelectorAll('[data-decision-option]').length, 0)
  assert.equal(fixture.model.sections.needs_you.length, 0)
 })
}

for (const [name, fixture] of Object.entries(cases).filter(([name]) => !name.startsWith('header_clock_') && !name.startsWith('retiring_') && !name.startsWith('tree_') && !name.startsWith('shared_human_') && !name.startsWith('composed_triage_group_'))) {
 test(`hub model golden: ${name}`, async () => {
  const model = fixture.model || fixture.expected
  assert.ok(model, 'golden contains the P1 output model')
  const before = JSON.stringify(model)
  await paint(m.renderModel(model, { now: fixture.now }))
  assert.equal(document.querySelector('[data-session-status]').textContent, model.header.label || model.header.state)
  assert.equal(document.querySelector('[data-hub-model]').getAttribute('data-hub-model'), model.revision)
  for (const [section, rows] of Object.entries(model.sections)) {
   assert.equal(document.querySelector(`[data-model-count="${section}"]`).textContent, String(model.header.counts[section]))
   const block = document.querySelector(`[data-model-section="${section}"]`)
   assert.equal(!!block, rows.length > 0)
   if (!block) continue
   const articles = [...block.querySelectorAll('[data-panel-row]')]
   assert.equal(articles.length, rows.length)
   for (const [i, raw] of rows.entries()) {
    const row = m.clockRow(raw, fixture.now)
    const article = articles[i]
    assert.equal(article.querySelector('[data-ask-title]').textContent, row.title)
    if (row.detail) assert.ok(article.textContent.includes(row.detail))
    if (row.details.length && section === 'needs_you') assert.deepEqual([...article.querySelectorAll('details > div')].map(e => e.textContent), row.details)
    // Line rows are one line: details[] ride the title tooltip, never a Details disclosure.
    if (section !== 'needs_you') {
     assert.equal(article.querySelector('details'), null)
     assert.equal(article.querySelector('[data-ask-title]').title, [row.title, ...row.details].join('\n'))
    }
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
test('canonical approval plan, limits, permissions, exclusions and outcomes remain accessible before choosing', async () => {
 const model = publicCases.approval_boundaries.model
 await paint(m.renderModel(model, { onChoose: () => {} }))
 const row = document.querySelector('[data-ask-row]')
 assert.ok(row.querySelector('button[data-choice-letter]:not(:disabled)'))
 const details = row.querySelector('details')
 for (const text of ['CANONICAL-PLAN distinct from the card brief', 'LIMIT-4-HOURS', 'permission: granted if you approve', 'EXCLUDED-SPEND', 'Stage A only', 'No execution', 'Only the bounded stage is authorized']) {
  assert.ok(details.textContent.includes(text), `missing consent boundary: ${text}`)
 }
})
// One wait colour category (user report: amber CI row beside a blue waiting square). Real reducer fixtures.
const DELIVERY_RGB = 'rgb(56, 189, 248)', AMBER_RGB = 'rgb(210, 153, 34)'
const waitingSquare = () => document.querySelector('[data-model-pip="waiting"] [data-card-colour]')?.dataset.cardColour
const rowBox = id => window.getComputedStyle(document.querySelector(`[data-panel-row$="${id}"] [data-row-pip]`) ||
  document.querySelector('[data-row-pip="waiting"]')).borderTopColor
test('a CI wait whose cached snapshot expires keeps one category on its row box and waiting square, then recovers', async () => {
 const { model, now } = publicCases.palette_ci_fresh
 const before = JSON.stringify(model)
 assert.equal(model.sections.waiting[0].wait_kind, 'ci')
 await paint(m.renderModel(model, { now }))
 assert.equal(rowBox(), DELIVERY_RGB)
 assert.equal(waitingSquare(), 'blue')
 // The source snapshot is no longer vouched for: historical text, no live producer claim, same colour category.
 await paint(m.renderModel(model, { now, vouched: false }))
 assert.match(document.querySelector('[data-row-detail]').textContent, /^status unknown · last known: waiting on CI/)
 assert.equal(rowBox(), DELIVERY_RGB, 'the row box keeps the known CI category')
 assert.equal(waitingSquare(), 'blue', 'the numbered square agrees with the row box')
 assert.ok(document.querySelector('[data-wait-eta]') === null)
 const effective = m.expireNativeWaits(model, now, false)
 const [row] = effective.sections.waiting, pip = effective.header.pips.find(p => p.section === 'waiting')
 assert.equal(row.wait_kind, null); assert.equal(row.last_wait_kind, 'ci')
 assert.equal(pip.wait_kind, undefined, 'the stale pip carries no live wait kind'); assert.equal(pip.last_wait_kind, 'ci')
 assert.equal(effective.header.wait_kind, null, 'the header dot never vouches for a stale producer')
 assert.equal(m.pipCategory(pip), 'blue')
 assert.equal(JSON.stringify(model), before)
 // Recovery: a fresh snapshot restores the live wait.
 await paint(m.renderModel(model, { now }))
 assert.equal(rowBox(), DELIVERY_RGB); assert.equal(waitingSquare(), 'blue')
})
test('a stale replica CI wait from the reducer paints the same category on row box and waiting square', async () => {
 const { model, now } = publicCases.palette_ci_stale_replica
 const [row] = model.sections.waiting, pip = model.header.pips.find(p => p.section === 'waiting')
 assert.equal(row.wait_kind, undefined); assert.equal(row.last_wait_kind, 'ci')
 assert.equal(pip.wait_kind, undefined); assert.equal(pip.last_wait_kind, 'ci')
 assert.equal(model.header.wait_kind, undefined)
 for (const vouched of [null, false]) {
  await paint(m.renderModel(model, { now, vouched }))
  assert.match(document.querySelector('[data-row-detail]').textContent, /^status unknown · last known: waiting on CI/)
  assert.doesNotMatch(document.querySelector('[data-row-detail]').textContent, /last known: status unknown/, 'one historical prefix')
  assert.match(document.querySelector('[data-row-detail]').textContent, /not synced for 58 min/)
  assert.equal(rowBox(), DELIVERY_RGB)
  assert.equal(waitingSquare(), 'blue')
  assert.ok(document.querySelector('[data-wait-eta]') === null)
 }
})
test('mixed waits: each row box keeps its own category; the square is amber while any row has none', async () => {
 const { model, now } = publicCases.palette_mixed
 await paint(m.renderModel(model, { now }))
 assert.equal(rowBox('t_ci'), DELIVERY_RGB)
 assert.equal(rowBox('t_plain'), AMBER_RGB)
 assert.equal(waitingSquare(), 'amber')
 // Every expired row keeps its category: the square follows the rows, not the source snapshot's pip.
 const fresh = structuredClone(publicCases.palette_ci_fresh.model)
 fresh.sections.waiting.push({ ...fresh.sections.waiting[0], key: `${fresh.sections.waiting[0].key}:agent`, id: 't_agent', wait_kind: 'agent', wait_eta: null })
 fresh.header.counts.waiting = 2; fresh.header.pips.find(p => p.section === 'waiting').count = 2
 const effective = m.expireNativeWaits(fresh, now, false)
 assert.deepEqual(effective.sections.waiting.map(r => [r.wait_kind, r.last_wait_kind]), [[null, 'ci'], [null, 'agent']])
 assert.equal(effective.header.pips.find(p => p.section === 'waiting').last_wait_kind, 'agent')
 assert.equal(m.pipCategory(effective.header.pips.find(p => p.section === 'waiting')), 'blue')
})
for (const name of ['ci', 'spend', 'triage', 'aged_triage']) test(`expired stuck ${name} never inherits a blue waiting category`, { skip: !publicCases[`palette_stuck_${name}`] }, async () => {
 const { model, now } = publicCases[`palette_stuck_${name}`]
 assert.equal(model.sections.stuck.length, 1)
 const before = JSON.stringify(model)
 await paint(m.renderModel(model, { now }))
 assert.ok(document.querySelector('[data-row-pip="stuck"]'))
 const effective = m.expireNativeWaits(model, now, false)
 const [row] = effective.sections.waiting
 assert.equal(row.last_wait_kind, undefined)
 assert.equal(row.wait_kind, null)
 assert.equal(row.attention, undefined)
 await paint(m.renderModel(model, { now, vouched: false }))
 assert.equal(rowBox(), AMBER_RGB)
 assert.equal(waitingSquare(), 'amber')
 assert.match(document.querySelector('[data-row-detail]').textContent, /^status unknown · last known:/)
 assert.equal(document.querySelector('[data-wait-eta]'), null)
 assert.equal(JSON.stringify(model), before)
 await paint(m.renderModel(model, { now }))
 assert.ok(document.querySelector('[data-row-pip="stuck"]'), 'fresh stuck presentation recovers')
})
for (const [name, kind] of [['unknown_ci', 'ci'], ['aged_agent', 'agent'], ['aged_card', 'card']]) test(`withdrawn ${name} keeps blue category without authority`, async () => {
 const { model, now } = publicCases[`palette_${name}`]
 const [row] = model.sections.waiting
 assert.equal(row.wait_kind, undefined); assert.equal(row.last_wait_kind, kind)
 assert.equal(row.evidence_state, 'unknown')
 assert.equal(model.header.wait_kind, undefined)
 await paint(m.renderModel(model, { now }))
 assert.equal(rowBox(), kind === 'agent' ? 'rgb(59, 130, 246)' : DELIVERY_RGB); assert.equal(waitingSquare(), 'blue')
 assert.equal(document.querySelector('[data-wait-eta]'), null)
})
test('header counts/pips remain server values, not recomputed row totals', async () => {
 const model = structuredClone(publicCases.all_sections.model)
 model.header.counts.running = 78; model.header.pips = [{ section: 'running', count: 91, label: 'hub value' }]
 await paint(m.renderModel(model))
 const pip = document.querySelector('[data-model-pip]')
 // The hub count sits inside the numbered square; the section words move to the tooltip.
 assert.equal(pip.textContent, '91')
 assert.equal(pip.querySelector('[data-card-colour]').dataset.cardColour, 'blue')
 assert.equal(pip.title, '91 hub value')
 assert.equal(document.querySelector('[data-model-section="running"] h3').textContent, 'In progress · 78')
})
test('hub renderer is unconditional, registers no settings, and switches cached scopes without raw reads', async () => {
 let page, registered
 const timers = [globalThis.setTimeout, globalThis.setInterval]
 globalThis.setTimeout = () => 0; globalThis.setInterval = () => 0
 const dispose = m.default.register({ register: p => { registered = p }, registerSettingsPage: p => { page = p } })
 ;[globalThis.setTimeout, globalThis.setInterval] = timers
 assert.equal(page, undefined)
 assert.equal(registered.area, 'composer.top')
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
 dispose()
})
test('Open card resolves the supplied owner target rather than the focused connection', async () => {
 const model = structuredClone(publicCases.own_letters.model)
 const row = model.sections.needs_you[0]
 // The ask row's own card (a dependency opens from the right-click menu instead).
 row.actions = [{ kind: 'open_card', label: 'Open original', target: { gateway: 'owner-gateway', profile: 'work', board: 'board-a', id: row.id } }]
 const opened = []
 window.__sessionTasks = {
  connectionForHubGateway: gateway => gateway === 'owner-gateway' ? 'owner-connection' : null,
  openTaskChip: chip => opened.push(chip)
 }
 m.ingestBundle(current.connectionId, current.profile, { session: current.session, model })
 await paint(J.jsx(m.Panel, {}))
 const button = [...document.querySelectorAll('button')].find(b => b.textContent === 'Open original')
 if (button) await React.act(async () => button.click())
 else {
  // A cold, unvouched ask is historical, not an actionable ask. Safe owner
  // navigation remains on its line-row square (the normal non-ask control).
  const pip = document.querySelector('[data-row-pip]')
  assert.ok(pip)
  await React.act(async () => pip.dispatchEvent(new dom.window.MouseEvent('dblclick', { bubbles: true })))
 }
 assert.deepEqual(opened, [{ id: row.id, kind: 'kanban', gatewayId: 'owner-gateway', profile: 'work', board: 'board-a', connectionId: 'owner-connection' }])
 await paint(null)
})
test('unmirrored card choices fail closed: no composer draft and no decision POST', async () => {
 const model = structuredClone(publicCases.own_letters.model)
 const row = model.sections.needs_you[0]
 row.request_id = null; row.actions = row.actions.filter(a => a.kind !== 'decide')
 let invoked = 0
 const before = drafts.length
 await paint(m.renderModel(model, { onChoose: () => invoked++ }))
 for (const option of document.querySelectorAll('[data-choice-letter]')) assert.equal(option.disabled, true)
 assert.ok(document.body.textContent.includes('Answer unavailable until the exact owner ask is mirrored'))
 await assert.rejects(m.chooseModelOption(row, row.options[0], current, { call: async () => { invoked++ } }), /No canonical decision binding/)
 assert.equal(invoked, 0)
 assert.deepEqual(drafts.slice(before), [])
 await assert.rejects(m.chooseModelOption(row, row.options[0], { ...current, session: 'stale-chat' }, { call: async () => { invoked++ } }), /No canonical decision binding/)
 assert.equal(drafts.length, before)
 row.source = 'question'
 await paint(m.renderModel(model, { onChoose: () => invoked++ }))
 assert.ok([...document.querySelectorAll('[data-choice-letter]')].every(o => o.disabled))
 await assert.rejects(m.chooseModelOption(row, row.options[0], current), /No canonical decision binding/)
})
test('partial models invalidate cached scope and render nothing instead of crashing', () => {
 const partial = { version: 1, header: { counts: {} }, sections: { needs_you: [] } }
 assert.equal(m.renderModel(partial), null)
 m.ingestBundle(current.connectionId, current.profile, { session: current.session, model: partial })
 assert.equal(m.cachedModel(current), null)
})
test('request choices use choice_id, verified owner route and exact revision with readback', async () => {
 const q = structuredClone(publicCases.own_letters.model.sections.needs_you[0])
 const posts = [], calls = []
 const action = q.actions.find(a => a.kind === 'decide')
 const request = { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner, scope: { complete: true, markdown: 'Full scope' }, choices: q.options.map(o => ({ id: o.choice_id, label: o.name, effect: 'answer' })) }
 const call = async (path, opts) => {
  calls.push([path, opts])
  if (path.endsWith('/decision-refresh')) return {}
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
test('2xx receipt repaints authoritative panel within 1s without polling', async () => {
 m.resetFeedForTests()
 const q = structuredClone(publicCases.own_letters.model.sections.needs_you[0]); q.request_revision += 21
 const action = q.actions.find(a => a.kind === 'decide'); action.revision = q.request_revision
 const route = { connectionId: 'instant', profile: 'default', session: 'instant-chat' }
 const request = { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner,
   scope: { complete: true, markdown: 'Exact scope' }, choices: [{ id: q.options[0].choice_id, effect: 'answer', label: 'Pick' }] }
 const calls = []
 let feedRev = 1
 window.hermesDesktop = { api: async () => ({ feed: 'instant-feed', epoch: 'instant-epoch', rev: feedRev,
   bundles: feedRev === 1 ? { [route.session]: { session: route.session, model: publicCases.own_letters.model } } : {} }) }
 const target = { ...route, sessions: [route.session] }
 await m.feedCatchUp(target)
 const started = performance.now()
 await m.chooseModelOption(q, q.options[0], route, { call: async (path, opts) => {
   calls.push(path)
   if (path.endsWith('/decision-refresh')) {
     // Another chat advanced the connection cursor after this scoped capture.
     // It must not suppress a newer decision model for this particular session.
     feedRev = 3; await m.feedCatchUp(target)
     return { feed: 'instant-feed', epoch: 'instant-epoch', rev: 2,
       bundle: { session: route.session, model: publicCases.instant_decision.model } }
   }
   if (path.endsWith('/decisions')) return { operation: { operationId: 'instant-op', dispatchState: 'delivered', idempotencyKey: opts.body.idempotencyKey },
     request: { ...request, resolution: { operationId: 'instant-op' } } }
   if (path.includes('/operations')) throw Error('Receipt GET deliberately blocked')
   return { request }
 } })
 // The decision handler no longer awaits canonical refresh. This case checks
 // its eventual per-scope revision fence; the slow-refresh case checks immediacy.
 await new Promise(resolve => setTimeout(resolve, 0))
 await paint(m.renderModel(m.cachedModel(route)))
 assert.ok(performance.now() - started < 1000)
 assert.equal(m.modelDecisionStatus(q).text, 'Decision delivered.')
 assert.equal(m.cachedModel(route).header.counts.needs_you, 0)
 assert.ok(document.querySelector('[data-model-section="needs_you"]') === null, 'answered ask left needs you')
 assert.equal(calls.filter(p => p.endsWith('/decisions')).length, 1)
 assert.equal(calls.filter(p => p.includes('/operations')).length, 0)
 assert.equal(m.ingestBundle(route.connectionId, route.profile, { session: route.session, model: publicCases.own_letters.model },
   { feed: 'instant-feed', epoch: 'instant-epoch', rev: 1 }), false)
 assert.equal(m.cachedModel(route).header.counts.needs_you, 0)
 delete window.hermesDesktop
 m.resetFeedForTests()
})
test('a real decision click emits exactly one monotonic client timing record; a failing timing POST changes nothing', async () => {
 for (const failTiming of [false, true]) {
  m.resetFeedForTests()
  const q = structuredClone(publicCases.own_letters.model.sections.needs_you[0]); q.request_revision += failTiming ? 91 : 81
  const action = q.actions.find(a => a.kind === 'decide'); action.revision = q.request_revision
  const route = { connectionId: 'timing' + failTiming, profile: 'default', session: 'timing-chat' }
  const request = { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner,
    scope: { complete: true, markdown: 'Exact scope' }, choices: [{ id: q.options[0].choice_id, effect: 'answer', label: 'Pick' }] }
  const timings = []
  const before = structuredClone(publicCases.own_letters.model); before.sections.needs_you = [q]
  window.hermesDesktop = { api: async (opts) => {
    const path = opts.path
    if (path.endsWith('/client-timing')) { timings.push(opts); if (failTiming) throw Error('timing hub down'); return { ok: true } }
    // A successful refresh that still contains the ask is not a successful repaint.
    if (path.endsWith('/decision-refresh')) return { feed: 'f', epoch: 'e', rev: 5, bundle: { session: route.session, model: before } }
    if (path.endsWith('/decisions')) return { operation: { operationId: 'timed-op', dispatchState: 'delivered', idempotencyKey: opts.body.idempotencyKey },
      request: { ...request, resolution: { operationId: 'timed-op' } } }
    if (path.includes('/requests/')) return { request }
    return { feed: 'f', epoch: 'e', rev: 5, bundles: {} }
  } }
  await m.feedCatchUp({ ...route, sessions: [route.session] })
  await m.chooseModelOption(q, q.options[0], route)
  assert.equal(timings.length, 0, 'response alone is not a committed render')
  current = route
  await paint(React.createElement(m.Panel))
  assert.equal(timings.length, 0, 'mounted old ask is not completion')
  await paint(null)
  // An empty attention section during a canonical request outage is unknown,
  // not evidence of departure. Keep tracking through recovery and a still-open ask.
  m.ingestBundle(route.connectionId, route.profile, { session: route.session, model: publicCases.derived_unknown_with_done_history.model })
  await paint(React.createElement(m.Panel))
  assert.equal(timings.length, 0, 'unavailable canonical requests cannot complete timing')
  await paint(null)
  m.ingestBundle(route.connectionId, route.profile, { session: route.session, model: before })
  await paint(React.createElement(m.Panel))
  assert.equal(timings.length, 0, 'recovered still-open ask retains timing')
  await paint(null)
  m.ingestBundle(route.connectionId, route.profile, { session: route.session, model: publicCases.instant_decision.model })
  await paint(React.createElement(m.Panel))
  await paint(React.createElement(m.Panel))
  assert.equal(timings.length, 1)
  const t = timings[0].body
  assert.equal(t.operation, 'timed-op')
  assert.equal(t.session, action.owner.storedSessionId)
  assert.equal(timings[0].profile, action.owner.profile)
  let previous = 0
  for (const k of ['posted_ms', 'responded_ms', 'refreshed_ms', 'painted_ms']) {
    assert.ok(Number.isFinite(t[k]) && t[k] >= previous, k); previous = t[k]
  }
  assert.equal(m.modelDecisionStatus(q).text, 'Decision delivered.')
  await paint(null)
  delete window.hermesDesktop
 }
 m.resetFeedForTests()
})
test('recorded receipt leaves NEEDS YOU before a two-second refresh, then records visible timing', async t => {
 m.resetFeedForTests()
 const before = structuredClone(publicCases.own_letters.model)
 const q = before.sections.needs_you[0]; q.request_revision += 301
 const action = q.actions.find(a => a.kind === 'decide'); action.revision = q.request_revision
 const route = { connectionId: 'provisional', profile: 'default', session: 'provisional-chat' }
 const request = { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner,
   scope: { complete: true, markdown: 'Exact scope' }, choices: [{ id: q.options[0].choice_id, effect: 'answer', label: 'Pick' }] }
 const timings = []; let refreshed = false
 window.hermesDesktop = { api: async () => ({ feed: 'f', epoch: 'e', rev: 1, bundles: {} }) }
 m.ingestBundle(route.connectionId, route.profile, { session: route.session, model: before })
 current = route
 await paint(React.createElement(m.Panel))
 // Timer callbacks are not a lower-bound performance clock. Keep transport
 // completion explicit and telemetry on a scoped, fractional-origin clock.
 const realNow = performance.now.bind(performance)
 let now = 1000.25, releaseRefresh
 const refreshGate = new Promise(resolve => { releaseRefresh = resolve })
 t.mock.method(performance, 'now', () => now)
 t.after(async () => {
   releaseRefresh()
   await paint(null); delete window.hermesDesktop; m.resetFeedForTests()
 })
 const clicked = realNow()
 await m.chooseModelOption(q, q.options[0], route, { call: async (path, opts) => {
   if (path.endsWith('/decision-refresh')) {
     await refreshGate; refreshed = true
     return { feed: 'f', epoch: 'e', rev: 2, bundle: { session: route.session, model: publicCases.instant_decision.model } }
   }
   if (path.endsWith('/client-timing')) { timings.push(opts.body); return {} }
   if (path.endsWith('/decisions')) return { operation: { operationId: 'provisional-op', dispatchState: 'recorded', idempotencyKey: opts.body.idempotencyKey }, request: { ...request, resolution: { operationId: 'provisional-op' } } }
   return { request }
 } })
 now += 100
 await paint(React.createElement(m.Panel))
 assert.ok(realNow() - clicked < 1000)
 assert.equal(refreshed, false)
 assert.ok(document.querySelector('[data-model-section="needs_you"]') === null, 'provisional ask departed')
 assert.equal(document.querySelector('[data-model-count="needs_you"]').textContent, '0')
 assert.ok(document.querySelector('[data-model-section="waiting"]'))
 assert.equal(m.cachedModel(route).header.counts.needs_you, 1, 'canonical cache untouched')
 assert.equal(m.decisionPresentation(before, { ...route, connectionId: 'other' }), before)
 assert.equal(timings.length, 0, 'refresh duration not yet known')
 now += 1900
 await React.act(async () => { releaseRefresh() })
 await paint(React.createElement(m.Panel))
 assert.equal(refreshed, true)
 assert.equal(timings.length, 1)
 assert.ok(timings[0].visible_ms < 1000)
 assert.equal(timings[0].visible_ms, 100)
 assert.equal(timings[0].painted_ms, timings[0].visible_ms)
 assert.ok(timings[0].refreshed_ms >= 2000)
 assert.equal(timings[0].refreshed_ms, 2000)
})
test('refresh failure restores the locked ask; failed POST operation never hides it', async () => {
 for (const [index, state] of ['recorded', 'failed', 'missing-key', 'bad-revision'].entries()) {
  m.resetFeedForTests()
  const before = structuredClone(publicCases.own_letters.model)
  const q = before.sections.needs_you[0]; q.request_revision += 401 + index
  const action = q.actions.find(a => a.kind === 'decide'); action.revision = q.request_revision
  const route = { connectionId: 'restore', profile: 'default', session: 'restore-chat' }
  const request = { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner,
    scope: { complete: true, markdown: 'Exact scope' }, choices: [{ id: q.options[0].choice_id, effect: 'answer', label: 'Pick' }] }
  let rejectRefresh
  m.ingestBundle(route.connectionId, route.profile, { session: route.session, model: before })
  await m.chooseModelOption(q, q.options[0], route, { call: async (path, opts) => {
    if (path.endsWith('/decision-refresh')) return new Promise((resolve, reject) => { rejectRefresh = reject })
    if (path.endsWith('/decisions')) return { operation: { operationId: 'restore-' + state,
      dispatchState: ['missing-key', 'bad-revision'].includes(state) ? 'pending' : state,
      ...(state === 'missing-key' ? {} : { idempotencyKey: opts.body.idempotencyKey }) },
      request: { ...request, revision: state === 'bad-revision' ? undefined : request.revision, resolution: { operationId: 'restore-' + state } } }
    return { request }
  } })
  assert.equal(m.decisionPresentation(before, route).header.counts.needs_you, state === 'recorded' ? 0 : 1)
  rejectRefresh(Error('Canonical read unavailable')); await new Promise(resolve => setTimeout(resolve, 0))
  await paint(m.renderModel(m.decisionPresentation(before, route), { decisionStatus: m.modelDecisionStatus, onCheck: () => {} }))
  assert.ok(document.querySelector('[data-model-section="needs_you"]'))
  assert.ok(document.body.textContent.includes('Check status'))
  assert.ok([...document.querySelectorAll('[data-choice-letter]')].every(button => button.disabled))
  assert.equal(m.modelDecisionStatus(q).locked, true)
  await paint(null)
 }
 m.resetFeedForTests()
})
test('delayed refresh retires only its exact receipt across owners, revisions and request IDs', async () => {
 let caseIndex = 0
 for (const variant of ['owner', 'revision', 'request']) for (const outcome of ['failure', 'success', 'stale']) {
  m.resetFeedForTests()
  const before = structuredClone(publicCases.own_letters.model)
  const a = before.sections.needs_you[0]; a.request_revision += 700 + 10 * caseIndex++
  a.actions.find(x => x.kind === 'decide').revision = a.request_revision
  const b = structuredClone(a)
  if (variant === 'owner') b.actions.find(x => x.kind === 'decide').owner.gatewayId += '-other'
  if (variant === 'revision') { b.request_revision++; b.actions.find(x => x.kind === 'decide').revision++ }
  if (variant === 'request') { b.request_id += '-other'; b.actions.find(x => x.kind === 'decide').request_id = b.request_id }
  b.key += '-other'
  before.sections.needs_you = [a, b]; before.header.counts.needs_you = 2
  const route = { connectionId: 'isolation-' + variant + outcome, profile: 'default', session: 'isolation-chat' }
  const pending = []; let posts = 0
  const requestFor = row => ({ requestId: row.request_id, revision: row.request_revision, state: 'open',
    owner: row.actions.find(x => x.kind === 'decide').owner, scope: { complete: true, markdown: 'Exact scope' },
    choices: [{ id: row.options[0].choice_id, effect: 'answer', label: 'Pick' }] })
  const choose = async (row, id) => {
   const request = requestFor(row)
   await m.chooseModelOption(row, row.options[0], route, { call: async (path, opts) => {
    if (path.endsWith('/decision-refresh')) return new Promise((resolve, reject) => pending.push({ resolve, reject }))
    if (path.endsWith('/decisions')) { posts++; return { operation: { operationId: id, dispatchState: 'recorded',
      idempotencyKey: opts.body.idempotencyKey }, request: { ...request, resolution: { operationId: id } } } }
    return { request }
   } })
  }
  m.ingestBundle(route.connectionId, route.profile, { session: route.session, model: before }, { feed: 'f', epoch: 'e', rev: 10 })
  await choose(a, variant + outcome + '-a'); await choose(b, variant + outcome + '-b')
  assert.equal(m.decisionPresentation(before, route).header.counts.needs_you, 0)
  if (outcome === 'failure') pending[0].reject(Error('Only A refresh failed'))
  else pending[0].resolve({ feed: 'f', epoch: 'e', rev: outcome === 'stale' ? 9 : 11,
    bundle: { session: route.session, model: before } })
  await new Promise(resolve => setTimeout(resolve, 0))
  const presented = m.decisionPresentation(m.cachedModel(route), route)
  assert.equal(presented.sections.waiting.filter(row => row.request_id === b.request_id && row.key === b.key).length, 1,
    variant + '/' + outcome + ': B remains provisionally waiting')
  assert.ok(!m.modelDecisionStatus(b).text.includes('Only A'), 'A error never reaches B')
  assert.equal(m.modelDecisionStatus(b).locked, true)
  await paint(m.renderModel(presented, { decisionStatus: m.modelDecisionStatus, onCheck: () => {} }))
  assert.equal(document.querySelectorAll('[data-model-section="waiting"]').length, 1)
  assert.equal(posts, 2, 'no decision repost')
  pending[1].reject(Error('B cleanup')); await new Promise(resolve => setTimeout(resolve, 0))
  await paint(null); window.localStorage.clear()
 }
 m.resetFeedForTests()
})
test('operation failure readback restores a provisional row without posting again', async () => {
 m.resetFeedForTests()
 const before = structuredClone(publicCases.own_letters.model)
 const q = before.sections.needs_you[0]; q.request_revision += 501
 const action = q.actions.find(a => a.kind === 'decide'); action.revision = q.request_revision
 const route = { connectionId: 'operation-failure', profile: 'default', session: 'operation-failure-chat' }
 const request = { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner,
   scope: { complete: true, markdown: 'Exact scope' }, choices: [{ id: q.options[0].choice_id, effect: 'answer', label: 'Pick' }] }
 let posts = 0, refreshes = 0, rejectOriginal, frozen
 const receipt = state => ({ operation: { operationId: 'later-failed-op', dispatchState: state, idempotencyKey: frozen.idempotencyKey },
   request: { ...request, resolution: { operationId: 'later-failed-op' } } })
 const call = async (path, opts) => {
   if (path.endsWith('/decision-refresh')) {
     if (++refreshes === 1) return new Promise((resolve, reject) => { rejectOriginal = reject })
     throw Error('Canonical refresh still unavailable')
   }
   if (path.endsWith('/decisions')) { posts++; frozen = opts.body; return receipt('pending') }
   if (path.includes('/operations/')) return receipt('failed')
   if (path.includes('/operations?')) return { operation: { operationId: 'later-failed-op' } }
   return { request }
 }
 m.ingestBundle(route.connectionId, route.profile, { session: route.session, model: before })
 await m.chooseModelOption(q, q.options[0], route, { call })
 assert.equal(m.decisionPresentation(before, route).header.counts.needs_you, 0)
 await m.checkModelDecision(q, { call })
 assert.equal(m.decisionPresentation(before, route).header.counts.needs_you, 1)
 assert.ok(m.modelDecisionStatus(q).text.includes('failed'))
 assert.equal(posts, 1)
 rejectOriginal(Error('Original refresh unavailable')); await new Promise(resolve => setTimeout(resolve, 0))
 m.resetFeedForTests()
})
test('never-started admission refusal offers explicit same-key retry without unlocking choices', async () => {
 const q = structuredClone(publicCases.own_letters.model.sections.needs_you[0]); q.request_revision += 51
 const action = q.actions.find(a => a.kind === 'decide'); action.revision = q.request_revision
 const request = { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner,
   scope: { complete: true, markdown: 'Frozen scope' }, choices: [{ id: q.options[0].choice_id, effect: 'answer', label: 'Pick' }] }
 const posts = []
 const call = async (path, opts) => {
  if (path.endsWith('/decision-refresh')) return {}
  if (path.endsWith('/decisions')) {
   posts.push(opts.body)
   if (posts.length === 1) throw new Error("Error invoking remote method 'hermes:api': Error: 503: {\"detail\":\"Decision not started: capacity busy\"}")
   return { operation: { operationId: 'retry-op', dispatchState: 'delivered', idempotencyKey: opts.body.idempotencyKey }, request: { ...request, resolution: { operationId: 'retry-op' } } }
  }
  return { request }
 }
 await m.chooseModelOption(q, q.options[0], current, { call })
 assert.equal(m.modelDecisionStatus(q).retry, true)
 await m.chooseModelOption(q, q.options[1], current, { call }); assert.equal(posts.length, 1)
 await paint(m.renderModel({ ...publicCases.own_letters.model, sections: { ...publicCases.own_letters.model.sections, needs_you: [q] } }, { decisionStatus: m.modelDecisionStatus, onCheck: () => {} }))
 assert.ok(document.body.textContent.includes('Retry decision'))
 await m.checkModelDecision(q, { call })
 assert.equal(posts.length, 2); assert.deepEqual(posts[1], posts[0])
 assert.equal(m.modelDecisionStatus(q).text, 'Decision delivered.')
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
test('generic 503 and a busy status GET never authorize a retry POST', async () => {
 const q = structuredClone(publicCases.own_letters.model.sections.needs_you[0]); q.request_revision += 52
 const action = q.actions.find(a => a.kind === 'decide'); action.revision = q.request_revision
 let posts = 0
 const call = async (path, opts) => {
  if (opts.method === 'POST') { posts++; throw Error('503: delivery service unavailable') }
  if (path.includes('/operations?')) throw Error('503: {"detail":"Decision not started: capacity busy"}')
  return { request: { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner, scope: { complete: true, markdown: 'scope' }, choices: [{ id: 'card-0', label: 'Pick', effect: 'answer' }] } }
 }
 await m.chooseModelOption(q, q.options[0], current, { call })
 assert.equal(m.modelDecisionStatus(q).retry, false)
 await m.checkModelDecision(q, { call }); await m.checkModelDecision(q, { call })
 assert.equal(m.modelDecisionStatus(q).retry, false); assert.equal(posts, 1)
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
test('owner lookup retries a restarting gateway, then explains a real outage in plain words without posting', async () => {
 const q = structuredClone(publicCases.own_letters.model.sections.needs_you[0])
 const action = q.actions.find(a => a.kind === 'decide')
 const request = { requestId: q.request_id, revision: q.request_revision, state: 'open', owner: action.owner }
 let fails = 1
 const flaky = async (path, opts) => { if (opts.connectionId !== current.connectionId || fails-- > 0) throw Error("Error invoking remote method 'hermes:api': RemoteBackendDownError: backend is down"); return { request } }
 const got = await m.modelRequestRoute(action, current, flaky, { delays: [0, 1] })
 assert.equal(got.request.requestId, q.request_id); assert.equal(got.route.connectionId, current.connectionId)
 let tries = 0
 const down = async () => { tries++; throw Error("Error invoking remote method 'hermes:api': RemoteBackendDownError: backend is down") }
 await assert.rejects(m.modelRequestRoute(action, current, down, { delays: [0, 1, 1] }), e => {
  assert.match(e.message, /^Couldn't reach the gateway that holds this question \(RemoteBackendDownError: backend is down\)\. Nothing was sent/)
  assert.doesNotMatch(e.message, /exact decision owner/); return true })
 assert.equal(tries, 6) // source + foreign candidate, three passes
 let tries404 = 0
 await assert.rejects(m.modelRequestRoute(action, current, async () => { tries404++; throw Error('404: {"detail":"Unknown stored session on this profile"}') }, { delays: [0, 1, 1] }))
 assert.equal(tries404, 2) // a definitive 4xx is not retried
})
test('bound decision bypasses a two-second background owner read and leaves needs you under one second', async () => {
 m.resetFeedForTests()
 const model = structuredClone(publicCases.bound_source.model)
 const q = model.sections.needs_you[0]; q.request_revision += 1000
 const a = q.actions.find(a => a.kind === 'decide'); a.revision = q.request_revision
 assert.equal(a.source_owner, true)
 assert.equal(a.admission.scope_complete, true)
 const route = { connectionId: 'bound-fast', profile: 'default', session: 'bound-chat' }
 let release, backgroundDone = false
 const background = new Promise(r => { release = () => { backgroundDone = true; r() } })
 const timer = setTimeout(release, 2000)
 const calls = []
 const call = async (path, opts) => {
   calls.push(path)
   if (!path.endsWith('/decisions')) { await background; return { request: { requestId: q.request_id, revision: q.request_revision,
     owner: a.owner, state: 'open', scope: { complete: true, markdown: 'scope' }, choices: a.admission.choices } } }
   return { request: { requestId: q.request_id, revision: q.request_revision, owner: a.owner, resolution: { operationId: 'bound-op' } },
     operation: { operationId: 'bound-op', dispatchState: 'pending', idempotencyKey: opts.body.idempotencyKey } }
 }
 try {
   let submitted
   await paint(m.renderModel(model, { onChoose: (row, option) => { submitted = m.chooseModelOption(row, option, route, { call }) } }))
   const started = performance.now()
   await React.act(async () => { document.querySelector('[data-choice-letter]').click(); await submitted })
   await paint(m.renderModel(m.decisionPresentation(model, route)))
   assert.ok(performance.now() - started < 1000, 'click must not await background owner GET')
   assert.equal(backgroundDone, false)
   assert.ok(document.querySelector('[data-model-section="needs_you"]') === null, 'receipt moved the ask')
   assert.equal(calls.filter(p => p.includes('?session=')).length, 0)
   assert.equal(calls.filter(p => p.endsWith('/decisions')).length, 1)
 } finally { clearTimeout(timer); release(); await paint(null); m.resetFeedForTests() }
})
test('connected foreign install owner uses real gateway-name lookup and verified discovery before POST', async () => {
 window.localStorage.clear()
 const { connectionForHubGateway } = await import('../../session-tasks/plugin.js')
 const q = structuredClone(publicCases.bound_foreign.model.sections.needs_you[0])
 q.request_revision += 1900
 const a = q.actions.find(a => a.kind === 'decide'); a.revision = q.request_revision
 assert.equal(a.source_owner, false)
 assert.ok(a.owner.gatewayId.startsWith('install:'))
 const storage = { getItem: () => JSON.stringify({ foreign: 'remote-gateway-name' }) }
 window.__sessionTasks = { connectionForHubGateway: id => connectionForHubGateway(id, storage) }
 assert.equal(window.__sessionTasks.connectionForHubGateway(a.owner.gatewayId), null)
 const posts = [], reads = []
 try {
   await m.chooseModelOption(q, q.options[0], current, { call: async (path, opts) => {
     if (path.endsWith('/decision-refresh')) return {}
     if (!path.endsWith('/decisions')) {
       reads.push(opts.connectionId)
       return { request: { requestId: q.request_id, owner: opts.connectionId === 'foreign' ? a.owner : { ...a.owner, gatewayId: 'wrong' } } }
     }
     posts.push(opts)
     return { request: { owner: a.owner, requestId: q.request_id, revision: q.request_revision, resolution: { operationId: 'real-foreign-op' } },
       operation: { operationId: 'real-foreign-op', dispatchState: 'delivered', idempotencyKey: opts.body.idempotencyKey } }
   } })
   assert.equal(posts.length, 1, 'connected installation owner must not be reported disconnected')
   assert.equal(posts[0].connectionId, 'foreign')
   assert.equal(posts[0].body.session, a.owner.storedSessionId)
   assert.deepEqual(reads, [current.connectionId, 'foreign'])
 } finally { delete window.__sessionTasks; window.localStorage.clear(); m.resetFeedForTests() }
})
test('partial local identity retains verified discovery on the source connection', async () => {
 window.localStorage.clear()
 const q = structuredClone(publicCases.bound_partial_identity.model.sections.needs_you[0])
 q.request_revision += 1950
 const a = q.actions.find(a => a.kind === 'decide'); a.revision = q.request_revision
 assert.equal(a.source_owner, false, 'missing installation aliases cannot establish source ownership')
 const reads = [], posts = []
 try {
   await m.chooseModelOption(q, q.options[0], current, { call: async (path, opts) => {
     if (path.endsWith('/decision-refresh')) return {}
     if (!path.endsWith('/decisions')) {
       reads.push(opts.connectionId)
       return { request: { requestId: q.request_id, owner: a.owner } }
     }
     posts.push(opts)
     return { request: { owner: a.owner, requestId: q.request_id, revision: q.request_revision, resolution: { operationId: 'partial-op' } },
       operation: { operationId: 'partial-op', dispatchState: 'delivered', idempotencyKey: opts.body.idempotencyKey } }
   } })
   assert.deepEqual(reads, [current.connectionId])
   assert.equal(posts.length, 1)
   assert.equal(posts[0].connectionId, current.connectionId)
 } finally { window.localStorage.clear(); m.resetFeedForTests() }
})
test('canonical click admission preserves foreign routing and rejects incomplete or changed bindings', async () => {
 let index = 0
 for (const variant of ['foreign', 'disconnected', 'scope', 'state', 'resolved', 'choice', 'effect', 'malformed', 'revision', 'server-stale', 'lost-post']) {
   window.localStorage.clear()
   const q = structuredClone(publicCases.bound_foreign.model.sections.needs_you[0])
   q.request_revision += 2000 + index++
   const a = q.actions.find(a => a.kind === 'decide'); a.revision = q.request_revision
   assert.equal(a.source_owner, false)
   if (variant === 'scope') a.admission.scope_complete = false
   if (variant === 'state') a.admission.state = 'withdrawn'
   if (variant === 'resolved') a.admission.resolved = true
   if (variant === 'choice') a.admission.choices = []
   if (variant === 'effect') a.admission.choices[0].effect = 'invented'
   if (variant === 'malformed') a.admission = null
   if (variant === 'revision') a.revision--
   const posts = []
   const call = async (path, opts) => {
     if (path.endsWith('/decision-refresh')) return {}
     if (!path.endsWith('/decisions')) {
       assert.ok(path.includes('?session='), 'only canonical owner discovery may precede POST')
       return { request: { requestId: q.request_id, owner: variant !== 'disconnected' && opts.connectionId === 'foreign' ? a.owner : { ...a.owner, gatewayId: 'wrong' } } }
     }
     posts.push(opts)
     if (variant === 'server-stale') throw Error('409: Request revision changed')
     if (variant === 'lost-post') throw Error('Response lost')
     return { request: { owner: a.owner, requestId: q.request_id, revision: q.request_revision, resolution: { operationId: 'foreign-op' } },
       operation: { operationId: 'foreign-op', dispatchState: 'delivered', idempotencyKey: opts.body.idempotencyKey } }
   }
   if (variant === 'revision') await assert.rejects(m.chooseModelOption(q, q.options[0], current, { call }), /No canonical decision binding/)
   else await m.chooseModelOption(q, q.options[0], current, { call })
   const sent = ['foreign', 'server-stale', 'lost-post'].includes(variant)
   assert.equal(posts.length, sent ? 1 : 0, variant)
   if (sent) {
     assert.equal(posts[0].connectionId, 'foreign')
     assert.equal(posts[0].body.session, a.owner.storedSessionId)
     assert.equal(posts[0].body.expectedRevision, a.revision)
     assert.equal(posts[0].body.revisionText, a.admission.choices[0].label)
     await m.chooseModelOption(q, q.options[1], current, { call })
     assert.equal(posts.length, 1, 'never repost uncertain or stale decision')
   }
 }
 delete window.__sessionTasks; window.localStorage.clear(); m.resetFeedForTests()
})
test.after(async () => { await React.act(async () => root.unmount()); dom.window.close() })

test('right-clicking a row offers Open session, Open card, Open dependency in that order', async () => {
 const model = structuredClone(cases.named_parent.model)
 const row = model.sections.waiting[0]
 row.actions = row.actions.filter(a => a.kind !== 'open_session')
 row.actions.push({ kind: 'open_session', label: 'Open session', target: { session: 'owner-chat', gateway: row.gateway, profile: row.profile } })
 const opened = []
 globalThis.hubHarness.menus = true
 try {
  await paint(m.renderModel(model, { onAction: (r, a) => opened.push([a.kind, a.target.session || a.target.id]), currentSession: 'chat' }))
  const menu = document.querySelector(`[data-row-menu="${row.key}"]`)
  assert.ok(menu, 'the row has a context menu')
  const items = [...menu.querySelectorAll('[data-row-menu-item]')]
  assert.deepEqual(items.map(b => b.textContent), ['Open session', 'Open card', 'Open dependency'])
  assert.ok(items.every(b => !b.disabled))
  for (const b of items) await React.act(async () => b.click())
  const dep = row.actions.find(a => a.role === 'dependency')
  assert.deepEqual(opened, [['open_session', 'owner-chat'], ['open_card', row.id], ['open_card', dep.target.id]])
  // The chat already in focus is not offered again; a row with no dependency greys that entry out.
  row.actions = row.actions.filter(a => a.role !== 'dependency')
  await paint(m.renderModel(model, { onAction: () => {}, currentSession: 'owner-chat' }))
  const again = [...document.querySelector(`[data-row-menu="${row.key}"]`).querySelectorAll('[data-row-menu-item]')]
  assert.deepEqual(again.map(b => [b.textContent, b.disabled]), [['Open session', true], ['Open card', false], ['Open dependency', true]])
 } finally { globalThis.hubHarness.menus = false }
})

test('an older model labelled Open parent card still fills Open dependency', () => {
 const row = { id: 'a', actions: [{ kind: 'open_card', label: 'Open card', target: { id: 'a' } }, { kind: 'open_card', label: 'Open parent card', target: { id: 'p' } }] }
 const items = m.rowMenuItems(row)
 assert.equal(items.find(i => i.key === 'dependency').action.target.id, 'p')
 assert.equal(items.find(i => i.key === 'card').action.target.id, 'a')
 assert.equal(items.find(i => i.key === 'session').action, null)
})

test('process row menu dispatches exact owner without inline navigation', async () => {
 const model = structuredClone(cases.named_parent.model)
 const row = model.sections.waiting[0]
 const target = {id: 'proc_owned', session: 'owner-chat', gateway: 'owner', profile: 'work'}
 row.actions = [{kind: 'open_process', label: 'Open process', target}]
 const opened = []
 globalThis.hubHarness.menus = true
 try {
  await paint(m.renderModel(model, {onAction: (r, a) => opened.push(a), currentSession: 'chat'}))
  const menu = document.querySelector(`[data-row-menu="${row.key}"]`)
  const item = [...menu.querySelectorAll('button')].find(b => b.textContent === 'Open process')
  assert.ok(item && !item.disabled)
  assert.equal(document.querySelector('a[href*="proc_owned"]'), null)
  await React.act(async () => item.click())
  assert.deepEqual(opened, [{kind: 'open_process', label: 'Open process', target}])
 } finally { globalThis.hubHarness.menus = false }
})

test('mounted Panel opens the process owner and cancels its dialog on session navigation', async () => {
 await paint(null); m.resetFeedForTests()
 const model = structuredClone(cases.named_parent.model)
 const row = model.sections.waiting[0]
 const target = {id: 'proc_owned', session: 'owner-chat', gateway: 'owner', profile: 'work'}
 row.actions = [{kind: 'open_process', target}]
 const calls = []
 const before = {...current}
 globalThis.hubHarness.menus = true
 host.state.gateways = atom(() => [{id: 'owner-connection', kind: 'remote'}])
 window.__sessionTasks = {connectionForHubGateway: g => g === 'owner' ? 'owner-connection' : null}
 host.requestProfile = async (...args) => {
  calls.push(args)
  return args[1] === 'session.resume' ? {stored_session_id: 'owner-chat', session_id: 'owner-runtime'} :
   {processes: [{session_id: 'proc_owned', command: 'sleep 600', status: 'running', output_tail: 'owned output'}]}
 }
 try {
  m.ingestBundle(current.connectionId, current.profile, {session: current.session, model})
  await paint(J.jsx(m.Panel, {}))
  const open = [...document.querySelectorAll('[data-row-menu-item]')].find(b => b.textContent === 'Open process')
  assert.ok(open)
  await React.act(async () => open.click())
  assert.match(document.querySelector('[data-process-view]').textContent, /sleep 600.*running.*owned output/)
  assert.equal(calls.length, 2)
  for (const [route] of calls) assert.deepEqual(route, {connectionId: 'owner-connection', mode: 'remote', profile: 'work', targetProfile: 'work'})
  assert.equal(calls[1][2].session_id, 'owner-runtime')
  current = {...current, session: 'other-chat'}
  await paint(J.jsx(m.Panel, {}))
  assert.equal(document.querySelector('[data-process-view]'), null)
  current = before
  await paint(J.jsx(m.Panel, {}))
  assert.equal(document.querySelector('[data-process-view]'), null, 'returning does not reopen a retired view')
 } finally {
  await paint(null); current = before; m.resetFeedForTests()
  delete host.state.gateways; delete host.requestProfile; delete window.__sessionTasks
  globalThis.hubHarness.menus = false
 }
})

test('Open session routes to the card owner gateway via host.openSession', async () => {
 const calls = []
 host.openSession = async (id, opts) => { calls.push([id, opts]) }
 window.__sessionTasks = { connectionForHubGateway: g => g === 'hub' ? 'conn-hub' : null }
 try {
  assert.equal(await m.openSessionAction({ kind: 'open_session', target: { session: 's1', gateway: 'hub', profile: 'default' } }, current), true)
  assert.deepEqual(calls, [['s1', { route: { connectionId: 'conn-hub', mode: 'remote', profile: 'default', targetProfile: 'default' } }]])
  assert.equal(await m.openSessionAction({ kind: 'open_session', target: { session: 's2', gateway: 'gone' } }, current), false)
  assert.equal(calls.length, 1, 'an unconnected gateway opens nothing')
 } finally { delete host.openSession; delete window.__sessionTasks }
})
