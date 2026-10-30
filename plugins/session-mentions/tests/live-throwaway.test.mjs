import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { hideThrowaway, deleteThrowaway } from './live-throwaway.mjs'

// Advance a virtual clock so the production 10s quiet / 30s deadline run unchanged.
async function run(t, respond, action = deleteThrowaway) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 100000 })
  const calls = []
  let revision = 0
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const entry = { url, ...options, at: Date.now() }
    calls.push(entry)
    const reply = respond(options.method, Date.now(), calls, entry)
    if (reply instanceof Response) return reply
    if (url.includes('/mutation-snapshot')) {
      return Response.json({ runtime_revision: ++revision, runtime_generation: 7 })
    }
    return Response.json({ detail: reply === 409 ? 'session_busy' : '' }, { status: reply })
  })
  let done = false, error
  const started = Date.now()
  const result = action('http://127.0.0.1:1234', 'test-token', 'test/key')
    .catch(e => { error = e }).finally(() => { done = true })
  for (let i = 0; i < 40 && !done; i++) {
    await setImmediate()
    if (!done) t.mock.timers.tick(1000)
  }
  assert.ok(done, 'cleanup must be bounded')
  await result
  return { calls, error, elapsed: Date.now() - started }
}
const mutations = calls => calls.filter(c => ['PATCH', 'DELETE'].includes(c.method))
const reads = calls => calls.filter(c => c.method === 'GET' && !c.url.includes('/mutation-snapshot'))
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

test('hide uses a fresh snapshot and fenced PATCH after row persistence / busy retries', async t => {
  let snapshots = 0, patches = 0
  const { calls, error } = await run(t, (method, now, calls, c) => {
    if (c.url.includes('/mutation-snapshot') && ++snapshots === 1) return new Response(null, { status: 404 })
    return method === 'PATCH' && ++patches === 1 ? 409 : 200
  }, hideThrowaway)
  assert.ifError(error)
  assert.equal(snapshots, 3)
  assert.equal(patches, 2)
  const bodies = mutations(calls).map(c => JSON.parse(c.body))
  bodies.forEach((body, i) => {
    assert.equal(body.hidden, true)
    assert.equal(body.profile, 'default')
    assert.equal(body.expected_revision, i + 1)
    assert.match(body.request_id, uuid)
  })
  assert.notEqual(bodies[0].request_id, bodies[1].request_id)
})

test('delete uses fresh revision and generation fences for each write guard retry', async t => {
  let deletes = 0
  const { error, calls } = await run(t, method => method === 'DELETE' ? (++deletes < 3 ? 409 : 200) : 404)
  assert.ifError(error)
  assert.equal(deletes, 3)
  const ids = new Set()
  mutations(calls).forEach((c, i) => {
    const url = new URL(c.url)
    assert.equal(url.pathname, '/api/sessions/test%2Fkey')
    assert.equal(url.searchParams.get('profile'), 'default')
    assert.equal(url.searchParams.get('expected_revision'), String(i + 1))
    assert.equal(url.searchParams.get('expected_generation'), '7')
    assert.match(url.searchParams.get('request_id'), uuid)
    ids.add(url.searchParams.get('request_id'))
  })
  assert.equal(ids.size, 3)
  assert.equal(reads(calls)[0].at, 102000)
})

test('waits for ten seconds of confirmed absence, with encoded key and explicit profile', async t => {
  const { calls, error, elapsed } = await run(t, method => method === 'DELETE' ? 200 : 404)
  assert.ifError(error)
  assert.ok(elapsed >= 10000)
  assert.ok(reads(calls).length >= 2)
  assert.ok(reads(calls).every(c => c.url.endsWith('/api/sessions/test%2Fkey?profile=default')))
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
  const lastDelete = mutations(calls).at(-1)
  assert.ok(calls.at(-1).at - lastDelete.at >= 10000)
})

for (const action of [hideThrowaway, deleteThrowaway]) {
  for (const detail of ['client_upgrade_required: update the app', 'stale_revision', 'stale_generation', 'unknown_conflict']) {
    test(`${action.name} fails fast for ${detail}, not a write guard`, async t => {
      const { calls, error, elapsed } = await run(t, method => ['PATCH', 'DELETE'].includes(method)
        ? Response.json({ detail }, { status: 409 }) : 200, action)
      assert.match(error?.message || '', new RegExp(detail))
      assert.equal(mutations(calls).length, 1)
      assert.equal(elapsed, 0)
    })
  }
}

test('already absent snapshot needs no DELETE but still verifies stable absence', async t => {
  const { calls, error, elapsed } = await run(t, () => new Response(null, { status: 404 }))
  assert.ifError(error)
  assert.equal(mutations(calls).length, 0)
  assert.ok(elapsed >= 10000)
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
    const { error } = await run(t, m => m === method ? new Response(null, { status: 500 }) : 200)
    assert.match(error?.message || '', /REST 500/)
  })
}
