import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, route, snapshot, fixtures, loadPlugin, mount, button, enrollment } from './sdk-mock.mjs'
const p = await loadPlugin()
test('physical nodes start collapsed including empty hosts; expand reveals lazy runner details', async () => {
  const s = snapshot(), calls = []
  s.data.hosts = [structuredClone(fixtures.Host), { ...structuredClone(fixtures.Host), id: '00000000-0000-4000-8000-000000000002', display_name: 'Empty node' }]
  s.data.runners[0].host_id = s.data.hosts[0].id
  host.profileRoutes = async () => [route]
  const ctx = { rest: async (path) => { calls.push(path); return path.includes('/enrollment') ? enrollment() : s } }
  const ui = await mount(React.createElement(p.Runners, { ctx }))
  try {
    await ui.click(button('Show runners'))
    const nodes = [...document.querySelectorAll('[data-node-row]')]
    assert.equal(nodes.length, 2)
    assert.ok(nodes.every(n => n.getAttribute('aria-expanded') === 'false'))
    assert.equal(document.querySelector('[data-runner-row]'), null)
    assert.match(document.body.textContent, /Empty node/)
    assert.match(document.body.textContent, /health unverified/)
    assert.equal(calls.filter(p => p.includes('/runners/')).length, 0)
    await ui.click(nodes[0])
    assert.equal(document.querySelectorAll('[data-runner-row]').length, 1)
    assert.equal(calls.filter(p => p.includes('/runners/')).length, 0)
    await ui.click(document.querySelector('[data-runner-row]'))
    assert.equal(calls.filter(p => p.includes('/runners/')).length, 1)
  } finally { await ui.close() }
})
test('source diagnostics are collapsed while material owner failure remains visible', async () => {
  const s = snapshot()
  s.data.state = 'owner_disconnected'
  s.data.hosts = [structuredClone(fixtures.Host)]
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : s } }))
  try {
    await ui.click(button('Show runners'))
    const diagnostics = document.querySelector('details[data-runners-diagnostics]')
    assert.ok(diagnostics, 'source diagnostics disclosure exists')
    assert.equal(diagnostics.open, false)
    assert.ok(document.querySelector('.hermes-runners-scroll').compareDocumentPosition(diagnostics) & Node.DOCUMENT_POSITION_FOLLOWING, 'diagnostics follow inventory')
    assert.match(diagnostics.textContent, /Collector heartbeat/)
    const status = document.querySelector('[role="status"]')
    assert.match(status.textContent, /Owner unavailable/)
    assert.match(document.querySelector('[data-node-row]').textContent, /Owner unavailable/)
  } finally { await ui.close() }
})
test('nonowner enrollment diagnostics remain secondary and do not become owner failures', async () => {
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async () => ({ contract_version: 1, profile: 'example', enrolled: false, capabilities: [] }) } }))
  try {
    await ui.click(button('Show runners'))
    assert.match(document.querySelector('[data-runners-diagnostics]').textContent, /Not enrolled/)
    assert.doesNotMatch(document.querySelector('[role="status"]').textContent, /Owner unavailable|Not enrolled/)
    assert.equal(document.querySelectorAll('[data-node-row]').length, 0)
  } finally { await ui.close() }
})
test('missing plugins on nonowner profiles stay secondary beside a healthy owner', async () => {
  const s = snapshot()
  s.data.hosts = [structuredClone(fixtures.Host)]
  host.profileRoutes = async () => [route, { ...route, targetProfile: 'absent-one' }, { ...route, targetProfile: 'absent-two' }]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => {
    if (path.includes('profile=absent-')) throw Object.assign(new Error('404'), { status: 404 })
    return path.includes('/enrollment') ? enrollment() : s
  } } }))
  try {
    await ui.click(button('Show runners'))
    assert.match(document.querySelector('[data-runners-diagnostics]').textContent, /Plugin unavailable/)
    assert.doesNotMatch(document.querySelector('[role="status"]').textContent, /Owner unavailable|Plugin unavailable/)
    assert.equal(document.querySelectorAll('[data-node-row]').length, 2)
  } finally { await ui.close() }
})
test('enrolled owner first-inventory permission failure stays primary', async () => {
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => {
    if (path.includes('/enrollment')) return enrollment()
    throw Object.assign(new Error('403'), { status: 403 })
  } } }))
  try {
    await ui.click(button('Show runners'))
    assert.match(document.querySelector('[role="status"]').textContent, /Partial permissions/)
    assert.doesNotMatch(document.querySelector('[role="status"]').textContent, /Owner unavailable/)
  } finally { await ui.close() }
})
test('node detail shows explicit maintenance and enforced capacity without free-slot claims', async () => {
  const s = snapshot()
  s.data.hosts = [{ ...structuredClone(fixtures.Host), capacity: { enforced: true, declared_max_jobs: 4, observed_active_jobs: null }, maintenance: { state: 'active', reason: 'Inspection', expires_at: new Date(Date.now() + 60000).toISOString() } }]
  s.data.runners[0].host_id = s.data.hosts[0].id
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : s } }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    const detail = document.querySelector('[data-node-evidence]')
    assert.ok(detail, 'expanded node has evidence')
    assert.match(detail.textContent, /Enforced limit: 4.*Observed active jobs: unknown/)
    assert.match(detail.textContent, /Maintenance: Inspection.*expires/)
    assert.doesNotMatch(detail.textContent, /slots|draining/i)
  } finally { await ui.close() }
})
test('poll retains expanded nodes and detail but prunes removed identities before rediscovery', async () => {
  let s = snapshot()
  s.data.hosts = [structuredClone(fixtures.Host)]
  s.data.runners[0].host_id = s.data.hosts[0].id
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : structuredClone(s) } }))
  const poll = async () => {
    await React.act(async () => { await ui.client.invalidateQueries({ queryKey: ['runners'] }) })
    await ui.flush()
  }
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    await ui.click(document.querySelector('[data-runner-row]'))
    s.data.hosts.push({ ...structuredClone(fixtures.Host), id: 'host_0000000000000002', display_name: 'New node' })
    await poll()
    let nodes = [...document.querySelectorAll('[data-node-row]')]
    assert.equal(nodes[0].getAttribute('aria-expanded'), 'true')
    assert.equal(nodes[1].getAttribute('aria-expanded'), 'false')
    assert.ok(document.querySelector('.hermes-runners-detail'))
    const original = s.data.hosts.shift()
    s.data.runners = []
    await poll()
    s.data.hosts.unshift(original)
    await poll()
    nodes = [...document.querySelectorAll('[data-node-row]')]
    assert.equal(nodes[0].getAttribute('aria-expanded'), 'false')
  } finally { await ui.close() }
})
test('search narrows physical nodes, highlights a matching empty host and labels scope totals', async () => {
  const s = snapshot()
  s.data.hosts = [structuredClone(fixtures.Host), { ...structuredClone(fixtures.Host), id: '00000000-0000-4000-8000-000000000002', display_name: 'Quartz empty' }]
  s.data.runners[0].host_id = s.data.hosts[0].id
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : s } }))
  try {
    await ui.click(button('Show runners'))
    await ui.key(document.querySelector('[data-node-row]'), 'Q')
    assert.equal(document.querySelectorAll('[data-node-row]').length, 1)
    assert.equal(document.querySelector('[data-node-row] mark').textContent, 'Q')
    assert.match(document.body.textContent, /Scope: 1 registrations/)
    assert.match(document.body.textContent, /Showing 1 nodes · 0 registrations/)
    await ui.key(document.querySelector('input'), 'Escape')
    assert.equal(document.querySelectorAll('[data-node-row]').length, 2)
  } finally { await ui.close() }
})
test('runner name search retains parent and highlights nested matching text', async () => {
  const s = snapshot()
  s.data.runners[0].display_name = 'Quartz runner'
  s.data.hosts = [structuredClone(fixtures.Host)]
  s.data.runners[0].host_id = s.data.hosts[0].id
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : s } }))
  try {
    await ui.click(button('Show runners'))
    await ui.key(document.querySelector('[data-node-row]'), 'Q')
    await ui.click(document.querySelector('[data-node-row]'))
    assert.equal(document.querySelectorAll('[data-runner-row]').length, 1)
    assert.equal(document.querySelector('[data-runner-row] mark')?.textContent, 'Q')
    assert.match(document.body.textContent, /Showing 1 nodes · 1 registrations/)
  } finally { await ui.close() }
})
test('metadata searches highlight OS and matching runner labels beyond the preview', async () => {
  const s = snapshot()
  s.data.hosts = [{ ...structuredClone(fixtures.Host), os: 'linux' }]
  s.data.runners[0].host_id = s.data.hosts[0].id
  s.data.runners[0].labels = ['one', 'two', 'three', 'Quartz']
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : s } }))
  try {
    await ui.click(button('Show runners'))
    await ui.key(document.querySelector('[data-node-row]'), 'l')
    assert.ok([...document.querySelectorAll('[data-node-row] .hermes-runners-meta mark')].some(e => e.textContent === 'l'), 'OS match highlighted')
    await ui.key(document.querySelector('input'), 'Escape')
    await ui.key(document.querySelector('[data-node-row]'), 'Q')
    await ui.click(document.querySelector('[data-node-row]'))
    assert.ok([...document.querySelectorAll('[data-runner-row] mark')].some(e => e.textContent === 'Q'), 'matching fourth label is visible and highlighted')
  } finally { await ui.close() }
})
test('runner settings palette aliases use the validated scope URL', async () => {
  const s = snapshot(), entries = new Map(), opened = []
  s.data.runners[0].endpoint = 'https://api.github.com'
  s.data.scopes[0].kind = 'repository'
  s.data.scopes[0].name = 'example/project'
  host.profileRoutes = async () => [route]
  const ctx = {
    os: { openExternal: url => opened.push(url) },
    register: entry => { entries.set(entry.id, entry); return () => entries.delete(entry.id) },
    rest: async path => path.includes('/enrollment') ? enrollment() : s
  }
  const ui = await mount(React.createElement(p.Runners, { ctx }))
  try {
    const settings = [...entries.values()].find(e => e.data.label.startsWith('Runner settings:'))
    assert.ok(settings, 'settings contributes to existing palette')
    assert.ok(settings.data.keywords.includes('configuration'))
    assert.ok(settings.data.keywords.includes('preferences'))
    settings.data.run()
    assert.deepEqual(opened, ['https://github.com/example/project/settings/actions/runners'])
  } finally { await ui.close() }
})
test('inventory observations do not churn unchanged palette registrations', async () => {
  const s = snapshot(), entries = new Map()
  let registrations = 0
  host.profileRoutes = async () => [route]
  const ctx = {
    register: entry => { registrations++; entries.set(entry.id, entry); return () => entries.delete(entry.id) },
    rest: async path => path.includes('/enrollment') ? enrollment() : structuredClone(s)
  }
  const ui = await mount(React.createElement(p.Runners, { ctx }))
  try {
    const initial = registrations
    assert.ok(initial > 0)
    s.meta.generated_at = new Date(Date.now() + 1000).toISOString()
    await React.act(async () => ui.client.invalidateQueries({ queryKey: ['runners'] }))
    await ui.flush()
    assert.equal(registrations, initial, 'observation-only poll keeps palette entries intact')
    s.data.runners[0].display_name = 'Renamed runner'
    await React.act(async () => ui.client.invalidateQueries({ queryKey: ['runners'] }))
    await ui.flush()
    assert.ok([...entries.values()].some(e => e.data.label === 'Show runner: Renamed runner'))
  } finally { await ui.close() }
  assert.equal(entries.size, 0)
})
test('native palette jumps to owner-qualified nodes and runners and removes stale contributions', async () => {
  let s = snapshot()
  s.data.hosts = [structuredClone(fixtures.Host)]
  s.data.runners[0].host_id = s.data.hosts[0].id
  const entries = new Map(), calls = []
  host.profileRoutes = async () => [route]
  const ctx = {
    register: entry => { entries.set(entry.id, entry); return () => entries.delete(entry.id) },
    rest: async (path, options) => { calls.push({ path, options }); return path.includes('/enrollment') ? enrollment() : structuredClone(s) }
  }
  const ui = await mount(React.createElement(p.Runners, { ctx }))
  try {
    const node = [...entries.values()].find(e => e.data.label.startsWith('Show node:'))
    const runner = [...entries.values()].find(e => e.data.label.startsWith('Show runner:'))
    assert.ok(node, 'node registered with native palette')
    assert.ok(runner, 'runner registered with native palette')
    assert.equal(node.area, 'palette')
    assert.equal(calls.filter(c => c.path.includes('/runners/')).length, 0)
    await React.act(async () => node.data.run())
    await ui.flush()
    assert.equal(document.querySelector('[data-node-row]').getAttribute('aria-expanded'), 'true')
    assert.ok(document.activeElement === document.querySelector('[data-node-row]'), 'jump focuses node')
    assert.equal(document.querySelector('.hermes-runners-detail'), null)
    await React.act(async () => runner.data.run())
    await ui.flush()
    assert.ok(document.activeElement === document.querySelector('[data-runner-row]'), 'jump focuses runner')
    assert.ok(document.querySelector('.hermes-runners-detail'))
    assert.equal(document.querySelector('select').value, JSON.stringify([route.connectionId, route.mode, route.profile, route.targetProfile]))
    const detail = calls.find(c => c.path.includes('/runners/'))
    assert.ok(detail, 'runner jump lazily fetches detail')
    s.data.runners = []
    await React.act(async () => ui.client.invalidateQueries({ queryKey: ['runners'] }))
    await ui.flush()
    assert.ok(!entries.has(runner.id), 'removed runner is removed from native palette')
  } finally { await ui.close() }
  assert.equal(entries.size, 0, 'unmount disposes palette contributions')
})
test('question mark opens shortcut help and Escape closes help before panel', async () => {
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : snapshot() } }))
  try {
    await ui.click(button('Show runners'))
    const node = document.querySelector('[data-node-row]')
    await ui.key(node, '?')
    assert.match(document.querySelector('[data-runners-help]')?.textContent || '', /Right.*expand/)
    await ui.key(node, 'Escape')
    assert.equal(document.querySelector('[data-runners-help]'), null)
    assert.ok(document.querySelector('[data-runners-panel]'))
  } finally { await ui.close() }
})
test('node keyboard traversal and layered Escape preserve focus', async () => {
  const s = snapshot()
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : s } }))
  try {
    await ui.click(button('Show runners'))
    const input = document.querySelector('input'), node = document.querySelector('[data-node-row]')
    await ui.key(input, 'ArrowDown')
    assert.ok(document.activeElement === node, 'node receives keyboard focus')
    await ui.key(node, 'ArrowRight')
    assert.equal(node.getAttribute('aria-expanded'), 'true')
    await ui.key(node, 'ArrowDown')
    const row = document.querySelector('[data-runner-row]')
    assert.ok(document.activeElement === row, 'runner receives focus')
    await ui.key(row, 'Enter')
    assert.ok(document.querySelector('.hermes-runners-detail'))
    await ui.key(row, 'Escape')
    assert.equal(document.querySelector('.hermes-runners-detail'), null)
    assert.ok(document.activeElement === row, 'runner receives focus')
    await ui.key(row, 'ArrowLeft')
    assert.ok(document.activeElement === node, 'node receives keyboard focus')
    assert.equal(node.getAttribute('aria-expanded'), 'false')
    await ui.key(node, 'Enter')
    assert.equal(node.getAttribute('aria-expanded'), 'true')
    await ui.key(node, 'Escape')
    assert.equal(node.getAttribute('aria-expanded'), 'false')
    assert.ok(document.querySelector('[data-runners-panel]'))
    await ui.key(node, 'Escape')
    assert.equal(document.querySelector('[data-runners-panel]'), null)
    assert.equal(document.activeElement, button('Show runners'))
  } finally { await ui.close() }
})
test('type-ahead seeds search at end and Escape clears query before collapsing node', async () => {
  const s = snapshot()
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : s } }))
  try {
    await ui.click(button('Show runners'))
    const node = document.querySelector('[data-node-row]')
    await ui.click(node)
    await ui.key(node, 'f')
    const input = document.querySelector('input')
    assert.ok(document.activeElement === input, 'type-ahead focuses input')
    assert.equal(input.value, 'f')
    assert.equal(input.selectionStart, 1)
    await ui.key(input, 'Escape')
    assert.equal(input.value, '')
    assert.equal(node.getAttribute('aria-expanded'), 'true')
    assert.ok(document.querySelector('[data-runners-panel]'))
  } finally { await ui.close() }
})
test('native menu right click, overflow, Shift+F10, Escape preserve details and owner refresh', async () => {
  const s = snapshot(),
    calls = [],
    links = []
  host.profileRoutes = async () => [route]
  const ctx = {
    rest: async (path, opts) => {
      calls.push([path, opts])
      if (path.includes('/enrollment')) return enrollment()
      return s
    },
    os: {
      openExternal: async (url) => {
        links.push(url)
        return true
      }
    }
  }
  const ui = await mount(
    React.createElement(p.Runners, {
      ctx,
      features: { repair_sessions: true }
    })
  )
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    const row = document.querySelector('[data-runner-row]')
    await ui.click(row)
    for (const kind of ['right', 'overflow', 'keyboard']) {
      if (kind === 'right') {
        await React.act(async () =>
          row.dispatchEvent(
            new MouseEvent('contextmenu', {
              bubbles: true,
              cancelable: true,
              clientX: 20,
              clientY: 20,
              button: 2
            })
          )
        )
        await ui.flush()
      }
      if (kind === 'overflow') await ui.click(button('Actions for Fictional registration'))
      if (kind === 'keyboard') await ui.key(row, 'F10', { shiftKey: true })
      const menu = document.querySelector('[data-runners-menu]')
      assert.ok(menu, kind)
      assert.match(menu.textContent, /Refresh this runner/)
      assert.match(menu.textContent, /Investigate in new session/)
      assert.match(menu.textContent, /Mark maintenance…/)
      await ui.key(menu, 'Escape')
      assert.equal(document.querySelector('[data-runners-menu]'), null)
      assert.ok(document.querySelector('[data-runners-panel]'))
      assert.match(document.body.textContent, /Source observations/)
      assert.ok(document.activeElement === row, 'runner receives focus')
    }
    await ui.click(button('Actions for Fictional registration'))
    await ui.click(
      [...document.querySelectorAll('[role=menuitem]')].find(
        (e) => e.textContent === 'Refresh this runner'
      )
    )
    const refresh = calls.filter(
      ([path, o]) => path.includes('/refresh') && o.body.target_ids.length
    )
    assert.equal(refresh.length, 1)
    assert.equal(refresh[0][1].connectionId, route.connectionId)
    assert.deepEqual(refresh[0][1].body.target_ids, [s.data.runners[0].id])
    await ui.click(button('Actions for Fictional registration'))
    await ui.click(
      [...document.querySelectorAll('[role=menuitem]')].find(
        (e) => e.textContent === 'Open GitHub runner settings'
      )
    )
    assert.deepEqual(links, ['https://github.com/example/project/settings/actions/runners'])
  } finally {
    await ui.close()
  }
})
for (const [state, label] of [
  ['not_configured', 'Not configured'],
  ['ready', 'No registrations'],
  ['owner_disconnected', 'Owner gateway disconnected'],
  ['plugin_unavailable', 'Plugin unavailable'],
  ['owner_conflict', 'Ownership conflict'],
  ['partial', 'Partial permissions'],
  ['provider_unavailable', 'Provider outage'],
  ['configuration_error', 'Configuration error']
])
  test(`render ${state} and always keep chip`, async () => {
    const s = snapshot()
    s.data.state = state
    s.data.runners = []
    host.profileRoutes = async () => [route]
    const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async (path) => path.includes('/enrollment') ? enrollment() : s } }))
    try {
      assert.ok(button('Show runners'))
      await ui.click(button('Show runners'))
      assert.match(document.body.textContent, new RegExp(label))
      assert.notEqual(
        document.querySelector('[data-slot=runners-chip] [data-tone]').dataset.tone,
        'good'
      )
    } finally {
      await ui.close()
    }
  })
