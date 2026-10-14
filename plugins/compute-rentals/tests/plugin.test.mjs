import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, loadPlugin, mount } from './ui-harness.mjs'
const pluginURL = new URL('../../../desktop-plugins/compute-rentals/plugin.js', import.meta.url)
const plugin = await loadPlugin(pluginURL)
test('rates keep currencies separate, preserve decimal precision and expose unknown/partial totals', () => {
  assert.equal(typeof plugin.summarizeMachines, 'function')
  const rows = [
    machine('a', scope, { hourly_rate: '0.10', currency: 'USD' }),
    machine('b', scope, { hourly_rate: '0.20', currency: 'USD' }),
    machine('c', scope, { hourly_rate: '1.005', currency: 'EUR' }),
    machine('d', scope, { hourly_rate: null, currency: 'USD' }),
  ]
  assert.deepEqual(plugin.summarizeMachines(rows, true), {
    count: 4,
    rental_count: 4,
    internal_count: 0,
    rates: { USD: '0.30', EUR: '1.005' },
    unknown_rates: 1,
    complete: true,
  })
  assert.match(plugin.summaryLabel(plugin.summarizeMachines(rows, false)), /partial/)
  assert.match(
    plugin.summaryLabel(plugin.summarizeMachines(rows, true)),
    /USD 0.30\/h.*EUR 1.01\/h|EUR 1.01\/h.*USD 0.30\/h/,
  )
  assert.match(
    plugin.summaryLabel(plugin.summarizeMachines(rows, true), { exact: true }),
    /EUR 1.005\/h/,
  )
  assert.match(plugin.summaryLabel(plugin.summarizeMachines(rows, true)), /1 unknown/)
  assert.equal(plugin.summaryLabel(undefined), '— · rate unknown')
  assert.equal(
    plugin.summaryLabel(plugin.summarizeMachines([], false)),
    '0 observed · rate unknown · partial',
  )
})

const scope = { gateway_id: 'gateway-a', profile: 'default', session_id: 'stored-1' }
const machine = (key, owner = scope, extra = {}) => ({
  key,
  provider: 'runpod',
  ownership: owner ? [owner] : [],
  ...extra,
})
const fixture = () => ({
  gateway_id: 'gateway-a',
  machines: [
    machine('a', scope, {
      display_name: 'Trainer A',
      hourly_rate: '0.10',
      currency: 'USD',
      status: 'running',
      hardware: 'H100',
      provider_id: 'pod-a',
    }),
    machine('b', null, {
      provider: 'vast',
      display_name: 'Global B',
      hourly_rate: null,
      currency: 'USD',
      status: 'stopped',
    }),
  ],
  providers: [
    { provider: 'runpod', status: 'ok', scope: 'account-a' },
    { provider: 'vast', status: 'ok', scope: 'account-b' },
  ],
  summary: { count: 2, rates: { USD: '0.10' }, unknown_rates: 1, complete: true },
  session_summary: { count: 1, rates: { USD: '0.10' }, unknown_rates: 0, complete: true },
  observed_at: '2026-10-09T20:00:00Z',
})
const button = (root, label) =>
  [...root.querySelectorAll('button')].find(
    (element) => element.textContent === label || element.getAttribute('aria-label') === label,
  )
// Sources is collapsed by default; expand it and every per-provider row to reach full coverage detail.
const openSources = async (ui) => {
  const coverage = () => ui.container.querySelector('[aria-label="Provider coverage"]')
  const toggle = button(coverage(), 'Sources')
  if (toggle.getAttribute('aria-expanded') !== 'true') await ui.click(toggle)
  for (const row of [...coverage().querySelectorAll('[data-source] > button[aria-expanded="false"]')]) await ui.click(row)
  return coverage()
}
const titled = (root, iso) => root.querySelector(`[title="${iso}"]`)

test('status contribution declares the generic gateway anchor without asserting live adjacency', () => {
  const registrations = []
  plugin.default.register({ register: (item) => registrations.push(item) })
  assert.equal(registrations.length, 1)
  assert.equal(registrations[0].area, 'statusBar.left')
  assert.deepEqual(registrations[0].data, { after: 'gateway-health' })
  assert.equal(registrations[0].order, -1000)
})

test('registered left status item polls and displays session dots plus both summaries; provider and dot clicks open scoped native content', async () => {
  assert.equal(typeof plugin.default?.register, 'function')
  const calls = []
  const registrations = []
  const ctx = {
    register: (item) => registrations.push(item),
    rest: async (path, options) => {
      calls.push({ path, options })
      return fixture()
    },
  }
  plugin.default.register(ctx)
  assert.equal(registrations.length, 1)
  assert.equal(registrations[0].area, 'statusBar.left')
  const ui = await mount(registrations[0].render())
  try {
    await ui.flush()
    assert.match(ui.container.textContent, /Session 1 · USD 0.10\/h/)
    assert.match(ui.container.textContent, /Global 2 · USD 0.10\/h \+ 1 unknown/)
    assert.equal(ui.container.querySelectorAll('[data-machine-dot]').length, 1)
    const query = ui.client.getQueryCache().getAll()[0]
    assert.equal(query.options.refetchInterval, 30000)
    assert.match(calls[0].path, /session_id=stored-1/)
    assert.match(calls[0].path, /profile=default/)
    await ui.click(button(ui.container, 'RunPod'))
    assert.match(ui.container.querySelector('section').textContent, /Trainer A/)
    assert.doesNotMatch(ui.container.querySelector('section').textContent, /Global B/)
    await ui.click(button(ui.container, 'Close compute rentals'))
    await ui.click(ui.container.querySelector('[data-machine-dot]'))
    assert.match(ui.container.querySelector('section').textContent, /pod-a/)
    assert.match(ui.container.querySelector('section').textContent, /H100/)
  } finally {
    await ui.close()
  }
})

