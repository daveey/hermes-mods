import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const outcome = (stripe = 'stuck') => ({ status: {
  status_version: 1, kind: 'decision', revision: 1, generation: 0,
  identity: { gateway: 'example', profile: 'default', session_id: 'chat', owner_epoch: 'epoch' },
  coverage: { state: 'complete', reasons: [] }, presentation: { stripe, reason_codes: [] }
} })

const P = await loadPlugin({
  SidebarSection: ({ label, count, children }) => React.createElement('section', null,
    React.createElement('h3', null, `${label} · ${count}`), children),
  SidebarSectionRow: ({ label, accentColor, onSelect }) => React.createElement('button',
    { style: { color: accentColor }, onClick: onSelect }, label)
})

test('unlinked red rows share pushed snapshot, exclude linked asks and clear after resolution', async () => {
  const ctx = { rest() { throw Error('sidebar must never start a poller') } }
  const store = P.attentionStore(ctx)
  P.sourceFor(ctx).subscribe = () => Object.assign(() => {}, { update() {}, refresh() {} })
  const rows = [
    { id: 'headless', key: 'default/headless', title: 'Choose route', lane: 'work', unlinked: true },
    { id: 'linked', key: 'default/linked', title: 'Linked ask', lane: 'work', unlinked: false }
  ]
  store.publish({ attention: { available: true, rows }, status: { you: 2 } })
  const view = await mount(React.createElement(P.UnlinkedAsks, { ctx }))
  try {
    assert.equal(view.container.querySelector('h3').textContent, 'Asks outside chats · 1')
    assert.equal(view.container.querySelectorAll('button').length, 1)
    assert.equal(view.container.querySelector('button').textContent, 'Choose route')
    assert.equal(view.container.querySelector('button').style.color, 'rgb(239, 68, 68)')
    await React.act(async () => store.publish({ attention: { available: true, rows: [] }, status: { you: 0 } }))
    assert.ok(view.container.querySelector('[data-unlinked-asks]') === null)
  } finally { await view.close() }
})

test('same snapshot covers linked scheduled and hidden-chat asks exactly once', () => {
  const rows = ['visible', 'scheduled', 'hidden', 'headless'].map(id => ({ id, unlinked: id === 'headless' }))
  const view = { attention: { available: true, rows }, status: { you: 4 } }
  const sessions = [{ outcome: outcome(), needs_you: [{ kind: 'card', ref: 'default/visible' }] },
    { id: 'legacy-device', kanban_blocked: 0 }]
  const missing = P.uncoveredAsks(view, sessions)
  assert.deepEqual(missing.map(row => row.id), ['scheduled', 'hidden', 'headless'])
  const covered = rows.filter(row => !missing.includes(row))
  assert.equal(new Set([...covered, ...missing].map(row => row.id)).size, view.status.you)
  assert.equal(covered.length + missing.length, view.status.you)
})

test('qualified board identity never suppresses a different board ask', () => {
  const view = { attention: { available: true, rows: [{ id: 't_ask', target: { board: 'default' } }] } }
  assert.equal(P.uncoveredAsks(view, [{ outcome: outcome(), needs_you: [{ kind: 'card', ref: 'other/t_ask' }] }]).length, 1)
  assert.equal(P.uncoveredAsks(view, [{ outcome: outcome(), needs_you: [{ kind: 'card', ref: 'default/t_ask' }] }]).length, 0)
})

