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

// After the recovery command the backend's row status decides what may be said. Only `ok` plus a
// healthy usage row for the row's current account id is "Reconnected"; a stale ready usage row, or a
// usage panel that has not loaded the new id yet, never turns quota, disabled or unknown into
// "Reconnected", and never turns quota or disabled into a sign-in offer.
test('only backend status ok can become Reconnected: quota, disabled and unknown keep their own state with ready or missing usage', async t => {
  const READY = [{ id: 'fp:new', label: 'me', state: 'ready' }]
  const QUOTA = [{ id: 'fp:new', label: 'me', state: 'limited', limit_kind: 'quota', reason: 'usage_limit' }]
  const MISSING = [DEAD]
  const quota = { done: true, error: undefined, needsSignIn: undefined, message: 'Signed in · out of quota until it resets' }
  const disabled = { done: undefined, error: true, needsSignIn: undefined, message: 'Account is disabled' }
  const unknown = { done: undefined, error: true, needsSignIn: true, message: 'Could not confirm the account — check again' }
  for (const [status, usage, groupsAfter, want] of [
    ['quota', 'ready', READY, quota], ['quota', 'missing', MISSING, quota], ['quota', 'quota', QUOTA, quota],
    ['disabled', 'ready', READY, disabled], ['disabled', 'missing', MISSING, disabled],
    ['unknown', 'ready', READY, unknown], ['unknown', 'missing', MISSING, unknown],
    [undefined, 'ready', READY, unknown], ['renewed', 'ready', READY, unknown],
    ['ok', 'ready', READY, { done: true, error: undefined, needsSignIn: undefined, message: 'Reconnected' }]
  ]) await t.test(`backend ${status} + usage ${usage}`, async () => {
    const term = fakeTerminal()
    let groups = [{ slug: 'anthropic', accounts: [DEAD] }]
    const { mod, store } = await setup({ term, groups: () => groups,
      rest: path => path === '/reconnect' ? deadGrant() : { found: true, account_id: 'fp:new', ...(status ? { status } : {}) } })
    const flow = store.signIn('anthropic', DEAD, ROUTE)
    await tick(); await tick(); await tick()
    groups = [{ slug: 'anthropic', accounts: groupsAfter }]
    term.end(0); await flow
    const st = store.$state.get()[store.key('anthropic', 'fp:old')]
    const name = `backend ${status} + usage ${usage}`
    assert.deepEqual({ done: st.done, error: st.error, needsSignIn: st.needsSignIn, message: st.message }, want, name)
    if (status !== 'ok') assert.notEqual(st.message, 'Reconnected', name)
    if (status === 'quota' || status === 'disabled') assert.equal(st.resolvedId, 'fp:new', name)
    if ((status === 'quota' || status === 'disabled') && usage === 'missing') {
      // Not a sign-in problem. The usage panel still lists the old id as signed out: that row
      // shows the backend's answer and a re-check, never "Sign in again" or "Reconnect".
      const state = store.$state.get()
      const line = controls(mod.ReconnectLine({ g: { slug: 'anthropic' }, a: DEAD, reconnectStore: store, reconnectState: state }))
      assert.deepEqual(line.map(x => x[1]), ['Check sign-in for me', want.message], name)
      assert.equal(mod.signInConfirmed(DEAD, state[store.key('anthropic', 'fp:old')], 'anthropic'), false, name)
      assert.deepEqual(mod.authCounts({ slug: 'anthropic', accounts: [DEAD] }, state), { signIn: 0, disconnected: 0 }, name)
    }
  })
})

test('a settled answer belongs to the row it was given for: the new account id dying later offers sign-in again', async () => {
  const term = fakeTerminal()
  let groups = [{ slug: 'anthropic', accounts: [DEAD] }]
  const { mod, store } = await setup({ term, groups: () => groups,
    rest: path => path === '/reconnect' ? deadGrant() : { found: true, account_id: 'fp:new', status: 'quota' } })
  const flow = store.signIn('anthropic', DEAD, ROUTE)
  await tick(); await tick(); await tick()
  term.end(0); await flow
  const later = { id: 'fp:new', label: 'me', state: 'unavailable', reason: 'auth' }
  const state = store.$state.get()
  assert.equal(mod.signInConfirmed(later, mod.rowFix(state, 'anthropic', later), 'anthropic'), true)
  const line = controls(mod.ReconnectLine({ g: { slug: 'anthropic' }, a: later, reconnectStore: store, reconnectState: state }))
  assert.equal(line[0][1], 'Sign in to me again')
})