test('machine caveats render as labelled text, never provider HTML', async () => {
  const data = fixture()
  data.machines[0].rate_note = 'Compute only; <img src=x onerror=alert(1)> excludes storage & network'
  data.machines[0].state_detail = 'Desired RUNNING; actual <script>alert(1)</script> pending'
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    await ui.click(ui.container.querySelector('[data-machine-dot]'))
    const panel = ui.container.querySelector('section')
    const details = Object.fromEntries([...panel.querySelectorAll('dt')].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]))
    assert.equal(details['Rate details'], data.machines[0].rate_note)
    assert.equal(details['Provider state'], data.machines[0].state_detail)
    assert.equal(panel.querySelector('img, script'), null)
  } finally {
    await ui.close()
  }
})

for (const value of [undefined, null, '', '   ', 42, { note: 'invalid' }]) {
  test(`missing or non-string machine caveats add no metadata noise: ${JSON.stringify(value)}`, async () => {
    const data = fixture()
    Object.assign(data.machines[0], { rate_note: value, state_detail: value })
    data.providers[0].last_success_at = value
    const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
    try {
      await ui.flush()
      await ui.click(ui.container.querySelector('[data-machine-dot]'))
      const labels = [...ui.container.querySelectorAll('dt')].map((dt) => dt.textContent)
      assert.ok(!labels.includes('Rate details'))
      assert.ok(!labels.includes('Provider state'))
      assert.ok(!labels.includes('Freshness'))
      const coverage = await openSources(ui)
      assert.match(coverage.textContent, /Coverage/)
      assert.doesNotMatch(coverage.textContent, /last success/i)
    } finally {
      await ui.close()
    }
  })
}

test('provider last success is distinct from response and observation times; stale machines are explicit', async () => {
  const data = fixture()
  Object.assign(data.providers[0], {
    status: 'error', error: 'timeout',
    observed_at: '2026-10-09T19:55:00Z',
    last_success_at: '2026-10-09T19:30:00Z',
  })
  Object.assign(data.machines[0], { stale: true, observed_at: '2026-10-09T19:29:00Z' })
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    await ui.click(ui.container.querySelector('[data-machine-dot]'))
    const panel = ui.container.querySelector('section')
    assert.ok(panel.querySelector('[title="Last updated (response): 2026-10-09T20:00:00Z"]'))
    const detailDl = panel.querySelector('[aria-label="Machine details"] dl')
    const details = Object.fromEntries([...detailDl.querySelectorAll('dt')].map((dt) => [dt.textContent, dt.nextElementSibling]))
    assert.equal(details.Observed.querySelector('[title]').getAttribute('title'), '2026-10-09T19:29:00Z')
    assert.match(details.Observed.textContent, / ago$/)
    assert.equal(details.Freshness.textContent, 'Stale — last-known provider data')
    const coverage = await openSources(ui)
    const runpod = coverage.querySelector('[data-source="runpod"]')
    const runpodDetails = Object.fromEntries([...runpod.querySelectorAll('dt')].map((dt) => [dt.textContent, dt.nextElementSibling]))
    assert.equal(runpodDetails.Observed.querySelector('[title]').getAttribute('title'), '2026-10-09T19:55:00Z')
    assert.equal(runpodDetails['Last success'].querySelector('[title]').getAttribute('title'), '2026-10-09T19:30:00Z')
    assert.match(runpod.textContent, /partial \/ possibly stale/)
    assert.match(runpod.textContent, /timeout/)
    const otherProvider = coverage.querySelector('[data-source="vast"]')
    assert.doesNotMatch(otherProvider.textContent, /last success/i)
  } finally {
    await ui.close()
  }
})

test('details expose read-only metadata, safe console and SSH, plus coverage, errors and refresh', async () => {
  const data = fixture()
  Object.assign(data.machines[0], {
    account: 'research',
    region: 'us-east',
    started_at: '2026-10-09T19:00:00Z',
    expires_at: '2026-10-10T19:00:00Z',
    rate_basis: 'estimated',
    console_url: 'https://runpod.io/console/pod-a',
    ssh: 'ssh user@host',
  })
  data.providers[1] = {
    provider: 'vast',
    status: 'error',
    error: 'token expired',
    scope: 'account-b',
    observed_at: '2026-10-09T19:00:00Z',
  }
  const calls = [],
    external = [],
    clipboard = []
  const ui = await mount(
    React.createElement(plugin.ComputeRentals, {
      ctx: {
        rest: async (path, options) => {
          calls.push({ path, options })
          return data
        },
        os: {
          openExternal: async (url) => {
            external.push(url)
            return true
          },
          writeClipboard: async (text) => {
            clipboard.push(text)
            return true
          },
        },
      },
    }),
  )
  try {
    await ui.flush()
    assert.equal(ui.container.querySelector('[data-machine-dot] span').dataset.tone, 'good')
    assert.match(ui.container.textContent, /partial/)
    await ui.click(ui.container.querySelector('[data-machine-dot]'))
    const panel = ui.container.querySelector('section')
    for (const text of ['research', 'us-east', 'estimated', 'gateway-a', 'default', 'stored-1', 'updated', 'token expired'])
      assert.ok(panel.textContent.includes(text), text)
    for (const iso of ['2026-10-09T19:00:00Z', '2026-10-10T19:00:00Z']) assert.ok(titled(panel, iso), iso)
    assert.ok(panel.querySelector('[title^="Last updated (response)"]'))
    assert.match(panel.querySelector('[aria-label="Provider coverage"]').textContent, /Vast.*token expired/)
    assert.ok(!panel.textContent.includes('account-b'), 'coverage text is collapsed by default')
    assert.match((await openSources(ui)).textContent, /account-b/)
    await ui.click(button(panel, 'Open console'))
    assert.deepEqual(external, ['https://runpod.io/console/pod-a'])
    await ui.click(button(panel, 'Copy SSH'))
    assert.deepEqual(clipboard, ['ssh user@host'])
    await ui.click(button(panel, 'Refresh'))
    assert.match(calls.at(-1).path, /refresh=true/)
    assert.ok(
      [...panel.querySelectorAll('button')].every(
        (el) => !/^(start|stop|delete|terminate|rent)$/i.test(el.textContent),
      ),
    )
  } finally {
    await ui.close()
  }
})

