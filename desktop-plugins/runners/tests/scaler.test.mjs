import test from 'node:test'
import assert from 'node:assert/strict'
import { React, loadPlugin, mount } from './sdk-mock.mjs'
const P = await loadPlugin()
const route = { connectionId: 'autoscaler', targetProfile: 'default' }
const limits = (over = {}) => ({ contract_version: 1, ok: true, min: 1, max: 5, active: 2, asg_max: 5, ...over })
const waitCommit = () => React.act(async () => { await new Promise(r => setTimeout(r, P.SCALER_COMMIT_MS + 30)) })
async function drag(input, value) {
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, String(value))
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
}
async function release(input, type = 'pointerup') {
  await React.act(async () => input.dispatchEvent(new window.Event(type, { bubbles: true })))
}
function fixture({ fail = false, result = {}, routes = [route] } = {}) {
  const calls = []
  const ctx = { rest: async (path, opts) => {
    calls.push({ path, ...opts })
    if (opts.method === 'POST') {
      if (fail) throw Error('capacity below current desired')
      return limits({ ...opts.body, ...result })
    }
    return limits()
  } }
  return { calls, ctx, client: P.createScalerClient(ctx, { profileRoutes: async () => routes }) }
}
const view = { at: Date.now(), gateways: { state: 'ready', machines: [] }, ci: { state: 'ready', machines: [] } }

test('panel slider -> debounced POST on release, readback and ACTIVE count', async () => {
  const f = fixture({ result: { min: 2, active: 3 } })
  const ui = await mount(React.createElement(P.Panel, { view, now: Date.now(), scalerClient: f.client }))
  try {
    const min = ui.container.querySelector('input[aria-label="Min workers"]')
    assert.equal(min.min, '0'); assert.equal(min.max, '5'); assert.equal(min.step, '1')
    assert.match(ui.container.textContent, /ACTIVE 2/)
    await drag(min, 3)
    assert.equal(f.calls.length, 1, 'drag does not POST')
    await release(min); await release(min, 'keyup'); await release(min, 'focusout')
    await waitCommit()
    assert.equal(f.calls.length, 2, 'pointer, key and blur commits coalesce')
    assert.deepEqual(f.calls[1], { path: '/v1/scaler/limits?profile=default', connectionId: 'autoscaler',
      timeoutMs: 60000, method: 'POST', body: { min: 3, max: 5 } })
    assert.equal(min.value, '2', 'show readback, not submitted guess')
    assert.match(ui.container.textContent, /ACTIVE 3/)
  } finally { await ui.close() }
})

test('lowering max below min lowers both, supports zero; keyboard release commits', async () => {
  const f = fixture()
  const ui = await mount(React.createElement(P.ScalerControls, { client: f.client }))
  try {
    const max = ui.container.querySelector('[aria-label="Max workers"]')
    await drag(max, 0); await release(max, 'keyup'); await waitCommit()
    assert.deepEqual(f.calls[1].body, { min: 0, max: 0 })
    assert.equal(ui.container.querySelector('[aria-label="Min workers"]').value, '0')
  } finally { await ui.close() }
})

test('raising min over max raises max; unchanged release does not write', async () => {
  const calls = []
  const client = { read: async () => ({ route, limits: limits({ max: 2 }) }),
    write: async (owner, value) => { calls.push(value); return { route, limits: limits(value) } } }
  const ui = await mount(React.createElement(P.ScalerControls, { client }))
  try {
    const min = ui.container.querySelector('[aria-label="Min workers"]')
    await drag(min, 1); await release(min); await waitCommit()
    assert.equal(calls.length, 0)
    await drag(min, 5); await release(min); await waitCommit()
    assert.deepEqual(calls, [{ min: 5, max: 5 }])
  } finally { await ui.close() }
})

test('CLI error is shown, unconfirmed state cannot be written again before refresh', async () => {
  const f = fixture({ fail: true })
  const ui = await mount(React.createElement(P.ScalerControls, { client: f.client }))
  try {
    await drag(ui.container.querySelector('input'), 3)
    await release(ui.container.querySelector('input')); await waitCommit()
    assert.match(ui.container.querySelector('[role="alert"]').textContent, /capacity below current desired/)
    assert.equal(ui.container.querySelector('input') === null, true)
    assert.equal(f.calls.length, 2)
  } finally { await ui.close() }
})