test('mounted lane filter preserves hidden-chat asks and clearing restores linked coverage without feedback', async () => {
  const sessions = [{ id: 'chat', outcome: outcome(), needs_you: [{ kind: 'card', ref: 'default/t_ask' }] }]
  const Q = await loadPlugin({ SESSION_FILTER_AREA: 'sessions.filter', useSidebarSessions: () => sessions,
    SidebarSection: ({ children }) => React.createElement('section', null, children),
    SidebarSectionRow: ({ label }) => React.createElement('span', { 'data-ask': '1' }, label) })
  const snap = { configured: true, sessions: { chat: { alpha: { you: 1 } } },
    lanes: { alpha: { share: 50, strip: { blocked: 1, running: 0, other: 0 }, status: { you: 1 } },
      beta: { share: 50, strip: { blocked: 0, running: 1, other: 0 }, status: { run: 1 } } },
    status: { you: 1, run: 1 }, attention: { available: true, rows: [
      { id: 't_ask', key: ':default:default:t_ask', title: 'Hidden ask', unlinked: false }] } }
  let registrations = 0
  const filters = new Set()
  const ctx = { rest: async () => snap, register(c) {
    registrations++; filters.add(c); return () => filters.delete(c)
  } }
  const store = Q.attentionStore(ctx)
  let push
  Q.sourceFor(ctx).subscribe = (_params, update) => {
    push = update
    update(snap)
    return Object.assign(() => {}, { update() {}, refresh() {} })
  }
  localStorage.clear()
  const pane = await mount(React.createElement(Q.UnlinkedAsks, { ctx }))
  const strip = await mount(React.createElement(Q.Lanes, { ctx }))
  await React.act(async () => push(snap))
  try {
    const fallback = () => pane.container.querySelectorAll('[data-ask]').length
    const visible = () => sessions.filter(s => [...filters].every(f => f.data.match(s))).length
    assert.equal(fallback(), 0)
    assert.equal(visible(), 1)
    await strip.click(strip.container.querySelector('[data-lane-pill="beta"]'))
    assert.equal(visible(), 0)
    assert.equal(fallback(), 1, 'the pre-plugin SDK chat must not hide its fallback')
    assert.equal(visible() + fallback(), snap.status.you)
    const count = registrations
    await React.act(async () => store.publish({ ...snap }))
    await pane.flush()
    assert.equal(registrations, count, 'attention publication does not re-register the filter')
    await strip.click(strip.container.querySelector('[data-status-all]'))
    assert.equal(visible(), 1)
    assert.equal(fallback(), 0)
    assert.equal(visible() + fallback(), snap.status.you)
    await strip.click(strip.container.querySelector('[data-status="run"]'))
    assert.equal(visible(), 0)
    assert.equal(fallback(), 1, 'category filters also preserve the ask')
    await strip.close()
    assert.equal(fallback(), 0, 'unmount clears only the lanes visibility filter')
  } finally { if (strip.container.isConnected) await strip.close(); await pane.close(); localStorage.clear() }
})

test('non-red canonical rows cannot suppress asks even with populated needs_you', () => {
  const row = { id: 'ask', target: { board: 'default' } }
  const view = { attention: { available: true, rows: [row] } }
  const session = { needs_you: [{ kind: 'card', ref: 'default/ask' }] }
  for (const stripe of ['waiting', 'none', 'attention', 'blocked', 'done', 'running', 'offline']) {
    assert.deepEqual(P.uncoveredAsks(view, [{ ...session, outcome: outcome(stripe) }]), [row], stripe)
  }
  for (const extra of [{ offline_cached: true }, { archived: true }, { outcome: undefined },
    { outcome: { status: { presentation: { stripe: 'stuck' } } } }]) {
    assert.deepEqual(P.uncoveredAsks(view, [{ ...session, outcome: outcome(), ...extra }]), [row])
  }
  assert.deepEqual(P.uncoveredAsks(view, [{ ...session, outcome: outcome() }, { ...session, outcome: outcome() }]), [])
  assert.deepEqual(P.uncoveredAsks(view, [{ ...session, outcome: outcome() }], () => false), [row])
  assert.deepEqual(P.uncoveredAsks({ attention: { available: false, rows: [row] } }, [session]), [])
})