test('focus change resets Global to Session and rejects stale query callbacks without fetching the new ambient backend', async () => {
  const calls = []
  const ctx = {
    rest: async (path) => {
      calls.push(path)
      const data = fixture()
      if (path.includes('stored-2')) {
        data.machines = []
        data.summary = { count: 0, rates: {}, unknown_rates: 0, complete: true }
      }
      return data
    },
  }
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    await ui.click(button(ui.container, 'Global'))
    const oldFn = ui.client.getQueryCache().getAll()[0].options.queryFn
    await React.act(async () => host.state.focusedStoredSessionId.set('stored-2'))
    await ui.flush()
    assert.equal(ui.container.querySelector('section'), null)
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.equal(button(ui.container, 'Session').getAttribute('aria-pressed'), 'true')
    assert.doesNotMatch(ui.container.textContent, /Trainer A|Global B/)
    const before = calls.length
    await assert.rejects(() => oldFn(), /Focus changed/)
    assert.equal(calls.length, before)
    const key = ui.client
      .getQueryCache()
      .getAll()
      .find((query) => query.isActive()).queryKey
    assert.ok(key.includes('stored-2'))
    assert.ok(key.includes('local'))
    assert.ok(key.includes('default'))
  } finally {
    await ui.close()
    host.state.focusedStoredSessionId.set('stored-1')
  }
})

test('unresolved server identity never presents a false zero session total and offers no manual link control', async () => {
  const data = fixture()
  delete data.gateway_id
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    assert.match(ui.container.textContent, /Session — · rate unknown/)
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.match(ui.container.textContent, /Session ownership unavailable/)
    await ui.click(button(ui.container, 'Global'))
    await ui.click(button(ui.container, 'Global B'))
    assert.equal(button(ui.container, 'Link to this session'), undefined)
  } finally {
    await ui.close()
  }
})

test('summary click clears a vendor filter even while popover is already open; reopening defaults Session', async () => {
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => fixture() } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'RunPod'))
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.ok(ui.container.querySelector('section'), 'summary keeps the all-provider view open')
    assert.doesNotMatch(ui.container.querySelector('section').textContent, /Provider: runpod/)
    await ui.click(button(ui.container, 'Global'))
    assert.match(ui.container.querySelector('section').textContent, /Global B/)
    await ui.click(button(ui.container, 'Close compute rentals'))
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.equal(button(ui.container, 'Session').getAttribute('aria-pressed'), 'true')
  } finally {
    await ui.close()
  }
})

test('late inventory from another session cannot leak into current dots or panel', async () => {
  let resolveOld
  const old = new Promise((resolve) => {
    resolveOld = resolve
  })
  const ctx = {
    rest: async (path) =>
      path.includes('stored-1')
        ? old
        : { ...fixture(), machines: [], summary: { count: 0, rates: {}, unknown_rates: 0, complete: true } },
  }
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx }))
  try {
    await React.act(async () => host.state.focusedStoredSessionId.set('stored-2'))
    await ui.flush()
    await React.act(async () => resolveOld(fixture()))
    await ui.flush()
    assert.equal(ui.container.querySelectorAll('[data-machine-dot]').length, 0)
    assert.match(ui.container.textContent, /Session 0/)
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.doesNotMatch(ui.container.textContent, /Trainer A/)
  } finally {
    await ui.close()
    host.state.focusedStoredSessionId.set('stored-1')
  }
})

test('same-connection cross-profile focus never reads or mutates the ambient backend', async () => {
  host.state.focusedSessionOwner.set({ connectionId: 'local', profile: 'other' })
  const calls = []
  const ui = await mount(React.createElement(plugin.ComputeRentals, {
    ctx: { rest: async (path, options) => {
      calls.push({ path, options })
      const data = fixture()
      data.machines[0].ownership = [{ ...scope, profile: 'other' }]
      return data
    } },
  }))
  try {
    await ui.flush()
    assert.equal(calls.length, 0)
    const disabledQuery = ui.client.getQueryCache().getAll()[0]
    await assert.rejects(() => disabledQuery.options.queryFn(), /unavailable/)
    assert.equal(calls.length, 0)
    assert.match(ui.container.textContent, /Session —/)
    assert.equal(ui.container.querySelectorAll('[data-machine-dot]').length, 0)
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.match(ui.container.textContent, /profile.*unavailable|unavailable.*profile/i)
    assert.equal(button(ui.container, 'Refresh').disabled, true)
    await ui.click(button(ui.container, 'Global'))
    assert.doesNotMatch(ui.container.textContent, /Trainer A|Global B/)
    assert.equal(button(ui.container, 'Unlink from this session'), undefined)
    assert.equal(calls.length, 0)
  } finally {
    await ui.close()
    host.state.focusedSessionOwner.set({ connectionId: 'local', profile: 'default' })
  }
})

test('cross-gateway focus fails closed, without using ambient inventory or mutating it', async () => {
  host.state.focusedSessionOwner.set({ connectionId: 'remote', profile: 'default' })
  const calls = []
  const ui = await mount(
    React.createElement(plugin.ComputeRentals, {
      ctx: {
        rest: async (path) => {
          calls.push(path)
          return fixture()
        },
      },
    }),
  )
  try {
    await ui.flush()
    assert.equal(calls.length, 0)
    assert.match(ui.container.textContent, /Session —/)
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.match(ui.container.textContent, /unavailable through this connection/)
    assert.equal(button(ui.container, 'Refresh').disabled, true)
  } finally {
    await ui.close()
    host.state.focusedSessionOwner.set({ connectionId: 'local', profile: 'default' })
  }
})