test('discovery ignores unsupported gateways, dedupes profiles, pins writes to supported connection', async () => {
  const calls = []
  const client = P.createScalerClient({ rest: async (path, opts) => {
    calls.push({ path, ...opts })
    if (opts.connectionId === 'other') throw Object.assign(Error('unsupported'), { status: 404 })
    return limits()
  } }, { profileRoutes: async () => [
    { connectionId: 'other', targetProfile: 'default' }, { ...route, targetProfile: 'alias' }, route
  ] })
  const owner = await client.read()
  await client.write(owner, { min: 1, max: 5 })
  assert.equal(calls.length, 3)
  assert.equal(calls[2].connectionId, 'autoscaler')
  assert.equal(calls[2].path, '/v1/scaler/limits?profile=default')
})

test('all unsupported hides controls; errors and multiple scalers fail closed', async () => {
  const client = P.createScalerClient({ rest: async () => { throw Object.assign(Error('unsupported'), { status: 404 }) } },
    { profileRoutes: async () => [route] })
  const ui = await mount(React.createElement(P.ScalerControls, { client }))
  assert.equal(ui.container.querySelector('input') === null, true)
  await ui.close()
  const f = fixture({ routes: [route, { connectionId: 'second', targetProfile: 'default' }] })
  await assert.rejects(f.client.read(), /Multiple autoscalers/)
  const broken = P.createScalerClient({ rest: async () => ({ min: 1, max: 5 }) }, { profileRoutes: async () => [route] })
  await assert.rejects(broken.read(), /Invalid scaler response/)
})

test('discovery tolerates an unrelated offline gateway but reports an unreachable scaler', async () => {
  const offline = { connectionId: 'offline', targetProfile: 'default' }
  const client = P.createScalerClient({ rest: async (_, opts) => {
    if (opts.connectionId === 'offline') throw Error('offline')
    return limits()
  } }, { profileRoutes: async () => [offline, route] })
  assert.equal((await client.read()).route.connectionId, route.connectionId)
  const broken = P.createScalerClient({ rest: async () => { throw Error('offline') } },
    { profileRoutes: async () => [offline] })
  await assert.rejects(broken.read(), /offline/)
})

test('a pending save disables both sliders and does not issue a duplicate write', async () => {
  let finish
  const f = fixture()
  let writes = 0
  const client = { read: () => f.client.read(), write: async (owner, next) => {
    writes++
    await new Promise(resolve => { finish = resolve })
    return { route, limits: limits(next) }
  } }
  const ui = await mount(React.createElement(P.ScalerControls, { client }))
  try {
    const input = ui.container.querySelector('input')
    await drag(input, 3); await release(input); await waitCommit()
    assert.equal([...ui.container.querySelectorAll('input')].every(e => e.disabled), true)
    await release(input); await waitCommit()
    assert.equal(writes, 1)
    await React.act(async () => finish())
    assert.equal(input.disabled, false)
    assert.equal(input.value, '3')
  } finally { await ui.close() }
})

for (const staleFailure of [false, true]) {
  test(`overlapping polls ignore stale ${staleFailure ? 'failure' : 'success'} and preserve latest min on max-only POST`, async (t) => {
    let poll
    t.mock.method(globalThis, 'setInterval', (callback) => { poll = callback; return 0 })
    t.mock.method(globalThis, 'clearInterval', () => {})
    const reads = [], writes = []
    const client = {
      read: () => new Promise((resolve, reject) => reads.push({ resolve, reject })),
      write: async (owner, value) => { writes.push(value); return { route, limits: limits(value) } }
    }
    const ui = await mount(React.createElement(P.ScalerControls, { client }))
    try {
      await React.act(async () => { void poll() })
      assert.equal(reads.length, 2)
      await React.act(async () => reads[1].resolve({ route, limits: limits({ min: 2, max: 4 }) }))
      await React.act(async () => staleFailure
        ? reads[0].reject(Error('older request failed'))
        : reads[0].resolve({ route, limits: limits() }))
      assert.equal(ui.container.querySelector('[role="alert"]') === null, true)
      assert.equal(ui.container.querySelector('[aria-label="Min workers"]')?.value, '2')
      const max = ui.container.querySelector('[aria-label="Max workers"]')
      assert.equal(max.value, '4')
      await drag(max, 3); await release(max); await waitCommit()
      assert.deepEqual(writes, [{ min: 2, max: 3 }])
    } finally { await ui.close() }
  })
}