test('malformed red envelopes remain uncovered just like unknown canonical sidebar status', () => {
  const view = { attention: { available: true, rows: [{ id: 'ask' }] } }
  const invalid = [
    { status_version: 2 }, { kind: 'future' }, { revision: -1 }, { generation: 0.5 },
    { identity: { gateway: 'example' } }, { coverage: { state: 'bad', reasons: [] } },
    { coverage: { state: 'complete', reasons: [1] } },
    { presentation: { stripe: 'stuck', reason_codes: [1] } }
  ]
  for (const patch of invalid) {
    const session = { outcome: { status: { ...outcome().status, ...patch } },
      needs_you: [{ kind: 'card', ref: 'default/ask' }] }
    assert.equal(P.uncoveredAsks(view, [session]).length, 1, JSON.stringify(patch))
  }
})

test('mounted canonical presentation contract keeps four grey-linked asks red in fallback', async t => {
  if (!process.env.HERMES_CORE_SOURCE) return t.skip('set HERMES_CORE_SOURCE for actual canonical selector')
  const { pathToFileURL } = await import('node:url')
  const { canonicalOutcomeStatus } = await import(pathToFileURL(
    `${process.env.HERMES_CORE_SOURCE}/apps/desktop/src/lib/session-outcome.ts`).href)
  const rows = Array.from({ length: 4 }, (_, i) => ({ id: `ask${i}`, key: `default/ask${i}`, title: `Ask ${i}` }))
  const sessions = [{ id: 'chat', outcome: outcome('waiting'), needs_you: rows.map(row => ({ kind: 'card', ref: `default/${row.id}` })) }]
  const Q = await loadPlugin({ useSidebarSessions: () => sessions,
    SidebarSection: ({ children }) => React.createElement('section', null, children),
    SidebarSectionRow: ({ label }) => React.createElement('span', { 'data-ask': '1' }, label) })
  const ctx = {}
  const snap = { status: { you: 4 }, attention: { available: true, rows } }
  const store = Q.attentionStore(ctx)
  Q.sourceFor(ctx).subscribe = () => Object.assign(() => {}, { update() {}, refresh() {} })
  store.publish(snap)
  const pane = await mount(React.createElement(Q.UnlinkedAsks, { ctx }))
  try {
    for (const [stripe, extra] of [['waiting', {}], ['none', {}], ['attention', {}],
      ['blocked', {}], ['stuck', { offline_cached: true }], ['stuck', { archived: true }],
      ['stuck', {}], ['waiting', {}]]) {
      sessions[0] = { ...sessions[0], offline_cached: false, archived: false, outcome: outcome(stripe), ...extra }
      await React.act(async () => store.publish({ ...snap }))
      const session = sessions[0]
      const paintedRed = !session.offline_cached && !session.archived && canonicalOutcomeStatus(session.outcome).stripe === 'stuck'
      const fallback = pane.container.querySelectorAll('[data-ask]').length
      assert.equal(fallback, paintedRed ? 0 : 4, `${stripe} ${JSON.stringify(extra)}`)
      assert.equal((paintedRed ? new Set(session.needs_you.map(ask => ask.ref)).size : 0) + fallback, snap.status.you)
      assert.equal(Q.hasRedAskCoverage(session), paintedRed, 'coverage agrees with actual core canonical selector')
    }
  } finally { await pane.close() }
})

test('same-id sessions only cover the ask on its linked gateway', () => {
  const row = { id: 'ask', target: { board: 'bb' }, links: [
    { chat_gateway: 'edge-b', chat_profile: null, chat_session: 'kanban_owner_terminal' }] }
  const session = gateway => ({ id: 'kanban_owner_terminal', connection_id: `conn-${gateway}`,
    outcome: { status: { ...outcome().status, identity: {
      gateway, profile: 'default', session_id: 'kanban_owner_terminal', owner_epoch: 'epoch' } } },
    needs_you: [{ kind: 'card', ref: 'bb/ask' }] })
  const a = session('edge-a'), b = session('edge-b')
  const view = { attention: { available: true, rows: [row] } }
  assert.deepEqual(P.uncoveredAsks(view, [a]), [row], 'foreign twin cannot suppress fallback')
  assert.deepEqual(P.uncoveredAsks(view, [a, b]), [])
  assert.deepEqual(P.uncoveredAsks(view, [b, a]), [])
})

