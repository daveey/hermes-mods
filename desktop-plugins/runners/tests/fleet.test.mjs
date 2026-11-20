// Fleet panel: one test per data path (registry, live leases, CI runners, unreachable),
// plus in-place refresh while open and backoff when a gateway is down.
import test from 'node:test'
import assert from 'node:assert/strict'
import { React, loadPlugin, mount, host, route } from './sdk-mock.mjs'

const P = await loadPlugin()
// These tests look at individual CI machines: start with every CI group expanded.
localStorage.setItem('hermes-runners-ci-open', JSON.stringify(P.CI_GROUPS))
const NOW = Date.parse('2026-10-28T23:00:00Z')

const fleet = (over = {}) => ({
  contract_version: 1, profile: 'example', generated_at: '2026-10-28T23:00:00Z',
  gateways: {
    state: 'ready', machines: [
      { name: 'hub', me: true, state: 'active', reason: null, labels: ['linux', 'machine:hub'], pinned_only: false, slots: 4, slots_source: 'local',
        running: [{ board: 'default', task: 't_aaaa1111', title: 'Fix the thing', started_at: NOW / 1000 - 120 }] },
      { name: 'worker-1', state: 'active', reason: null, labels: ['linux'], pinned_only: false, slots: 3, slots_source: 'registry', running: [] },
      { name: 'mini', state: 'asleep', reason: 'not seen for 30 min', labels: ['macos'], pinned_only: true, slots: null, slots_source: null, running: [] }
    ]
  },
  ci: {
    state: 'ready', hidden_deregistered: 220, machines: [
      { name: 'ec2-gwgen', ephemeral: false, labels: ['ci-fast'], warnings: [],
        slots: [{ runner: 'ec2-gwgen-1', state: 'busy', labels: [] }, { runner: 'ec2-gwgen-2', state: 'idle', labels: [] }] },
      { name: 'mac-mini', ephemeral: false, labels: ['bb-sign'], warnings: ['mac-mini: tool:xcode failed'],
        slots: [{ runner: 'mac-mini', state: 'idle', labels: [] }] },
      { name: 'metta4', ephemeral: false, labels: [], warnings: [],
        slots: [{ runner: 'metta4', state: 'offline', labels: [] }, { runner: 'metta4-2', state: 'offline', labels: [] }] }
    ]
  },
  ...over
})

function ctxWith(responses) {
  const calls = []
  return {
    calls,
    rest: async (path, opts) => {
      if (path.startsWith('/v1/scaler/limits')) throw Object.assign(Error('unsupported'), { status: 404 })
      calls.push([path, opts])
      const next = responses.length > 1 ? responses.shift() : responses[0]
      if (next instanceof Error) throw next
      return structuredClone(next)
    },
    onDispose() {}, register() {}
  }
}

async function render(view, now = NOW) {
  return mount(React.createElement(P.Panel, { view, now }))
}

test('registry: gateway machines show fixed slots, flags and unset slot counts', async () => {
  const view = P.merge([{ route, body: fleet(), at: NOW }], NOW)
  const ui = await render(view)
  const hub = ui.container.querySelector('[data-machine="hub"]')
  assert.equal(hub.querySelectorAll('.hermes-fleet-slot').length, 4)
  assert.match(hub.textContent, /1\/4/)
  const mini = ui.container.querySelector('[data-machine="mini"]')
  // Flags and reasons are hover text, not panel text.
  const note = mini.querySelector(':scope > .hermes-fleet-info')
  assert.equal(note.textContent, 'i')
  assert.equal(note.dataset.tip, 'pinned only · slots not set · not seen for 30 min · load not reported')
  assert.equal(note.getAttribute('aria-label'), `mini: ${note.dataset.tip}`)
  assert.equal(note.title, '', 'no native title: the tooltip is the CSS one')
  assert.equal(mini.lastElementChild, note, 'rightmost')
  assert.equal(mini.title, '', 'the hover text is on the (i), not the whole row')
  assert.doesNotMatch(mini.textContent, /pinned only|slots not set|not seen/)
  assert.equal(ui.container.querySelector('[data-machine="worker-1"] > .hermes-fleet-info').dataset.tip, 'load not reported')
  // Labels (capabilities) are not shown on gateway rows.
  assert.doesNotMatch(mini.textContent, /macos/)
  assert.doesNotMatch(hub.textContent, /linux/)
  assert.equal(mini.querySelectorAll('.is-lost').length, 0)
  assert.match(ui.container.textContent, /1 \/ 7 slots busy/)
  await ui.close()
})

test('waiting: header counts dispatched cards; rows split placement fit with tooltips', async () => {
  const body = fleet()
  body.gateways.waiting = 90
  body.gateways.machines = body.gateways.machines.map((m) => m.name === 'hub' ? { ...m, waiting: 18, ineligible: 72 } : { ...m, waiting: 0, ineligible: 90 })
  const ui = await render(P.merge([{ route, body, at: NOW }], NOW))
  assert.match(ui.container.textContent, /1 \/ 7 slots busy · 90 waiting/)
  assert.match(ui.container.querySelector('.hermes-fleet-head [title]').title, /gateway-dispatched profiles.*counted once/)
  const hub = ui.container.querySelector('[data-machine="hub"]')
  assert.equal(hub.querySelector('.hermes-fleet-count').textContent, '1/4')
  const fit = hub.querySelector(':scope > .hermes-fleet-waiting')
  assert.equal(fit.textContent, '18')
  assert.match(fit.title, /Need a slot here.*other machines/)
  const excluded = hub.querySelector('.hermes-fleet-ineligible')
  assert.equal(excluded.textContent, '72')
  assert.match(excluded.title, /placement labels, skill labels, machine pins or pinned-only/)
  const mini = ui.container.querySelector('[data-machine="mini"]')
  assert.deepEqual(['waiting', 'ineligible'].map((k) => mini.querySelector(`.hermes-fleet-${k}`).textContent), ['0', '90'])
  // The units are in the column header, once.
  assert.deepEqual([...ui.container.querySelectorAll('.hermes-fleet-cols > span')].map((e) => e.textContent),
    ['Machine', 'Slots', 'Waiting', "Can't run", 'CPU', 'RAM', 'Disk free', 'Tok/slot', 'Busy', ''])
  await ui.close()
  const old = await render(P.merge([{ route, body: fleet(), at: NOW }], NOW))  // older gateway: no count
  assert.doesNotMatch(old.container.textContent, /waiting/)
  assert.equal(old.container.querySelector('[data-machine="hub"] > .hermes-fleet-waiting').textContent, '')
  await old.close()
})