test('unsafe console URLs cannot open and OS capability failure is visible', async () => {
  const data = fixture()
  data.machines[0].console_url = 'javascript:alert(1)'
  data.machines[0].ssh = 'ssh user@host'
  let opens = 0
  const ui = await mount(
    React.createElement(plugin.ComputeRentals, {
      ctx: {
        rest: async () => data,
        os: {
          openExternal: async () => {
            opens++
            return true
          },
          writeClipboard: async () => false,
        },
      },
    }),
  )
  try {
    await ui.flush()
    await ui.click(ui.container.querySelector('[data-machine-dot]'))
    assert.equal(button(ui.container, 'Open console').disabled, true)
    await ui.click(button(ui.container, 'Open console'))
    assert.equal(opens, 0)
    await ui.click(button(ui.container, 'Copy SSH'))
    assert.match(ui.container.textContent, /Desktop action unavailable/)
  } finally {
    await ui.close()
  }
})

test('draft session still exposes global inventory but has no manual link control', async () => {
  host.state.focusedStoredSessionId.set(null)
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => fixture() } }))
  try {
    await ui.flush()
    assert.match(ui.container.textContent, /Session —/)
    assert.match(ui.container.textContent, /Global 2/)
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.match(ui.container.textContent, /No saved session focused/)
    await ui.click(button(ui.container, 'Global'))
    await ui.click(button(ui.container, 'Global B'))
    assert.equal(button(ui.container, 'Link to this session'), undefined)
  } finally {
    await ui.close()
    host.state.focusedStoredSessionId.set('stored-1')
  }
})

test('initial inventory failure exposes unknown count and rate, never a zero bill', async () => {
  const ui = await mount(
    React.createElement(plugin.ComputeRentals, {
      ctx: {
        rest: async () => {
          throw Error('backend offline')
        },
      },
    }),
  )
  try {
    await ui.flush()
    assert.match(ui.container.textContent, /Global — · rate unknown/)
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.match(ui.container.textContent, /Inventory unavailable: backend offline/)
  } finally {
    await ui.close()
  }
})

test('provider groups are dynamic across RunPod, Modal, Prime Intellect and Slurm/docker-hosts', async () => {
  const data = fixture()
  const names = ['runpod', 'modal', 'prime-intellect', 'slurm-cluster']
  data.machines = names.map((provider, index) =>
    machine(String(index), scope, {
      provider,
      display_name: `${provider} worker`,
      provider_id: `node-${index}`,
      hardware: 'GPU allocation',
      status: 'running',
      hourly_rate: null,
      currency: 'USD',
    }),
  )
  data.providers = names.map((provider) => ({ provider, status: 'ok', scope: 'workspace' }))
  data.summary = { count: 4, rates: {}, unknown_rates: 4, complete: true }
  const calls = []
  const ui = await mount(
    React.createElement(plugin.ComputeRentals, {
      ctx: {
        rest: async (path) => {
          calls.push(path)
          return data
        },
      },
    }),
  )
  try {
    await ui.flush()
    assert.equal(ui.container.querySelectorAll('[data-machine-dot]').length, 4)
    for (const provider of names) assert.ok(button(ui.container, plugin.providerLabel(provider)), provider)
    assert.ok(button(ui.container, 'RunPod'))
    await ui.click(button(ui.container, 'prime-intellect'))
    assert.match(ui.container.querySelector('section').textContent, /prime-intellect worker/)
    assert.doesNotMatch(ui.container.querySelector('section').textContent, /modal worker/)
    const params = new URL(calls[0], 'http://test').searchParams
    assert.equal(params.get('gateway_id'), '')
    assert.equal(params.get('refresh'), 'false')
  } finally {
    await ui.close()
  }
})

test('native Escape closes popover and summary reopening resets scope', async () => {
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => fixture() } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    await ui.click(button(ui.container, 'Global'))
    await React.act(async () =>
      document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })),
    )
    assert.equal(ui.container.querySelector('section'), null)
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.equal(button(ui.container, 'Session').getAttribute('aria-pressed'), 'true')
  } finally {
    await ui.close()
  }
})

test('session inventory requires exact durable session, profile and gateway ownership; dedupes machine keys', () => {
  assert.equal(typeof plugin.sessionMachines, 'function', 'sessionMachines is implemented')
  const a = machine('a')
  assert.deepEqual(
    plugin
      .sessionMachines(
        [
          a,
          a,
          machine('b', { ...scope, profile: 'other' }),
          machine('c', { ...scope, gateway_id: 'other' }),
          machine('d', { ...scope, session_id: 'runtime-1' }),
          machine('e', null),
        ],
        scope,
      )
      .map((x) => x.key),
    ['a'],
  )
  assert.deepEqual(plugin.sessionMachines([a], { ...scope, session_id: '' }), [])
})


