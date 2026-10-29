// Fleet panel: one test per data path (registry, live leases, CI runners, unreachable),
// plus in-place refresh while open and backoff when a gateway is down.
import test from 'node:test'
import assert from 'node:assert/strict'
import { React, loadPlugin, mount, host, route } from './sdk-mock.mjs'

const P = await loadPlugin()
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
  assert.match(mini.textContent, /pinned only · slots not set · not seen for 30 min/)
  assert.equal(mini.querySelectorAll('.is-lost').length, 0)
  assert.match(ui.container.textContent, /1 \/ 7 slots busy/)
  await ui.close()
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
  const hub = ui.container.querySelector('[data-machine="hub"] .hermes-fleet-load')
  assert.equal(hub.textContent, 'CPU  50%RAM  95%Disk  57 GB free')
  assert.equal(hub.querySelectorAll('.hermes-fleet-bar').length, 3)
  assert.equal(hub.querySelectorAll('.is-high').length, 1, 'RAM ≥ 90% is highlighted')
  const w1 = ui.container.querySelector('[data-machine="worker-1"] .hermes-fleet-load')
  assert.ok(w1.classList.contains('is-stale'))
  assert.match(w1.textContent, /CPU  13%\(10m old\)/)
  assert.equal(ui.container.querySelector('[data-machine="mini"] .hermes-fleet-load').textContent, 'load not reported')
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
  assert.match(document.querySelector('[data-slot="runners-chip"]').textContent, /Runners 3\/12/)
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