test('registry: each machine keeps its own cap whichever gateway answered last', () => {
  const hubView = fleet()
  const macView = fleet()
  macView.gateways.machines = macView.gateways.machines.map((m) =>
    m.name === 'hub' ? { ...m, me: false, slots: null, slots_source: null } : m)
  const view = P.merge([
    { route, body: hubView, at: NOW - 5000 },
    { route: { ...route, connectionId: 'local' }, body: macView, at: NOW }
  ], NOW)
  const hub = view.gateways.machines.find((m) => m.name === 'hub')
  assert.equal(hub.slots, 4)
  assert.equal(hub.slots_source, 'local')
})

test('load meters: reported, absent and stale', async () => {
  const body = fleet()
  body.gateways.machines[0].load = { cpu_load1: 8, cpus: 16, mem_used_pct: 95.4, disk_free_gb: 57.2, disk_total_gb: 387, at: NOW - 30000, stale: false }
  body.gateways.machines[1].load = { cpu_load1: 1, cpus: 8, at: NOW - 10 * 60000, stale: true }
  body.ci.machines[1].load = { cpu_load1: 30.35, disk_free_gb: 24, at: NOW, stale: false }
  const ui = await render(P.merge([{ route, body, at: NOW }], NOW))
  const cells = (name) => [...ui.container.querySelectorAll(`[data-machine="${name}"] > .hermes-fleet-meter`)]
  const hub = ui.container.querySelector('[data-machine="hub"]')
  assert.deepEqual(cells('hub').map((c) => [c.dataset.meter, c.textContent]), [['cpu', '50%'], ['ram', '95%'], ['disk', '57 GB']])
  assert.equal(cells('hub')[2].title, 'Disk 57 GB free')
  assert.equal(hub.querySelectorAll('.hermes-fleet-bar').length, 3)
  assert.equal(hub.querySelectorAll('.is-high').length, 1, 'RAM ≥ 90% is highlighted')
  assert.deepEqual(cells('worker-1').map((c) => c.textContent), ['13%', '–', '–'])
  assert.ok(cells('worker-1').every((c) => c.classList.contains('is-stale')))
  assert.match(ui.container.querySelector('[data-machine="worker-1"] > .hermes-fleet-info').dataset.tip, /load 10m old/)
  assert.match(cells('worker-1')[0].title, /CPU 13% \(10m old\)/)
  assert.deepEqual(cells('mini').map((c) => c.textContent), ['–', '–', '–'])
  assert.match(ui.container.querySelector('[data-machine="mini"] > .hermes-fleet-info').dataset.tip, /load not reported/)
  assert.doesNotMatch(ui.container.querySelector('.hermes-fleet-gw').textContent, /load not reported|old/)
  assert.equal(P.loadParts({ cpus: 16, at: NOW }), null, 'a report with no readable values is "not reported"')
  // CI is a separate surface; no CI rows or meters remain in the gateway panel.
  assert.ok(ui.container.querySelector('[data-machine="mac-mini"]') === null)
  await ui.close()
})

test('live leases: busy slots name the card and open it on click', async () => {
  const opened = []
  globalThis.__hermesKanbanOpenTask = (id, board) => (opened.push([id, board]), true)
  const ui = await render(P.merge([{ route, body: fleet(), at: NOW }], NOW))
  const busy = ui.container.querySelector('[data-machine="hub"] .is-busy')
  assert.match(busy.title, /t_aaaa1111 · Fix the thing/)
  assert.match(busy.title, /board default · running 2m ago/)
  await ui.click(busy)
  assert.deepEqual(opened, [['t_aaaa1111', 'default']])
  delete globalThis.__hermesKanbanOpenTask
  await ui.close()
})

test('Runners panel renders only gateway workers, never CI', async () => {
  const ui = await render(P.merge([{ route, body: fleet(), at: NOW }], NOW))
  assert.doesNotMatch(ui.container.textContent, /CI|GitHub|deregistered|mac-mini|ec2-gwgen|metta4/)
  assert.ok(ui.container.querySelector('[data-ci-section]') === null)
  await ui.close()
})

test('unreachable: a failing gateway backs off and keeps last data marked stale, never red', async () => {
  let t = NOW
  const ctx = ctxWith([fleet(), Object.assign(new Error('fetch failed'), { status: 0 })])
  const transport = P.createTransport(ctx, host, () => t)
  let view = await transport.poll()
  assert.equal(view.gateways.state, 'ready')
  t += 15000
  view = await transport.poll() // fails: 1st backoff 15s
  assert.equal(view.gateways.state, 'stale')
  assert.match(view.gateways.reason, /last update 15s ago · fetch failed/)
  assert.equal(P.summarize(view).tone, 'warn')
  const before = ctx.calls.length
  t += 5000
  await transport.poll() // inside the backoff window: no request
  assert.equal(ctx.calls.length, before)
  for (let i = 0; i < 10; i++) { t += P.BACKOFF_MAX_MS; await transport.poll() }
  const s = [...transport.routes.values()][0]
  assert.ok(s.nextAt - t <= P.BACKOFF_MAX_MS, 'backoff is capped')
  assert.equal(ctx.calls[0][1].timeoutMs, P.REQUEST_TIMEOUT_MS)
  // No gateway ever answered: unknown with the reason, still not red.
  const cold = P.createTransport(ctxWith([new Error('fetch failed')]), host, () => NOW)
  const v = await cold.poll()
  assert.equal(v.gateways.state, 'unknown')
  assert.match(v.gateways.reason, /fetch failed/)
  assert.notEqual(P.summarize(v).tone, 'bad')
})

// One gateway, many profiles: the gateway section is machine-wide and only the owner
// profile serves CI, so a poll asks one profile per connection, not all of them.
const PROFILES = ['alpha', 'beta', 'default', 'delta', 'echo', 'owner', 'golf', 'hotel', 'india', 'juliet']
const profileRoutes = (connectionId, profiles = PROFILES) =>
  profiles.map((p) => ({ connectionId, mode: 'remote', profile: p, targetProfile: p }))
function fleetCtx({ owner = 'owner', down = new Set() } = {}) {
  const calls = []
  return {
    calls, down,
    rest: async (path, opts) => {
      const profile = new URL(path, 'http://x').searchParams.get('profile')
      calls.push([opts.connectionId, profile])
      if (down.has(profile)) throw Object.assign(new Error('timed out'), { status: 0 })
      return profile === owner
        ? fleet({ profile })
        : fleet({ profile, ci: { state: 'unavailable', reason: 'owner_profile_mismatch', machines: [] } })
    },
    onDispose() {}, register() {}
  }
}
const calledSince = (ctx, n) => ctx.calls.slice(n).map(([c, p]) => `${c}/${p}`).sort()