test('worker-owned ask is covered only by its exact qualified card ref', () => {
  const row = { id: 'ask', target: { gateway: 'worker', profile: 'work', board: 'bb' },
    links: [{ chat_gateway: 'example', chat_profile: 'default', chat_session: 'chat' }] }
  const view = { attention: { available: true, rows: [row] } }
  const session = ref => ({ id: 'chat', outcome: outcome(), needs_you: [{ kind: 'card', ref }] })
  assert.deepEqual(P.uncoveredAsks(view, [session('worker/work/bb/ask')]), [])
  for (const ref of ['other/work/bb/ask', 'worker/default/bb/ask', 'worker/work/other/ask',
    'worker/work/bb/other', 'bb/ask', '']) {
    assert.deepEqual(P.uncoveredAsks(view, [session(ref)]), [row], ref)
  }
  const local = { ...row, target: { gateway: 'example', profile: 'default', board: 'bb' } }
  assert.deepEqual(P.uncoveredAsks({ attention: { available: true, rows: [local] } }, [session('bb/ask')]), [])
})

test('muted and offline owners remain red fallbacks with explicit reasons', async () => {
  const row = { id: 'ask', key: 'default/ask', title: 'Choose route', links: [
    { chat_gateway: 'example', chat_profile: 'default', chat_session: 'chat' }] }
  const sessions = [{ id: 'chat', outcome: outcome('blocked'), muted: true,
    needs_you: [{ kind: 'card', ref: 'default/ask' }] }]
  const Q = await loadPlugin({ useSidebarSessions: () => sessions,
    SidebarSection: ({ label, children }) => React.createElement('section', null, label, children),
    SidebarSectionRow: ({ label, meta, tip, accentColor }) => React.createElement('span',
      { 'data-ask': '1', title: tip, style: { color: accentColor } }, label, meta) })
  const ctx = {}, store = Q.attentionStore(ctx)
  const snap = { attention: { available: true, rows: [row] } }
  Q.sourceFor(ctx).subscribe = () => Object.assign(() => {}, { update() {}, refresh() {} })
  store.publish(snap)
  const pane = await mount(React.createElement(Q.UnlinkedAsks, { ctx }))
  try {
    assert.match(pane.container.textContent, /Asks outside chats/)
    assert.match(pane.container.textContent, /Chat muted/)
    assert.equal(pane.container.querySelector('[data-ask]').style.color, 'rgb(239, 68, 68)')
    sessions[0] = { id: 'chat', outcome: outcome(), offline_cached: true }
    await React.act(async () => store.publish({ ...snap }))
    assert.match(pane.container.textContent, /Chat offline/)
    // Live SDK recents may be red while the actual sidebar hook has a cached
    // copy with the same canonical revision and without needs_you.
    sessions[0] = { id: 'chat', outcome: outcome(), needs_you: [{ kind: 'card', ref: 'default/ask' }] }
    await React.act(async () => store.publish({ ...snap }))
    assert.ok(pane.container.querySelector('[data-ask]') === null)
  } finally { await pane.close() }
})

test('sidebar owns the shared subscription without a strip; joining never starts a second reader', async () => {
  const ctx = {}
  let starts = 0, stops = 0, updates = 0
  P.sourceFor(ctx).subscribe = () => {
    starts++
    return Object.assign(() => { stops++ }, { update() { updates++ }, refresh() {} })
  }
  const sidebar = await mount(React.createElement(P.UnlinkedAsks, { ctx }))
  assert.equal(starts, 1)
  const strip = P.attentionStore(ctx).connect({ expanded: true }, () => {}, () => {}, true)
  assert.equal(starts, 1)
  assert.equal(updates, 1)
  strip()
  assert.equal(stops, 0)
  await sidebar.close()
  assert.equal(stops, 1)
})
