// Anthropic selected-row sign-in through Desktop's recovery terminal.
// Synthetic Desktop terminal bridge and REST; loads the actual plugin source.
import { readFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
import test from 'node:test'

const source = await readFile(new URL('../../../desktop-plugins/account-usage/plugin.js', import.meta.url), 'utf8')
function atom(value) { const listeners = new Set(); return { get: () => value, set(v) { value = v; listeners.forEach(f => f(v)) }, subscribe(f) { listeners.add(f); f(value); return () => listeners.delete(f) } } }
const tick = () => new Promise(r => setTimeout(r, 0))

// A Desktop terminal bridge that records every call. `start` decides what startRecovery returns.
function fakeTerminal(start = () => ({ id: 'pty-1', recovery: true, connectionId: 'gw-hub' })) {
  const t = { calls: [], data: new Map(), exit: new Map(), disposed: [], written: [] }
  t.api = {
    startRecovery: async opts => { t.calls.push(opts); return start(opts) },
    start: async () => { throw new Error('an ordinary shell must never be started') },
    onData: (id, fn) => { t.data.set(id, fn); return () => t.data.delete(id) },
    onExit: (id, fn) => { t.exit.set(id, fn); return () => t.exit.delete(id) },
    attach: async () => true,
    write: async (id, text) => { t.written.push([id, text]); return true },
    dispose: async id => { t.disposed.push(id); return true }
  }
  t.print = (text, id = 'pty-1') => t.data.get(id)?.(text)
  t.end = (code = 0, id = 'pty-1') => t.exit.get(id)?.({ code, signal: null })
  return t
}

async function setup({ term, groups, rest }) {
  const prelude = 'const host=globalThis.__auTestHost, atom=globalThis.__auTestAtom;\nconst jsx=(type,props)=>({type,props}), jsxs=jsx;\n'
  const state = Object.fromEntries(Object.entries({ connectionId: 'gw-hub', profile: 'work' }).map(([k, v]) => [k, atom(v)]))
  const nav = []
  globalThis.__auTestHost = { state, navigate: p => nav.push(p) }
  globalThis.__auTestAtom = atom
  globalThis.window = { hermesDesktop: term ? { terminal: term.api } : {} }
  const mod = await import('data:text/javascript;base64,' + Buffer.from(prelude + source.replace(/^import .*$/gm, '') + '\n//' + Math.random()).toString('base64'))
  const restCalls = [], apiCalls = [], opened = []
  const ctx = {
    onDispose() {},
    os: { openExternal: async url => { opened.push(url); return true } },
    api: async req => { apiCalls.push(req); throw new Error('the dashboard OAuth API must not be used for Anthropic') },
    rest: async (path, opts = {}) => { restCalls.push({ path, ...opts }); return rest(path, opts) }
  }
  const store = mod.createReconnectStore(ctx, async () => groups())
  return { mod, store, nav, restCalls, apiCalls, opened }
}

const ROUTE = { connectionId: 'gw-hub', profile: 'work' }
const DEAD = { id: 'fp:old', label: 'me', state: 'unavailable', reason: 'auth' }
const deadGrant = (mode = { mode: 'target', credential_id: 'pool-9' }) => ({ ok: false, status: 'needs_sign_in', needs_sign_in: true, auth_flow: 'external', ...(mode ? { sign_in: mode } : {}) })
const controls = el => el ? el.props.children.map(c => c.type === 'button' ? ['button', c.props['aria-label'], c.props.onClick] : [c.type, c.props.children ?? c.props['aria-label']]) : null

test('dead Claude grant: sign-in runs the recovery for that stored row on the chip gateway, then follows the row to its new account', async () => {
  const term = fakeTerminal()
  let groups = [{ slug: 'anthropic', accounts: [DEAD, { id: 'fp:twin', label: 'me', state: 'ready' }] }]
  let health = { found: true, provider: 'anthropic', credential_id: 'pool-9', account_id: 'fp:new', status: 'ok' }
  const { mod, store, nav, restCalls, apiCalls, opened } = await setup({ term, groups: () => groups,
    rest: path => path === '/reconnect' ? deadGrant() : path === '/credential-health' ? health : (() => { throw new Error('unexpected ' + path) })() })
  const k = store.key('anthropic', 'fp:old')
  const flow = store.signIn('anthropic', DEAD, ROUTE)
  await tick(); await tick(); await tick()

  // Exactly one recovery request: gateway, profile, provider and the row the backend named. No command text.
  assert.deepEqual(term.calls, [{ connectionId: 'gw-hub', profile: 'work', provider: 'anthropic', credentialId: 'pool-9', cols: 96, rows: 24 }])
  assert.equal(restCalls[0].path, '/reconnect'); assert.equal(restCalls[0].connectionId, 'gw-hub'); assert.equal(restCalls[0].profile, 'work')
  let st = store.$state.get()[k]
  assert.equal(st.terminal, true); assert.equal(st.authFlow, 'terminal')

  // The command's prompts are shown as plain text; escape sequences are dropped.
  term.print('\x1b[1mReplace this selected credential?\x1b[0m Type replace to continue: ')
  term.print('\r\nOpen https://claude.ai/oauth/authorize?code=true&state=abc to sign in\r\nPaste the code: ')
  st = store.$state.get()[k]
  assert.match(st.output, /^Replace this selected credential\? Type replace to continue: \nOpen https:/)
  assert.ok(!st.output.includes('\x1b'))
  const line = controls(mod.ReconnectLine({ g: { slug: 'anthropic' }, a: DEAD, reconnectStore: store, reconnectState: store.$state.get() }))
  assert.deepEqual(line.map(x => x[1]).slice(0, 3), ['Cancel sign-in for me', 'Open sign-in page for me', 'Follow the steps below to sign in'])
  line[1][2](); assert.deepEqual(opened, ['https://claude.ai/oauth/authorize?code=true&state=abc'])

  // The user's answers go to that terminal and nowhere else.
  assert.equal(await store.sendInput('anthropic', DEAD, 'replace'), true)
  assert.equal(await store.sendInput('anthropic', DEAD, 'SECRET-CODE#st'), true)
  assert.deepEqual(term.written, [['pty-1', 'replace\r'], ['pty-1', 'SECRET-CODE#st\r']])
  assert.ok(!JSON.stringify(store.$state.get()).includes('SECRET-CODE'), 'an answer never enters panel state')
  assert.ok(!JSON.stringify(restCalls).includes('SECRET-CODE'))

  // The command exits. Its exit proves nothing: the backend is asked what the row is now.
  groups = [{ slug: 'anthropic', accounts: [{ id: 'fp:new', label: 'me', state: 'ready' }, { id: 'fp:twin', label: 'me', state: 'ready' }] }]
  term.end(0); await flow
  const asked = restCalls.find(c => c.path === '/credential-health')
  assert.deepEqual(asked.body, { provider: 'anthropic', credential_id: 'pool-9' })
  assert.equal(asked.connectionId, 'gw-hub'); assert.equal(asked.profile, 'work')
  st = store.$state.get()[k]
  assert.equal(st.done, true); assert.equal(st.message, 'Reconnected'); assert.equal(st.resolvedId, 'fp:new')
  assert.equal(st.output, undefined, 'the transcript is dropped once the sign-in ends')
  assert.deepEqual(term.disposed, ['pty-1'])
  assert.deepEqual(nav, []); assert.equal(apiCalls.length, 0)
  // The result shows on the row the backend named, not on a same-label sibling.
  assert.equal(mod.rowFix(store.$state.get(), 'anthropic', { id: 'fp:new', label: 'me' }).message, 'Reconnected')
  assert.deepEqual(mod.rowFix(store.$state.get(), 'anthropic', { id: 'fp:twin', label: 'me' }), {})
  assert.equal(await store.sendInput('anthropic', DEAD, 'late'), false, 'nothing is written after the terminal ended')
})

test('exit code 0 with a still-dead row is not success; a non-zero exit with a healthy row is', async () => {
  for (const [code, health, groupsAfter, want] of [
    [0, { found: true, account_id: 'fp:old', status: 'needs_sign_in' }, [DEAD], { error: true, done: undefined, message: 'Sign-in did not finish — nothing was saved' }],
    [1, { found: true, account_id: 'fp:new', status: 'ok' }, [{ id: 'fp:new', label: 'me', state: 'ready' }], { error: undefined, done: true, message: 'Reconnected' }],
    [0, { found: true, account_id: 'fp:new', status: 'quota' }, [{ id: 'fp:new', label: 'me', state: 'limited', limit_kind: 'quota', reason: 'usage_limit' }], { error: undefined, done: true, message: 'Signed in · out of quota until it resets' }],
    [0, { found: true, account_id: 'fp:new', status: 'ok' }, [DEAD], { error: undefined, done: undefined, message: 'Signed in — usage has not updated yet, check again' }],
    [0, { found: false, account_id: null, status: 'unknown' }, [], { error: true, done: undefined, message: 'This account is no longer in the pool — nothing was saved' }]
  ]) {
    const term = fakeTerminal()
    let groups = [{ slug: 'anthropic', accounts: [DEAD] }]
    const { store } = await setup({ term, groups: () => groups, rest: path => path === '/reconnect' ? deadGrant() : health })
    const flow = store.signIn('anthropic', DEAD, ROUTE)
    await tick(); await tick(); await tick()
    groups = [{ slug: 'anthropic', accounts: groupsAfter }]
    term.end(code); await flow
    const st = store.$state.get()[store.key('anthropic', 'fp:old')]
    assert.deepEqual({ error: st.error, done: st.done, message: st.message }, want, `exit ${code} ${health.status}`)
  }
})

test('health readback that fails leaves the row unverified with a re-check, which asks the backend again', async () => {
  const term = fakeTerminal()
  let groups = [{ slug: 'anthropic', accounts: [DEAD] }], fail = true
  const { mod, store, restCalls } = await setup({ term, groups: () => groups, rest: path => {
    if (path === '/reconnect') return deadGrant()
    if (fail) throw new Error('gateway restarting')
    return { found: true, account_id: 'fp:new', status: 'ok' }
  } })
  const k = store.key('anthropic', 'fp:old')
  const flow = store.signIn('anthropic', DEAD, ROUTE)
  await tick(); await tick(); await tick()
  term.end(0); await flow
  let st = store.$state.get()[k]
  assert.equal(st.unverified, true); assert.equal(st.check, true); assert.notEqual(st.done, true)
  assert.equal(st.message, 'Could not confirm the account — check again')
  const line = controls(mod.ReconnectLine({ g: { slug: 'anthropic' }, a: DEAD, reconnectStore: store, reconnectState: store.$state.get() }))
  assert.deepEqual(line.map(x => x[1]), ['Sign in to me again', 'Check sign-in for me', 'Could not confirm the account — check again'])
  fail = false; groups = [{ slug: 'anthropic', accounts: [{ id: 'fp:new', label: 'me', state: 'ready' }] }]
  await store.check('anthropic', DEAD, ROUTE)
  st = store.$state.get()[k]
  assert.equal(st.done, true); assert.equal(st.resolvedId, 'fp:new')
  assert.equal(restCalls.filter(c => c.path === '/credential-health').length, 2)
})

test('cancel ends the terminal, reports the stored row as it is, and a late exit changes nothing', async () => {
  const term = fakeTerminal()
  const groups = [{ slug: 'anthropic', accounts: [DEAD] }]
  const { store } = await setup({ term, groups: () => groups, rest: path => path === '/reconnect' ? deadGrant() : { found: true, account_id: 'fp:old', status: 'needs_sign_in' } })
  const k = store.key('anthropic', 'fp:old')
  const flow = store.signIn('anthropic', DEAD, ROUTE)
  await tick(); await tick(); await tick()
  term.print('Paste the code: ')
  const exit = term.exit.get('pty-1')
  await store.cancelAuth('anthropic', DEAD, ROUTE)
  let st = store.$state.get()[k]
  assert.deepEqual(term.disposed, ['pty-1'])
  assert.equal(st.cancelled, true); assert.equal(st.message, 'Sign-in cancelled'); assert.equal(st.needsSignIn, true)
  assert.equal(st.terminal, undefined); assert.equal(st.output, undefined)
  exit({ code: 130, signal: null }); await flow
  assert.equal(store.$state.get()[k].message, 'Sign-in cancelled')
  assert.equal(await store.sendInput('anthropic', DEAD, 'x'), false)
  // Signing in again starts a fresh recovery for the same row.
  const again = store.signIn('anthropic', DEAD, ROUTE)
  await tick(); await tick(); await tick()
  assert.equal(term.calls.length, 2); assert.equal(term.calls[1].credentialId, 'pool-9')
  term.end(0); await again
})

test('nothing is started when Desktop refuses, lacks the recovery terminal, or the row cannot be bound', async () => {
  const groups = () => [{ slug: 'anthropic', accounts: [DEAD] }]
  const k = 'anthropic\u0000fp:old'
  // Desktop refuses: each reason is said; a gateway without a terminal is never replaced by another.
  for (const [reason, pattern] of [['no-shell', /no terminal connection/], ['missing', /no longer configured/], ['no-runtime', /not installed/], ['unsupported', /not available/], ['invalid', /could not start/]]) {
    const term = fakeTerminal(() => ({ id: '', recovery: true, unavailable: reason }))
    const { store, nav, apiCalls } = await setup({ term, groups, rest: () => deadGrant() })
    await store.signIn('anthropic', DEAD, ROUTE)
    const st = store.$state.get()[k]
    assert.match(st.message, pattern); assert.equal(st.error, true); assert.equal(st.needsSignIn, true); assert.equal(st.terminal, undefined)
    assert.equal(term.calls.length, 1); assert.deepEqual(term.disposed, []); assert.deepEqual(nav, []); assert.equal(apiCalls.length, 0)
  }
  // A session Desktop did not mark as a recovery (an ordinary shell) is ended, never shown or written to.
  const shell = fakeTerminal(() => ({ id: 'pty-shell', shell: 'zsh' }))
  let r = await setup({ term: shell, groups, rest: () => deadGrant() })
  await r.store.signIn('anthropic', DEAD, ROUTE)
  assert.deepEqual(shell.disposed, ['pty-shell']); assert.equal(r.store.$state.get()[k].terminal, undefined)
  assert.equal(await r.store.sendInput('anthropic', DEAD, 'replace'), false); assert.deepEqual(shell.written, [])
  // startRecovery throws.
  const broken = fakeTerminal(() => { throw new Error('ipc down') })
  r = await setup({ term: broken, groups, rest: () => deadGrant() })
  await r.store.signIn('anthropic', DEAD, ROUTE)
  assert.match(r.store.$state.get()[k].message, /could not start/)
  // An older Desktop: said so, nothing started, no Settings hand-off.
  r = await setup({ term: null, groups, rest: () => deadGrant() })
  await r.store.signIn('anthropic', DEAD, ROUTE)
  assert.match(r.store.$state.get()[k].message, /Update Hermes Desktop/); assert.deepEqual(r.nav, [])
  // Rows this panel cannot bind (env, borrowed, several rows) and an older backend: no terminal at all.
  for (const [mode, pattern] of [[{ mode: 'unsupported' }, /managed outside this profile/], [{ mode: 'singleton' }, /managed outside this profile/], [null, /Restart this gateway/]]) {
    const term = fakeTerminal()
    r = await setup({ term, groups, rest: () => deadGrant(mode) })
    await r.store.signIn('anthropic', DEAD, ROUTE)
    assert.match(r.store.$state.get()[k].message, pattern); assert.equal(term.calls.length, 0)
  }
  // No gateway for the chips: nothing started.
  const term = fakeTerminal()
  r = await setup({ term, groups, rest: () => deadGrant() })
  await r.store.signIn('anthropic', DEAD, { connectionId: '', profile: 'work' })
  assert.equal(term.calls.length, 0); assert.match(r.store.$state.get()[k].message, /Pick the gateway/)
})

test('a transient refresh failure, quota or a renewed grant never opens a terminal', async () => {
  const groups = () => [{ slug: 'anthropic', accounts: [{ ...DEAD, state: 'ready', reason: undefined }] }]
  for (const answer of [{ ok: false, status: 'transient' }, { ok: false, status: 'quota' }, { ok: true, status: 'renewed', renewed: 1 }]) {
    const term = fakeTerminal()
    const { store } = await setup({ term, groups, rest: () => answer })
    await store.signIn('anthropic', DEAD, ROUTE)
    assert.equal(term.calls.length, 0, answer.status)
  }
})

test('terminal text helpers: plain text only, bounded, https links only', async () => {
  const { mod } = await setup({ term: null, groups: () => [], rest: () => ({}) })
  assert.equal(mod.appendOutput('', '\x1b]0;title\x07a\x1b[31mb\x1b[0m\r\nc\x08\x00'), 'ab\nc')
  assert.equal(mod.appendOutput('x'.repeat(7000), 'END').length, 6000)
  assert.ok(mod.appendOutput('x'.repeat(7000), 'END').endsWith('END'))
  assert.equal(mod.signInUrl('go to https://claude.ai/oauth/authorize?x=1&y=2 now'), 'https://claude.ai/oauth/authorize?x=1&y=2')
  assert.equal(mod.signInUrl('http://insecure.example/ and file:///etc/passwd and javascript:alert(1)'), null)
  assert.equal(mod.signInUrl(''), null)
})