test('one request per connection per poll once discovery has found the CI owner', async () => {
  let t = NOW
  const ctx = fleetCtx()
  const list = [...profileRoutes('mini'), ...profileRoutes('hub', ['default', 'owner', 'x'])]
  const transport = P.createTransport(ctx, { profileRoutes: async () => list }, () => t)
  let view = await transport.poll() // discovery: every route
  assert.equal(ctx.calls.length, 13)
  assert.equal(view.ci.state, 'ready')
  for (let i = 0; i < 4; i++) {
    t += P.POLL_OPEN_MS
    const n = ctx.calls.length
    view = await transport.poll()
    assert.deepEqual(calledSince(ctx, n), ['hub/owner', 'mini/owner'], 'exactly 1 request per connection')
    assert.equal(view.ci.state, 'ready')
    assert.equal(view.gateways.state, 'ready')
  }
})

test('the CI owner route leads over default; default leads while no profile serves CI', async () => {
  let t = NOW
  const ctx = fleetCtx({ owner: 'owner' })
  const transport = P.createTransport(ctx, { profileRoutes: async () => profileRoutes('mini') }, () => t)
  await transport.poll()
  t += P.POLL_OPEN_MS
  let n = ctx.calls.length
  await transport.poll()
  assert.deepEqual(calledSince(ctx, n), ['mini/owner'])
  // No owner among the profiles: one sweep, then `default` alone (no sweep every poll).
  const none = fleetCtx({ owner: 'nobody' })
  const plain = P.createTransport(none, { profileRoutes: async () => profileRoutes('mini') }, () => t)
  await plain.poll()
  assert.equal(none.calls.length, 10)
  t += P.POLL_OPEN_MS
  n = none.calls.length
  await plain.poll()
  assert.deepEqual(calledSince(none, n), ['mini/default'])
})

test('when the lead fails, the next poll asks the other profiles; a healthy one stands in', async () => {
  let t = NOW
  const ctx = fleetCtx()
  const transport = P.createTransport(ctx, { profileRoutes: async () => profileRoutes('mini') }, () => t)
  await transport.poll()
  t += P.POLL_OPEN_MS
  ctx.down.add('owner')
  let n = ctx.calls.length
  let view = await transport.poll() // the lead times out
  assert.deepEqual(calledSince(ctx, n), ['mini/owner'])
  assert.equal(view.ci.state, 'stale')
  t += P.POLL_OPEN_MS
  n = ctx.calls.length
  await transport.poll() // the others, plus the owner (its 15s backoff has passed)
  assert.deepEqual(calledSince(ctx, n), profileRoutes('mini').map((r) => `mini/${r.targetProfile}`).sort())
  t += P.POLL_OPEN_MS
  n = ctx.calls.length
  view = await transport.poll() // owner backing off (30s): `default` keeps gateways fresh
  assert.deepEqual(calledSince(ctx, n), ['mini/default'])
  assert.equal(view.gateways.state, 'ready')
  assert.equal(view.ci.state, 'stale')
  ctx.down.clear()
  t += P.POLL_OPEN_MS
  n = ctx.calls.length
  view = await transport.poll() // owner due again and back
  assert.deepEqual(calledSince(ctx, n), ['mini/default', 'mini/owner'])
  assert.equal(view.ci.state, 'ready')
  t += P.POLL_OPEN_MS
  n = ctx.calls.length
  await transport.poll()
  assert.deepEqual(calledSince(ctx, n), ['mini/owner'])
})

test('discovery sweeps every profile again after DISCOVERY_MS', async () => {
  assert.equal(P.DISCOVERY_MS, 300000)
  let t = NOW
  const ctx = fleetCtx()
  const transport = P.createTransport(ctx, { profileRoutes: async () => profileRoutes('mini') }, () => t)
  await transport.poll()
  const counts = []
  for (let elapsed = P.POLL_OPEN_MS; elapsed <= P.DISCOVERY_MS; elapsed += P.POLL_OPEN_MS) {
    t = NOW + elapsed
    const n = ctx.calls.length
    await transport.poll()
    counts.push(ctx.calls.length - n)
  }
  assert.deepEqual(counts.slice(0, -1), Array(counts.length - 1).fill(1))
  assert.equal(counts.at(-1), 10, 'the poll at DISCOVERY_MS sweeps all profiles')
})

test('a remounted chip keeps the transport: no new discovery sweep per mount', async () => {
  // The desktop status bar remounts contributions whenever its contribution list changes.
  const ctx = fleetCtx()
  const saved = host.profileRoutes
  host.profileRoutes = async () => profileRoutes('mini')
  try {
    let ui = await mount(React.createElement(P.Runners, { ctx }))
    await ui.close()
    assert.equal(ctx.calls.length, 10, 'first mount sweeps every profile')
    ui = await mount(React.createElement(P.Runners, { ctx }))
    await ui.close()
    assert.deepEqual(calledSince(ctx, 10), ['mini/owner'], 'remount asks the lead only')
    assert.equal(P.transportFor(ctx), P.transportFor(ctx))
  } finally {
    host.profileRoutes = saved
  }
})

test('polls every 15s open or closed, updates the open panel in place, stops while hidden', async () => {
  const later = fleet()
  later.gateways.machines[1].running = [{ board: 'default', task: 't_bbbb2222', title: 'New card', started_at: NOW / 1000 }]
  assert.equal(P.POLL_CLOSED_MS, 15000)
  assert.equal(P.POLL_OPEN_MS, 15000)
  // mount fetch, then the refetch on open, then the next poll brings a new card
  const ctx = ctxWith([fleet(), fleet(), later])
  const ui = await mount(React.createElement(P.Runners, { ctx }))
  try {
  const observer = () => ui.client.getQueryCache().find({ queryKey: ['runners', 'fleet'] }).observers[0]
  assert.equal(observer().options.refetchInterval, 15000, 'closed panel still polls')
  window.dispatchEvent(new window.Event('hermes-show-runners'))
  await ui.flush()
  const row = () => document.querySelector('[data-machine="worker-1"]')
  assert.ok(row(), 'panel open')
  const node = row()
  assert.equal(node.querySelectorAll('.is-busy').length, 0)
  await React.act(async () => ui.client.refetchQueries({ queryKey: ['runners', 'fleet'] }))
  await ui.flush()
  assert.equal(row(), node, 'same DOM row, updated in place')
  assert.equal(row().querySelectorAll('.is-busy').length, 1)
  const chip = document.querySelector('[data-slot="runners-chip"]')
  assert.match(chip.getAttribute('aria-label'), /hub 1\/4 · workers 1\/3/)
  assert.equal(chip.querySelectorAll('[data-pip-machine="worker-1"] .is-busy').length, 1, 'pips updated in place')
  assert.equal(ctx.calls.length, 3, 'one request per poll')
  // Hidden window: polling stops (no interval left running).
  Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
  await React.act(async () => document.dispatchEvent(new window.Event('visibilitychange')))
  await ui.flush()
  const q = ui.client.getQueryCache().find({ queryKey: ['runners', 'fleet'] })
  assert.equal(q.observers[0].options.refetchInterval, false)
  } finally {
    delete document.visibilityState
    await ui.close()
  }
})

