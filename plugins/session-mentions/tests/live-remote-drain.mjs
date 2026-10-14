// Opt-in live repro of the remote-drain race behind "cross_session send fails when the target
// chat is open in a window".
//   SM_OWNER_URL=http://127.0.0.1:<this Mac's Desktop backend port>
//   SM_REMOTE_URL=http://127.0.0.1:<port of the SSH-isolated serve another Mac's Desktop spawned here>
//   node live-remote-drain.mjs
// Plays the other Mac's courier against the REAL remote-facing backend: writes one cross_session
// send request (the tool's file format) for a NEW throwaway chat owned by this Mac's Desktop, then
// drains the outbox through SM_REMOTE_URL as fast as it can. Before the fix the remote drain wins
// and the submit through that non-owning backend is refused. After it, the remote drain gets
// nothing while this Mac's Desktop courier is alive, and that courier delivers the message.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { hideThrowaway, deleteThrowaway } from './live-throwaway.mjs'
const ownerUrl = process.env.SM_OWNER_URL
const remoteUrl = process.env.SM_REMOTE_URL
for (const u of [ownerUrl, remoteUrl]) if (!u || !/^http:\/\/127\.0\.0\.1:\d+$/.test(u)) throw Error('Set SM_OWNER_URL and SM_REMOTE_URL to explicit local backends')
const home = process.env.HOME + '/.hermes'
const sm = home + '/session_mentions'

async function backend(base) {
  const token = (await (await fetch(base + '/')).text()).match(/__HERMES_SESSION_TOKEN__\s*=\s*["']([^"']+)/)?.[1]
  if (!token) throw Error('no bootstrap token at ' + base)
  const ws = new WebSocket(base.replace('http:', 'ws:') + '/api/ws?token=' + encodeURIComponent(token))
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(Error('ws failed ' + base)) })
  const pending = new Map(); let n = 0
  ws.onmessage = e => { const m = JSON.parse(e.data); const p = pending.get(m.id); if (p) { pending.delete(m.id); m.error ? p.reject(Error(m.error.message || JSON.stringify(m.error))) : p.resolve(m.result) } }
  const rpc = (method, params = {}) => new Promise((resolve, reject) => { const id = 'smrd-' + ++n; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params })) })
  const rest = async (path, opts = {}) => {
    const r = await fetch(base + path, { method: opts.method || 'GET', headers: { 'X-Hermes-Session-Token': token, 'Content-Type': 'application/json' }, body: opts.body ? JSON.stringify(opts.body) : undefined })
    if (!r.ok) throw Error(`REST ${r.status} ${path}`)
    return r.json()
  }
  return { base, token, rpc, rest, close: () => ws.close() }
}

const owner = await backend(ownerUrl)
const remote = await backend(remoteUrl)
const out = {}
let sid, key
let remoteLive
try {
  const created = await owner.rpc('session.create', { profile: 'default', source: 'desktop', title: 'Session Mentions remote-drain live test', cwd: home + '/cache/scratch' })
  sid = created.session_id
  key = created.stored_session_id || created.session_key
  await owner.rpc('prompt.submit', { session_id: sid, text: 'Isolated transport test. Do not use tools. Reply exactly: READY', queued: true })
  await hideThrowaway(owner.base, owner.token, key)
  const lease = async () => JSON.parse(await fs.readFile(home + '/runtime/active_sessions.json', 'utf8')).entries.find(e => e.session_id === key)
  const before = await lease()
  assert.ok(before, 'throwaway chat has no owner lease')
  out.session_id = key
  out.owner = { pid: before.pid, surface: before.surface }
  out.remote_ping = await remote.rest('/api/plugins/session-mentions/ping')

  const rid = crypto.randomUUID().replaceAll('-', '')
  const marker = 'SM-RD-' + rid.slice(0, 8)
  const req = { id: rid, action: 'send', args: { target: '@session:default/' + key, message: 'Do not use tools. Reply exactly: ' + marker }, wait_for_reply: 0, sender: { session_id: '', title: 'remote-drain live test' }, created_at: Date.now() / 1000, ttl: 600 }
  await fs.writeFile(`${sm}/outbox/.${rid}.tmp`, JSON.stringify(req))
  await fs.rename(`${sm}/outbox/.${rid}.tmp`, `${sm}/outbox/${rid}.json`)

  // The other Mac's courier: drain through the non-owning backend, then send through it.
  let claimed = null
  const until = Date.now() + 6000
  while (!claimed && Date.now() < until) {
    const res = await remote.rest('/api/plugins/session-mentions/outbox')
    claimed = (res.requests || []).find(r => r.id === rid) || null
    if (!claimed) await new Promise(r => setTimeout(r, 50))
  }
  out.remote_drain_claimed = !!claimed
  if (claimed) {
    let result
    try {
      const resumed = await remote.rpc('session.resume', { session_id: key, omit_messages: true, profile: 'default' })
      remoteLive = resumed?.session_id
      result = { ok: true, status: (await remote.rpc('prompt.submit', { session_id: resumed.session_id, text: claimed.args.message, queued: true, profile: 'default' }))?.status }
    } catch (error) {
      result = { ok: false, error: String(error.message).split('\n')[0] }
    }
    out.remote_send = result
    await remote.rest('/api/plugins/session-mentions/result', { method: 'POST', body: { id: rid, result } })
  }
  // Whoever handled it posted a result; the tool would read it from here.
  for (let i = 0; i < 60; i++) {
    try { out.tool_result = JSON.parse(await fs.readFile(`${sm}/results/${rid}.json`, 'utf8')); break } catch { await new Promise(r => setTimeout(r, 500)) }
  }
  await fs.rm(`${sm}/results/${rid}.json`, { force: true })
  out.tool_result = out.tool_result && { gateway: out.tool_result.gateway, ok: out.tool_result.ok, status: out.tool_result.status, error: out.tool_result.error?.split('\n')[0] }
  for (let i = 0; i < 30; i++) {
    const page = await owner.rest(`/api/sessions/${key}/messages?profile=default&limit=200&order=latest`)
    out.landed = page.messages.filter(m => m.role === 'user' && m.content?.includes(marker)).length === 1
    if (out.landed) break
    await new Promise(r => setTimeout(r, 2000))
  }
  const after = await lease()
  out.owner_unchanged = after?.pid === before.pid && after?.lease_id === before.lease_id
  console.log(JSON.stringify(out, null, 2))
  if (process.env.SM_EVIDENCE) await fs.writeFile(process.env.SM_EVIDENCE, JSON.stringify(out, null, 2))
} finally {
  if (sid) await owner.rpc('session.close', { session_id: sid }).catch(() => undefined)
  if (remoteLive) await remote.rpc('session.close', { session_id: remoteLive }).catch(() => undefined)
  if (key) await deleteThrowaway(owner.base, owner.token, key).catch(e => console.error(String(e)))
  owner.close(); remote.close()
}