// ---- Workloads, internal prepaid nodes, resource kinds and display rounding (synthetic fixtures) ----
const ASSOC = 'Associated through a bound machine; not exclusive workload ownership'
const workload = (key, extra = {}) => ({
  key,
  provider: 'dockerhosts',
  account: 'host-a',
  provider_id: `id-${key}`,
  resource_kind: 'container',
  display_name: key,
  status: 'running',
  parent_machine_keys: ['node-1'],
  hardware: 'unknown',
  started_at: null,
  expires_at: null,
  observed_at: '2026-10-09T20:00:00Z',
  state_detail: null,
  stale: false,
  ...extra,
})
const kinds = (o) => ({ instance: 0, physical_node: 0, pod: 0, serverless_worker: 0, sandbox: 0, ...o })
const wfixture = ({ containers } = {}) => {
  const workloads = [
    workload('alloc-1', {
      provider: 'slurm', resource_kind: 'allocation', display_name: 'Slurm job 1',
      expires_at: '2026-10-13T02:20:50Z', started_at: '2026-10-08T02:15:50Z',
    }),
    workload('queued-2', {
      provider: 'slurm', resource_kind: 'queued_job', display_name: 'Slurm job 2',
      status: 'queued', parent_machine_keys: [],
    }),
    ...(containers || [
      workload('c-run-1'),
      workload('c-run-2'),
      workload('c-stop-1', { status: 'stopped' }),
      workload('c-stop-2', { status: 'stopped' }),
      workload('c-stop-3', { status: 'stopped' }),
      workload('c-orphan', { parent_machine_keys: ['missing-host'] }),
    ]),
  ]
  return {
    gateway_id: 'gateway-a',
    machines: [
      machine('ec2-a', scope, {
        provider: 'ec2', resource_kind: 'instance', billing_class: 'unknown', display_name: 'paid-a',
        provider_id: 'i-a', status: 'running', currency: 'USD', hourly_rate: '0.1344000000',
      }),
      machine('node-1', scope, {
        provider: 'slurm', resource_kind: 'physical_node', billing_class: 'internal_prepaid',
        display_name: 'gpu-node-1', provider_id: 'gpu-node-1', status: 'running', currency: null,
        hourly_rate: null, rate_basis: 'unknown', rate_note: 'Internal prepaid; no incremental rental charge',
      }),
      machine('pod-r', null, {
        provider: 'runpod', resource_kind: 'pod', billing_class: 'unknown', display_name: 'pod-r',
        provider_id: 'pod-r', status: 'running', currency: 'credits', hourly_rate: '0.495',
      }),
    ],
    providers: [
      { provider: 'ec2', status: 'ok', scope: 'acct' },
      { provider: 'slurm', status: 'ok', scope: 'cluster' },
      { provider: 'runpod', status: 'ok', scope: 'acct' },
      { provider: 'dockerhosts', label: 'Docker Hosts', status: 'partial', scope: 'hosts', error: 'host-x: Docker inventory unavailable', last_success_at: null },
    ],
    summary: {
      count: 3, by_resource_kind: kinds({ instance: 1, physical_node: 1, pod: 1 }),
      internal_count: 1, rental_count: 2, rates: { USD: '0.1344000000', credits: '0.495' },
      unknown_rates: 0, complete: false,
    },
    session_summary: {
      count: 2, by_resource_kind: kinds({ instance: 1, physical_node: 1 }), internal_count: 1,
      rental_count: 1, rates: { USD: '0.1344000000' }, unknown_rates: 0, complete: false,
    },
    workloads,
    session_workloads: workloads
      .filter((w) => w.parent_machine_keys.includes('node-1'))
      .map((w) => ({ ...w, association: { basis: 'explicit_parent_machine_binding', exclusive: false, parent_machine_keys: ['node-1'], label: ASSOC } })),
    workload_summary: { count: workloads.length, complete: false },
    session_workload_summary: { count: 0, complete: false },
    observed_at: '2026-10-09T20:00:00Z',
  }
}
const section = (ui) => ui.container.querySelector('section')

test('display rates round to cents; exact form preserved; internal prepaid excluded from paid totals', () => {
  assert.equal(plugin.formatRate('21.70885353635031022'), '21.71')
  assert.equal(plugin.formatRate('0.1344000000'), '0.13')
  assert.equal(plugin.formatRate('0.005'), '0.01')
  assert.equal(plugin.formatRate('0.004'), '<0.01')
  assert.equal(plugin.formatRate('0'), '0.00')
  assert.equal(plugin.formatRate('9.995'), '10.00')
  const global = { count: 46, internal_count: 6, rental_count: 40, rates: { USD: '21.70885353635031022' }, unknown_rates: 3, complete: false }
  const label = plugin.summaryLabel(global)
  assert.match(label, /^40 observed · USD 21\.71\/h \+ 3 unknown \+ 6 internal · partial$/)
  assert.match(plugin.summaryLabel(global, { exact: true }), /USD 21\.70885353635031022\/h/)
  const rows = [
    machine('paid', scope, { hourly_rate: '0.10', currency: 'USD', billing_class: 'rental' }),
    machine('int-1', scope, { provider: 'slurm', hourly_rate: null, currency: null, billing_class: 'internal_prepaid' }),
    machine('int-2', scope, { provider: 'slurm', hourly_rate: '5.00', currency: 'USD', billing_class: 'internal_prepaid' }),
  ]
  assert.deepEqual(plugin.summarizeMachines(rows, true), {
    count: 3, rental_count: 1, internal_count: 2, rates: { USD: '0.10' }, unknown_rates: 0, complete: true,
  })
  assert.equal(plugin.summaryLabel(plugin.summarizeMachines(rows, true)), '1 · USD 0.10/h + 2 internal')
  assert.equal(plugin.summaryLabel({ count: 1, rates: { credits: '0.495' }, unknown_rates: 0, complete: true }), '1 · credits 0.50/h')
})

test('friendly provider labels for every live provider; unknown providers fall back to raw id', () => {
  const expected = {
    vast: 'Vast', sfcompute: 'SF Compute', ec2: 'EC2',
    runpod: 'RunPod', modal: 'Modal', primeintellect: 'Prime Intellect', slurm: 'Slurm',
  }
  for (const [id, label] of Object.entries(expected)) assert.equal(plugin.providerLabel(id), label)
  assert.equal(plugin.providerLabel('new-cloud'), 'new-cloud')
  // Extra (backend-loaded) providers name themselves through the inventory's provider rows.
  assert.equal(plugin.providerLabel('extra_cloud'), 'extra_cloud')
  plugin.registerProviderLabels([{ provider: 'extra_cloud', label: 'Extra Cloud' }, { provider: 'blank', label: ' ' }, null])
  assert.equal(plugin.providerLabel('extra_cloud'), 'Extra Cloud')
  assert.equal(plugin.providerLabel('blank'), 'blank')
})

