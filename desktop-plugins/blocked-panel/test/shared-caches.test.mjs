// node --test: blocked-panel takes the focused chat's lineage, the owner gateway's card list and pushed card details
// from session-tasks / session-panel while they are current, with no requests of its own for them, and reads for
// itself when that data is missing or not current, and on a forced refresh.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,const s=()=>globalThis.__bp;export const COMPOSER_AREAS={top:"composer.top"};export const host={activeConnectionId:()=>"local",connections:async()=>[{id:"local"}],state:{focusedStoredSessionId:{get:()=>s().stored},focusedSessionOwner:{get:()=>({connectionId:"local",profile:"default"})},profile:{get:()=>"default"}}};export const connectionForHubGateway=()=>undefined;export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
globalThis.__bp = { stored: 's1' }
const m = await import('../plugin.js')

const ask = { action: 'Pick a deploy window.', why_human: 'Owner scheduling decision.', interaction: 'reply', scope: 'Reviewed deployment.', evidence: 'No window selected.' }
const row = (id, status = 'blocked') => ({ id, title: `Card ${id}`, status, board: 'default', assignee: 'ops-agent', session_id: globalThis.__bp.stored })
const slim = (id, extra = {}) => ({ task: { id, title: `Card ${id}`, status: 'blocked', assignee: 'ops-agent', board: 'default', block_kind: 'needs_input', human_request: ask, body: 'card body', ...extra }, events: [], links: { parents: [], children: [] }, link_tasks: [], slim: true })
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setTimeout(r, 0)) }

function wire({ lineage, rows, card, vouched = true, trusted = true, detailAt = Date.now(), rowsAt = Date.now(), rowsFeed = false }) {
  const calls = [], spCalls = []
  globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} }
  globalThis.window = {
    localStorage: { getItem: () => null },
    __sessionTasks: { lineageIds: async (stored, conn, profile) => (calls.push({ shared: 'lineage', stored, conn, profile }), lineage), kanbanRows: (route, ids) => (calls.push({ shared: 'rows', route, ids: [...ids] }), rows ? { rows: rows.map(r => ({ ...r })), at: rowsAt, feed: rowsFeed } : null) },
    __sessionPanel: {
      cachedCard: (chip, id) => (spCalls.push({ chip, id }), card?.(id) ? { detail: card(id), board: 'default', at: detailAt, trusted, stale: false, pending: false } : null),
      feedVouches: route => (spCalls.push({ vouch: route }), vouched)
    },
    hermesDesktop: { api: async req => {
      calls.push(req)
      if (req.path.startsWith('/api/sessions/')) return { id: globalThis.__bp.stored, parent_session_id: null }
      if (req.path.startsWith('/api/session-kanban-tasks')) return { tasks: (rows || globalThis.__bp.liveRows || []).map(r => ({ ...r })) }
      if (req.path.startsWith('/api/plugins/hub-sync/')) throw new Error('hub-sync not installed')
      if (req.path === '/api/plugins/blocked-panel/ping') return { ok: true }
      if (req.path === '/api/plugins/blocked-panel/steps') return { steps: ['Pick a window.', 'Reply with it.'] }
      if (req.path.startsWith('/api/plugins/kanban/tasks/')) {
        const id = decodeURIComponent(req.path.split('/')[5].split('?')[0])
        // The live owner answers what the feed pushed (same card), plus comments.
        return { task: (card?.(id) || slim(id)).task, events: [], comments: [{ body: 'earlier' }, { body: 'use the Tuesday window' }] }
      }
      throw new Error(`unexpected ${req.path}`)
    } }
  }
  const reads = kind => calls.filter(c => c.path?.startsWith(kind) && !c.method)
  return { calls, spCalls, reads }
}