test('status bar: per-machine slot pips; green with room, red when the pool is full', async () => {
  const v = fleet()
  const s = P.summarize(P.merge([{ route, body: v, at: NOW, failures: 0, nextAt: 0 }], NOW))
  // hub: 1 of 4; workers: worker-1 0 of 3 (the uncapped mini adds no capacity)
  assert.deepEqual(s.hub, { busy: 1, slots: 4 })
  assert.deepEqual(s.workers, { busy: 0, slots: 3 })
  assert.equal(P.meterTone(s.hub), 'ok')
  assert.equal(P.meterTone({ busy: 3, slots: 3 }), 'full')
  assert.equal(P.meterTone({ busy: 5, slots: 3 }), 'full')
  assert.equal(P.meterTone({ busy: 0, slots: 0 }), 'none')

  const full = fleet()
  full.gateways.machines[1].running = [1, 2, 3].map((i) => ({ board: 'default', task: `t_cccc000${i}`, title: 'x', started_at: NOW / 1000 }))
  const ui = await mount(React.createElement(P.Runners, { ctx: ctxWith([full]) }))
  try {
    const chip = document.querySelector('[data-slot="runners-chip"]')
    const hub = chip.querySelector('[data-pip-machine="hub"]'), workers = chip.querySelector('[data-pip-machine="worker-1"]')
    assert.equal(hub.querySelectorAll('.is-busy').length, 1)
    assert.equal(hub.querySelectorAll('.is-free').length, 3)
    assert.equal(workers.querySelectorAll('.is-lost').length, 3)
    assert.equal(chip.textContent, '', 'no Hub/Workers text labels')
    assert.equal(chip.title, 'hub: 1/4 busy · worker-1: 3/3 busy')
  } finally { await ui.close() }
})