test('status bar: internal prepaid nodes are hollow dots outside paid totals; workloads never become dots', async () => {
  const data = wfixture()
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    const dots = [...ui.container.querySelectorAll('[data-machine-dot]')]
    assert.deepEqual(dots.map((d) => d.dataset.machineDot).sort(), ['ec2-a', 'node-1'])
    const internal = ui.container.querySelector('[data-machine-dot="node-1"]')
    assert.equal(internal.dataset.billing, 'internal_prepaid')
    assert.equal(internal.querySelector('[data-tone]'), null, 'internal node is not a filled status dot')
    const hollow = internal.querySelector('[data-hollow-dot]')
    assert.ok(hollow, 'internal node draws a hollow dot')
    assert.match(hollow.getAttribute('style'), /border/)
    assert.match(internal.getAttribute('aria-label'), /internal prepaid/i)
    assert.ok(ui.container.querySelector('[data-machine-dot="ec2-a"] [data-tone="good"]'))
    const summary = button(ui.container, 'Compute rentals summary')
    assert.equal(summary.querySelector('[data-bar-visible]').textContent, 'Session 1 · $0.13/h+  │  All 2 · 0.50 credits/h + $0.13/h+')
    assert.match(summary.textContent, /Session 1 observed · USD 0\.13\/h \+ 1 internal · partial/)
    assert.match(summary.textContent, /Global 2 observed · credits 0\.50\/h \+ USD 0\.13\/h \+ 1 internal · partial/)
    assert.match(summary.title, /Global 2 observed · credits 0\.495\/h \+ USD 0\.1344000000\/h/)
    // Credits are never relabelled as dollars; only ISO USD gets "$".
    assert.doesNotMatch(summary.textContent, /\$0\.50|\$\s*0\.495/)
    assert.match(summary.getAttribute('title'), /USD 0\.1344000000\/h/)
    assert.match(summary.getAttribute('title'), /credits 0\.495\/h/)
    for (const w of data.workloads) assert.equal(ui.container.querySelector(`[data-machine-dot="${w.key}"]`), null)
    assert.doesNotMatch(ui.container.textContent, /c-run-1|Slurm job/)
    assert.ok(button(ui.container, 'Slurm'))
    assert.ok(button(ui.container, 'EC2'))
    assert.equal(button(ui.container, 'Docker Hosts'), undefined, 'workload-only provider has no status-bar group')
  } finally {
    await ui.close()
  }
})

test('machine detail lists child workloads: allocation deadline, active vs collapsed stopped containers', async () => {
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => wfixture() } }))
  try {
    await ui.flush()
    await ui.click(ui.container.querySelector('[data-machine-dot="node-1"]'))
    const panel = section(ui)
    const details = Object.fromEntries([...panel.querySelectorAll('dt')].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]))
    assert.equal(details.Rate, 'Internal prepaid')
    assert.equal(details.Kind, 'physical_node')
    const children = panel.querySelector('[aria-label="Child workloads"]')
    assert.ok(children)
    assert.match(children.textContent, /Slurm job 1/)
    const deadline = titled(children, '2026-10-13T02:20:50Z')
    assert.match(deadline.textContent, /^deadline (in )?\d+[smhd]( ago)?$/)
    assert.match(children.textContent, /Containers: 2 active · 3 stopped/)
    assert.match(children.textContent, /c-run-1/)
    assert.doesNotMatch(children.textContent, /c-stop-1|c-orphan|Slurm job 2/)
    await ui.click(button(children, 'Show 3 stopped'))
    assert.match(section(ui).querySelector('[aria-label="Child workloads"]').textContent, /c-stop-1.*c-stop-3/s)
    await ui.click(ui.container.querySelector('[data-machine-dot="ec2-a"]'))
    assert.equal(section(ui).querySelector('[aria-label="Child workloads"]'), null)
  } finally {
    await ui.close()
  }
})

test('global view: queued jobs listed in Slurm section; Docker Hosts shows workload counts, partial error, unattached rows', async () => {
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => wfixture() } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    await ui.click(button(ui.container, 'Global'))
    const panel = section(ui)
    const slurm = panel.querySelector('[data-provider-section="slurm"]')
    assert.ok(slurm)
    const queued = slurm.querySelector('[aria-label="Queued jobs"]')
    assert.match(queued.textContent, /Queued jobs \(1\)/)
    assert.match(queued.textContent, /Slurm job 2/)
    assert.match(slurm.textContent, /gpu-node-1/)
    assert.match(slurm.textContent, /internal prepaid/)
    assert.doesNotMatch(slurm.textContent, /rate unknown/)
    const badge = slurm.querySelector('[data-machine-row="node-1"] .hermes-cr-badge')
    assert.equal(badge.textContent, '1 job · 5 containers')
    assert.equal(badge.getAttribute('title'), '1 allocation · 2 active / 5 containers')
    const ec2 = panel.querySelector('[data-provider-section="ec2"]')
    assert.equal(ec2.querySelector('h3 [data-section-label]').textContent, 'EC2')
    const ec2Row = ec2.querySelector('[data-machine-row="ec2-a"]')
    assert.match(ec2Row.textContent, /\$0\.13\/hr/)
    assert.match(ec2Row.getAttribute('title'), /instance.*USD 0\.1344000000\/h/)
    const runpod = panel.querySelector('[data-provider-section="runpod"]')
    assert.match(runpod.querySelector('[data-machine-row="pod-r"]').textContent, /0\.50 credits\/hr/)
    assert.match(runpod.querySelector('[data-machine-row="pod-r"]').getAttribute('title'), /pod.*credits 0\.495\/h/)
    const dockerhosts = panel.querySelector('[data-provider-section="dockerhosts"]')
    assert.ok(dockerhosts, 'workload-only provider gets its own section')
    assert.equal(dockerhosts.querySelector('h3 [data-section-label]').textContent, 'Docker Hosts')
    assert.match(dockerhosts.textContent, /6 containers · 3 active · 3 stopped/)
    assert.match(dockerhosts.textContent, /5 attached under Slurm hosts/)
    assert.match(dockerhosts.textContent, /host-x: Docker inventory unavailable/)
    assert.match(dockerhosts.textContent, /c-orphan/)
    assert.doesNotMatch(dockerhosts.textContent, /c-run-1/)
    const coverage = await openSources(ui)
    assert.equal(coverage.querySelector('[data-source="dockerhosts"]').dataset.status, 'partial')
    assert.match(coverage.querySelector('[data-source="dockerhosts"]').textContent, /^.?Docker Hosts partial/)
    assert.match(panel.textContent, /No machines|gpu-node-1/)
    assert.equal(ui.container.querySelectorAll('[data-machine-dot]').length, 2)
  } finally {
    await ui.close()
  }
})