test('current shared data: lineage, owner list and blocked-card details are taken with no requests of their own', async () => {
  globalThis.__bp.stored = 's-current'
  const ids = new Set(['s-current', 's-parent'])
  const { calls, spCalls, reads } = wire({ lineage: ids, rows: [row('t_ask1'), row('t_issue1', 'triage')], card: id => (id === 't_ask1' ? slim(id) : slim(id, { status: 'triage', block_kind: null, human_request: null })) })
  await m.refresh()
  await settle()
  assert.equal(reads('/api/sessions/').length, 0, 'lineage from session-tasks')
  assert.equal(reads('/api/session-kanban-tasks').length, 0, 'owner list from session-tasks')
  assert.deepEqual(calls.find(c => c.shared === 'lineage'), { shared: 'lineage', stored: 's-current', conn: 'local', profile: 'default' })
  assert.deepEqual(calls.find(c => c.shared === 'rows').route, { connectionId: 'local', profile: 'default', session: 's-current' })
  assert.deepEqual(calls.find(c => c.shared === 'rows').ids.sort(), ['s-current', 's-parent'])
  // Details come from session-panel's cache, read through the owner route, vouched by the feed.
  assert.deepEqual(spCalls.find(c => c.id === 't_ask1').chip, { connectionId: 'local', profile: 'default', board: 'default' })
  // The only card read is the one the rewrite needs (a pushed detail has no comments), made once per ask.
  const cardReads = reads('/api/plugins/kanban/tasks/')
  assert.equal(cardReads.length, 1)
  assert.match(cardReads[0].path, /t_ask1/)
  const post = calls.find(c => c.path === '/api/plugins/blocked-panel/steps')
  assert.deepEqual(post.body.comments, ['earlier', 'use the Tuesday window'], 'the rewrite sees the same comments as before')
  calls.length = 0
  await m.refresh()
  await settle()
  assert.equal(reads('/api/sessions/').length + reads('/api/session-kanban-tasks').length + reads('/api/plugins/kanban/tasks/').length, 0, 'steady state: none of these read')
})

test('snapshot from shared data matches what the same data read live produces', async () => {
  globalThis.__bp.stored = 's-parity'
  const rows = [row('t_ask2'), row('t_issue2', 'triage')]
  const card = id => (id === 't_ask2' ? slim(id) : slim(id, { status: 'triage', block_kind: null, human_request: null }))
  wire({ lineage: new Set(['s-parity']), rows, card })
  const dispose = m.default.register({ register() {} })
  try {
    await m.refresh()
    const shared = structuredClone(globalThis.window.__blockedPanel.snapshot)
    // The same chat read live (forced): identical asks and issues, identity included.
    globalThis.__bp.liveRows = rows
    await m.refresh(true)
    const live = globalThis.window.__blockedPanel.snapshot
    const pick = s => ({ items: s.items.map(i => [i.id, i.connectionId, i.profile, i.board, i.text, i.kindLabel]), issues: s.issues.map(i => [i.id, i.connectionId, i.owner, i.failureKind]) })
    assert.deepEqual(pick(shared), pick(live))
    assert.equal(shared.items.length, 1)
    assert.equal(shared.items[0].profile, 'default')
    assert.equal(shared.issues.length, 1)
  } finally { dispose(); delete globalThis.__bp.liveRows }
})

test('missing or not-current shared data: blocked-panel reads lineage, list and details itself', async () => {
  globalThis.__bp.stored = 's-stale'
  const incomplete = new Set(['s-stale']); incomplete.incomplete = 'GET /api/sessions failed'
  globalThis.__bp.liveRows = [row('t_ask3')]
  const { calls, reads } = wire({ lineage: incomplete, rows: null, card: id => slim(id), detailAt: Date.now() - 120_000 })
  try {
    await m.refresh()
    await settle()
    assert.equal(reads('/api/sessions/').length, 1, 'incomplete shared lineage: walked here')
    const lists = reads('/api/session-kanban-tasks')
    assert.equal(lists.length, 1, 'no current rows: read the owner list')
    assert.equal(lists[0].connectionId, 'local')
    assert.equal(lists[0].profile, 'default')
    assert.equal(reads('/api/plugins/kanban/tasks/').filter(c => /t_ask3/.test(c.path)).length >= 1, true, 'stale, unvouched detail: read it')
    assert.ok(calls.some(c => c.shared === 'rows'), 'asked session-tasks first')
  } finally { delete globalThis.__bp.liveRows }
})

