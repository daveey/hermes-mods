// Hide persisted throwaways and delete them after session.close, using owner fences.
import { randomUUID } from 'node:crypto'
const call = (base, token, method, path, body, signal) => fetch(base + path, {
  method, signal,
  headers: { 'X-Hermes-Session-Token': token, 'Content-Type': 'application/json' },
  body: body ? JSON.stringify(body) : undefined,
})
const pause = ms => new Promise(res => setTimeout(res, ms))
const pathFor = key => `/api/sessions/${encodeURIComponent(key)}`
const snapshotFor = key => `${pathFor(key)}/mutation-snapshot?profile=default`
const json = async r => { try { return await r.json() } catch { return {} } }
const failure = (action, key, r, data) => Error(`${action} throwaway ${key}: REST ${r.status} ${JSON.stringify(data)}`)
// Only an explicit busy refusal is retryable, never an upgrade/stale/unknown 409.
const busy = (r, data) => r.status === 409 && data.detail === 'session_busy'

export async function hideThrowaway(base, token, key) {
  // First prompt persistence can lag; read a NEW snapshot on every attempt.
  for (let i = 0; i < 30; i++) {
    const snapshot = await call(base, token, 'GET', snapshotFor(key))
    const fence = await json(snapshot)
    if (snapshot.ok) {
      const r = await call(base, token, 'PATCH', pathFor(key), {
        hidden: true, profile: 'default', request_id: randomUUID(), expected_revision: fence.runtime_revision,
      })
      const data = await json(r)
      if (r.ok) return
      if (r.status !== 404 && !busy(r, data)) throw failure('hide', key, r, data)
    } else if (snapshot.status !== 404 && !busy(snapshot, fence)) {
      throw failure('snapshot hide', key, snapshot, fence)
    }
    await pause(500)
  }
  throw Error(`hide throwaway ${key}: row never appeared or stayed busy`)
}

// Late close finalizers can resurrect rows: require 10s quiet within a 30s deadline.
export async function deleteThrowaway(base, token, key) {
  const deadline = Date.now() + 30000
  const path = `${pathFor(key)}?profile=default`
  const timeout = () => Error(`delete throwaway ${key}: not stably absent after 30s`)
  const request = async (method, target = path) => {
    const remaining = deadline - Date.now()
    if (remaining <= 0) throw timeout()
    try {
      const r = await call(base, token, method, target, undefined, AbortSignal.timeout(remaining))
      return { r, data: await json(r) }
    } catch (e) {
      if (e.name === 'TimeoutError' || Date.now() >= deadline) throw timeout()
      throw e
    }
  }
  let needsDelete = true, absentSince = null
  while (Date.now() < deadline) {
    if (needsDelete) {
      const snapshot = await request('GET', snapshotFor(key))
      if (snapshot.r.ok) {
        const params = new URLSearchParams({
          profile: 'default', request_id: randomUUID(),
          expected_revision: snapshot.data.runtime_revision,
          expected_generation: snapshot.data.runtime_generation,
        })
        const { r, data } = await request('DELETE', `${pathFor(key)}?${params}`)
        if (!r.ok && r.status !== 404 && !busy(r, data)) throw failure('delete', key, r, data)
        needsDelete = busy(r, data)
      } else if (snapshot.r.status === 404) {
        needsDelete = false
      } else if (!busy(snapshot.r, snapshot.data)) {
        throw failure('snapshot delete', key, snapshot.r, snapshot.data)
      }
      absentSince = null
    }
    if (!needsDelete) {
      const { r, data } = await request('GET')
      if (r.status === 404) {
        absentSince ??= Date.now()
        if (Date.now() - absentSince >= 10000) return
      } else if (r.ok) {
        needsDelete = true
        absentSince = null
      } else {
        throw failure('verify delete', key, r, data)
      }
    }
    await pause(Math.min(1000, Math.max(0, deadline - Date.now())))
  }
  throw timeout()
}
