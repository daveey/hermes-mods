import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { deleteThrowaway } from './live-throwaway.mjs'

// Advance a virtual clock so the production 10s quiet / 30s deadline run unchanged.
async function run(t, respond) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 100000 })
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, ...options, at: Date.now() })
    return new Response(null, { status: respond(options.method, Date.now(), calls) })
  })
  let done = false, error
  const started = Date.now()
  const result = deleteThrowaway('http://127.0.0.1:1234', 'test-token', 'test/key')
    .catch(e => { error = e }).finally(() => { done = true })
  for (let i = 0; i < 40 && !done; i++) {
    await setImmediate()
    if (!done) t.mock.timers.tick(1000)
  }
  assert.ok(done, 'cleanup must be bounded')
  await result
  return { calls, error, elapsed: Date.now() - started }
}

test('waits for ten seconds of confirmed absence, with encoded key and explicit profile', async t => {
  const { calls, error, elapsed } = await run(t, method => method === 'DELETE' ? 200 : 404)
  assert.ifError(error)
  assert.ok(elapsed >= 10000)
  assert.ok(calls.filter(c => c.method === 'GET').length >= 2)
  assert.ok(calls.every(c => c.url.endsWith('/api/sessions/test%2Fkey?profile=default')))
  assert.ok(calls.every(c => c.headers['X-Hermes-Session-Token'] === 'test-token'))
})

test('re-deletes a late close write after an initially successful DELETE and 404', async t => {
  let deletes = 0
  const { calls, error } = await run(t, (method, now) => {
    if (method === 'DELETE') { deletes++; return 200 }
    return now >= 103000 && deletes === 1 ? 200 : 404
  })
  assert.ifError(error)
  assert.equal(deletes, 2)
  const lastDelete = calls.filter(c => c.method === 'DELETE').at(-1)
  assert.ok(calls.at(-1).at - lastDelete.at >= 10000)
})

test('retries a write guard before checking absence', async t => {
  let deletes = 0
  const { error, calls } = await run(t, method => method === 'DELETE' ? (++deletes < 3 ? 409 : 200) : 404)
  assert.ifError(error)
  assert.equal(deletes, 3)
  assert.equal(calls.find(c => c.method === 'GET').at, 102000)
})

test('fails within thirty seconds when a closing writer keeps recreating the row', async t => {
  const { error, elapsed } = await run(t, () => 200)
  assert.match(error?.message || '', /not stably absent after 30s/)
  assert.ok(elapsed <= 30000)
})

test('fails within thirty seconds for a persistent write guard', async t => {
  const { error, elapsed } = await run(t, () => 409)
  assert.match(error?.message || '', /not stably absent after 30s/)
  assert.ok(elapsed <= 30000)
})

for (const method of ['DELETE', 'GET']) {
  test(`does not treat ${method} server errors as absence`, async t => {
    const { error } = await run(t, m => m === method ? 500 : 200)
    assert.match(error?.message || '', /REST 500/)
  })
}
