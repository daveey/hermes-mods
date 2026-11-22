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
test('absolute timestamps render 38m and started 38 min ago at the local clock', async () => {
 const { model, now } = cases.relative_clock
 const before = JSON.stringify(model)
 await paint(m.renderModel(model, { now: now + 120 }))
 assert.equal(document.querySelector('[data-run-meta]')?.textContent, 'worker · example-model · 38m')
 assert.ok(document.body.textContent.includes('started 38 min ago · last event tool 3 min ago'))
 await paint(m.renderModel(model, { now: now + 180 }))
 assert.equal(document.querySelector('[data-run-meta]')?.textContent, 'worker · example-model · 39m')
 assert.equal(JSON.stringify(model), before, 'local repaint never changes the cached wire model')
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
 window.hermesDesktop = { api: async () => {
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

test('named dependency paints human title and a parent-card action', async () => {
 const model = cases.named_parent.model
 await paint(m.renderModel(model))
 const row = document.querySelector('[data-panel-row]')
 assert.ok(row.textContent.includes('waiting on: Publish the release on remote · 2m'))
 assert.ok([...row.querySelectorAll('button')].some(b => b.textContent === 'Open parent card'))
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
 assert.ok([...el.querySelectorAll('button')].some(b => b.textContent === 'Open parent card'))
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

for (const [name, fixture] of Object.entries(cases).filter(([name]) => !name.startsWith('retiring_'))) {
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
test('canonical approval plan, limits, permissions, exclusions and outcomes remain accessible before choosing', async () => {
 const model = publicCases.approval_boundaries.model
 await paint(m.renderModel(model, { onChoose: () => {} }))
 const row = document.querySelector('[data-ask-row]')
 assert.ok(row.querySelector('button[data-choice-letter]:not(:disabled)'))
 const details = row.querySelector('details')
 for (const text of ['CANONICAL-PLAN distinct from the card brief', 'LIMIT-4-HOURS', 'permissionRequired: true', 'EXCLUDED-SPEND', 'Stage A only', 'No execution', 'Only the bounded stage is authorized']) {
  assert.ok(details.textContent.includes(text), `missing consent boundary: ${text}`)
 }
})
test('header counts/pips remain server values, not recomputed row totals', async () => {
 const model = structuredClone(publicCases.all_sections.model)
 model.header.counts.running = 78; model.header.pips = [{ section: 'running', count: 91, label: 'hub value' }]
 await paint(m.renderModel(model))
 assert.equal(document.querySelector('[data-model-pip]').textContent, '91 hub value')
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
 row.actions = [{ kind: 'open_card', label: 'Open original', target: { gateway: 'owner-gateway', profile: 'work', board: 'board-a', id: 't_example' } }]
 const opened = []
 window.__sessionTasks = {
  connectionForHubGateway: gateway => gateway === 'owner-gateway' ? 'owner-connection' : null,
  openTaskChip: chip => opened.push(chip)
 }
 m.ingestBundle(current.connectionId, current.profile, { session: current.session, model })
 await paint(J.jsx(m.Panel, {}))
 const button = [...document.querySelectorAll('button')].find(b => b.textContent === 'Open original')
 assert.ok(button)
 await React.act(async () => button.click())
 assert.deepEqual(opened, [{ id: 't_example', kind: 'kanban', gatewayId: 'owner-gateway', profile: 'work', board: 'board-a', connectionId: 'owner-connection' }])
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
test.after(async () => { await React.act(async () => root.unmount()); dom.window.close() })