test('Check again after an unconfirmed readback applies the same rule', async () => {
  const term = fakeTerminal()
  let groups = [{ slug: 'anthropic', accounts: [DEAD] }], status = 'unknown'
  const { store } = await setup({ term, groups: () => groups,
    rest: path => path === '/reconnect' ? deadGrant() : { found: true, account_id: 'fp:new', status } })
  const k = store.key('anthropic', 'fp:old')
  const flow = store.signIn('anthropic', DEAD, ROUTE)
  await tick(); await tick(); await tick()
  groups = [{ slug: 'anthropic', accounts: [{ id: 'fp:new', label: 'me', state: 'ready' }] }]
  term.end(0); await flow
  assert.equal(store.$state.get()[k].check, true)
  status = 'disabled'
  await store.check('anthropic', DEAD, ROUTE)
  assert.equal(store.$state.get()[k].message, 'Account is disabled'); assert.notEqual(store.$state.get()[k].done, true)
  // A settled row can be re-checked: once the backend reports it healthy, it is Reconnected.
  status = 'ok'
  await store.check('anthropic', DEAD, ROUTE)
  assert.equal(store.$state.get()[k].message, 'Reconnected'); assert.equal(store.$state.get()[k].settled, undefined)
})

// What the user sees. The usage panel replaces the pre-recovery id with the account id the backend
// named, so the backend's answer has to show on THAT row: rendered here through AccountRow and
// ReconnectLine for fp:new, not read from the store entry kept under fp:old.
function render(el, out = { text: [], buttons: [], auth: [] }) {
  if (el == null || el === false) return out
  if (Array.isArray(el)) { el.forEach(c => render(c, out)); return out }
  if (typeof el !== 'object') { out.text.push(String(el)); return out }
  if (typeof el.type === 'function') return render(el.type(el.props || {}), out)
  const p = el.props || {}
  if (p['data-auth']) out.auth.push(p['data-auth'])
  if (el.type === 'button' && p.onClick && p['aria-label'] && !/^(Try|Details for|Disable|Enable) /.test(p['aria-label'])) out.buttons.push([p['aria-label'], p.onClick])
  return render(p.children, out)
}
const NEW = { ready: { id: 'fp:new', label: 'me', state: 'ready' },
  limited: { id: 'fp:new', label: 'me', state: 'limited', limit_kind: 'quota', reason: 'usage_limit' },
  unavailable: { id: 'fp:new', label: 'me', state: 'unavailable', reason: 'auth' } }
async function recovered(status, usage, cancel = false) {
  let starts = 0
  const term = fakeTerminal(() => refuse ? { unavailable: refuse } : { id: `pty-${++starts}`, recovery: true, connectionId: 'gw-hub' })
  let groups = [{ slug: 'anthropic', accounts: [DEAD] }], answer = status, named = 'fp:new', refuse = null, down = false, hold = null, noUsage = false
  const r = await setup({ term, groups: () => { if (noUsage) throw new Error('usage down'); return groups },
    rest: async path => {
      if (path === '/reconnect') return deadGrant()
      if (hold) await hold
      if (down) throw new Error('gateway down')
      return answer === 'gone' ? { found: false } : { found: true, account_id: named, ...(answer ? { status: answer } : {}) }
    } })
  const flow = r.store.signIn('anthropic', DEAD, ROUTE)
  await tick(); await tick(); await tick()
  if (usage) groups = [{ slug: 'anthropic', accounts: [NEW[usage]] }]
  const exit = term.exit.get('pty-1')
  if (cancel) { await r.store.cancelAuth('anthropic', DEAD, ROUTE); exit({ code: 130, signal: null }) }
  else term.end(0)
  await flow
  const row = a => {
    const state = r.store.$state.get(), g = { slug: 'anthropic', accounts: [a] }
    return { ...render(r.mod.AccountRow({ g, a, open: false, onToggle() {}, selectionStore: { select() {} }, selectionState: {}, reconnectStore: r.store, reconnectState: state })),
      line: controls(r.mod.ReconnectLine({ g, a, reconnectStore: r.store, reconnectState: state })), counts: r.mod.authCounts(g, state) }
  }
  return { ...r, term, row, answer: (s, id = 'fp:new') => { answer = s; named = id }, usage: u => { groups = [{ slug: 'anthropic', accounts: [NEW[u]] }] },
    down: v => { down = v }, refuse: v => { refuse = v }, noUsage: v => { noUsage = v },
    // Park the next /credential-health call until the returned function is called.
    hold: () => { let go; hold = new Promise(ok => { go = ok }); return () => { hold = null; go() } } }
}

