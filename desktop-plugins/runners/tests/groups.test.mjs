// Approved CI composition: self-hosted capacity and elastic activity stay separate.
import test from 'node:test'
import assert from 'node:assert/strict'
import { React, loadPlugin, mount, host, route } from './sdk-mock.mjs'

const P = await loadPlugin()
export const NOW = Date.parse('2026-11-20T04:23:25Z')
export function fixture(state = 'now') {
  const machine = (name, os, states, extra = {}) => ({ name, os, provider: null, labels: ['build'], warnings: [],
    slots: states.map((state, i) => ({ id: `${name}-${i}`, runner: `${name}-${i}`, state, labels: ['build'] })), ...extra })
  const machines = [machine('mini', 'mac', ['busy', 'busy', 'idle', 'idle', ...Array(5).fill('offline')]),
    machine('canary', 'linux', ['idle']), machine('unseen', 'linux', Array(5).fill('unknown')),
    machine('cloud-small', 'linux', Array(7).fill('busy'), { provider: 'blacksmith', size: '2 vCPU' }),
    machine('cloud-large', 'linux', Array(77).fill('busy'), { provider: 'blacksmith', size: '4 vCPU' }),
    machine('cloud-history', 'linux', Array(675).fill('offline'), { provider: 'blacksmith' }),
    machine('ns-mac', 'mac', ['idle'], { provider: 'namespace' })]
  if (state === 'quiet') for (const m of machines) for (const s of m.slots) if (s.state === 'busy') s.state = 'idle'
  if (state === 'full') {
    machines[0].slots.forEach((s) => { if (s.state === 'idle') s.state = 'busy' })
    machines.at(-1).slots[0].state = 'busy'
  }
  if (state === 'trouble') { machines[0].slots[2].warning = true; machines[1].slots[0].state = 'offline' }
  return { at: NOW, gateways: { state: 'ready', machines: [] }, ci: { state: 'ready',
    observed_at: new Date(NOW - (state === 'stale' ? 14 * 60000 : 0)).toISOString(), machines, hidden_deregistered: 941 } }
}

for (const state of ['now', 'quiet', 'full', 'trouble', 'stale']) test(`CI board state: ${state}`, async () => {
  const view = fixture(state), model = P.ciModel(view.ci, NOW)
  const ui = await mount(React.createElement(P.CiChip, { model }))
  try {
    assert.equal(ui.container.querySelector('.hermes-ci-label').textContent, 'CI')
    assert.equal(ui.container.querySelectorAll('.hermes-ci-pip').length, 10)
    assert.deepEqual([...ui.container.querySelectorAll('[data-ci-os]')].map((n) => n.dataset.ciOs), ['mac', 'linux'])
    const busy = ui.container.querySelectorAll('.hermes-ci-pip.is-busy')
    const full = ui.container.querySelectorAll('.hermes-ci-pip.is-full')
    assert.equal(busy.length, state === 'quiet' || state === 'full' ? 0 : 2)
    assert.equal(full.length, state === 'full' ? 4 : 0)
    assert.equal(ui.container.querySelectorAll('.hermes-ci-number').length, state === 'quiet' ? 0 : state === 'full' ? 2 : 1)
    assert.equal(ui.container.querySelector('.hermes-ci-chip').classList.contains('is-stale'), state === 'stale')
    assert.equal(ui.container.querySelector('[data-ci-tone]')?.dataset.ciTone, state === 'trouble' ? 'bad' : state === 'stale' ? 'warn' : undefined)
    assert.equal(ui.container.querySelectorAll('.hermes-ci-pip.is-warn').length, state === 'trouble' ? 1 : 0)
    assert.match(model.title, /Mac: .*self-hosted busy.*offline.*\nLinux:/)
    if (state === 'stale') assert.match(model.title, /CI data 14 min old/)
    if (state === 'now') assert.match(model.title, /Blacksmith 84 running/)
  } finally { await ui.close() }
})

test('CI panel: Mac then Linux, online rows, provider sizes, offline and hidden history', async () => {
  const ui = await mount(React.createElement(P.CiPanel, { view: fixture(), now: NOW }))
  try {
    assert.match(ui.container.querySelector('.hermes-ci-head').textContent, /^CI \(GitHub Actions\)86 running$/)
    assert.deepEqual([...ui.container.querySelectorAll('[data-ci-section]')].map((e) => e.dataset.ciSection), ['mac', 'linux'])
    assert.equal(ui.container.querySelectorAll('[data-machine]').length, 5)
    assert.match(ui.container.querySelector('[data-ci-provider="blacksmith"]').textContent, /7 × 2 vCPU · 77 × 4 vCPU/)
    assert.match(ui.container.textContent, /5 offline: mini-4, mini-5, mini-6, mini-7, mini-8/)
    assert.match(ui.container.textContent, /5 not observed/)
    assert.match(ui.container.textContent, /676 finished elastic registrations and 941 deregistered runners hidden/)
  } finally { await ui.close() }
})

