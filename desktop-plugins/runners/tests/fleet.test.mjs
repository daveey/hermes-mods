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
    ['Machine', 'Slots', 'Waiting', "Can't run", 'CPU', 'RAM', 'Disk free', 'Busy', ''])
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
  // CI: the collector's probe has load (no core count) and disk; machines without a probe show no meter line.
  assert.match(ui.container.querySelector('[data-machine="mac-mini"] .hermes-fleet-load').textContent, /load\s+30.35.*24 GB free/)
  assert.equal(ui.container.querySelector('[data-machine="ec2-gwgen"] .hermes-fleet-load'), null)
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

test('CI runners: one slot per runner, failed checks are warnings, history hidden', async () => {
  const ui = await render(P.merge([{ route, body: fleet(), at: NOW }], NOW))
  const gen = ui.container.querySelector('[data-machine="ec2-gwgen"]')
  assert.deepEqual([...gen.querySelectorAll('.hermes-fleet-slot')].map((e) => e.className.split('is-')[1]), ['busy', 'idle'])
  const mini = ui.container.querySelector('[data-machine="mac-mini"]')
  assert.match(mini.textContent, /⚠ mac-mini: tool:xcode failed/)
  assert.equal(mini.querySelectorAll('.is-lost, .is-busy').length, 0)
  assert.match(ui.container.querySelector('[data-machine="metta4"]').textContent, /2 offline/)
  assert.match(ui.container.textContent, /1 \/ 5 busy · 2 offline/)
  assert.match(ui.container.textContent, /220 deregistered runners \(history\) hidden/)
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
  assert.equal(chip.querySelector('[data-meter="workers"] .hermes-fleet-chipbar>span').style.width, '33%', 'bar updated in place')
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

test('status bar: one meter for the hub, one for worker gateways; track = capacity, fill = scheduled, red when full', async () => {
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
    const hub = chip.querySelector('[data-meter="hub"]'), workers = chip.querySelector('[data-meter="workers"]')
    assert.equal(hub.dataset.tone, 'ok')
    assert.ok(hub.querySelector('.hermes-fleet-chipbar.is-ok'))
    assert.equal(hub.querySelector('.hermes-fleet-chipbar>span').style.width, '25%')
    assert.equal(workers.dataset.tone, 'full')
    assert.ok(workers.querySelector('.hermes-fleet-chipbar.is-full'))
    assert.equal(workers.querySelector('.hermes-fleet-chipbar>span').style.width, '100%')
    assert.match(chip.textContent, /Hub.*Workers/)
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
      ['hermes-fleet-name', 'hermes-fleet-slots', 'hermes-fleet-cell', 'hermes-fleet-cell', 'hermes-fleet-cell', 'hermes-fleet-cell', 'hermes-fleet-cell', 'hermes-fleet-count', 'hermes-fleet-info'])
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