test('a replaced client cannot publish a late read into the new effect', async () => {
  let finishOld, replaceClient
  const oldClient = { read: () => new Promise(resolve => { finishOld = resolve }) }
  const newClient = { read: async () => ({ route, limits: limits({ min: 2, max: 4 }) }) }
  function Harness() {
    const [client, setClient] = React.useState(oldClient)
    replaceClient = () => setClient(newClient)
    return React.createElement(P.ScalerControls, { client })
  }
  const ui = await mount(React.createElement(Harness))
  try {
    await React.act(async () => replaceClient())
    await React.act(async () => finishOld({ route, limits: limits() }))
    assert.equal(ui.container.querySelector('[aria-label="Min workers"]').value, '2')
    assert.equal(ui.container.querySelector('[aria-label="Max workers"]').value, '4')
  } finally { await ui.close() }
})

test('bare Desktop 404 errors hide controls without an alert', async () => {
  const client = P.createScalerClient({ rest: async () => { throw Error('404: {"detail":"scaler_unsupported"}') } },
    { profileRoutes: async () => [route] })
  assert.equal(await client.read(), null)
  const ui = await mount(React.createElement(P.ScalerControls, { client }))
  try {
    assert.ok(ui.container.querySelector('input') === null)
    assert.ok(ui.container.querySelector('[role="alert"]') === null)
  } finally { await ui.close() }
})

for (const fail of [false, true]) {
  test(`replaced client fences old write ${fail ? 'failure' : 'success'} and its cleanup`, async () => {
    let finishOld, replaceClient, finishNew
    const oldClient = { read: async () => ({ route, limits: limits() }),
      write: () => new Promise((resolve, reject) => { finishOld = () => fail
        ? reject(Error('old write failed')) : resolve({ route, limits: limits({ min: 3 }) }) }) }
    const writes = []
    const newClient = { read: async () => ({ route, limits: limits({ min: 2, max: 4 }) }),
      write: (owner, value) => { writes.push(value); return new Promise(resolve => {
        finishNew = () => resolve({ route, limits: limits(value) })
      }) } }
    function Harness() {
      const [client, setClient] = React.useState(oldClient)
      replaceClient = () => setClient(newClient)
      return React.createElement(P.ScalerControls, { client })
    }
    const ui = await mount(React.createElement(Harness))
    try {
      await drag(ui.container.querySelector('input'), 3)
      await release(ui.container.querySelector('input')); await waitCommit()
      await React.act(async () => replaceClient())
      assert.equal(ui.container.querySelector('[aria-label="Min workers"]')?.value, '2')
      const max = ui.container.querySelector('[aria-label="Max workers"]')
      await drag(max, 3); await release(max); await waitCommit()
      await React.act(async () => finishOld())
      assert.ok(ui.container.querySelector('[role="alert"]') === null)
      assert.equal(ui.container.querySelector('[aria-label="Min workers"]')?.value, '2')
      assert.equal(max.disabled, true, 'old finally cannot clear the new write busy state')
      assert.equal(max.value, '3', 'old finally cannot clear the new draft')
      await React.act(async () => finishNew())
      assert.deepEqual(writes, [{ min: 2, max: 3 }])
      assert.equal(max.disabled, false)
    } finally { await ui.close() }
  })
}

test('unmount cancels a queued commit', async () => {
  const f = fixture()
  const ui = await mount(React.createElement(P.ScalerControls, { client: f.client }))
  await drag(ui.container.querySelector('input'), 3); await release(ui.container.querySelector('input'))
  await ui.close(); await waitCommit()
  assert.equal(f.calls.length, 1)
})