test('warning ordering and stale/error sources do not masquerade as fresh', () => {
  const view = fixture('trouble'), model = P.ciModel(view.ci, NOW)
  assert.equal(model.sections[0].fixed[0].runner, 'mini-2')
  view.ci.state = 'stale'
  assert.equal(P.ciModel(view.ci, NOW).stale, true)
  assert.equal(P.ciModel(view.ci, NOW).tone, 'bad', 'readiness warning takes precedence')
  assert.equal(P.ciModel({ state: 'unknown', machines: [] }, NOW).tone, 'warn')
})

test('CI chip opens its own panel by click and Show CI; both chips share one fleet request', async () => {
  let calls = 0
  const ctx = { rest: async () => { calls++; return { contract_version: 1, ...fixture() } } }
  const ui = await mount(React.createElement(React.Fragment, null,
    React.createElement(P.Runners, { ctx }), React.createElement(P.CI, { ctx })))
  try {
    await ui.flush()
    assert.equal(calls, 1, 'same query key deduplicates both chips')
    assert.equal(P.transportFor(ctx), P.transportFor(ctx))
    await ui.click(ui.container.querySelector('[data-slot="ci-chip"]'))
    assert.match(document.body.querySelector('[aria-label="CI (GitHub Actions)"]').textContent, /CI \(GitHub Actions\)/)
    assert.ok(document.body.querySelector('[aria-label="Runners"]') === null)
  } finally { await ui.close() }
  const commandUI = await mount(React.createElement(P.CI, { ctx }))
  try {
    await React.act(async () => window.dispatchEvent(new window.Event('hermes-show-ci')))
    assert.ok(document.body.querySelector('[aria-label="CI (GitHub Actions)"]'))
  } finally { await commandUI.close() }
})

test('CI panel switches one repair popover between slots and can close it', async () => {
  const view = fixture()
  view.ci.route = { connectionId: 'example', targetProfile: 'example' }
  const prepared = []
  const repairClient = { prepare: async (_route, slot) => { prepared.push(slot.id); return { enabled: false, reason: 'No incident' } } }
  const ui = await mount(React.createElement(P.CiPanel, { view, now: NOW, repairClient }))
  try {
    await ui.click(ui.container.querySelector('[data-runner-slot="mini-0"]'))
    assert.equal(ui.container.querySelectorAll('[role="dialog"]').length, 1)
    await ui.click(ui.container.querySelector('[data-runner-slot="mini-1"]'))
    assert.equal(ui.container.querySelectorAll('[role="dialog"]').length, 1)
    assert.equal(ui.container.querySelector('[role="dialog"]').getAttribute('aria-label'), 'Actions for mini-1')
    assert.deepEqual(prepared, ['mini-0', 'mini-1'])
    await ui.click(ui.container.querySelector('[aria-label="Close CI runner actions"]'))
    assert.ok(ui.container.querySelector('[role="dialog"]') === null)
  } finally { await ui.close() }
})

test('CI tokens preserve the approved round pips, readiness rings and stale opacity', () => {
  assert.match(P.CSS, /hermes-ci-chip\.is-stale\{opacity:\.45\}/)
  assert.match(P.CSS, /hermes-ci-pip\{width:4px;height:4px;border-radius:50%/)
  assert.match(P.CSS, /hermes-ci-pip\.is-warn\{background:transparent;box-shadow:inset 0 0 0 1px #d64545/)
  const view = fixture()
  view.ci.machines[2].warnings = ['unseen: a check failed']
  assert.equal(P.ciModel(view.ci, NOW).tone, 'bad')
  assert.match(P.ciModel(view.ci, NOW).title, /unseen: a check failed/)
  assert.equal(P.ciModel({ state: 'not_configured', machines: [] }, NOW).stale, false)
})

test('registration and commands keep CI separate from gateway workers', () => {
  const contributions = []
  const dispose = []
  P.default.register({ register: (c) => contributions.push(c), onDispose: (fn) => dispose.push(fn) })
  assert.equal(contributions.find((c) => c.id === 'chip').order, 6)
  assert.equal(contributions.find((c) => c.id === 'ci-chip').order, 7)
  assert.equal(contributions.find((c) => c.id === 'show-ci').data.label, 'Show CI')
  assert.ok(!contributions.find((c) => c.id === 'show-runners').data.keywords.includes('ci'))
  dispose.forEach((fn) => fn())
})