test('loading, unavailable, hidden polling and palette registration', async () => {
  let release
  host.profileRoutes = async () => [route]
  const contributions = [],
    dispose = []
  const ctx = {
    rest: async (path) => path.includes('/enrollment') ? enrollment() : new Promise((r) => (release = r)),
    register: (c) => { contributions.push(c); return () => contributions.splice(contributions.indexOf(c), 1) },
    onDispose: (f) => dispose.push(f)
  }
  p.default.register(ctx)
  assert.equal(contributions[0].order, 6)
  assert.equal(contributions[1].data.label, 'Show runners')
  const ui = await mount(contributions[0].render())
  try {
    await React.act(async () => contributions[1].data.run())
    await ui.flush()
    assert.match(document.body.textContent, /Loading inventory/)
    assert.ok(button('Show runners'))
    await React.act(async () => {
      Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        value: 'hidden'
      })
      document.dispatchEvent(new window.Event('visibilitychange'))
    })
    await ui.flush()
    const q = ui.client
      .getQueryCache()
      .getAll()
      .find((q) => q.queryKey[1] === 'directory')
    assert.equal(q.getObserversCount(), 1)
    assert.equal(q.isActive(), false)
    await React.act(async () => release(snapshot()))
    await ui.flush()
  } finally {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible'
    })
    await ui.close()
    dispose.forEach((f) => f())
  }
})
test('UI transport errors preserve chip and distinguish unavailable source classes', async () => {
  for (const [status, label] of [
    [404, 'Plugin unavailable'],
    [403, 'Partial permissions'],
    [503, 'Owner gateway disconnected']
  ]) {
    host.profileRoutes = async () => [route]
    const ui = await mount(
      React.createElement(p.Runners, {
        ctx: {
          rest: async () => {
            throw Object.assign(Error('raw secret'), { status })
          }
        }
      })
    )
    try {
      await ui.click(button('Show runners'))
      assert.match(document.body.textContent, new RegExp(label))
      assert.doesNotMatch(document.body.textContent, /raw secret/)
      assert.ok(button('Show runners'))
    } finally {
      await ui.close()
    }
  }
})
