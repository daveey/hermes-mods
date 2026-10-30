// Lifecycle for the throwaway chats the live tests open on a real Desktop backend.
// session.close only stops the runtime; the stored row stays in the sidebar forever (and the
// fixed title then collides on reruns, so the auto-titler names the next one "... #2").
// So: hide the row the moment it exists (a crashed/killed run leaves nothing visible), and
// delete it — row + transcript — once the runtime is closed.
const call = (base, token, method, path, body, signal) => fetch(base + path, {
  method, signal,
  headers: { 'X-Hermes-Session-Token': token, 'Content-Type': 'application/json' },
  body: body ? JSON.stringify(body) : undefined,
})

// The stored row only exists once the first prompt is persisted, so call this after the first
// prompt.submit; 404 until then is retried.
export async function hideThrowaway(base, token, key) {
  for (let i = 0; i < 30; i++) {
    const r = await call(base, token, 'PATCH', `/api/sessions/${encodeURIComponent(key)}`, { hidden: true, profile: 'default' })
    if (r.ok) return
    if (r.status !== 404) throw Error(`hide throwaway ${key}: REST ${r.status}`)
    await new Promise(res => setTimeout(res, 500))
  }
  throw Error(`hide throwaway ${key}: row never appeared`)
}

// Call after session.close. Its late finalizer may recreate a row AFTER DELETE returns 200.
// Require 10s of observed absence, re-delete on resurrection, and bound all requests to 30s.
export async function deleteThrowaway(base, token, key) {
  const deadline = Date.now() + 30000
  const path = `/api/sessions/${encodeURIComponent(key)}?profile=default`
  const timeout = () => Error(`delete throwaway ${key}: not stably absent after 30s`)
  const request = async method => {
    const remaining = deadline - Date.now()
    if (remaining <= 0) throw timeout()
    let r
    try {
      r = await call(base, token, method, path, undefined, AbortSignal.timeout(remaining))
    } catch (e) {
      if (e.name === 'TimeoutError' || Date.now() >= deadline) throw timeout()
      throw e
    }
    // No body is needed; release it before another poll.
    await r.body?.cancel()
    return r
  }
  let needsDelete = true, absentSince = null
  while (Date.now() < deadline) {
    if (needsDelete) {
      const r = await request('DELETE')
      if (!r.ok && r.status !== 409) throw Error(`delete throwaway ${key}: REST ${r.status}`)
      needsDelete = r.status === 409
      absentSince = null
    }
    if (!needsDelete) {
      const r = await request('GET')
      if (r.status === 404) {
        absentSince ??= Date.now()
        if (Date.now() - absentSince >= 10000) return
      } else if (r.ok) {
        needsDelete = true
        absentSince = null
      } else {
        throw Error(`verify delete throwaway ${key}: REST ${r.status}`)
      }
    }
    await new Promise(res => setTimeout(res, Math.min(1000, Math.max(0, deadline - Date.now()))))
  }
  throw timeout()
}
