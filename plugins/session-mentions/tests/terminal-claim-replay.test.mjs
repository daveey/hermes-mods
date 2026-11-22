// Courier replay uses the same request id; repeated drains never duplicate an in-flight start.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
const conns = [
  { id: 'local', key: 'desk', kind: 'local', device: 'desk', aliases: ['desk'] },
  { id: 'cloud', key: 'cloud', kind: 'ssh' }
]
async function load(api) {
  globalThis.__claimHost = {}
  globalThis.window = { hermesDesktop: { api } }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__claimHost; const COMPOSER_AREAS = {}')
  return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}
test('replayed pickup preserves request identity and folds concurrent drains', async () => {
  let starts = 0
  let finish
  const req = { id: 'a'.repeat(32), action: 'user_terminal', args: { command: 'echo ok' }, sender: { platform: 'cli' } }
  const replies = []
  const m = await load(async call => {
    if (call.path.includes('/outbox')) {
      assert.equal(new URL(call.path, 'http://example.com').searchParams.get('replay_user_terminal'), '1')
      return { requests: [req] }
    }
    if (call.path.endsWith('/start')) {
      starts++
      assert.equal(call.body.origin.request_id, req.id)
      return new Promise(resolve => { finish = resolve })
    }
    if (call.path.endsWith('/result')) replies.push(call.body)
    return { ok: true }
  })
  await m.drainConnection(conns[1], conns)
  await m.drainConnection(conns[1], conns)
  assert.equal(starts, 1)
  finish({ ok: true, job_id: 'job', status: 'ran', exit_code: 0 })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(replies.length, 1)
  assert.equal(replies[0].id, req.id)
  assert.equal(replies[0].result.job_id, 'job')
})
