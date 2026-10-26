import test from 'node:test'
import assert from 'node:assert/strict'
import { route, snapshot, loadPlugin, enrollment } from './sdk-mock.mjs'
const p = await loadPlugin()
test('explicitly unenrolled profiles never fetch inventory and render a clear state', async () => {
  const calls = []
  const a = p.createTransport({ rest: async (path) => {
    calls.push(path)
    return { contract_version: 1, profile: 'example', enrolled: false, capabilities: [] }
  } }, { profileRoutes: async () => [route] })
  const sources = await a.inventory()
  assert.deepEqual(calls, ['/v1/enrollment?profile=example'])
  assert.equal(sources[0].enrolled, false)
  assert.equal(sources[0].error, undefined)
  assert.match(p.projectFleet(sources).messages.join(' '), /Not enrolled/)
})
test('enrollment revocation clears cached inventory and never fetches it again', async () => {
  let enrolled = true, reads = 0
  const a = p.createTransport({ rest: async (path) => {
    if (path.includes('/enrollment')) return { ...enrollment(), enrolled, capabilities: enrolled ? ['inventory'] : [] }
    reads++
    return snapshot()
  } }, { profileRoutes: async () => [route] })
  assert.ok((await a.inventory())[0].snapshot)
  enrolled = false
  const source = (await a.inventory())[0]
  assert.equal(source.snapshot, undefined)
  assert.equal(source.enrolled, false)
  assert.equal(reads, 1)
  await assert.rejects(a.refresh(source), /disconnected/)
})
test('invalid enrollment or discovery failure cannot fall through to inventory', async () => {
  for (const response of [null, {}, { ...enrollment(), profile: 'other' },
    { ...enrollment(), enrolled: 'true' }, { ...enrollment(), capabilities: [] }]) {
    const calls = []
    const a = p.createTransport({ rest: async (path) => { calls.push(path); return response } },
      { profileRoutes: async () => [route] })
    assert.match((await a.inventory())[0].error, /Invalid enrollment/)
    assert.equal(calls.length, 1)
  }
})
test('legacy enrollment 404 retains validated inventory as deployment-pending, never ready', async () => {
  const calls = []
  const a = p.createTransport({ rest: async (path) => {
    calls.push(path)
    if (path.includes('/enrollment')) throw Object.assign(Error('Not Found'), { status: 404 })
    return snapshot()
  } }, { profileRoutes: async () => [route] })
  const sources = await a.inventory()
  assert.ok(sources[0].snapshot)
  assert.match(sources[0].error, /Deployment pending/)
  assert.equal(calls.length, 2)
  const fleet = p.projectFleet(sources)
  assert.ok(fleet.rows.length > 0)
  assert.equal(fleet.counts.ready, 0)
  await assert.rejects(a.refresh(sources[0]), /disconnected/)
})
test('legacy fallback never adopts wrong-profile inventory or bypasses authorization', async () => {
  for (const status of [401, 403, 503]) {
    let reads = 0
    const a = p.createTransport({ rest: async () => {
      reads++
      throw Object.assign(Error('private detail'), { status })
    } }, { profileRoutes: async () => [route] })
    assert.equal((await a.inventory())[0].snapshot, undefined)
    assert.equal(reads, 1)
  }
  const wrong = snapshot()
  wrong.meta.owner.profile = 'other'
  const a = p.createTransport({ rest: async (path) => {
    if (path.includes('/enrollment')) throw Object.assign(Error('Not Found'), { status: 404 })
    return wrong
  } }, { profileRoutes: async () => [route] })
  const result = (await a.inventory())[0]
  assert.equal(result.snapshot, undefined)
  assert.match(result.error, /Owner identity/)
})
test('legacy fallback retains prior rows on unavailable inventory and recovers after upgrade', async () => {
  let mode = 'legacy'
  const a = p.createTransport({ rest: async (path) => {
    if (path.includes('/enrollment')) {
      if (mode !== 'upgraded') throw Object.assign(Error('Not Found'), { status: 404 })
      return enrollment()
    }
    if (mode === 'unavailable') throw Object.assign(Error('Not Found'), { status: 404 })
    return snapshot()
  } }, { profileRoutes: async () => [route] })
  const prior = (await a.inventory())[0].snapshot
  mode = 'unavailable'
  assert.equal((await a.inventory())[0].snapshot, prior)
  mode = 'upgraded'
  assert.equal((await a.inventory())[0].error, undefined)
})
test('same-owner epoch advance recovers inventory but old captured actions remain fenced', async () => {
  let s = snapshot()
  const a = p.createTransport({ rest: async (path) => path.includes('/enrollment') ? enrollment() : s }, { profileRoutes: async () => [route] })
  const old = (await a.inventory())[0]
  s = snapshot()
  s.meta.owner.owner_epoch = 2
  const current = (await a.inventory())[0]
  assert.equal(current.error, undefined)
  assert.equal(current.snapshot.meta.owner.owner_epoch, 2)
  await assert.rejects(a.refresh(old), /Owner identity/)
  s = snapshot()
  assert.match((await a.inventory())[0].error, /Owner identity/)
})
test('explicit route profile query, stable owner cache key, never ambient rest', async () => {
  const calls = []
  const a = p.createTransport(
    {
      rest: async (...args) => {
        calls.push(args)
        if (args[0].includes('/enrollment')) return enrollment()
        return snapshot()
      }
    },
    { profileRoutes: async () => [route] }
  )
  const sources = await a.inventory()
  assert.equal(sources.length, 1)
  assert.equal(calls[0][0], '/v1/enrollment?profile=example')
  assert.equal(calls[1][0], '/v1/inventory?profile=example')
  assert.equal(calls[0][1].connectionId, 'example')
  assert.deepEqual(p.ownerQueryKey(sources[0].snapshot.meta.owner), [
    'runners',
    'owner',
    '00000000-0000-4000-8000-000000000001',
    'example'
  ])
  await assert.rejects(a.request({ ...route, connectionId: '' }, '/inventory'), /route/i)
  assert.equal(calls.length, 2)
})
test('route replacement mid request discards response', async () => {
  let routes = [route],
    release
  const a = p.createTransport(
    { rest: () => new Promise((r) => (release = r)) },
    { profileRoutes: async () => routes }
  )
  const pending = a.inventory()
  while (!release) await new Promise((r) => setTimeout(r, 0))
  routes = [{ ...route, targetProfile: 'other' }]
  release(snapshot())
  const result = await pending
  assert.equal(result[0].snapshot, undefined)
  assert.match(result[0].error, /route|disconnected/i)
})
test('owner profile mismatch and owner change fail closed', async () => {
  let s = snapshot()
  s.meta.owner.profile = 'other'
  const a = p.createTransport({ rest: async (path) => path.includes('/enrollment') ? enrollment() : s }, { profileRoutes: async () => [route] })
  assert.match((await a.inventory())[0].error, /owner/i)
  s = snapshot()
  await a.inventory()
  s = snapshot()
  s.meta.owner.gateway_id = '00000000-0000-4000-8000-000000000002'
  assert.match((await a.inventory())[0].error, /owner/i)
})
test('disconnect retains last snapshot; removed owner never falls back to focused gateway', async () => {
  let routes = [route],
    calls = 0
  const a = p.createTransport(
    {
      rest: async (path) => {
        calls++
        if (path.includes('/enrollment')) return enrollment()
        return snapshot()
      }
    },
    { profileRoutes: async () => routes }
  )
  await a.inventory()
  routes = []
  const sources = await a.inventory()
  assert.equal(sources.length, 1)
  assert.ok(sources[0].snapshot)
  assert.match(sources[0].error, /disconnected/i)
  await assert.rejects(a.refresh(sources[0], []), /disconnected/i)
  assert.equal(calls, 2)
})
test('fanout bounded to four, one cached inventory per owner and refresh singleflight', async () => {
  let active = 0,
    max = 0,
    count = 0
  const routes = Array.from({ length: 9 }, (_, i) => ({
    ...route,
    connectionId: `route-${i}`,
    targetProfile: `profile-${i}`
  }))
  const ctx = {
    rest: async (path, o) => {
      active++
      max = Math.max(active, max)
      count++
      await new Promise((r) => setTimeout(r, 5))
      active--
      const s = snapshot()
      s.meta.owner.profile = routes.find((r) => r.connectionId === o.connectionId).targetProfile
      if (path.includes('/enrollment')) return enrollment(s.meta.owner.profile)
      return s
    }
  }
  const a = p.createTransport(ctx, { profileRoutes: async () => routes })
  const sources = await a.inventory()
  assert.equal(count, 18)
  assert.equal(max, 4)
  await Promise.all([a.refresh(sources[0], []), a.refresh(sources[0], [])])
  assert.equal(count, 19)
})
test('plugin missing vs permission vs disconnected errors do not leak raw response', async () => {
  for (const [status, label] of [
    [404, 'Plugin unavailable'],
    [403, 'Partial permissions'],
    [503, 'Owner gateway disconnected']
  ]) {
    const a = p.createTransport(
      {
        rest: async () => {
          throw Object.assign(Error('secret raw body'), { status })
        }
      },
      { profileRoutes: async () => [route] }
    )
    const r = await a.inventory()
    assert.equal(r[0].error, label)
    assert.doesNotMatch(r[0].error, /secret/)
  }
})
