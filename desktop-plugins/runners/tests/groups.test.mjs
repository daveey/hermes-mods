// CI machines grouped by category: collapsed group rows with capacity and health, expand to see machines.
import test from 'node:test'
import assert from 'node:assert/strict'
import { React, loadPlugin, mount, route } from './sdk-mock.mjs'

const P = await loadPlugin()
const NOW = Date.parse('2026-10-29T17:00:00Z')
const slot = (runner, state, labels = []) => ({ runner, state, labels })
const ci = [
  { name: 'davids-mac-mini', category: 'Macs', ephemeral: false, labels: [], warnings: ['davids-mac-mini: tool:xcode failed'], slots: [slot('davids-mac-mini', 'idle')] },
  { name: 'ec2-gwgen', category: 'EC2 Linux', ephemeral: false, labels: [], warnings: [], slots: [slot('ec2-gwgen-1', 'busy'), slot('ec2-gwgen-2', 'idle')] },
  { name: 'ec2-gwhub', ephemeral: false, labels: [], warnings: [], slots: [slot('ec2-gwhub', 'busy', ['self-hosted', 'Linux'])] },
  { name: 'metta0', category: 'Metta', ephemeral: false, labels: [], warnings: [], slots: [slot('metta0', 'offline'), slot('metta0-2', 'idle')] },
  { name: 'tfgar-probe', category: 'Ephemeral', ephemeral: true, labels: [], warnings: [], slots: [slot('tfgar-probe_i-1', 'busy')] }
]
const view = () => P.merge([{
  route, at: NOW, body: {
    contract_version: 1, profile: 'example', generated_at: '2026-10-29T17:00:00Z',
    gateways: { state: 'ready', machines: [] }, ci: { state: 'ready', machines: ci }
  }
}], NOW)

test('grouping: categories in fixed order with capacity, offline and health; old gateways fall back by name/labels', () => {
  const g = P.groupCi(ci)
  assert.deepEqual(g.map((x) => [x.name, x.busy, x.total, x.offline, x.health]), [
    ['Macs', 0, 1, 0, 'bad'], ['EC2 Linux', 2, 3, 0, 'good'], ['Metta', 0, 2, 1, 'warn'], ['Ephemeral', 1, 1, 0, 'good']])
  assert.equal(P.ciCategory({ name: 'mac-mini', slots: [] }), 'Macs')
  assert.equal(P.ciCategory({ name: 'x', slots: [slot('x', 'idle', ['macOS'])] }), 'Macs')
  assert.equal(P.ciCategory({ name: 'metta1-cogamer', slots: [slot('m', 'idle', ['Linux'])] }), 'Metta')
})

test('collapsed by default: one row per group with busy/total, no machine rows', async () => {
  localStorage.clear()
  const ui = await mount(React.createElement(P.Panel, { view: view(), now: NOW }))
  const heads = [...ui.container.querySelectorAll('[data-ci-group]')]
  assert.deepEqual(heads.map((e) => e.dataset.ciGroup), ['Macs', 'EC2 Linux', 'Metta', 'Ephemeral'])
  assert.match(ui.container.querySelector('[data-ci-group="EC2 Linux"]').textContent, /EC2 Linux \(2\).*2\/3/)
  assert.match(ui.container.querySelector('[data-ci-group="Metta"]').textContent, /1 offline/)
  assert.match(ui.container.querySelector('[data-ci-group="Macs"]').textContent, /⚠ 1/)
  assert.equal(ui.container.querySelector('[data-machine="ec2-gwgen"]'), null)
})

test('expand: a click shows the group machines, the state is remembered, a second click collapses', async () => {
  localStorage.clear()
  const ui = await mount(React.createElement(P.Panel, { view: view(), now: NOW }))
  const head = () => ui.container.querySelector('[data-ci-group="EC2 Linux"] > button')
  await ui.click(head())
  assert.equal(head().getAttribute('aria-expanded'), 'true')
  assert.ok(ui.container.querySelector('[data-machine="ec2-gwgen"]'))
  assert.ok(ui.container.querySelector('[data-machine="ec2-gwhub"]'))
  assert.equal(ui.container.querySelector('[data-machine="metta0"]'), null)
  assert.deepEqual(JSON.parse(localStorage.getItem('hermes-runners-ci-open')), ['EC2 Linux'])
  const again = await mount(React.createElement(P.Panel, { view: view(), now: NOW }))
  assert.ok(again.container.querySelector('[data-machine="ec2-gwgen"]'))
  await ui.click(head())
  assert.equal(ui.container.querySelector('[data-machine="ec2-gwgen"]'), null)
  assert.deepEqual(JSON.parse(localStorage.getItem('hermes-runners-ci-open')), [])
})