test('draining: hover text carries the hold reason, holder and age; the row shows no reason text', async () => {
  const body = fleet()
  const held = { state: 'draining', reason: 'on hold: finishes running cards, takes no new ones', labels: [], pinned_only: false, slots: 3, slots_source: 'registry', running: [] }
  body.gateways.machines = [
    { name: 'spare', ...held, hold_reason: 'disk cleanup', hold_by: 'operator-a', hold_at: NOW - 5 * 60000 },
    { name: 'legacy', ...held, hold_reason: null, hold_by: 'operator-b', hold_at: NOW - 2 * 3600000 },
    { name: 'stale', ...held }
  ]
  const ui = await render(P.merge([{ route, body, at: NOW }], NOW))
  const spare = ui.container.querySelector('[data-machine="spare"]')
  const want = 'draining: disk cleanup (held by operator-a, 5m ago); finishes running cards, takes no new ones'
  const info = spare.querySelector(':scope > .hermes-fleet-info')
  assert.equal(info.textContent, 'i')
  assert.ok(info.dataset.tip.startsWith(want), info.dataset.tip)
  assert.doesNotMatch(spare.textContent, /disk cleanup|operator-a|on hold/)
  assert.equal(spare.querySelector('.hermes-fleet-drain-label')?.textContent, 'draining')
  assert.equal(spare.querySelector('.hermes-fleet-machine-name')?.textContent, 'spare')
  const css = (await import('node:fs')).readFileSync(new URL('../plugin.js', import.meta.url), 'utf8')
  assert.match(css, /\.hermes-fleet-drain-label\{flex-shrink:0/)
  assert.match(css, /max-width:min\(480px,calc\(100vw - 32px\)\);overflow-wrap:anywhere/)
  const slots = [...spare.querySelectorAll('.hermes-fleet-slot.is-draining')]
  assert.equal(slots.length, 3)
  for (const s of slots) assert.equal(s.title, want)
  const stale = ui.container.querySelector('[data-machine="stale"]')
  const unavailable = 'draining: reason unavailable (dashboard older than hold metadata)'
  assert.ok(stale.querySelector('.hermes-fleet-info').dataset.tip.startsWith(unavailable))
  for (const slot of stale.querySelectorAll('.hermes-fleet-slot.is-draining')) assert.ok(slot.title.startsWith(unavailable))
  assert.doesNotMatch(stale.querySelector('.hermes-fleet-info').dataset.tip, /no reason recorded|held by unknown/)
  const legacy = ui.container.querySelector('[data-machine="legacy"]')
  assert.ok(legacy.querySelector('.hermes-fleet-info').dataset.tip.startsWith('draining: no reason recorded (held by operator-b, 2h ago); finishes running cards, takes no new ones'))
  await ui.close()
})

test('stopped: a held machine that is off has no row and no slots, only a name in the off line', async () => {
  const body = fleet()
  const off = (name) => ({ name, state: 'stopped', reason: 'stopped: held, not seen for 500 min', labels: [], pinned_only: false, slots: 10, slots_source: 'registry', running: [], waiting: 8, ineligible: 3, load: { at: NOW - 500 * 60000, cpu_pct: 18 }, hold_reason: 'shutdown', hold_by: 'admin', hold_owner: 'session:s@hub', hold_at: NOW - 2 * 3600000 })
  const up = { name: 'worker-1', state: 'active', reason: null, labels: [], pinned_only: false, slots: 10, slots_source: 'registry', running: [] }
  body.gateways.machines = [up, off('worker-2'), off('worker-5')]
  const view = P.merge([{ route, body, at: NOW }], NOW)
  const s = P.summarize(view)
  assert.equal(s.gSlots, 10)
  assert.equal(s.workers.slots, 10)
  const ui = await render(view)
  try {
    assert.equal(ui.container.querySelectorAll('.hermes-fleet-row[data-machine="worker-2"], .hermes-fleet-row[data-machine="worker-5"]').length, 0)
    assert.ok(ui.container.querySelector('.hermes-fleet-row[data-machine="worker-1"]'))
    const note = ui.container.querySelector('.hermes-fleet-stopped')
    assert.equal(note.textContent, '2 off: worker-2, worker-5')
    assert.equal(note.querySelector('[data-machine="worker-5"]').title, 'stopped: shutdown (held by session:s@hub, 2h ago); machine is off (stopped: held, not seen for 500 min)')
    assert.equal(note.querySelectorAll('.hermes-fleet-slot').length, 0)
  } finally { await ui.close() }
  body.gateways.machines = [up]
  const none = await render(P.merge([{ route, body, at: NOW }], NOW))
  try { assert.equal(none.container.querySelector('.hermes-fleet-stopped'), null) } finally { await none.close() }
})

test('draining: hover names the hold_owner card/session, falling back to hold_by', async () => {
  const body = fleet()
  const held = { state: 'draining', reason: 'draining', labels: [], pinned_only: false, slots: 2, slots_source: 'registry', running: [] }
  body.gateways.machines = [
    { name: 'carded', ...held, hold_reason: 'roll', hold_by: 'admin', hold_owner: 'card:t_01a1ce31e56561be', hold_at: NOW - 60000 },
    { name: 'plain', ...held, hold_reason: 'roll', hold_by: 'admin', hold_owner: null, hold_at: NOW - 60000 }
  ]
  const ui = await render(P.merge([{ route, body, at: NOW }], NOW))
  const carded = ui.container.querySelector('[data-machine="carded"]')
  const want = 'draining: roll (held by card:t_01a1ce31e56561be, 1m ago); finishes running cards, takes no new ones'
  assert.ok(carded.querySelector(':scope > .hermes-fleet-info').dataset.tip.startsWith(want), carded.querySelector('.hermes-fleet-info').dataset.tip)
  for (const s of carded.querySelectorAll('.hermes-fleet-slot.is-draining')) assert.equal(s.title, want)
  const plain = ui.container.querySelector('[data-machine="plain"]')
  assert.ok(plain.querySelector('.hermes-fleet-info').dataset.tip.startsWith('draining: roll (held by admin, 1m ago)'))
  await ui.close()
})

test('gateway rows: one grid line each in a shared column grid, no Investigate control', async () => {
  const body = fleet()
  body.gateways.machines = body.gateways.machines.map((m) => ({ ...m, waiting: 1, ineligible: 2, load: { cpu_load1: 1, cpus: 8, mem_used_pct: 10, disk_free_gb: 5, disk_total_gb: 50, at: NOW, stale: false } }))
  const ui = await render(P.merge([{ route, body, at: NOW }], NOW))
  const rows = [...ui.container.querySelectorAll('.hermes-fleet-gw > .hermes-fleet-row')]
  assert.deepEqual(rows.map((r) => r.dataset.machine), ['hub', 'worker-1', 'mini'])
  for (const r of rows) {
    assert.deepEqual([...r.children].map((c) => c.className.split(' ')[0]),
      ['hermes-fleet-name', 'hermes-fleet-slots', 'hermes-fleet-cell', 'hermes-fleet-cell', 'hermes-fleet-cell', 'hermes-fleet-cell', 'hermes-fleet-cell', 'hermes-fleet-cell', 'hermes-fleet-count', 'hermes-fleet-info'])  // + Tok/slot
    assert.equal(r.children.length, ui.container.querySelectorAll('.hermes-fleet-cols > span').length, 'one cell per header column')
  }
  // Flags are hover text on the (i): a row with nothing to say keeps an empty, hidden placeholder so columns hold.
  assert.deepEqual(rows.map((r) => { const e = r.querySelector(':scope > .hermes-fleet-info'); return [e.dataset.tip || '', e.textContent, e.classList.contains('is-empty')] }),
    [['slots from local cap', 'i', false], ['', '', true], ['pinned only · slots not set · not seen for 30 min', 'i', false]])
  // CI rows keep their own layout outside the gateway grid.
  assert.equal(ui.container.querySelector('.hermes-fleet-gw [data-machine="ec2-gwgen"]'), null)
  assert.doesNotMatch(ui.container.textContent, /Investigat/)
  assert.equal(ui.container.querySelector('[data-investigate]'), null)
  // The shared columns come from the stylesheet: rows are subgrids of one gateway grid, on one grid row.
  const css = (await import('node:fs')).readFileSync(new URL('../plugin.js', import.meta.url), 'utf8')
  assert.match(css, /\n\.hermes-fleet-gw\{display:grid;/)
  assert.match(css, /\.hermes-fleet-info:hover::after,[^{]*\{content:attr\(data-tip\)/)
  assert.match(css, /scrollbarGutter: 'stable'/)
  assert.match(css, /\n\.hermes-fleet-gw>\.hermes-fleet-row,\.hermes-fleet-cols\{[^}]*grid-template-columns:subgrid[^}]*white-space:nowrap/)
  assert.match(css, /\n\.hermes-fleet-gw>\.hermes-fleet-row>\*\{grid-row:1\}/)
  await ui.close()
})

test('the chip opens the runners popover, whatever other panels are loaded', async () => {
  const ctx = ctxWith([fleet(), fleet(), fleet()])
  const ui = await mount(React.createElement(P.Runners, { ctx }))
  let shown = 0
  const onLanes = () => { shown += 1 }
  window.addEventListener('hermes-show-lanes', onLanes)
  try {
    await ui.click(document.querySelector('[data-slot="runners-chip"]'))
    assert.ok(document.querySelector('[data-machine="worker-1"]'), 'runners popover is open')
    assert.equal(shown, 0, 'the runners chip never opens the lanes panel')
  } finally {
    window.removeEventListener('hermes-show-lanes', onLanes)
    await ui.close()
  }
})

// ── chip pip model ────────────────────────────────────────────────────────────
const pipMachine = (name, slots, busy, state = 'active', extra = {}) => ({
  name, slots, state, labels: [], running: Array.from({ length: busy }, () => ({})), ...extra
})
const pipView = (...machines) => ({ gateways: { state: 'ready', machines }, ci: { state: 'not_configured', machines: [] } })

test('pips: quiet, busy, full pools, throttle at the end, hub first and stopped excluded', () => {
  const view = pipView(pipMachine('z', 6, 0), pipMachine('a', 6, 3), pipMachine('central', 8, 1, 'active', { labels: ['machine:hub'] }), pipMachine('off', 6, 0, 'stopped'), pipMachine('uncapped', null, 0))
  const groups = P.pipGroups(view, null, NOW)
  assert.deepEqual(groups.map((g) => g.name), ['central', 'a', 'z'])
  assert.deepEqual(groups[0].pips, ['busy', ...Array(7).fill('free')])
  assert.deepEqual(groups[1].pips, [...Array(3).fill('busy'), ...Array(3).fill('free')])
  assert.deepEqual(groups[2].pips, Array(6).fill('free'))
  const full = pipView(pipMachine('hub', 8, 5), pipMachine('a', 6, 6), pipMachine('b', 6, 6))
  const throttle = { slots: { gateways: { hub: { allowed: 5 } } } }
  const p = P.pipGroups(full, throttle, NOW)
  assert.deepEqual(p[0].pips, [...Array(5).fill('lost'), ...Array(3).fill('thr')])
  assert.deepEqual(p[1].pips, Array(6).fill('lost'))
  assert.equal(p[0].lost, false, 'full is red but not a lost-machine outline')
  assert.equal(p[0].title, 'hub: 5/8 busy, 3 throttled')
})

test('pips: draining blue and leaving outlines; lost red with group outline flag and tooltips', () => {
  const p = P.pipGroups(pipView(pipMachine('drain', 6, 2, 'draining'), pipMachine('lost', 6, 3, 'lost')), null, NOW)
  assert.deepEqual(p[0].pips, ['drain', 'drain', ...Array(4).fill('gone')])
  assert.equal(p[0].title, 'drain: draining, 2 cards finishing')
  assert.deepEqual(p[1].pips, [...Array(3).fill('lost'), ...Array(3).fill('free')])
  assert.equal(p[1].lost, true)
  assert.equal(p[1].title, 'lost: lost, holding 3 cards')
})

test('pips: launching uses age / typical, fallback, clamp, and never adds scheduling capacity', async () => {
  const view = pipView(pipMachine('hub', 8, 5), pipMachine('worker-a3', 6, 0, 'launching', { launched_at: NOW / 1000 - 14 * 60 }))
  const group = () => P.pipGroups(view, null, NOW)[1]
  assert.deepEqual(group().pips, ['booted', 'booted', ...Array(4).fill('boot')])
  assert.equal(group().title, 'worker-a3: launching, 14 min in (ready in ~20)')
  assert.equal(P.summarize(view).gSlots, 8)
  assert.deepEqual(P.summarize(view).workers, { busy: 0, slots: 0 })
  view.gateways.launch_ready_s = 28 * 60
  assert.equal(group().pips.filter((p) => p === 'booted').length, 3)
  view.gateways.launch_ready_s = 5 * 60
  assert.deepEqual(group().pips, Array(6).fill('booted'))
  view.gateways.machines[1].launched_at = NOW / 1000 + 60
  assert.deepEqual(group().pips, Array(6).fill('boot'))
  const ui = await render(view)
  try { assert.ok(ui.container.querySelector('[data-machine="worker-a3"]'), 'panel tolerates launching rows') } finally { await ui.close() }
})

test('pips: enrolling registry holds keep amber boot progress until ready, not draining', () => {
  const m = pipMachine('worker-a3', 6, 0, 'draining', {
    hold_reason: 'worker-scaler: enrolling i-example: readiness pending', launched_at: NOW / 1000 - 600
  })
  const view = pipView(m)
  view.gateways.launch_ready_s = 1200
  const g = P.pipGroups(view, null, NOW)[0]
  assert.equal(g.state, 'launching')
  assert.deepEqual(g.pips, [...Array(3).fill('booted'), ...Array(3).fill('boot')])
  assert.equal(g.title, 'worker-a3: launching, 10 min in (ready in ~10)')
  assert.equal(P.summarize(view).workers.slots, 0)
  m.state = 'active'
  delete m.hold_reason
  assert.deepEqual(P.pipGroups(view, null, NOW)[0].pips, Array(6).fill('free'))
})

test('pips: merge retains launching rows and median from the ledger host, no registered duplicates', () => {
  const ledger = fleet()
  ledger.gateways.events = [ev(1, 'worker-a3', 'launched')]
  ledger.gateways.launch_ready_s = 1200
  ledger.gateways.machines.push(pipMachine('worker-a3', 6, 0, 'launching', { launched_at: NOW / 1000 - 60 }))
  const other = fleet()
  let v = P.merge([{ route, body: ledger, at: NOW - 1 }, { route, body: other, at: NOW }], NOW)
  assert.equal(v.gateways.launch_ready_s, 1200)
  assert.equal(P.pipGroups(v, null, NOW).at(-1).state, 'launching')
  other.gateways.machines.push(pipMachine('worker-a3', 6, 0))
  v = P.merge([{ route, body: ledger, at: NOW - 1 }, { route, body: other, at: NOW }], NOW)
  assert.equal(v.gateways.machines.filter((m) => m.name === 'worker-a3').length, 1)
  assert.equal(v.gateways.machines.at(-1).state, 'active')
  // The freshest registry can arrive from a host without the scaler ledger.
  other.gateways.machines.at(-1).state = 'draining'
  other.gateways.machines.at(-1).hold_reason = 'worker-scaler: enrolling i-example: readiness pending'
  v = P.merge([{ route, body: ledger, at: NOW - 1 }, { route, body: other, at: NOW }], NOW)
  const enrolling = P.pipGroups(v, null, NOW).at(-1)
  assert.equal(enrolling.state, 'launching')
  assert.deepEqual(enrolling.pips, Array(6).fill('booted'))
  assert.match(enrolling.title, /launching, 60 min in/)
})

// ── worker lifecycle timeline (gateways.events) ──────────────────────────────────────────────────
const NOW_S = NOW / 1000
const H = 3600
const ev = (hoursAgo, worker, kind, reason = `${kind} reason`) => ({ at: NOW_S - hoursAgo * H, worker, kind, reason, instance: null })
const withEvents = (events, over = {}) => {
  const body = fleet()
  body.gateways = { ...body.gateways, events, events_since: NOW_S - 24 * H, ...over }
  return P.merge([{ route, body, at: NOW }], NOW)
}
const tickEls = (ui) => [...ui.container.querySelectorAll('.hermes-fleet-tl-track > .hermes-fleet-tl-tick')]

test('timeline: one tick per event, four kinds with four distinct colours, each with an accessible label', async () => {
  const ui = await render(withEvents([
    ev(20, 'worker-1', 'launched'), ev(15, 'worker-1', 'ready'), ev(10, 'worker-1', 'draining'), ev(5, 'worker-1', 'removed')]))
  try {
    const ticks = tickEls(ui)
    assert.deepEqual(ticks.map((t) => t.dataset.kind), ['launched', 'ready', 'draining', 'removed'])
    assert.deepEqual(ticks.map((t) => [...t.classList].filter((c) => c.startsWith('is-'))), [['is-launched'], ['is-ready'], ['is-draining'], ['is-removed']])
    for (const t of ticks) {
      assert.equal(t.getAttribute('role'), 'img')
      assert.equal(t.getAttribute('aria-label'), t.title)
      assert.ok(t.title.includes('worker-1'))
    }
    const timeline = ui.container.querySelector('.hermes-fleet-timeline')
    assert.equal(timeline.getAttribute('role'), 'group')
    assert.equal(timeline.getAttribute('aria-label'), 'Worker lifecycle, last 24 hours: 4 events')
    // Under the machine grid, above the "N off" line and the CI section.
    assert.equal(timeline.previousElementSibling, ui.container.querySelector('.hermes-fleet-gw'))
  } finally { await ui.close() }
  // The plugin's stylesheet: yellow launched, green ready, blue draining, red removed.
  let style
  const before = new Set(document.head.querySelectorAll('style'))
  const disposers = []
  P.default.register({ register() {}, onDispose: (f) => disposers.push(f) })
  try {
    style = [...document.head.querySelectorAll('style')].find((s) => !before.has(s)).textContent
  } finally { disposers.forEach((f) => f()) }
  const colour = (kind) => style.match(new RegExp(`\\.hermes-fleet-timeline \\.is-${kind}\\{background:(#[0-9a-f]{6})\\}`))[1]
  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
  const hues = Object.fromEntries(Object.keys(P.EVENT_WORDS).map((k) => [k, rgb(colour(k))]))
  assert.equal(new Set(Object.values(hues).map(String)).size, 4)
  const [yr, yg, yb] = hues.launched
  assert.ok(yr > 180 && yg > 140 && yb < 90, 'launched is yellow')
  assert.ok(hues.ready[1] > hues.ready[0] && hues.ready[1] > hues.ready[2], 'ready is green')
  assert.ok(hues.draining[2] > hues.draining[0] && hues.draining[2] > hues.draining[1], 'draining is blue')
  assert.ok(hues.removed[0] > 2 * hues.removed[1] && hues.removed[0] > 2 * hues.removed[2], 'removed is red')
})

test('timeline: tick position is proportional to time in the last 24 h; events outside it are not drawn', async () => {
  assert.equal(P.timelineX(NOW - 24 * H * 1000, NOW), 0)
  assert.equal(P.timelineX(NOW - 6 * H * 1000, NOW), 0.75)
  assert.equal(P.timelineX(NOW, NOW), 1)
  assert.equal(P.timelineX(NOW - 24 * H * 1000 - 1, NOW), null)
  assert.equal(P.timelineX(NOW + 1, NOW), null)
  const ui = await render(withEvents([
    ev(25, 'worker-9', 'launched'), ev(24, 'worker-1', 'launched'), ev(18, 'worker-1', 'ready'), ev(12, 'worker-1', 'draining'),
    ev(0, 'worker-1', 'removed'), ev(-1, 'worker-9', 'ready'),
    { at: 'soon', worker: 'worker-9', kind: 'ready', reason: 'x' }, { at: NOW_S - H, worker: 'worker-9', kind: 'rebooted', reason: 'x' },
    { at: NOW_S - H, worker: '', kind: 'ready', reason: 'x' }, null]))
  try {
    const ticks = tickEls(ui)
    assert.ok(ticks.every((t) => t.style.left.endsWith('%')))
    assert.deepEqual(ticks.map((t) => parseFloat(t.style.left)), [0, 25, 50, 100])  // oldest at the left edge, now at the right
    assert.ok(ticks.every((t) => t.dataset.worker === 'worker-1'))
    assert.equal(ui.container.querySelector('.hermes-fleet-timeline').dataset.timeline, '4')
    // Hour labels: local round hours every 6 h, at their place on the same axis.
    const labels = [...ui.container.querySelectorAll('.hermes-fleet-tl-axis > span')]
    const axis = P.timelineAxis(NOW)
    assert.ok(axis.length >= 3 && axis.length <= 5)
    assert.deepEqual(labels.map((l) => l.textContent), axis.map((a) => a.label))
    for (const [i, a] of axis.entries()) {
      const at = new Date(NOW - (1 - a.x) * 24 * H * 1000)
      assert.equal(at.getMinutes(), 0)
      assert.equal(at.getHours() % 6, 0)
      assert.match(a.label, /^\d\d:00$/)
      assert.ok(Math.abs(parseFloat(labels[i].style.left) - a.x * 100) < 0.001)
    }
  } finally { await ui.close() }
})

test('timeline: hover text is worker id, what happened in words, the reason and the local time', async () => {
  const at = NOW_S - 3 * H - 19 * 60
  const local = new Date(at * 1000)
  const hhmm = `${String(local.getHours()).padStart(2, '0')}:${String(local.getMinutes()).padStart(2, '0')}`
  assert.equal(P.clockTime(at * 1000), hhmm)
  assert.equal(P.eventTip({ at, worker: 'worker-3', kind: 'draining', reason: 'consolidating spare capacity (4 cards finishing)' }),
    `worker-3 · draining · consolidating spare capacity (4 cards finishing) · ${hhmm}`)
  assert.equal(P.eventTip({ at, worker: 'worker-3', kind: 'ready', reason: 'bootstrap finished' }), `worker-3 · ready for scheduling · bootstrap finished · ${hhmm}`)
  assert.equal(P.eventTip({ at, worker: 'worker-3', kind: 'launched', reason: '6 cards waiting' }), `worker-3 · launched · 6 cards waiting · ${hhmm}`)
  assert.equal(P.eventTip({ at, worker: 'worker-3', kind: 'removed', reason: '' }), `worker-3 · removed · ${hhmm}`)
  const ui = await render(withEvents([{ at, worker: 'worker-3', kind: 'draining', reason: 'consolidating spare capacity (4 cards finishing)', instance: 'i-0abc' }]))
  try {
    const [tick] = tickEls(ui)
    assert.equal(tick.title, `worker-3 · draining · consolidating spare capacity (4 cards finishing) · ${hhmm}`)
    assert.equal(tick.getAttribute('aria-label'), tick.title)
  } finally { await ui.close() }
})

test('timeline: overlapping workers stack in lanes, one worker stays in one lane, every tick keeps its own place', async () => {
  const events = [
    ev(20, 'worker-1', 'launched'), ev(20, 'worker-2', 'launched'), ev(20, 'worker-3', 'launched'),
    ev(19.98, 'worker-1', 'ready'), ev(19.98, 'worker-2', 'ready'),  // ~1 min later: under the gap
    ev(8, 'worker-1', 'draining'), ev(8, 'worker-2', 'draining'), ev(2, 'worker-1', 'removed'), ev(1, 'worker-3', 'removed')]
  const ticks = P.timelineTicks(events, NOW)
  assert.equal(ticks.length, events.length)
  const laneOf = (w) => [...new Set(ticks.filter((t) => t.worker === w).map((t) => t.lane))]
  assert.deepEqual([laneOf('worker-1'), laneOf('worker-2'), laneOf('worker-3')], [[0], [1], [2]])
  // Same lane: never closer than the gap, the true time kept beside the drawn place.
  for (const a of ticks) for (const b of ticks) {
    if (a === b || a.lane !== b.lane) continue
    assert.ok(Math.abs(a.pos - b.pos) >= P.TIMELINE_GAP - 1e-9, `${a.key} covers ${b.key}`)
  }
  const ready = ticks.find((t) => t.worker === 'worker-1' && t.kind === 'ready')
  const launched = ticks.find((t) => t.worker === 'worker-1' && t.kind === 'launched')
  assert.ok(Math.abs(ready.pos - (launched.pos + P.TIMELINE_GAP)) < 1e-9 && ready.x < ready.pos)
  assert.equal(ticks.find((t) => t.kind === 'draining').pos, ticks.find((t) => t.kind === 'draining').x)  // spaced: untouched
  // Deterministic: the order the events arrive in does not matter.
  const plain = (list) => list.map(({ key, ...t }) => t)
  assert.deepEqual(plain(P.timelineTicks([...events].reverse(), NOW)).sort((a, b) => a.at - b.at || a.worker.localeCompare(b.worker)),
    plain(ticks).sort((a, b) => a.at - b.at || a.worker.localeCompare(b.worker)))
  assert.deepEqual(P.timelineTicks(events, NOW), ticks)
  // More simultaneous workers than lanes: at most TIMELINE_LANES lanes, and still no tick on top of another.
  const crowd = P.timelineTicks(Array.from({ length: 7 }, (_, i) => ev(6, `worker-${i}`, 'launched')), NOW)
  assert.deepEqual([...new Set(crowd.map((t) => t.lane))].sort(), [0, 1, 2, 3])
  assert.equal(new Set(crowd.map((t) => `${t.lane}:${t.pos.toFixed(6)}`)).size, 7)
  // At the right edge the crowd is pulled back inside the axis, not pushed past "now".
  const edge = P.timelineTicks([ev(0, 'worker-1', 'launched'), ev(0, 'worker-1', 'ready'), ev(0, 'worker-1', 'draining')], NOW)
  assert.deepEqual(edge.map((t) => +t.pos.toFixed(6)), [1 - 2 * P.TIMELINE_GAP, 1 - P.TIMELINE_GAP, 1].map((v) => +v.toFixed(6)))
  const ui = await render(withEvents(events))
  try {
    const els = tickEls(ui)
    assert.equal(els.length, events.length)
    const lane = (w) => [...new Set(els.filter((t) => t.dataset.worker === w).map((t) => t.dataset.lane))]
    assert.deepEqual([lane('worker-1'), lane('worker-2'), lane('worker-3')], [['0'], ['1'], ['2']])
    assert.equal(new Set(els.map((t) => t.style.top)).size, 3)
    assert.equal(new Set(els.map((t) => `${t.style.left}/${t.style.top}`)).size, events.length)
    assert.equal(ui.container.querySelector('.hermes-fleet-tl-track').dataset.lanes, '3')
  } finally { await ui.close() }
})

test('timeline: no events, or an older gateway without the field, draws nothing; with events there is a legend', async () => {
  for (const view of [
    P.merge([{ route, body: fleet(), at: NOW }], NOW),                 // older backend: field absent
    withEvents([]),                                                     // no events
    withEvents([ev(30, 'worker-1', 'launched')]),                       // none in the window
    withEvents(null), withEvents('nope')]) {
    const ui = await render(view)
    try {
      assert.equal(ui.container.querySelector('.hermes-fleet-timeline'), null)
      assert.equal(ui.container.querySelector('[class*="hermes-fleet-tl-"]'), null)
      assert.doesNotMatch(ui.container.textContent, /ready for scheduling/)
      assert.ok(ui.container.querySelector('[data-machine="hub"]'))  // the rest of the panel is unaffected
    } finally { await ui.close() }
  }
  const ui = await render(withEvents([ev(3, 'worker-1', 'ready')]))
  try {
    const legend = ui.container.querySelector('.hermes-fleet-timeline .hermes-fleet-tl-legend')
    assert.deepEqual([...legend.children].map((c) => [c.dataset.kind, c.textContent, c.querySelector('i').className]), [
      ['launched', 'launched', 'hermes-fleet-tl-key is-launched'], ['ready', 'ready for scheduling', 'hermes-fleet-tl-key is-ready'],
      ['draining', 'draining', 'hermes-fleet-tl-key is-draining'], ['removed', 'removed', 'hermes-fleet-tl-key is-removed']])
    assert.equal(tickEls(ui).length, 1)  // legend keys are not ticks
  } finally { await ui.close() }
})

test('timeline: events come from the gateway that has them, whichever gateway answered last', () => {
  const hub = fleet()
  hub.gateways = { ...hub.gateways, events: [ev(2, 'worker-1', 'ready')], events_since: NOW_S - 24 * H }
  const mac = fleet()
  mac.gateways = { ...mac.gateways, events: [], events_since: NOW_S - 24 * H + 5 }
  const other = { ...route, connectionId: 'other' }
  const view = P.merge([{ route, body: hub, at: NOW - 10000 }, { route: other, body: mac, at: NOW }], NOW)
  assert.equal(view.gateways.connectionId, 'other')  // the freshest ready section still leads
  assert.deepEqual(view.gateways.events, hub.gateways.events)
  assert.equal(view.gateways.events_since, NOW_S - 24 * H)
  const none = P.merge([{ route: other, body: mac, at: NOW }], NOW)
  assert.deepEqual(none.gateways.events, [])
  assert.equal('events' in P.merge([{ route, body: fleet(), at: NOW }], NOW).gateways, false)
})
