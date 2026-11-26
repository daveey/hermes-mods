// LLM split chip + panel: share maths, verdict words, and the rendered chip/panel against a mock ctx.rest
// (load, open via click, drag the traffic bar -> PUT manual, Auto -> PUT auto, keyboard, error path).
import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, button, React } from './sdk-mock.mjs'

const P = await loadPlugin()
const h = React.createElement
const POOLS = [{ id: 'anthropic', label: 'Claude' }, { id: 'openai-codex', label: 'GPT' }, { id: 'xai-oauth', label: 'Grok' }]

function snap(over = {}) {
  return {
    pools: POOLS, mode: 'manual', homes: ['/h'], mixed_homes: [], updated_at: 1,
    quota: { source: 'account-usage', at: Date.now() / 1000 - 30, dry: [],
      share: { anthropic: 13.1, 'openai-codex': 86.3, 'xai-oauth': 0.6 },
      accounts: { anthropic: { n: 10, out: 8, headroom: 122 }, 'openai-codex': { n: 12, out: 2, headroom: 805 } } },
    traffic: { weights: { anthropic: 1, 'openai-codex': 1, 'xai-oauth': 1 },
      share: { anthropic: 100 / 3, 'openai-codex': 100 / 3, 'xai-oauth': 100 / 3 } },
    ...over
  }
}

function fakeCtx(initial) {
  const calls = []
  let state = initial
  return {
    calls,
    set(s) { state = s },
    rest: async (path, opts = {}) => {
      calls.push({ path, method: opts.method || 'GET', body: opts.body })
      if (state instanceof Error) throw state
      if (opts.method === 'PUT') {
        const b = opts.body
        if (b.mode === 'auto') state = snap({ mode: 'auto', traffic: { weights: { anthropic: 13, 'openai-codex': 86, 'xai-oauth': 1 },
          share: { anthropic: 13, 'openai-codex': 86, 'xai-oauth': 1 } } })
        else state = snap({ mode: 'manual', traffic: { weights: {}, share: b.share } })
      }
      return state
    }
  }
}

const chip = () => document.querySelector('[data-testid="llm-split-chip"]')
const bar = () => document.querySelector('[data-testid="traffic-bar"]')

test('splitAt keeps Grok, clamps, gives GPT the rest', () => {
  assert.deepEqual(P.splitAt(30, 1), { anthropic: 30, 'openai-codex': 69, 'xai-oauth': 1 })
  assert.deepEqual(P.splitAt(120, 1), { anthropic: 99, 'openai-codex': 0, 'xai-oauth': 1 })
  assert.deepEqual(P.splitAt(-5, 0), { anthropic: 0, 'openai-codex': 100, 'xai-oauth': 0 })
})

test('summarize: no over/under verdict, only ANT dry', () => {
  assert.equal(P.summarize(snap()).word, '')                    // 33 traffic vs 13 quota: no "ANT over"
  assert.equal(P.summarize(snap({ mode: 'auto' })).word, '')
  assert.equal(P.summarize(snap(), { anthropic: 2, 'openai-codex': 97, 'xai-oauth': 1 }).word, '') // no "GPT over"
  const dry = snap({ quota: { ...snap().quota, dry: ['anthropic'], share: { anthropic: 0, 'openai-codex': 100 } } })
  assert.equal(P.summarize(dry).word, 'ANT dry')
  assert.equal(P.noteFor(P.summarize(snap())), 'Manual split')
  assert.equal(P.ratio({ anthropic: 4, 'openai-codex': 94.4, 'xai-oauth': 1 }), '4:94:1:0')
  assert.equal(P.ratio({ anthropic: 10, 'openai-codex': 70, 'xai-oauth': 5, gemini: 15 }), '10:70:5:15')
})

test('validSnapshot rejects junk', () => {
  assert.ok(P.validSnapshot(snap()))
  for (const bad of [null, {}, { pools: [] }, { pools: [], traffic: {} }]) assert.equal(P.validSnapshot(bad), false)
})

