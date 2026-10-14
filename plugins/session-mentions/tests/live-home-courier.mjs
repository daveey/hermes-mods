// Opt-in live repro for "cross_session send fails when the target chat is open in a window".
// SM_LIVE_URL=http://127.0.0.1:<desktop backend port> node live-home-courier.mjs [rounds]
//
// Owner: a NEW throwaway session opened on this Mac's Desktop backend (same lease a chat open in
// a Desktop window holds). Sender: the real cross_session tool code (python handle()), which
// writes to the outbox; whichever real Desktop courier drains it performs the send. Before the
// fix, another Mac's Desktop (reaching this Mac through its SSH-isolated serve process) often
// drained first and core refused the submit ("This chat is open in another Hermes window").
// Records, per round, which gateway's courier answered and whether the message landed.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs/promises'
import { hideThrowaway, deleteThrowaway } from './live-throwaway.mjs'
const base = process.env.SM_LIVE_URL
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base)) throw Error('Set SM_LIVE_URL to an explicit local backend')
const rounds = Number(process.argv[2] || 4)
const home = process.env.HOME + '/.hermes'
const html = await (await fetch(base + '/')).text()
const token = html.match(/__HERMES_SESSION_TOKEN__\s*=\s*["']([^"']+)/)?.[1]
if (!token) throw Error('Local bootstrap token unavailable')
const ws = new WebSocket(base.replace('http:', 'ws:') + '/api/ws?token=' + encodeURIComponent(token))
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(Error('Websocket failed')) })
const pending = new Map(); let count = 0
ws.onmessage = e => { const m = JSON.parse(e.data); const p = pending.get(m.id); if (p) { pending.delete(m.id); m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result) } }
const rpc = (method, params = {}) => new Promise((resolve, reject) => { const id = 'smhc-' + ++count; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params })) })
const rest = async (path, tries = 3) => {
  try { const r = await fetch(base + path, { headers: { 'X-Hermes-Session-Token': token } }); if (!r.ok) throw Error('REST ' + r.status); return r.json() } catch (e) { if (tries > 1) { await new Promise(r => setTimeout(r, 1000)); return rest(path, tries - 1) } throw e }
}
const pluginDir = new URL('..', import.meta.url).pathname
const tool = (target, message) => JSON.parse(execFileSync('python3', ['-c', `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("sm_tool", sys.argv[1] + "__init__.py", submodule_search_locations=[sys.argv[1]])
m = importlib.util.module_from_spec(spec); sys.modules["sm_tool"] = m; spec.loader.exec_module(m)
print(m.handle({"action": "send", "target": sys.argv[2], "message": sys.argv[3]}, session_id=None))
`, pluginDir, target, message], { encoding: 'utf8', timeout: 300_000 }).trim().split('\n').pop())

const evidence = { started: new Date().toISOString(), rounds: [] }
let sid, key
try {
  const created = await rpc('session.create', { profile: 'default', source: 'desktop', title: 'Session Mentions home-courier live test', cwd: home + '/cache/scratch' })
  sid = created.session_id
  key = created.stored_session_id || created.session_key
  evidence.session_id = key
  await rpc('prompt.submit', { session_id: sid, text: 'Isolated transport test. Do not use tools. Reply exactly: READY', queued: true })
  await hideThrowaway(base, token, key)
  const lease = async () => JSON.parse(await fs.readFile(home + '/runtime/active_sessions.json', 'utf8')).entries.find(e => e.session_id === key)
  const owner = await lease()
  assert.ok(owner, 'throwaway session has no owner lease')
  evidence.owner = { pid: owner.pid, surface: owner.surface, live: owner.metadata?.live_session_id }
  for (let i = 0; i < rounds; i++) {
    const marker = `SM-HC-${i}-${crypto.randomUUID().slice(0, 8)}`
    const res = tool('@session:default/' + key, `Do not use tools. Reply exactly: ${marker}`)
    let landed = false
    for (let t = 0; t < 45 && res.ok; t++) {
      const page = await rest(`/api/sessions/${key}/messages?profile=default&limit=200&order=latest`)
      landed = page.messages.filter(m => m.role === 'user' && m.content?.includes(marker)).length === 1
      if (landed) break
      await new Promise(r => setTimeout(r, 2000))
    }
    const row = { marker, gateway: res.gateway, ok: res.ok, status: res.status, error: res.error?.split('\n')[0], landed }
    evidence.rounds.push(row)
    console.log(JSON.stringify(row))
  }
  const after = await lease()
  evidence.owner_unchanged = after?.pid === owner.pid && after?.lease_id === owner.lease_id
  evidence.all_landed = evidence.rounds.every(r => r.landed)
  console.log(JSON.stringify({ session_id: key, owner: evidence.owner, owner_unchanged: evidence.owner_unchanged, all_landed: evidence.all_landed }))
  if (process.env.SM_EVIDENCE) await fs.writeFile(process.env.SM_EVIDENCE, JSON.stringify(evidence, null, 2))
} finally {
  if (sid) await rpc('session.close', { session_id: sid }).catch(() => undefined)
  if (key) await deleteThrowaway(base, token, key).catch(e => console.error(String(e)))
  ws.close()
}
