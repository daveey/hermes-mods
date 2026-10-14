// Opt-in live check of the cross-gateway forward. Run on the TARGET machine:
//   SM_LIVE_URL=http://127.0.0.1:<its Desktop backend port> SM_MARKER=<text> node live-forward-owner.mjs
// Opens a NEW throwaway chat on that Desktop's backend (the lease a chat open in a window holds),
// prints {"session_id": ...}, then waits up to 180s for exactly one user message containing
// SM_MARKER — sent from ANOTHER Mac with cross_session — and reports whether it landed, with the
// owner lease unchanged. Closes only the throwaway chat.
import fs from 'node:fs/promises'
const base = process.env.SM_LIVE_URL
const marker = process.env.SM_MARKER
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || !marker) throw Error('Set SM_LIVE_URL and SM_MARKER')
const home = process.env.HERMES_HOME || process.env.HOME + '/.hermes'
const token = (await (await fetch(base + '/')).text()).match(/__HERMES_SESSION_TOKEN__\s*=\s*["']([^"']+)/)?.[1]
if (!token) throw Error('no bootstrap token')
const ws = new WebSocket(base.replace('http:', 'ws:') + '/api/ws?token=' + encodeURIComponent(token))
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(Error('ws failed')) })
const pending = new Map(); let n = 0
ws.onmessage = e => { const m = JSON.parse(e.data); const p = pending.get(m.id); if (p) { pending.delete(m.id); m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result) } }
const rpc = (method, params = {}) => new Promise((resolve, reject) => { const id = 'smfo-' + ++n; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params })) })
const rest = async path => { const r = await fetch(base + path, { headers: { 'X-Hermes-Session-Token': token } }); if (!r.ok) throw Error('REST ' + r.status); return r.json() }
let sid
try {
  const created = await rpc('session.create', { profile: 'default', source: 'desktop', title: 'Session Mentions forward live test', cwd: home + '/cache/scratch' })
  sid = created.session_id
  const key = created.stored_session_id || created.session_key
  await rpc('prompt.submit', { session_id: sid, text: 'Isolated transport test. Do not use tools. Reply exactly: READY', queued: true })
  const lease = async () => JSON.parse(await fs.readFile(home + '/runtime/active_sessions.json', 'utf8')).entries.find(e => e.session_id === key)
  const before = await lease()
  console.log(JSON.stringify({ session_id: key, owner_pid: before?.pid, surface: before?.surface }))
  let landed = false
  for (let i = 0; i < 90 && !landed; i++) {
    await new Promise(r => setTimeout(r, 2000))
    const page = await rest(`/api/sessions/${key}/messages?profile=default&limit=200&order=latest`)
    landed = page.messages.filter(m => m.role === 'user' && m.content?.includes(marker)).length === 1
  }
  const after = await lease()
  console.log(JSON.stringify({ landed, owner_unchanged: after?.pid === before?.pid && after?.lease_id === before?.lease_id }))
} finally {
  if (sid) await rpc('session.close', { session_id: sid }).catch(() => undefined)
  ws.close()
}
