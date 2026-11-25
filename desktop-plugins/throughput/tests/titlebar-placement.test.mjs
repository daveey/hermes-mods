import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const T = await loadPlugin()

// The titlebar tool hosts the chip: the status-bar fallback stands down, only one Throughput subscription runs,
// the chip is a span (no button nested in the titlebar's button), and Enter on the titlebar button opens the panel.
test('titlebar placement hosts the single chip; status-bar fallback stands down', async () => {
  let active = 0, peak = 0
  function Probe() {
    React.useEffect(() => { active++; peak = Math.max(peak, active); return () => { active-- } }, [])
    return React.createElement('span', { 'data-throughput-probe': true }, 'tok/m')
  }
  const bar = await mount(React.createElement(T.ChipPlacement, { ctx: {}, component: Probe }))
  assert.equal(bar.container.querySelectorAll('[data-throughput-probe]').length, 1)
  const tool = T.titlebarTool({}, Probe)
  const tb = await mount(tool.icon)
  try {
    await bar.flush()
    assert.equal(tb.container.querySelectorAll('[data-throughput-probe]').length, 1)
    assert.equal(bar.container.querySelectorAll('[data-throughput-probe]').length, 0)
    assert.equal(active, 1)
    assert.equal(peak, 1, "never two active subscriptions")
    await tb.close()
    await bar.flush()
    assert.equal(bar.container.querySelectorAll('[data-throughput-probe]').length, 1, 'fallback returns when the titlebar unmounts')
  } finally {
    await bar.close()
  }
  assert.equal(T.stripArea().hosts.size, 0)
})

test('titlebar tool: keyboard select opens the panel, mouse select leaves it to the span trigger', () => {
  const tool = T.titlebarTool({})
  let shown = 0
  const on = () => shown++
  window.addEventListener(T.TOGGLE_EVENT, on)
  try {
    tool.onSelect({ detail: 1 })
    assert.equal(shown, 0)
    tool.onSelect({ detail: 0 })
    assert.equal(shown, 1)
    const outer = {}
    tool.onSelect({ detail: 1, target: outer, currentTarget: outer })
    assert.equal(shown, 2, 'outer button padding toggles')
    tool.onSelect({ detail: 1, target: {}, currentTarget: outer })
    assert.equal(shown, 2, 'span descendants are handled by Radix, not twice')
  } finally {
    window.removeEventListener(T.TOGGLE_EVENT, on)
  }
})