test('session scope shows associated session workloads with non-exclusive association label', async () => {
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => wfixture() } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    const panel = section(ui)
    const sw = panel.querySelector('[aria-label="Session workloads"]')
    assert.ok(sw)
    assert.match(sw.textContent, new RegExp(ASSOC))
    assert.match(sw.textContent, /non-exclusive/)
    assert.match(sw.textContent, /Slurm job 1/)
    assert.match(sw.textContent, /c-run-1/)
    assert.doesNotMatch(sw.textContent, /c-orphan|Slurm job 2/)
    await ui.click(button(ui.container, 'Global'))
    assert.equal(section(ui).querySelector('[aria-label="Session workloads"]'), null)
  } finally {
    await ui.close()
  }
})

test('session workloads are withheld when the session identity is unresolved', async () => {
  const data = wfixture()
  delete data.gateway_id
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.equal(section(ui).querySelector('[aria-label="Session workloads"]'), null)
  } finally {
    await ui.close()
  }
})

test('200 workloads render with capped rows and "N more" affordances', async () => {
  const containers = Array.from({ length: 200 }, (_, i) =>
    workload(`bulk-${String(i).padStart(3, '0')}`, { status: i < 120 ? 'running' : 'stopped' }),
  )
  const data = wfixture({ containers })
  const started = performance.now()
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    await ui.click(ui.container.querySelector('[data-machine-dot="node-1"]'))
    let children = section(ui).querySelector('[aria-label="Child workloads"]')
    assert.match(children.textContent, /Containers: 120 active · 80 stopped/)
    assert.ok(children.querySelectorAll('[data-workload]').length <= 50)
    assert.match(children.textContent, /71 more/) // 1 allocation + 120 active = 121 rows, 50 shown
    await ui.click(button(children, 'Show 80 stopped'))
    children = section(ui).querySelector('[aria-label="Child workloads"]')
    assert.ok(children.querySelectorAll('[data-workload]').length <= 50)
    assert.match(children.textContent, /151 more/)
    await ui.click(button(ui.container, 'Session'))
    const sw = section(ui).querySelector('[aria-label="Session workloads"]')
    assert.ok(sw.querySelectorAll('[data-workload]').length <= 50)
    assert.match(sw.textContent, /\d+ more/)
    assert.ok(ui.container.querySelectorAll('[data-workload]').length <= 150)
    assert.equal(ui.container.querySelectorAll('[data-machine-dot]').length, 2)
    assert.ok(performance.now() - started < 10_000)
  } finally {
    await ui.close()
  }
})


// ---- Redesigned popover: Sources disclosure, relative times, empty session, no raw ISO ----
test('relative time formatting covers past, future, microsecond ISO and invalid values', () => {
  const now = Date.parse('2026-10-10T00:02:00Z')
  assert.equal(plugin.relativeTime('2026-10-10T00:01:48Z', now), '12s ago')
  assert.equal(plugin.relativeTime('2026-10-10T00:01:34.215156+00:00', now), '25s ago')
  assert.equal(plugin.relativeTime('2026-10-09T23:59:00Z', now), '3m ago')
  assert.equal(plugin.relativeTime('2026-10-09T21:02:00Z', now), '3h ago')
  assert.equal(plugin.relativeTime('2026-10-12T00:02:00Z', now), 'in 2d')
  assert.equal(plugin.relativeTime('2026-10-10T00:01:58Z', now), 'just now')
  for (const bad of [null, undefined, '', 'soon', 42]) assert.equal(plugin.relativeTime(bad, now), 'unknown')
})

test('Sources disclosure is collapsed by default; expanding reveals per-provider status and full coverage', async () => {
  const data = fixture()
  data.providers.push({ provider: 'sfcompute', status: 'unconfigured', scope: 'All marketplace workspaces', error: 'SF credential unavailable', observed_at: '2026-10-09T19:59:00Z' })
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    await ui.click(button(ui.container, 'Global'))
    const panel = section(ui)
    const toggle = button(panel, 'Sources')
    assert.equal(toggle.getAttribute('aria-expanded'), 'false')
    assert.equal(panel.querySelector('[data-source]'), null)
    for (const hidden of ['account-a', 'account-b', 'All marketplace workspaces', 'SF credential unavailable'])
      assert.ok(!panel.textContent.includes(hidden), hidden)
    assert.equal(panel.querySelector('[data-provider-section="sfcompute"]'), null, 'unconfigured providers are not sections')
    assert.match(toggle.textContent, /2 ok.*1 not configured/)
    await ui.click(toggle)
    const rows = [...section(ui).querySelectorAll('[data-source]')]
    assert.deepEqual(rows.map((row) => row.dataset.source), ['runpod', 'vast', 'sfcompute'])
    assert.match(rows[2].textContent, /SF Compute not configured/)
    assert.match(rows[2].querySelector('[title="2026-10-09T19:59:00Z"]').textContent, /^checked /)
    assert.ok(!section(ui).textContent.includes('account-a'), 'full coverage stays inside per-provider detail')
    const coverage = await openSources(ui)
    assert.match(coverage.textContent, /account-a/)
    assert.match(coverage.textContent, /All marketplace workspaces/)
    await ui.click(button(section(ui), 'Sources'))
    assert.equal(section(ui).querySelector('[data-source]'), null)
  } finally {
    await ui.close()
  }
})

