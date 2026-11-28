import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React, host } from './sdk-mock.mjs'
const P = await loadPlugin()

test('warming strip renders loading rather than an error or empty counts', async () => {
  const view = await mount(React.createElement(P.Lanes, {ctx: {rest: async () => ({configured: true, warming: true})}}))
  try {
    assert.equal(view.container.querySelector('[data-view=warming]')?.textContent, 'Loading…')
    assert.ok(view.container.querySelector('[data-lane-pill]') === null)
    assert.doesNotMatch(view.container.textContent, /Unavailable|error/i)
  } finally { await view.close() }
})

test('warming GET retries soon and stops its retry on teardown', async () => {
  const deadlines = new Map(); let next = 0, calls = 0
  const timers = {setTimeout(fn, ms) {const id = ++next; deadlines.set(id, {fn, ms}); return id},
    clearTimeout(id) {deadlines.delete(id)}, setInterval() {return 1}, clearInterval() {}}
  const source = P.createSource({rest: async (path) => {if (path === '/v1/lanes/steering') throw Error('old server'); calls++; return {configured: true, warming: true}}}, host)
  const stop = source.subscribe({hours: 6, window: '6h', expanded: false}, () => {}, () => {}, timers)
  const flush = () => new Promise(r => setTimeout(r, 10))
  try {
    await flush()
    assert.equal(calls, 1)
    const retry = [...deadlines.values()].find(t => t.ms === 1000)
    assert.ok(retry)
    retry.fn(); await flush()
    assert.equal(calls, 2)
  } finally {stop()}
  assert.equal(deadlines.size, 0)
})
