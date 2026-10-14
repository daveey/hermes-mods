// Lifecycle for the throwaway chats the live tests open on a real Desktop backend.
// session.close only stops the runtime; the stored row stays in the sidebar forever (and the
// fixed title then collides on reruns, so the auto-titler names the next one "... #2").
// So: hide the row the moment it exists (a crashed/killed run leaves nothing visible), and
// delete it — row + transcript — once the runtime is closed.
const call = (base, token, method, path, body) => fetch(base + path, {
  method,
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

// Call after session.close. 409 = the closing runtime still holds its write guard; retry briefly.
export async function deleteThrowaway(base, token, key) {
  for (let i = 0; i < 30; i++) {
    const r = await call(base, token, 'DELETE', `/api/sessions/${encodeURIComponent(key)}?profile=default`)
    if (r.ok) return
    if (r.status !== 409) throw Error(`delete throwaway ${key}: REST ${r.status}`)
    await new Promise(res => setTimeout(res, 1000))
  }
  throw Error(`delete throwaway ${key}: still write-guarded after 30s`)
}