test('the new account row shows the backend answer: disabled or quota is said on fp:new and never offers sign-in', async t => {
  for (const [status, message] of [['disabled', 'Account is disabled'], ['quota', 'Signed in · out of quota until it resets']]) {
    for (const usage of ['ready', 'limited', 'unavailable']) await t.test(`backend ${status} + usage lists fp:new ${usage}`, async () => {
      const r = await recovered(status, usage)
      const seen = r.row(NEW[usage])
      assert.ok(seen.text.includes(message), `${message} is on the visible row: ${JSON.stringify(seen.text)}`)
      assert.deepEqual(seen.auth, [], 'no sign-in or disconnected badge')
      assert.deepEqual(seen.buttons.map(b => b[0]), ['Check sign-in for me'])
      assert.deepEqual(seen.line.map(x => x[1]), ['Check sign-in for me', message])
      assert.deepEqual(seen.counts, { signIn: 0, disconnected: 0 })
      assert.ok(!seen.text.includes('Reconnected'))
      // Check again on the new row asks the backend about the stored row, not the fingerprint.
      r.answer('ok'); r.usage('ready')
      await seen.buttons[0][1]()
      const asked = r.restCalls.filter(c => c.path === '/credential-health')
      assert.equal(asked.length, 2); assert.deepEqual(asked[1].body, { provider: 'anthropic', credential_id: 'pool-9' })
      assert.ok(r.row(NEW.ready).text.includes('Reconnected'))
    })
  }
})

test('the new account row shows an unconfirmed answer as a caveat with a re-check, not as healthy', async t => {
  for (const status of ['unknown', undefined, 'renewed']) await t.test(`backend ${status} + usage lists fp:new ready`, async () => {
    const r = await recovered(status, 'ready')
    const seen = r.row(NEW.ready)
    assert.ok(seen.text.includes('Could not confirm the account — check again'), JSON.stringify(seen.text))
    assert.ok(!seen.text.includes('Reconnected'))
    assert.deepEqual(seen.buttons.map(b => b[0]), ['Check sign-in for me'])
    assert.deepEqual(seen.counts, { signIn: 0, disconnected: 0 })
    r.answer('disabled')
    await seen.buttons[0][1]()
    assert.ok(r.row(NEW.ready).text.includes('Account is disabled'))
  })
  // Usage has not loaded the new id: the old row keeps its sign-in offer and the re-check.
  let r = await recovered('unknown', null)
  assert.deepEqual(r.row(DEAD).line.map(x => x[1]), ['Sign in to me again', 'Check sign-in for me', 'Could not confirm the account — check again'])
  // Nothing confirmed and usage says the new id is signed out: that row offers its own sign-in.
  r = await recovered('unknown', 'unavailable')
  const dead = r.row(NEW.unavailable)
  assert.equal(dead.line[0][1], 'Sign in to me again'); assert.deepEqual(dead.auth, ['sign-in']); assert.deepEqual(dead.counts, { signIn: 1, disconnected: 0 })
  assert.ok(!dead.text.includes('Could not confirm the account — check again'))
})

