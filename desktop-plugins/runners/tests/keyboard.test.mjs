import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, route, snapshot, loadPlugin, mount, button, enrollment } from './sdk-mock.mjs'
const p = await loadPlugin()
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
      assert.match(menu.textContent, /Investigate in new session — not available yet/)
      await ui.key(menu, 'Escape')
      assert.equal(document.querySelector('[data-runners-menu]'), null)
      assert.ok(document.querySelector('[data-runners-panel]'))
      assert.match(document.body.textContent, /Source observations/)
      assert.equal(document.activeElement, row)
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
    register: (c) => contributions.push(c),
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
