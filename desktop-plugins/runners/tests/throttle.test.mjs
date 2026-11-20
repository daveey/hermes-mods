// Token throttle in the Runners panel: slots a limit will not let run are crossed out, the cap line shows the
// cap, and the chip's Workers bar hatches the throttled part. One test per path: pure maths, panel render,
// slider commit, chip, and a gateway without the throughput plugin.
import test from 'node:test'
import assert from 'node:assert/strict'
import { React, loadPlugin, mount } from './sdk-mock.mjs'

const P = await loadPlugin()
const NOW = Date.parse('2026-11-17T23:00:00Z')
const run = (n) => ({ board: 'default', task: `t_${n}`, title: `card ${n}`, started_at: NOW / 1000 - 60 })
const view = () => ({
  at: NOW,
  gateways: {
    state: 'ready', machines: [
      { name: 'hub', me: true, state: 'active', labels: ['machine:hub'], slots: 4, slots_source: 'local', running: [run(1)] },
      { name: 'worker-1', state: 'active', labels: [], slots: 6, slots_source: 'registry', running: [run(2), run(3), run(4)] },
      { name: 'worker-a1', state: 'active', labels: [], slots: 8, slots_source: 'registry', running: [run(5), run(6)] },
      { name: 'mini', state: 'active', labels: [], slots: null, slots_source: null, running: [] }
    ]
  },
  ci: { state: 'ready', machines: [] }
})
const row = (over = {}) => ({
  share: 0.5, cap_per_s: 500, rate_per_s: 800, avg_busy: 3, tokens_per_slot_per_s: 250, cost_source: 'measured',
  by_throttle: 2, allowed: 2, ...over
})
const throttle = (gateways) => ({
  percent: 40, cap_per_s: 1000, rate_per_s: 1600, holding: true,
  pools: [{ provider: 'anthropic', pacing: 'paced', pacing_factor: 0.03 }, { provider: 'xai-oauth', pacing: 'open', pacing_factor: null }],
  slots: { window_s: 900, gateways, pacing: { held_starts: 7, window_s: 300 } }
})

test('slotLimit: clamped to the machine; no limit known means nothing crossed out', () => {
  const m = { slots: 6, running: [] }
  assert.equal(P.slotLimit(m, null), null)
  assert.equal(P.slotLimit(m, row({ allowed: null })), null, 'throttle off: nothing crossed out')
  assert.equal(P.slotLimit({ slots: null }, row()), null, 'a machine without a slot cap has nothing to cross out')
  const t = P.slotLimit(m, row())
  assert.deepEqual([t.allowed, t.throttled], [2, 4])
  assert.match(t.why, /token throttle allows 2 of 6 slots here: 30k tok\/min share ÷ 15k per slot/)
  assert.deepEqual([P.slotLimit(m, row({ allowed: 99 })).allowed, P.slotLimit(m, row({ allowed: 99 })).throttled], [6, 0])
  assert.equal(P.slotLimit(m, row({ allowed: 0, by_throttle: 0 })).throttled, 6, 'level 100 %: every slot')
})

test('panel: slots beyond the token limit are crossed out, busy and free, with the reason and the cost column', async () => {
  const th = throttle({
    'worker-1': row(),                                                        // 3 busy, 6 slots, 2 allowed
    'worker-a1': row({ by_throttle: 5, allowed: 5, cost_source: 'fleet_median' }),
    hub: row({ by_throttle: null, allowed: null })           // no limit
  })
  const ui = await mount(React.createElement(P.Panel, { view: view(), now: NOW, throttle: th }))
  const slots = (name) => [...ui.container.querySelectorAll(`[data-machine="${name}"] .hermes-fleet-slot`)]
  const crossed = (name) => slots(name).map((e) => e.classList.contains('is-throttled'))
  assert.deepEqual(crossed('worker-1'), [false, false, true, true, true, true])
  assert.equal(slots('worker-1')[2].classList.contains('is-busy'), true, 'the third running card is over the limit')
  assert.match(slots('worker-1')[2].title, /over the limit: token throttle allows 2 of 6/)
  assert.match(slots('worker-1')[5].title, /^cannot start: token throttle allows 2 of 6/)
  assert.deepEqual(crossed('worker-a1'), [false, false, false, false, false, true, true, true])
  assert.match(slots('worker-a1')[7].title, /^cannot start: token throttle allows 5 of 8/)
  assert.equal(crossed('hub').some(Boolean), false)
  const tok = (name) => ui.container.querySelector(`[data-machine="${name}"] .hermes-fleet-tok`)
  assert.equal(tok('worker-1').textContent, '15k')  // 250 tokens/s per slot = 15k per minute
  assert.match(tok('worker-a1').title, /fleet median/)
  assert.equal(tok('mini').textContent, '–')
  assert.match(ui.container.querySelector('[data-machine="worker-1"] .hermes-fleet-info').dataset.tip, /4 slot\(s\) crossed out/)
  assert.match(ui.container.querySelector('[data-slot="runners-throttle"]').textContent,
    /Cap 60k tok\/min · fleet 96k tok\/min · pacing: anthropic paced 3% · 7 card starts held by pacing in the last 5 min/)
  await ui.close()
})