test('no session-tasks / session-panel loaded: everything is read here, as before', async () => {
  globalThis.__bp.stored = 's-alone'
  globalThis.__bp.liveRows = [row('t_ask4')]
  const { reads } = wire({ lineage: null, rows: null, card: null })
  delete globalThis.window.__sessionTasks
  delete globalThis.window.__sessionPanel
  try {
    await m.refresh()
    assert.equal(reads('/api/sessions/').length, 1)
    assert.equal(reads('/api/session-kanban-tasks').length, 1)
    assert.ok(reads('/api/plugins/kanban/tasks/').some(c => /t_ask4/.test(c.path)))
  } finally { delete globalThis.__bp.liveRows }
})

test('a forced refresh reads list and details itself even when shared data is current', async () => {
  globalThis.__bp.stored = 's-force'
  const rows = [row('t_ask5')]
  globalThis.__bp.liveRows = rows
  const { calls, reads } = wire({ lineage: new Set(['s-force']), rows, card: id => slim(id) })
  try {
    await m.refresh(true)
    assert.equal(reads('/api/session-kanban-tasks').length, 1)
    assert.ok(reads('/api/plugins/kanban/tasks/').some(c => /t_ask5/.test(c.path)))
    assert.ok(!calls.some(c => c.shared === 'rows'), 'shared rows are not consulted on a forced refresh')
  } finally { delete globalThis.__bp.liveRows }
})

test('sharedDetail: only a trusted record within the TTL; board, id, staleness and pending checked; its own time kept', () => {
  const t = { id: 't_x', board: 'default', connectionId: 'local', readProfile: 'default' }
  const now = Date.now()
  const sp = rec => ({ cachedCard: () => rec })
  const rec = extra => ({ detail: slim('t_x'), board: 'default', at: now - 5_000, trusted: true, stale: false, pending: false, ...extra })
  assert.deepEqual(m.sharedDetail(t, 's', now, sp(rec())), { detail: slim('t_x'), at: now - 5_000 }, 'the record keeps its own time')
  assert.equal(m.sharedDetail(t, 's', now, sp(rec({ trusted: false }))), null, 'an untrusted / unvouched record')
  assert.equal(m.sharedDetail(t, 's', now, sp(rec({ trusted: undefined }))), null, 'an older session-panel without the flag')
  assert.equal(m.sharedDetail({ ...t, readProfile: undefined }, 's', now, sp(rec())), null, 'a row not read through the owner route')
  assert.equal(m.sharedDetail(t, 's', now, sp(rec({ board: 'ops' }))), null, 'another board under the same id')
  assert.equal(m.sharedDetail(t, 's', now, sp(rec({ detail: slim('t_y') }))), null, 'another card')
  assert.equal(m.sharedDetail(t, 's', now, sp(rec({ stale: true }))), null)
  assert.equal(m.sharedDetail(t, 's', now, sp(rec({ pending: true }))), null)
  assert.equal(m.sharedDetail(t, 's', now, sp(rec({ at: now - 61_000 }))), null, 'older than the detail TTL, trusted or not')
  assert.equal(m.sharedDetail(t, 's', now, sp(rec({ at: now + 5_000 }))), null, 'a time in the future')
  assert.equal(m.sharedDetail(t, 's', now, sp(rec({ detail: null }))), null, 'a read-only summary (no detail)')
  assert.equal(m.sharedDetail(t, 's', now, null), null, 'session-panel not loaded')
})

