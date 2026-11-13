// run_in_user_terminal from a gateway with no screen (nous-1, hermes-hub) is relayed as a
// `user_terminal` request to the owner's Mac. This Desktop courier runs it on its OWN backend's
// user-terminal routes, which open the run window. These tests pin the courier half:
// the origin shown in the window is named by the courier (the connection it drained), a
// non-owner session is refused a second time here, and status reads carry that gateway name.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
async function load(host, api) {
  globalThis.__smHost = host
  globalThis.window = { hermesDesktop: { api } }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
  return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}
// The MacBook's Desktop: its own machine is `local` (device macbook); nous-1 is a cloud connection.
const conns = [
  { id: 'local', label: 'This device', key: 'macbook', kind: 'local', primary: true, device: 'macbook', aliases: ['macbook', 'laptop-1'] },
  { id: 'cloud-1', label: 'nous-1', key: 'nous-1', kind: 'cloud', primary: false }
]
const state = { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }
const sender = { session_id: '20261021_000505_e1e78133', title: 'Revoke token', profile: 'alpha', platform: 'desktop' }

test('start: runs on THIS Mac\'s backend, origin gateway named by the courier, not the request', async () => {
  const calls = []
  const m = await load({ state, connections: async () => conns }, async req => {
    calls.push(req)
    return { ok: true, job_id: 'j1', status: 'waiting_for_user', terminal: 'iTerm' }
  })
  const res = await m.performRequest(
    { id: '684589fa119b473585210034b1f63b93', action: 'user_terminal', route: 'macbook', args: { command: 'echo ok', reason: 'why', cwd: '' }, sender: { ...sender, gateway: 'forged' } },
    'cloud-1',
    conns
  )
  assert.equal(res.ok, true)
  assert.equal(res.job_id, 'j1')
  assert.equal(res.device, 'macbook')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].connectionId, 'local')
  assert.equal(calls[0].path, '/api/plugins/user-terminal/start')
  assert.equal(calls[0].method, 'POST')
  // request_id lets the gateway find the job again by its own request id if the job_id answer is lost
  assert.deepEqual(calls[0].body.origin, { gateway: 'nous-1', gateway_label: 'nous-1', profile: 'alpha', session_id: sender.session_id, title: 'Revoke token', request_id: '684589fa119b473585210034b1f63b93' })
  assert.equal(calls[0].body.command, 'echo ok')
})

test('status: resume by job_id reads with the drained gateway name and a bounded wait', async () => {
  const calls = []
  const m = await load({ state, connections: async () => conns }, async req => {
    calls.push(req)
    return { ok: true, job_id: 'j1', status: 'ran', exit_code: 0, output: 'ok' }
  })
  const res = await m.performRequest({ action: 'user_terminal', args: { job_id: 'j1', wait_s: 999 }, sender }, 'cloud-1', conns)
  assert.equal(res.status, 'ran')
  assert.equal(calls[0].connectionId, 'local')
  assert.equal(calls[0].path, '/api/plugins/user-terminal/status/j1?gateway=nous-1&wait_s=25')
})

// t_01a1d47bf0506084: the gateway takes back a request this Mac's courier already claimed.
test('withdraw: goes to THIS Mac\'s /start as origin.withdraw (empty command), gateway named by the courier', async () => {
  const calls = []
  const m = await load({ state, connections: async () => conns }, async req => {
    calls.push(req)
    return { ok: true, status: 'withdrawn', job_id: null }
  })
  const res = await m.performRequest(
    { id: 'ab'.repeat(16), action: 'user_terminal', route: 'macbook', args: { withdraw_request: 'e2aafe9a2ec443a3a651405b8f90c8ac', withdraw_job: '', reason: 'superseded', job_id: undefined }, sender: { ...sender, gateway: 'forged' } },
    'cloud-1',
    conns
  )
  assert.equal(res.status, 'withdrawn')
  assert.equal(res.device, 'macbook')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].connectionId, 'local')
  assert.equal(calls[0].path, '/api/plugins/user-terminal/start')
  assert.equal(calls[0].body.command, '') // a Mac module from before withdrawal refuses it: no window
  assert.deepEqual(calls[0].body.origin, { gateway: 'nous-1', request_id: 'e2aafe9a2ec443a3a651405b8f90c8ac', job_id: '', withdraw: 'superseded' })
})

test('withdraw: a non-owner session is refused without touching the Mac backend', async () => {
  const calls = []
  const m = await load({ state, connections: async () => conns }, async req => calls.push(req))
  const res = await m.performRequest({ action: 'user_terminal', args: { withdraw_request: 'e2aafe9a2ec443a3a651405b8f90c8ac', reason: 'x' }, sender: { ...sender, platform: 'slack' } }, 'cloud-1', conns)
  assert.equal(res.ok, false)
  assert.equal(calls.length, 0)
})

for (const platform of ['slack', 'desktop,slack', '', 'cron']) {
  test(`refuses a non-owner session (${platform || 'unknown'}) without touching the Mac backend`, async () => {
    const calls = []
    const m = await load({ state, connections: async () => conns }, async req => {
      calls.push(req)
      return { ok: true }
    })
    const res = await m.performRequest({ action: 'user_terminal', args: { command: 'echo ok' }, sender: { ...sender, platform } }, 'cloud-1', conns)
    assert.equal(res.ok, false)
    assert.match(res.error, /refused/)
    assert.equal(calls.length, 0)
  })
}

test('a Mac backend without the routes yields a clear error, not a hang', async () => {
  const m = await load({ state, connections: async () => conns }, async () => {
    throw Error('404 Not Found')
  })
  const res = await m.performRequest({ action: 'user_terminal', args: { command: 'echo ok' }, sender }, 'cloud-1', conns)
  assert.equal(res.ok, false)
  assert.match(res.error, /no user-terminal routes/)
})

test('ownerSurface: every identity must be an owner surface', async () => {
  const m = await load({ state, connections: async () => conns }, async () => ({}))
  assert.equal(m.ownerSurface('desktop'), true)
  assert.equal(m.ownerSurface('api_server,desktop'), true)
  assert.equal(m.ownerSurface('desktop,slack'), false)
  assert.equal(m.ownerSurface(''), false)
})