test('a disabled or quota answer does not hide a sign-in that dies afterwards', async t => {
  for (const status of ['disabled', 'quota']) for (const usage of [null, 'ready', 'limited']) await t.test(`backend ${status}, usage ${usage} at the answer, fp:new signed out later`, async () => {
    const r = await recovered(status, usage)
    const later = r.row(NEW.unavailable)
    assert.equal(later.line[0][1], 'Sign in to me again')
    assert.deepEqual(later.auth, ['sign-in']); assert.deepEqual(later.counts, { signIn: 1, disconnected: 0 })
    assert.ok(!later.text.includes('Account is disabled') && !later.text.includes('Signed in · out of quota until it resets'))
  })
})

// The backend says the stored row is still signed out while usage lists it, stale, as ready under
// its new account id: that answer must show on the visible row, not vanish with the old id.
test('a still-signed-out answer follows the row to its new account id instead of reading as healthy', async t => {
  const MSG = 'Sign-in did not finish — nothing was saved'
  for (const first of ['disabled', 'quota', 'unknown']) await t.test(`${first} on stale-ready fp:new, then Check again says needs_sign_in for fp:new`, async () => {
    const r = await recovered(first, 'ready')
    r.answer('needs_sign_in', 'fp:new')
    await r.row(NEW.ready).buttons[0][1]()
    const asked = r.restCalls.filter(c => c.path === '/credential-health')
    assert.deepEqual(asked[asked.length - 1].body, { provider: 'anthropic', credential_id: 'pool-9' })
    // Usage still says ready; the backend says signed out. The visible row says so and is counted.
    const seen = r.row(NEW.ready)
    assert.ok(seen.text.includes(MSG), `the answer is on the visible row: ${JSON.stringify(seen.text)}`)
    assert.deepEqual(seen.auth, ['sign-in']); assert.ok(seen.text.includes('sign-in needed'))
    assert.deepEqual(seen.counts, { signIn: 1, disconnected: 0 }); assert.equal(r.mod.signInCount([{ slug: 'anthropic', accounts: [NEW.ready] }], r.store.$state.get()), 1)
    assert.deepEqual(seen.buttons.map(b => b[0]), ['Sign in to me again', 'Check sign-in for me'], 'sign-in and the re-check are on the visible row')
    assert.deepEqual(seen.line.map(x => x[1]), ['Sign in to me again', 'Check sign-in for me', MSG])
    assert.ok(!seen.text.includes('Reconnected') && !seen.text.includes('Account is disabled'))
    // A disabled listing of that id is neither counted nor offered sign-in; another id with the label is untouched.
    const off = r.row({ ...NEW.ready, disabled: true })
    assert.deepEqual(off.counts, { signIn: 0, disconnected: 0 }); assert.deepEqual(off.auth, []); assert.deepEqual(off.buttons, [])
    const twin = r.row({ id: 'fp:twin', label: 'me', state: 'ready' })
    assert.deepEqual(twin.counts, { signIn: 0, disconnected: 0 }); assert.deepEqual(twin.auth, []); assert.deepEqual(twin.buttons, [])
    // Usage catching up (signed out) keeps the same single count and offer.
    const dead = r.row(NEW.unavailable)
    assert.equal(dead.line[0][1], 'Sign in to me again'); assert.deepEqual(dead.auth, ['sign-in']); assert.deepEqual(dead.counts, { signIn: 1, disconnected: 0 })
    // The re-check still asks about the stored row, twice in a row, and can finish.
    await seen.buttons[1][1]()
    assert.ok(r.row(NEW.ready).text.includes(MSG)); assert.deepEqual(r.row(NEW.ready).counts, { signIn: 1, disconnected: 0 })
    r.answer('ok')
    await r.row(NEW.ready).buttons[1][1]()
    assert.equal(r.restCalls.filter(c => c.path === '/credential-health').every(c => c.body.credential_id === 'pool-9'), true)
    const ok = r.row(NEW.ready)
    assert.ok(ok.text.includes('Reconnected')); assert.deepEqual(ok.auth, []); assert.deepEqual(ok.counts, { signIn: 0, disconnected: 0 })
  })
  await t.test('the first readback says needs_sign_in while usage already lists stale-ready fp:new', async () => {
    const r = await recovered('needs_sign_in', 'ready')
    const seen = r.row(NEW.ready)
    assert.ok(seen.text.includes(MSG), JSON.stringify(seen.text))
    assert.deepEqual(seen.buttons.map(b => b[0]), ['Sign in to me again', 'Check sign-in for me'])
    assert.deepEqual(seen.auth, ['sign-in']); assert.deepEqual(seen.counts, { signIn: 1, disconnected: 0 })
    // Sign in again from the visible row starts the recovery for the stored row the answer is
    // about, already bound to pool-9: /reconnect is not asked about the new fingerprint.
    const before = r.term.calls.length, asked = r.restCalls.filter(c => c.path === '/reconnect').length
    const flow = seen.buttons[0][1]()
    await tick(); await tick(); await tick()
    assert.equal(r.term.calls.length, before + 1); assert.equal(r.term.calls[before].credentialId, 'pool-9')
    assert.equal(r.restCalls.filter(c => c.path === '/reconnect').length, asked)
    r.answer('ok'); r.term.end(0, 'pty-2'); await flow
    assert.ok(r.row(NEW.ready).text.includes('Reconnected')); assert.deepEqual(r.row(NEW.ready).counts, { signIn: 0, disconnected: 0 })
  })
  await t.test('cancel with the row still signed out is shown on the stale-ready new row too', async () => {
    const r = await recovered('needs_sign_in', 'ready', true)
    const seen = r.row(NEW.ready)
    assert.ok(seen.text.includes('Sign-in cancelled'), JSON.stringify(seen.text))
    assert.deepEqual(seen.buttons.map(b => b[0]), ['Sign in to me again', 'Check sign-in for me'])
    assert.deepEqual(seen.auth, ['sign-in']); assert.deepEqual(seen.counts, { signIn: 1, disconnected: 0 })
  })
  await t.test('a readback with no account id and no status confirms nothing: no sign-in label on a ready row', async () => {
    const r = await recovered('disabled', 'ready')
    r.answer(undefined, null)
    await r.row(NEW.ready).buttons[0][1]()
    const seen = r.row(NEW.ready)
    assert.ok(seen.text.includes(MSG)); assert.deepEqual(seen.buttons.map(b => b[0]), ['Check sign-in for me'])
    assert.deepEqual(seen.auth, []); assert.deepEqual(seen.counts, { signIn: 0, disconnected: 0 })
  })
  for (const first of ['disabled', 'quota', 'unknown', 'needs_sign_in']) await t.test(`${first} on stale-ready fp:new, then needs_sign_in naming no account id: message and re-check only`, async () => {
    const r = await recovered(first, 'ready')
    r.answer('needs_sign_in', null)
    await r.row(NEW.ready).buttons.pop()[1]()
    const seen = r.row(NEW.ready)
    assert.ok(seen.text.includes(MSG), JSON.stringify(seen.text)); assert.deepEqual(seen.buttons.map(b => b[0]), ['Check sign-in for me'])
    assert.deepEqual(seen.auth, []); assert.deepEqual(seen.counts, { signIn: 0, disconnected: 0 })
  })
  await t.test('usage still lists the old id: that row keeps its sign-in offer and badge', async () => {
    const r = await recovered('needs_sign_in', null)
    const old = r.row(DEAD)
    assert.equal(old.line[0][1], 'Sign in to me again'); assert.deepEqual(old.auth, ['sign-in']); assert.deepEqual(old.counts, { signIn: 1, disconnected: 0 })
    assert.ok(old.text.includes(MSG))
  })
})