test('register: one titleBar.tools.right item, 8 cells wide right of throughput, CSS injected and removed', () => {
  const regs = []
  const disposers = []
  P.default.register({ register: (r) => regs.push(r), onDispose: (f) => disposers.push(f), rest: async () => snap() })
  assert.equal(regs.length, 1)
  assert.equal(regs[0].area, 'titleBar.tools.right')
  assert.equal(regs[0].order, 50) // throughput's titlebar chip is order 40, so it sits to the left
  assert.equal(regs[0].data.span, 8)
  assert.ok(React.isValidElement(regs[0].data.icon))
  const css = [...document.head.querySelectorAll('style')].map((s) => s.textContent).join('')
  assert.ok(css.includes('.llms-tool{width:calc(8*var(--titlebar-control-size,24px))'))
  assert.ok(css.includes('.llms-mini{position:relative;display:flex;width:84px'))
  disposers.forEach((f) => f())
  assert.ok(![...document.head.querySelectorAll('style')].some((s) => s.textContent.includes('.llms-chip')))
})

test('chip renders the split and verdict; click opens the panel with quota, traffic and accounts', async () => {
  const ctx = fakeCtx(snap())
  const m = await mount(h(P.Chip, { ctx }))
  assert.equal(chip().querySelector('.llms-num').textContent, '33:33:33:0')
  assert.equal(chip().querySelector('.llms-word'), null)
  assert.ok(chip().querySelector('.llms-qm').style.left.startsWith('13.1'))
  await m.click(chip())
  assert.ok(bar(), 'panel open')
  const text = document.body.textContent
  assert.match(text, /Claude2\/10 readyquota 13%traffic 33%/)
  assert.match(text, /GPT10\/12 readyquota 86%/)
  assert.doesNotMatch(text, /vs quota/)
  assert.match(text, /Manual split/)
  assert.match(text, /weights 1:1:1/)
  await m.close()
})

test('drag on the traffic bar sets a manual split and PUTs it on release', async () => {
  const ctx = fakeCtx(snap())
  const m = await mount(h(P.Chip, { ctx }))
  await m.click(chip())
  const b = bar()
  b.getBoundingClientRect = () => ({ left: 100, width: 200, top: 0, height: 16, right: 300, bottom: 16 })
  const PE = window.PointerEvent || MouseEvent
  const fire = async (type, x) => React.act(async () => b.dispatchEvent(new PE(type, { bubbles: true, cancelable: true, clientX: x, button: 0 })))
  await fire('pointerdown', 140)       // 20%
  await fire('pointermove', 150)       // 25% while dragging: chip reads the draft
  assert.equal(chip().querySelector('.llms-num').textContent, '25:42:33:0')
  await fire('pointerup', 160)         // 30%
  await m.flush()
  const put = ctx.calls.find((c) => c.method === 'PUT')
  assert.ok(put, 'PUT sent')
  assert.deepEqual(put.body, { mode: 'manual', share: { anthropic: 30, 'openai-codex': 36.7, 'xai-oauth': 33.3 } })
  assert.equal(chip().querySelector('.llms-num').textContent, '30:37:33:0')
  assert.equal(ctx.calls.filter((c) => c.method === 'PUT').length, 1, 'moves do not PUT')
  await m.close()
})

test('Grok boundary changes Grok independently, cancel and invalid geometry never PUT', async () => {
  const ctx = fakeCtx(snap({ traffic: { weights: {}, share: { anthropic: 20, 'openai-codex': 55, 'xai-oauth': 25 } } }))
  const m = await mount(h(P.Chip, { ctx }))
  await m.click(chip())
  const knob = document.querySelector('[aria-label="Grok share of traffic"]')
  assert.ok(knob, 'second accessible boundary')
  bar().getBoundingClientRect = () => ({ left: 100, width: 200 })
  const PE = window.PointerEvent || MouseEvent
  const fire = (type, x) => React.act(async () => knob.dispatchEvent(new PE(type, { bubbles: true, cancelable: true, clientX: x, button: 0 })))
  await fire('pointerdown', 250)
  await fire('pointermove', 220)
  await fire('pointerup', 220)
  await m.flush()
  assert.deepEqual(ctx.calls.filter(c => c.method === 'PUT').at(-1).body.share,
    { anthropic: 20, 'openai-codex': 40, 'xai-oauth': 40 })
  await fire('pointerdown', 220); await fire('pointercancel', 180); await fire('pointerup', 180)
  assert.equal(ctx.calls.filter(c => c.method === 'PUT').length, 1)
  bar().getBoundingClientRect = () => ({ left: 100, width: 0 })
  await fire('pointerdown', 220); await fire('pointerup', 180)
  assert.equal(ctx.calls.filter(c => c.method === 'PUT').length, 1)
  await m.close()
})