test('sharedOwnerRows checks the age it is given: 15 s for a read, 60 s for a feed push', () => {
  const now = Date.now(), route = { connectionId: 'local', profile: 'default' }
  const st = (at, feed) => ({ kanbanRows: () => ({ rows: [row('t_r')], at, feed }) })
  assert.equal(m.sharedOwnerRows('s', ['s'], route, now, st(now - 10_000, false)).length, 1)
  assert.equal(m.sharedOwnerRows('s', ['s'], route, now, st(now - 16_000, false)), null, 'an old read')
  assert.equal(m.sharedOwnerRows('s', ['s'], route, now, st(now - 50_000, true)).length, 1, 'a recent push')
  assert.equal(m.sharedOwnerRows('s', ['s'], route, now, st(now - 61_000, true)), null, 'pushes stopped')
  assert.equal(m.sharedOwnerRows('s', ['s'], route, now, st(undefined, false)), null, 'no time: not current')
  assert.equal(m.sharedOwnerRows('s', ['s'], route, now, st(now + 1_000, false)), null, 'a time in the future')
})

test('an untrusted detail or an old list is not taken: read here', async () => {
  globalThis.__bp.stored = 's-untrusted'
  globalThis.__bp.liveRows = [row('t_ask6')]
  const { reads } = wire({ lineage: new Set(['s-untrusted']), rows: [row('t_ask6')], rowsAt: Date.now() - 20_000, card: id => slim(id), trusted: false })
  try {
    await m.refresh()
    assert.equal(reads('/api/session-kanban-tasks').length, 1, 'list older than 15 s: read')
    assert.ok(reads('/api/plugins/kanban/tasks/').some(c => /t_ask6/.test(c.path)), 'untrusted detail: read')
  } finally { delete globalThis.__bp.liveRows }
})

test('unblocking a card forces a read of everything (never a neighbour copy taken before the change)', async () => {
  globalThis.__bp.stored = 's-act'
  const rows = [row('t_ask7')]
  globalThis.__bp.liveRows = rows
  const { calls, reads } = wire({ lineage: new Set(['s-act']), rows, card: id => slim(id) })
  try {
    await m.refresh()
    assert.equal(reads('/api/session-kanban-tasks').length, 0, 'before: shared')
    calls.length = 0
    await m.unblockCard({ id: 't_ask7', board: 'default', connectionId: 'local' }, '', async () => ({ ok: true }))
    await settle()
    assert.equal(reads('/api/session-kanban-tasks').length, 1, 'after the action: read here')
    assert.ok(!calls.some(c => c.shared === 'rows'))
  } finally { delete globalThis.__bp.liveRows }
})

test('refresh(true) arriving while a poll runs is queued, then runs once, forced; callers get that run', async () => {
  globalThis.__bp.stored = 's-queue'
  const rows = [row('t_ask8')]
  globalThis.__bp.liveRows = rows
  const { calls, reads } = wire({ lineage: new Set(['s-queue']), rows, card: id => slim(id) })
  let release
  const gate = new Promise(r => { release = r })
  const api = window.hermesDesktop.api
  window.hermesDesktop.api = async req => { if (req.path.startsWith('/api/plugins/hub-sync/')) await gate; return api(req) }
  try {
    const poll = m.refresh() // a plain poll: shared data, stuck on the hub read
    await settle()
    const forcedA = m.refresh(true)
    const forcedB = m.refresh() // a later plain request joins the queued forced run
    assert.equal(forcedA, forcedB, 'one queued run')
    assert.equal(reads('/api/session-kanban-tasks').length, 0, 'nothing forced yet: the poll is still running')
    release()
    await poll
    await forcedA
    assert.equal(reads('/api/session-kanban-tasks').length, 1, 'the queued run read the list itself (forced)')
    assert.equal(calls.filter(c => c.shared === 'rows').length, 1, 'only the poll consulted shared rows')
    await settle()
    assert.equal(reads('/api/session-kanban-tasks').length, 1, 'and ran once')
  } finally { delete globalThis.__bp.liveRows }
})