// Two stored rows the backend maps to one account id: the row shows the most recent answer.
test('answers from two stored rows naming one account id: the latest answer is the one shown', async () => {
  const { mod, store } = await setup({ term: null, groups: () => [], rest: () => ({}) })
  const base = { provider: 'anthropic', resolvedId: 'fp:new', scope: mod.scopeOf(ROUTE) }
  const quota = { ...base, id: 'fp:a', settled: true, done: true, message: 'Signed in · out of quota until it resets', seq: 3 }
  const off = { ...base, id: 'fp:b', settled: true, error: true, message: 'Account is disabled', seq: 7 }
  for (const state of [{ a: quota, b: off }, { b: off, a: quota }]) assert.equal(mod.rowFix(state, 'anthropic', NEW.ready).message, 'Account is disabled')
  assert.equal(mod.rowFix({ a: { ...quota, seq: 9 }, b: off }, 'anthropic', NEW.ready).message, 'Signed in · out of quota until it resets')
  // The row's own entry still wins over any followed one.
  assert.equal(mod.rowFix({ a: quota, b: off, [store.key('anthropic', 'fp:new')]: { id: 'fp:new', message: 'own' } }, 'anthropic', NEW.ready).message, 'own')
  // The store numbers its answers in order.
  const r = await recovered('disabled', 'ready')
  const first = Object.values(r.store.$state.get())[0].seq
  r.answer('quota'); await r.row(NEW.ready).buttons[0][1]()
  assert.ok(Object.values(r.store.$state.get())[0].seq > first)
})