test('empty session shows a friendly state with a Browse global action', async () => {
  const data = fixture()
  data.machines[0].ownership = []
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    assert.equal(button(ui.container, 'Session').getAttribute('aria-pressed'), 'true')
    assert.match(section(ui).textContent, /No machines linked to this session/)
    assert.match(section(ui).textContent, /0 rentals/)
    assert.doesNotMatch(section(ui).textContent, /Trainer A|Global B/)
    await ui.click(button(ui.container, 'Browse global'))
    assert.equal(button(ui.container, 'Global').getAttribute('aria-pressed'), 'true')
    assert.match(section(ui).textContent, /Trainer A/)
    assert.match(section(ui).textContent, /Global B/)
  } finally {
    await ui.close()
  }
})

test('no raw ISO timestamps appear in visible text; exact values live in titles', async () => {
  const data = wfixture()
  const iso = /\d{4}-\d\d-\d\dT\d\d:\d\d/
  data.observed_at = '2026-10-10T00:01:44.130951+00:00'
  data.providers = data.providers.map((row, i) => ({
    ...row,
    observed_at: `2026-10-10T00:01:3${i}.215156+00:00`,
    last_success_at: `2026-10-10T00:01:3${i}.215412+00:00`,
  }))
  for (const m of data.machines) Object.assign(m, { started_at: '2026-09-08T21:53:43+00:00', observed_at: '2026-10-09T23:33:49.230114+00:00' })
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    await ui.click(button(ui.container, 'Global'))
    await openSources(ui)
    assert.doesNotMatch(section(ui).textContent, iso)
    assert.ok(section(ui).querySelector('[title="2026-10-10T00:01:30.215156+00:00"]'))
    assert.ok(section(ui).querySelector('[title="Last updated (response): 2026-10-10T00:01:44.130951+00:00"]'))
    await ui.click(section(ui).querySelector('[data-machine-row="node-1"]'))
    assert.match(section(ui).textContent, /Workloads on this machine/)
    assert.doesNotMatch(section(ui).textContent, iso)
    assert.ok(section(ui).querySelector('[title="2026-09-08T21:53:43+00:00"]'))
    assert.ok(section(ui).querySelector('[title="2026-10-13T02:20:50Z"]'))
  } finally {
    await ui.close()
  }
})

test('provider section headers collapse rows and show a status-coloured dot and subtotal', async () => {
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => wfixture() } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    await ui.click(button(ui.container, 'Global'))
    const head = () => section(ui).querySelector('[data-provider-section="ec2"] h3 button')
    assert.equal(head().getAttribute('aria-expanded'), 'true')
    assert.equal(head().querySelector('[data-tone]').dataset.tone, 'good')
    assert.match(head().textContent, /EC21\$0\.13\/hr/)
    assert.equal(section(ui).querySelector('[data-provider-section="dockerhosts"] h3 [data-tone]').dataset.tone, 'warn')
    await ui.click(head())
    assert.equal(head().getAttribute('aria-expanded'), 'false')
    assert.equal(section(ui).querySelector('[data-machine-row="ec2-a"]'), null)
    assert.match(section(ui).querySelector('.hermes-cr-summary').textContent, /2 rentals.*\+1 internal.*partial/)
  } finally {
    await ui.close()
  }
})

test('status bar label is compact; caveats marked with + and detailed in tooltip', () => {
  assert.equal(plugin.barLabel(undefined), '— · ?')
  assert.equal(plugin.barLabel({ count: 46, rental_count: 40, internal_count: 6, rates: { USD: '21.70885' }, unknown_rates: 3, complete: false }), '40 · $21.71/h+')
  assert.equal(plugin.barLabel({ count: 1, rates: { USD: '0.1' }, unknown_rates: 0, complete: true }), '1 · $0.10/h')
  assert.equal(plugin.barLabel({ count: 0, rates: {}, unknown_rates: 0, complete: true }), '0 · $0/h')
  assert.equal(plugin.barLabel({ count: 2, rates: {}, unknown_rates: 2, complete: true }), '2 · ?')
})

test('machine detail has no manual link control; links come from the rent-machine skill', async () => {
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => fixture() } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    await ui.click(button(ui.container, 'Trainer A'))
    assert.equal(button(ui.container, 'Link to this session'), undefined)
    assert.equal(button(ui.container, 'Unlink from this session'), undefined)
  } finally {
    await ui.close()
  }
})

test('Mine shows machines linked to any session, hiding unowned account machines', async () => {
  const data = fixture()
  data.machines.push(machine('c', { gateway_id: 'gateway-a', profile: 'default', session_id: 'other-session' }, {
    provider: 'vast', display_name: 'Other Session C', hourly_rate: '0.30', currency: 'USD', status: 'running',
  }))
  const ui = await mount(React.createElement(plugin.ComputeRentals, { ctx: { rest: async () => data } }))
  try {
    await ui.flush()
    await ui.click(button(ui.container, 'Compute rentals summary'))
    await ui.click(button(ui.container, 'Mine'))
    assert.equal(button(ui.container, 'Mine').getAttribute('aria-pressed'), 'true')
    const text = section(ui).textContent
    assert.match(text, /Trainer A/)
    assert.match(text, /Other Session C/)
    assert.doesNotMatch(text, /Global B/)
    assert.deepEqual(plugin.mineMachines(data.machines).map((m) => m.key), ['a', 'c'])
  } finally {
    await ui.close()
  }
})
