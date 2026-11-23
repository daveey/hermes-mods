import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const T = await loadPlugin()
// Same React runtime and DOM, actual components from both independently loaded plugins.
const L = await loadPlugin(new URL('../../lanes/plugin.js', import.meta.url))

for (const lanesFirst of [false, true]) test(`strip relocation single lease, load order lanesFirst=${lanesFirst}`, async () => {
  let active = 0, peak = 0
  function Probe() {
    React.useEffect(() => { active++; peak = Math.max(peak, active); return () => { active-- } }, [])
    return React.createElement('button', { 'data-throughput-probe': true }, 'tok/m · cards/m')
  }
  const views = []
  const host = () => mount(React.createElement(L.StripRight))
  const fallback = () => mount(React.createElement(T.ChipPlacement, { ctx: {}, component: Probe }))
  let stop
  try {
    let strip
    if (lanesFirst) { strip = await host(); views.push(strip) }
    await React.act(async () => { stop = T.registerStripChip({}, Probe) })
    const bar = await fallback(); views.push(bar)
    if (!lanesFirst) {
      assert.equal(bar.container.querySelectorAll('[data-throughput-probe]').length, 1)
      strip = await host(); views.push(strip)
    }
    await bar.flush()
    assert.equal(strip.container.querySelectorAll('[data-throughput-probe]').length, 1)
    assert.equal(bar.container.querySelectorAll('[data-throughput-probe]').length, 0)
    assert.equal(active, 1)
    // More than one configured pane must still elect exactly one host.
    const second = await host(); views.push(second)
    assert.equal(second.container.querySelectorAll('[data-throughput-probe]').length, 0)
    await strip.close(); views.splice(views.indexOf(strip), 1)
    await bar.flush()
    assert.equal(second.container.querySelectorAll('[data-throughput-probe]').length, 1)
    await second.close(); views.splice(views.indexOf(second), 1)
    await bar.flush()
    assert.equal(bar.container.querySelectorAll('[data-throughput-probe]').length, 1)
    // Hot-reload host and contribution; a stale disposer cannot delete the replacement.
    strip = await host(); views.push(strip)
    let replacement
    await React.act(async () => { replacement = T.registerStripChip({}, Probe); stop() })
    stop = replacement
    await bar.flush()
    assert.equal(strip.container.querySelectorAll('[data-throughput-probe]').length, 1)
    assert.equal(peak, 1, 'never two active throughput subscriptions across independent roots')
  } finally {
    await React.act(async () => stop?.())
    for (const v of views.reverse()) await v.close()
  }
  assert.equal(active, 0)
  assert.equal(T.stripArea().hosts.size, 0)
  assert.equal(T.stripArea().listeners.size, 0)
})