// A re-check that never reaches the backend, or is still running, has learned nothing: the visible
// row keeps what the last answer said (label, count, re-check) instead of reading as healthy.
test('a re-check that fails or is still running keeps the last answer on the visible row', async t => {
  const FAIL = 'Could not confirm the account — check again'
  const WAS = { disabled: { auth: [], counts: { signIn: 0, disconnected: 0 }, buttons: ['Check sign-in for me'] },
    quota: { auth: [], counts: { signIn: 0, disconnected: 0 }, buttons: ['Check sign-in for me'] },
    unknown: { auth: [], counts: { signIn: 0, disconnected: 0 }, buttons: ['Check sign-in for me'] },
    needs_sign_in: { auth: ['sign-in'], counts: { signIn: 1, disconnected: 0 }, buttons: ['Sign in to me again', 'Check sign-in for me'] } }
  for (const [first, was] of Object.entries(WAS)) await t.test(`${first} followed onto stale-ready fp:new, then the re-check throws`, async () => {
    const r = await recovered(first, 'ready')
    const same = (seen, text) => {
      assert.ok(seen.text.includes(text), `${text} is on the visible row: ${JSON.stringify(seen.text)}`)
      assert.deepEqual(seen.auth, was.auth); assert.deepEqual(seen.counts, was.counts); assert.deepEqual(seen.buttons.map(b => b[0]), was.buttons)
      assert.ok(!seen.text.includes('Reconnected'))
    }
    // While the request is out the row is not healthy either.
    const go = r.hold(); r.down(true)
    const pending = r.row(NEW.ready).buttons.pop()[1]()
    await tick()
    same(r.row(NEW.ready), 'Checking account…')
    go(); await pending
    same(r.row(NEW.ready), FAIL)
    // Again, still down; then the backend answers and the row follows that answer.
    await r.row(NEW.ready).buttons.pop()[1]()
    same(r.row(NEW.ready), FAIL)
    assert.equal(r.restCalls.filter(c => c.path === '/credential-health').every(c => c.body.credential_id === 'pool-9'), true)
    r.down(false); r.answer('ok')
    await r.row(NEW.ready).buttons.pop()[1]()
    const ok = r.row(NEW.ready)
    assert.ok(ok.text.includes('Reconnected')); assert.deepEqual(ok.auth, []); assert.deepEqual(ok.counts, { signIn: 0, disconnected: 0 })
  })
  await t.test('backend ok but usage cannot be read: the caveat is on the visible row, not Reconnected', async () => {
    const r = await recovered('disabled', 'ready')
    r.answer('ok'); r.noUsage(true)
    await r.row(NEW.ready).buttons[0][1]()
    const seen = r.row(NEW.ready)
    assert.ok(seen.text.includes(FAIL), JSON.stringify(seen.text)); assert.ok(!seen.text.includes('Reconnected'))
    assert.deepEqual(seen.buttons.map(b => b[0]), ['Check sign-in for me'])
    r.noUsage(false)
    await seen.buttons[0][1]()
    assert.ok(r.row(NEW.ready).text.includes('Reconnected'))
  })
  await t.test('the stored row left the pool: the answer stays on its own line and follows nowhere', async () => {
    const r = await recovered('disabled', 'ready')
    r.answer('gone')
    await r.row(NEW.ready).buttons[0][1]()
    const state = r.store.$state.get()
    const groups = r.mod.withSignedOut([{ slug: 'anthropic', accounts: [NEW.ready], total: 1 }], state)
    assert.deepEqual(groups[0].accounts.map(a => a.id), ['fp:new', 'fp:old'])
    assert.ok(r.row(groups[0].accounts[1]).text.includes('This account is no longer in the pool — nothing was saved'))
    assert.deepEqual(r.row(NEW.ready).buttons, [])
  })
})

