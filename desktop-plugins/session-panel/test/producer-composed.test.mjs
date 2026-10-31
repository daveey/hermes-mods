// End-to-end producer regression: real plugins, real producers, real-shaped backend rows.
//
// Live packaged QA (Direction B, mods a21b2ecc) showed card t_01a18c6adae1fae9 twice: once as the session panel's
// unasked row and again under the strip's "Earlier asks". session-tasks qualified its chip with the owner read route
// (connectionId + profile), but blocked-panel's snapshot item carried only connectionId/board/id: core's
// `/api/session-kanban-tasks` rows have no profile/gateway/connectionId, and blocked-panel never qualified them, so
// user-steps' strict sameCard (correctly) refused to match. The older composed test hand-built items that already
// had profile, so it never exercised this producer. Here nothing is hand-qualified: session-tasks, blocked-panel,
// user-steps and session-panel all register and refresh against one fake bridge that answers with core's row shapes.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve } from 'node:path'
import { homedir } from 'node:os'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.test', pretendToBeVisual: true }) // visible: session-tasks skips hidden docs
globalThis.window = dom.window; globalThis.document = dom.window.document
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const atom = value => ({ value, get() { return this.value } })
// The focused chat is owned by connection C1, profile default (live: hermes-hub/default). C2 is another registered
// gateway whose board happens to hold a card with the same id.
const host = {
  activeConnectionId: () => 'C1',
  connections: async () => [{ id: 'C1' }, { id: 'C2' }],
  request: async method => (method === 'process.list' ? { processes: [] } : { subagents: [] }),
  state: {
    focusedSessionId: atom('R1'), focusedStoredSessionId: atom('S1'), busy: atom(false), connectionId: atom('C1'),
    profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' })
  },
  composer: { setDraft: async () => true, focus() {}, submit: () => true }, notify: () => {}
}
globalThis.producerHarness = { React, J, host }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={
  'react':'const R=globalThis.producerHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef',
  'react/jsx-runtime':'const J=globalThis.producerHarness.J;export const jsx=J.jsx,jsxs=J.jsxs',
  '@hermes/plugin-sdk':'export const host=globalThis.producerHarness.host,COMPOSER_AREAS={top:"composer.top"},TRANSCRIPT_DIRECTIVE_AREA="transcript.directives",useValue=a=>a?.get?.(),MessageTextContent=({text})=>text'
};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const tasksPlugin = await import('../../session-tasks/plugin.js')
const blocked = await import('../../blocked-panel/plugin.js')
const steps = await import('../../user-steps/plugin.js')
const panel = await import('../plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))

const ID = 't_01a18c6adae1fae9'
const TITLE = 'Approve rental spend cap for the study'
const S = Math.floor(Date.now() / 1000)
const ask = { action: 'Reply in this chat with a spend cap.', why_human: 'Spend needs the owner.', interaction: 'reply', scope: 'Rented GPU spend only', evidence: 'Budget review lists the missing approval.', choices: [{ label: '$200 cap', value: '200' }, { label: 'No rentals', value: '0' }] }
// Exactly core's `_read_session_tasks` columns (hermes_cli/kanban_session_activity.py): no profile, gateway or
// connectionId. `human_request` is the raw TEXT column; blocked-panel reads the parsed one from the detail.
const coreRow = (extra = {}) => ({
  id: ID, title: TITLE, status: 'blocked', assignee: 'default', session_id: 'S1', created_at: S - 3600, started_at: null,
  block_kind: 'needs_input', human_request: JSON.stringify(ask), current_run_started_at: null, via: 'created',
  activity_kind: 'blocked', activity_at: S - 600, note: null, pr_url: null, board: 'default', key: `default/${ID}`, ...extra
})
const human = { wait_state: 'human', waiting_on: 'you', waiting_since: S - 900 }
// Plugin kanban `_task_dict` detail: dataclass fields, still no profile/gateway.
const detail = row => ({ task: { id: row.id, title: row.title, status: 'blocked', assignee: 'default', board: row.board, block_kind: 'needs_input', human_request: ask }, events: [], comments: [] })
const STEP = {
  id: 'us_cd30db5858', text: 'Choose a spending cap', kind: 'choose', options: ['$200 cap', 'No rentals'], status: 'open', created: S - 3000, session: 'S1',
  requestId: '0dd02bb1-bcb2-434b-acb2-ab36816ce2a5', requestOwner: { gatewayId: 'install:ef08', profile: 'default', storedSessionId: 'S1' }, requestRevision: 1
}
// Separate canonical request: its only alias is the user_step, never the Kanban card.
const REQUEST = {
  schemaVersion: 2, requestId: STEP.requestId, revision: 1, kind: 'choice', title: 'Choose a spending cap', state: 'open',
  owner: { gatewayId: 'install:ef08', profile: 'default', storedSessionId: 'S1' },
  scope: { markdown: 'Pick a cap.', complete: true, sourceRefs: [{ gatewayId: 'install:ef08', profile: 'default', type: 'user_step', id: STEP.id }] },
  aliases: [{ gatewayId: 'install:ef08', profile: 'default', type: 'user_step', id: STEP.id }],
  limits: [], exclusions: [], outcomes: { answer: 'Send this answer to the agent.' },
  choices: [{ id: 'answer-0', label: '$200 cap', effect: 'answer' }, { id: 'answer-1', label: 'No rentals', effect: 'answer' }]
}

function wire(byConnection) {
  const calls = []
  window.localStorage.clear()
  window.hermesDesktop = {
    api: async req => {
      calls.push(req)
      const { path, connectionId } = req
      if (path.startsWith('/api/sessions/')) return { id: 'S1', parent_session_id: null }
      if (path.startsWith('/api/session-kanban-tasks')) return { tasks: (byConnection[connectionId] || []).map(r => ({ ...r })) }
      if (path.startsWith('/api/plugins/hub-sync/')) throw new Error('hub-sync not installed')
      if (path.startsWith('/api/plugins/kanban/tasks/')) {
        const id = decodeURIComponent(path.split('/')[5].split('?')[0]), board = new URLSearchParams(path.split('?')[1] || '').get('board') || 'default'
        const row = (byConnection[connectionId] || []).find(r => r.id === id && r.board === board)
        if (!row) throw new Error('404')
        return detail(row)
      }
      if (path.startsWith('/api/plugins/blocked-panel/ping')) return { ok: false }
      if (path.startsWith('/api/plugins/compute-rentals/')) return { machines: [], gateway_id: 'install:ef08' }
      if (path.includes('/session-cards')) return { tasks: [], includes: ['done'] }
      if (path.includes('/requests')) return { requests: [REQUEST] }
      if (path.includes('/steps')) return { steps: [STEP], card: null, own_cards: [] }
      if (path.includes('/ping')) return { ok: true }
      return {}
    }
  }
  const disposers = [tasksPlugin, blocked, steps, panel].map(p => p.default.register({ register() {} }))
  return { calls, dispose: () => disposers.reverse().forEach(d => d?.()) }
}

async function render(key) {
  await React.act(async () => { await window.__sessionTasks.refresh(); await blocked.refresh(true); await flush() })
  await React.act(async () => { root.render(React.createElement(panel.Panel, { key })); await flush() })
  await React.act(async () => { await steps.refresh(); await steps.refreshRequests(); await flush(); await flush() })
}

const panelRows = id => [...el().querySelectorAll('[data-panel-row]')].filter(r => r.getAttribute('data-panel-row').endsWith(`:${id}`))
const stripCards = id => [...el().querySelectorAll(`[data-user-steps-card="${id}"]`)]

test('producer: real missing-profile rows qualify from the owner route; the same card renders once', async () => {
  const { calls, dispose } = wire({
    C1: [
      coreRow({ wait: human }), // the live card: needs you, unasked
      coreRow({ board: 'ops', key: `ops/${ID}` }) // same id, another board on the owner gateway
    ],
    C2: [coreRow()] // same id + board on another registered gateway
  })
  try {
    await render('producer')
    // Rendering: the exact same qualified card is one panel row and is not repeated under "Earlier asks".
    assert.equal(panelRows(ID).filter(r => r.getAttribute('data-panel-row') === `kb:C1:default:default:${ID}`).length, 1, el().innerHTML)
    const repeats = stripCards(ID)
    // Remaining strip cards are exactly the two collisions: (C1, default, ops) and (C2, unqualified).
    assert.equal(repeats.length, 2, 'only the exact (C1, default, default) card is deduplicated')

    // Producer evidence: the owner read is scoped to the owner's profile, other gateways are not.
    const lists = calls.filter(c => c.path.startsWith('/api/session-kanban-tasks'))
    assert.equal(lists.filter(c => c.connectionId === 'C1').at(-1).profile, 'default')
    assert.equal(lists.filter(c => c.connectionId === 'C2').at(-1).profile, undefined)
    const items = window.__blockedPanel.snapshot.items
    const own = items.find(i => i.connectionId === 'C1' && i.board === 'default')
    assert.equal(own.profile, 'default', 'owner-route row is qualified with the read route profile')
    assert.equal(items.find(i => i.connectionId === 'C1' && i.board === 'ops').profile, 'default')
    assert.equal(items.find(i => i.connectionId === 'C2').profile, undefined, 'a non-owner gateway read is never given a guessed profile')
    // user-steps' snapshot is the blocked-panel snapshot, unchanged.
    assert.deepEqual(steps.getSnapshot().kanban.map(i => [i.connectionId, i.board, i.profile]).sort(), items.map(i => [i.connectionId, i.board, i.profile]).sort())
    // The separate unaliased canonical request stays its own tile.
    const tiles = el().querySelectorAll('[data-action-request]')
    assert.equal(tiles.length, 1)
    assert.match(tiles[0].getAttribute('data-action-request'), /0dd02bb1-bcb2-434b-acb2-ab36816ce2a5/)
    // Unknown legacy cards are references only: no approval/decision buttons, just "Open card".
    for (const card of repeats) {
      assert.match(card.textContent, /Legacy reference — not an approval request\./)
      assert.deepEqual([...card.querySelectorAll('button')].map(b => b.textContent), ['Open card'])
    }
  } finally { dispose() }
})

test('producer: a hide chip differing only in gateway, connection, profile or board never hides the real item', async () => {
  const { dispose } = wire({ C1: [coreRow({ wait: human })] })
  try {
    await render('collisions')
    const chip = window.__sessionTasks.snapshot.chips.find(c => c.id === ID)
    const item = window.__blockedPanel.snapshot.items.find(i => i.id === ID)
    assert.ok(chip && item, JSON.stringify({ chips: window.__sessionTasks.snapshot, items: window.__blockedPanel.snapshot }))
    assert.equal(steps.sameCard(chip, item), true, 'real chip and real item are the same qualified card')
    for (const variant of [{ connectionId: 'C2' }, { profile: 'other' }, { board: 'ops' }, { connectionId: '', gateway: 'G2', gatewayId: 'G2' }]) {
      const other = { ...chip, ...variant }
      assert.equal(steps.sameCard(other, item), false, JSON.stringify(variant))
      await React.act(async () => { root.render(React.createElement(steps.Strip, { hideCards: [other] })); await flush() })
      assert.equal(stripCards(ID).length, 1, `collision ${JSON.stringify(variant)} must not dedup`)
    }
    await React.act(async () => { root.render(React.createElement(steps.Strip, { hideCards: [chip] })); await flush() })
    assert.equal(stripCards(ID).length, 0, 'the exact chip hides it')
  } finally { dispose() }
})

test('producer: an owner without a profile-qualified route leaves items unqualified (fail open, never merged)', async () => {
  host.state.focusedSessionOwner.value = { connectionId: null, profile: 'default' }
  const { dispose } = wire({ C1: [coreRow({ wait: human })] })
  try {
    await React.act(async () => { await blocked.refresh(true); await flush() })
    for (const i of window.__blockedPanel.snapshot.items) assert.equal(i.profile, undefined)
  } finally { dispose(); host.state.focusedSessionOwner.value = { connectionId: 'C1', profile: 'default' } }
})

test('producer: explicit backend ownership is never overwritten by the read route', () => {
  const route = { connectionId: 'C1', profile: 'default' }
  const q = blocked.withOwnerRoute
  assert.deepEqual(q({ id: 'a' }, route, 'C1'), { id: 'a', connectionId: 'C1', profile: 'default' })
  assert.equal(q({ id: 'a', profile: 'work' }, route, 'C1').profile, 'work')
  assert.equal(q({ id: 'a', profile: null }, route, 'C1').profile, null, 'explicitly unknown stays unknown')
  assert.equal(q({ id: 'a', ownership_state: 'unknown' }, route, 'C1').profile, undefined)
  assert.equal(q({ id: 'a', gateway: 'G2' }, route, 'C1').profile, undefined, 'replica rows of other gateways keep their own owner')
  assert.equal(q({ id: 'a' }, route, 'C2').profile, undefined, 'another connection is not the owner route')
  assert.equal(q({ id: 'a' }, { connectionId: 'C1', profile: '' }, 'C1').profile, undefined)
})

test('done', () => { root.unmount() })