test('panel without the throughput plugin: no slider, nothing crossed out', async () => {
  const ui = await mount(React.createElement(P.Panel, { view: view(), now: NOW }))
  assert.equal(ui.container.querySelector('[data-slot="runners-throttle"]'), null)
  assert.equal(ui.container.querySelectorAll('.is-throttled').length, 0)
  assert.equal(ui.container.querySelector('[data-machine="worker-1"] .hermes-fleet-tok').textContent, '–')
  await ui.close()
})

test('limit line: read-only, pointing at the Throughput panel where it is changed', async () => {
  const th = { ...throttle({}), user_cap_per_s: 1000 }
  const ui = await mount(React.createElement(P.Panel, { view: view(), now: NOW, throttle: th }))
  const bar = ui.container.querySelector('[data-slot="runners-throttle"]')
  assert.equal(bar.querySelector('input'), null, 'no control here')
  assert.equal(bar.querySelector('button'), null)
  assert.match(bar.textContent, /^Cap 60k tok\/min \(yours\).*change it in Throughput$/)
  await ui.close()
})

test('cap line: whose cap, the budget, this gateway\'s open slots', () => {
  const base = { rate_per_s: 500, pools: [], slots: null }
  assert.equal(P.throttleLine({ ...base, cap_per_s: null }), 'No token cap · fleet 30k tok/min')
  assert.equal(P.throttleLine({ ...base, cap_per_s: 0 }), 'Cap 0 · holding every call · fleet 30k tok/min')
  assert.equal(P.throttleLine({ ...base, cap_per_s: 1000, user_cap_per_s: 1000, budget: { budget_per_s: 5000 },
    slot_gate: { allowed: 6, running: 4, per_slot_per_s: 50 } }),
  'Cap 60k tok/min (yours) · budget 300k · fleet 30k tok/min · here 4/6 slots at 3k/slot')
  assert.equal(P.throttleLine({ ...base, cap_per_s: 1000, user_cap_per_s: null, budget: { budget_per_s: 1000 },
    slot_gate: { allowed: 2, running: 0, per_slot_per_s: null } }),
  'Cap 60k tok/min (budget) · fleet 30k tok/min · here 0/2 slots (unmeasured)')
  assert.equal(P.throttleLine({ ...base, cap_per_s: null, limit_mode: 'no_limit' }), 'No token limit (budget overridden) · fleet 30k tok/min')
})

test('an advisory row from another gateway is marked as an estimate', () => {
  const t = P.slotLimit({ slots: 6, running: [] }, row({ enforced: false }))
  assert.match(t.why, /\(estimate; that gateway enforces its own count\)$/)
  assert.doesNotMatch(P.slotLimit({ slots: 6, running: [] }, row({ enforced: true })).why, /estimate/)
})

test('a machine the autoscaler is enrolling reads "enrolling", not "draining"', async () => {
  const v = view()
  v.gateways.machines.push({ name: 'worker-a3', state: 'draining', labels: [], slots: 16, slots_source: 'registry', running: [],
    hold_reason: 'worker-scaler: enrolling i-0abc: readiness pending', hold_by: 'admin', hold_at: NOW - 20 * 60000 })
  const ui = await mount(React.createElement(P.Panel, { view: v, now: NOW }))
  const r = ui.container.querySelector('[data-machine="worker-a3"]')
  assert.equal(r.querySelector('.hermes-fleet-drain-label').textContent, 'enrolling')
  assert.match(r.querySelector('.hermes-fleet-drain-label').title, /^enrolling: worker-scaler: enrolling i-0abc: readiness pending \(held by admin, 20m ago\); a new machine/)
  assert.equal(P.holdWord({ state: 'draining', hold_reason: 'worker-scaler: consolidate' }), 'draining')
  await ui.close()
})

test('summarize and the chip: usable slots, a full bar once the usable ones are taken', () => {
  const th = throttle({ 'worker-1': row(), 'worker-a1': row({ allowed: 2 }) })  // 2 of 6 and 2 of 8
  const s = P.summarize(view(), th)
  assert.deepEqual(s.workers, { busy: 5, slots: 14, usable: 4 })
  assert.deepEqual(s.hub, { busy: 1, slots: 4, usable: 4 })
  assert.equal(P.meterTone(s.workers), 'full', '5 busy of 4 usable: no room, although 9 slots look free')
  assert.equal(P.meterTone(P.summarize(view()).workers), 'ok')
  assert.deepEqual(P.summarize(view()).workers, { busy: 5, slots: 14 }, 'no throttle data: the old shape')
})
