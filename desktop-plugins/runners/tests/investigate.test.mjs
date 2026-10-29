// Investigate button: one test per path (start, already running, needs you, done, error).
import test from 'node:test'
import assert from 'node:assert/strict'
import { React, loadPlugin, mount, route } from './sdk-mock.mjs'

const P = await loadPlugin()
const NOW = Date.parse('2026-10-29T17:00:00Z')

const view = (investigation) => P.merge([{
  route, at: NOW, body: {
    contract_version: 1, profile: 'example', generated_at: '2026-10-29T17:00:00Z',
    gateways: {
      state: 'ready', machines: [{
        name: 'mini', state: 'active', reason: null, labels: ['macos'], pinned_only: true, slots: null,
        slots_source: null, running: [],
        load: { cpu_load1: 13.8, cpus: 8, mem_used_pct: 78, disk_free_gb: 20, disk_total_gb: 228, at: NOW, stale: false },
        investigation
      }]
    },
    ci: { state: 'ready', machines: [] }
  }
}], NOW)

const other = { connectionId: 'hub', mode: 'remote', profile: 'default', targetProfile: 'default' }
let routes = [route]
function setup(responses, list = [route]) {
  routes = list
  const calls = [], opened = []
  globalThis.__hermesKanbanOpenTask = (task, board) => { opened.push([task, board]); return true }
  const ctx = {
    rest: async (path, opts) => {
      calls.push([path, opts])
      const next = responses.shift()
      if (next instanceof Error) throw next
      return structuredClone(next)
    }
  }
  const hostRef = { profileRoutes: async () => routes }
  return { calls, opened, investigator: P.createInvestigator(ctx, hostRef) }
}

const render = (v, investigator) => mount(React.createElement(P.Panel, { view: v, now: NOW, investigator }))
const button = (ui) => ui.container.querySelector('[data-investigate-action="mini"]')

test('start: posts the machine and its numbers to the gateway route, then opens the new card', async () => {
  const { calls, opened, investigator } = setup([{ created: true, machine: 'mini', investigation: { task: 't_0123abcd', status: 'ready' } }])
  const ui = await render(view(null), investigator)
  assert.equal(button(ui).textContent, 'Investigate')
  await ui.click(button(ui))
  assert.equal(calls.length, 1)
  const [path, opts] = calls[0]
  assert.equal(path, '/v1/investigations?profile=example')
  assert.equal(opts.method, 'POST')
  assert.equal(opts.connectionId, 'example')
  assert.deepEqual(opts.body, { machine: 'mini', load: { cpu_load1: 13.8, cpus: 8, mem_used_pct: 78, disk_free_gb: 20, disk_total_gb: 228 } })
  assert.deepEqual(opened, [['t_0123abcd', 'default']])
  assert.match(ui.container.querySelector('[data-investigate="mini"]').textContent, /Investigating · t_0123abcd/)
  assert.equal(button(ui).textContent, 'Open investigation')
})

test('running: the row says so and a click opens the existing card without posting', async () => {
  const { calls, opened, investigator } = setup([])
  const ui = await render(view({ task: 't_beef0001', board: 'default', status: 'running', created_at: NOW / 1000 - 60 }), investigator)
  assert.match(ui.container.textContent, /Investigating · t_beef0001/)
  await ui.click(button(ui))
  assert.equal(calls.length, 0)
  assert.deepEqual(opened, [['t_beef0001', 'default']])
})

test('queued without a card id yet: button disabled, nothing to open', async () => {
  const { investigator } = setup([])
  const ui = await render(view({ task: null, status: 'queued' }), investigator)
  assert.equal(button(ui).disabled, true)
  assert.equal(button(ui).textContent, 'Investigation queued')
})

test('needs you: a blocked investigation shows its ask and opens the card', async () => {
  const { calls, opened, investigator } = setup([])
  const ui = await render(view({ task: 't_beef0002', board: 'default', status: 'blocked', verdict: 'Delete 30 GB of old worktrees?' }), investigator)
  const row = ui.container.querySelector('[data-investigate="mini"]')
  assert.match(row.className, /is-blocked/)
  assert.match(row.textContent, /Needs you · Delete 30 GB of old worktrees\?/)
  await ui.click(button(ui))
  assert.equal(calls.length, 0)
  assert.deepEqual(opened, [['t_beef0002', 'default']])
})

test('done: shows the verdict and age, the verdict opens the result, the button starts a new one', async () => {
  const { calls, opened, investigator } = setup([{ created: true, machine: 'mini', investigation: { task: 't_0000new1', status: 'ready' } }])
  const done = { task: 't_beef0003', board: 'default', status: 'done', verdict: 'mini: BabyBear rpc loop polls gh every 2 s', completed_at: NOW / 1000 - 720 }
  const ui = await render(view(done), investigator)
  const row = ui.container.querySelector('[data-investigate="mini"]')
  assert.match(row.textContent, /Investigated 12m ago · mini: BabyBear rpc loop polls gh every 2 s/)
  await ui.click(row.querySelector('.hermes-fleet-inv-text'))
  assert.deepEqual(opened, [['t_beef0003', 'default']])
  await ui.click(button(ui))
  assert.equal(calls.length, 1)
  assert.deepEqual(opened.at(-1), ['t_0000new1', 'default'])
})

test('error: a failed start says why and leaves the button usable', async () => {
  const { investigator } = setup([Object.assign(new Error('unknown_machine'), { status: 404 })])
  const ui = await render(view(null), investigator)
  await ui.click(button(ui))
  assert.match(ui.container.querySelector('[role="alert"]').textContent, /Could not start: unknown_machine/)
  assert.equal(button(ui).disabled, false)
})

test('old gateway: a 404 from the section gateway retries on the next connected one', async () => {
  const { calls, opened, investigator } = setup([Object.assign(new Error('Not Found'), { status: 404 }),
    { created: true, machine: 'mini', investigation: { task: 't_0123abce', status: 'created' } }], [route, other])
  const ui = await render(view(null), investigator)
  await ui.click(button(ui))
  assert.deepEqual(calls.map(([, o]) => o.connectionId), ['example', 'hub'])
  assert.equal(calls[1][0], '/v1/investigations?profile=default')
  assert.deepEqual(opened, [['t_0123abce', 'default']])
})

test('no investigator (old host): no button', async () => {
  const ui = await mount(React.createElement(P.Panel, { view: view(null), now: NOW }))
  assert.equal(button(ui), null)
})
