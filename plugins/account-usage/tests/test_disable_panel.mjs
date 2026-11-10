// Disable / Enable in the account-usage panel. Synthetic host/API only; loads the actual plugin source.
import { readFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
import test from 'node:test'
const source = await readFile(new URL('../../../desktop-plugins/account-usage/plugin.js', import.meta.url), 'utf8')
function atom(value) { const listeners = new Set(); return { get: () => value, set(v) { value = v; listeners.forEach(f => f(v)) }, subscribe(f) { listeners.add(f); f(value); return () => listeners.delete(f) } } }
async function load() {
  const state = Object.fromEntries(Object.entries({ focusedSessionId: 'runtime-a', focusedSessionOwner: { connectionId: 'local', profile: 'default' }, connectionId: 'local', profile: 'default', model: 'm' }).map(([k, v]) => [k, atom(v)]))
  globalThis.__auTestHost = { state }
  globalThis.__auTestAtom = atom
  return import('data:text/javascript;base64,' + Buffer.from('const host=globalThis.__auTestHost, atom=globalThis.__auTestAtom;\n' + source.replace(/^import .*$/gm, '') + '\n//' + Math.random()).toString('base64'))
}
const payload = { providers: [
  { slug: 'anthropic', usage: { accounts: [
    { id: 'A', label: 'a@example.com', state: 'ready', windows: [] },
    { id: 'B', label: 'b@example.com', state: 'ready', windows: [] },
    { id: 'C', label: 'c@example.com', state: 'limited', limit_kind: 'quota', resets_at: '2099-01-01T00:00:00Z', windows: [] }] } },
  { slug: 'gemini', usage: { accounts: [{ id: 'gemini', label: 'key', state: 'ready', windows: [] }] } }] }

test('disabled accounts are grey, not counted ready, and the toggle shows only when supported', async () => {
  const p = await load()
  const [claude, gemini] = p.summarize(payload, {}, Date.now(), { supported: true, accounts: { anthropic: ['A', 'C'] } })
  assert.equal(claude.ready, 1)
  assert.equal(claude.disabled, 2)
  assert.equal(claude.limited, 0, 'a disabled limited account is shown as disabled, not out of quota')
  assert.equal(claude.nextBack, null)
  assert.equal(claude.canDisable, true)
  assert.equal(gemini.canDisable, false, 'Gemini API keys are not pooled accounts')
  const a = claude.accounts.find(x => x.id === 'A')
  assert.equal(p.tone(a), 'disabled')
  assert.match(p.describe(a), /disabled/)
  assert.equal(p.hasDetails(claude, claude.accounts.find(x => x.id === 'B')), true)
  const legacy = p.summarize(payload, {}, Date.now(), null)[0]
  assert.equal(legacy.canDisable, false)
  assert.equal(legacy.ready, 2)
  assert.equal(p.summarize(payload, {}, Date.now(), { supported: false, accounts: { anthropic: ['A'] } })[0].disabled, 0)
})

test('toggle posts the inverse state and refreshes; failures and root-held disables are reported', async () => {
  const p = await load()
  const calls = []
  let reply = { ok: true }
  const ctx = { async rest(path, opts) { calls.push({ path, ...opts }); if (reply instanceof Error) throw reply; return reply } }
  let refreshed = 0
  const st = p.createDisableStore(ctx, async () => { refreshed++ })
  await st.toggle('anthropic', { id: 'A', state: 'ready' })
  assert.deepEqual(calls[0], { path: '/disabled', method: 'POST', body: { provider: 'anthropic', account_id: 'A', disabled: true } })
  assert.match(st.$state.get()[st.key('anthropic', 'A')].message, /^Disabled/)
  assert.equal(refreshed, 1)
  await st.toggle('anthropic', { id: 'A', disabled: true })
  assert.equal(calls[1].body.disabled, false)
  assert.equal(st.$state.get()[st.key('anthropic', 'A')].message, 'Enabled')
  reply = { ok: false, disabled: true }
  await st.toggle('anthropic', { id: 'A', disabled: true })
  assert.match(st.$state.get()[st.key('anthropic', 'A')].message, /main Hermes home/)
  reply = new Error('501 no core support')
  await st.toggle('anthropic', { id: 'B' })
  const s = st.$state.get()[st.key('anthropic', 'B')]
  assert.equal(s.error, true)
  assert.match(s.message, /Disable failed: 501/)
})

test('a disabled account can never be queued for the next request', async () => {
  const p = await load()
  const calls = []
  const row = { session_id: 'runtime-a', provider: 'anthropic', model: 'm', revision: 0, account_id: null, state: 'none' }
  const ctx = { onDispose() {}, async rest(path, opts = {}) { calls.push({ path, ...opts }); return { ...row } } }
  const sel = p.createSelectionStore(ctx)
  await sel.refresh()
  await sel.select('anthropic', { id: 'A', state: 'ready', disabled: true })
  assert.equal(calls.filter(c => c.method === 'PUT').length, 0)
  assert.match(sel.$state.get().error, /disabled/)
})