// Sign in again on the followed row acts on the stored row: whatever happens to that sign-in is
// shown on the visible row, and a failure never hides that the row is still signed out.
test('Sign in again from a followed signed-out row keeps the label and count through every outcome', async t => {
  const dead = async () => {
    const r = await recovered('needs_sign_in', 'ready')
    return { r, calls: r.restCalls.filter(c => c.path === '/reconnect').length }
  }
  const labelled = seen => { assert.deepEqual(seen.auth, ['sign-in']); assert.deepEqual(seen.counts, { signIn: 1, disconnected: 0 }) }
  await t.test('Desktop refuses to start the recovery', async () => {
    const { r, calls } = await dead()
    r.refuse('no-shell')
    await r.row(NEW.ready).buttons[0][1]()
    const seen = r.row(NEW.ready)
    labelled(seen); assert.deepEqual(seen.buttons.map(b => b[0]), ['Sign in to me again', 'Check sign-in for me'])
    assert.equal(seen.line.length, 3); assert.ok(seen.line[2][1] && seen.line[2][1] !== 'Sign-in did not finish — nothing was saved', 'the refusal is said')
    assert.equal(r.restCalls.filter(c => c.path === '/reconnect').length, calls, '/reconnect is never asked about the new fingerprint')
    assert.equal(Object.keys(r.store.$state.get()).length, 1, 'no entry is written under the visible account id')
  })
  await t.test('the terminal runs on the visible row; input and cancel reach it; the readback then fails', async () => {
    const { r, calls } = await dead()
    const flow = r.row(NEW.ready).buttons[0][1]()
    await tick(); await tick(); await tick()
    assert.equal(r.term.calls.at(-1).credentialId, 'pool-9')
    let seen = r.row(NEW.ready)
    labelled(seen); assert.deepEqual(seen.buttons.map(b => b[0]), ['Cancel sign-in for me', 'Send answer for me'])
    assert.ok(await r.store.sendInput('anthropic', { id: 'fp:old' }, 'yes')); assert.deepEqual(r.term.written.at(-1), ['pty-2', 'yes\r'])
    r.down(true)
    const exit = r.term.exit.get('pty-2')
    await seen.buttons[0][1]() // Cancel sign-in, from the visible row
    assert.ok(r.term.disposed.includes('pty-2'))
    exit({ code: 130, signal: null }); await flow
    seen = r.row(NEW.ready)
    labelled(seen); assert.deepEqual(seen.buttons.map(b => b[0]), ['Sign in to me again', 'Check sign-in for me'])
    assert.ok(seen.text.includes('Could not confirm the account — check again'))
    assert.equal(r.restCalls.filter(c => c.path === '/reconnect').length, calls)
    assert.equal(Object.keys(r.store.$state.get()).length, 1)
  })
  await t.test('usage lists both the old and the new account id: one count, and one line when the old id is only a ghost', async () => {
    const { r } = await dead()
    const state = r.store.$state.get()
    assert.deepEqual(r.mod.authCounts({ slug: 'anthropic', accounts: [DEAD, NEW.ready] }, state), { signIn: 1, disconnected: 0 })
    const removed = Object.fromEntries(Object.entries(state).map(([k, st]) => [k, { ...st, removed: true }]))
    assert.deepEqual(r.mod.withSignedOut([{ slug: 'anthropic', accounts: [NEW.ready], total: 1 }], removed)[0].accounts.map(a => a.id), ['fp:new'])
    assert.deepEqual(r.mod.withSignedOut([{ slug: 'anthropic', accounts: [], total: 0 }], removed)[0].accounts.map(a => a.id), ['fp:old'])
  })
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