test('Grok keys clamp at both extremes and canceled keyboard draft never commits', async () => {
  const ctx = fakeCtx(snap({ traffic: { weights: {}, share: { anthropic: 20, 'openai-codex': 55, 'xai-oauth': 25 } } }))
  const m = await mount(h(P.Chip, { ctx }))
  await m.click(chip())
  const knob = document.querySelector('[aria-label="Grok share of traffic"]')
  assert.ok(knob)
  const key = k => React.act(async () => knob.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true })))
  await key('End')
  assert.equal(knob.getAttribute('aria-valuenow'), '80')
  const ant = document.querySelector('[aria-label="Claude share of traffic"]')
  assert.notEqual(knob.style.top, ant.style.top, 'coincident boundaries remain separately hittable')
  await key('Home')
  assert.equal(knob.getAttribute('aria-valuenow'), '0')
  await key('Escape')
  await React.act(async () => new Promise(r => setTimeout(r, P.KEY_COMMIT_MS + 50)))
  assert.equal(ctx.calls.filter(c => c.method === 'PUT').length, 0)
  await m.close()
})

test('Auto button PUTs mode auto and the chip says auto', async () => {
  const ctx = fakeCtx(snap())
  const m = await mount(h(P.Chip, { ctx }))
  await m.click(chip())
  await m.click(button('Auto'))
  assert.deepEqual(ctx.calls.at(-1), { path: '/v1/split', method: 'PUT', body: { mode: 'auto' } })
  assert.equal(chip().querySelector('.llms-word'), null)
  assert.equal(chip().querySelector('.llms-num').textContent, '13:86:1:0')
  assert.match(document.body.textContent, /Follows quota, re-checked every 5 min/)
  await m.close()
})

test('arrow keys on the knob move Claude by 1 (shift 10) and commit once after a pause', async () => {
  const ctx = fakeCtx(snap({ traffic: { weights: {}, share: { anthropic: 20, 'openai-codex': 79, 'xai-oauth': 1 } } }))
  const m = await mount(h(P.Chip, { ctx }))
  await m.click(chip())
  const knob = document.querySelector('[role="slider"]')
  const key = async (k, shift = false) => React.act(async () => knob.dispatchEvent(new KeyboardEvent('keydown', { key: k, shiftKey: shift, bubbles: true })))
  await key('ArrowRight'); await key('ArrowRight', true)
  assert.equal(knob.getAttribute('aria-valuenow'), '31')
  assert.equal(ctx.calls.filter((c) => c.method === 'PUT').length, 0)
  await React.act(async () => new Promise((r) => setTimeout(r, P.KEY_COMMIT_MS + 50)))
  await m.flush()
  const puts = ctx.calls.filter((c) => c.method === 'PUT')
  assert.equal(puts.length, 1)
  assert.deepEqual(puts[0].body.share, { anthropic: 31, 'openai-codex': 68, 'xai-oauth': 1 })
  await m.close()
})

test('a failed PUT shows the error and reverts the draft', async () => {
  const ctx = fakeCtx(snap())
  const m = await mount(h(P.Chip, { ctx }))
  await m.click(chip())
  ctx.set(new Error('config.yaml is read-only'))
  await m.click(button('Auto'))
  assert.match(document.body.textContent, /config.yaml is read-only/)
  await m.close()
})

test('backend down: chip shows a dim placeholder, no crash', async () => {
  const ctx = fakeCtx(new Error('404'))
  const m = await mount(h(P.Chip, { ctx }))
  assert.equal(chip().querySelector('.llms-num').textContent, '–:–:–:–')
  assert.equal(chip().querySelector('.llms-word').textContent, 'off')
  await m.close()
})

test('keyboard-activated titlebar button (detail 0) toggles the panel; a mouse click (detail 1) does not double-toggle', async () => {
  const ctx = fakeCtx(snap())
  const t = P.tool(ctx)
  const m = await mount(h(P.Chip, { ctx }))
  await React.act(async () => t.onSelect({ detail: 0 }))
  await m.flush()
  assert.ok(bar(), 'opened by keyboard')
  await React.act(async () => t.onSelect({ detail: 1 }))
  await m.flush()
  assert.ok(bar(), 'mouse click left to the trigger')
  const outer = {}
  await React.act(async () => t.onSelect({ detail: 1, target: outer, currentTarget: outer }))
  await m.flush()
  assert.ok(!bar(), 'outer button padding closes the panel')
  await React.act(async () => t.onSelect({ detail: 1, target: outer, currentTarget: outer }))
  await m.flush()
  assert.ok(bar(), 'outer button padding opens the panel')
  await m.close()
})
