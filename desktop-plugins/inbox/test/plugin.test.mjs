// node --test: helpers + store actions (SDK and React stubbed by the loader below).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,' + encodeURIComponent(
    'export const host = globalThis.__host = { state: {} };' +
    'export const atom = v => { let x = v; return { get: () => x, set: n => { x = n } } };' +
    'export const COMPOSER_AREAS = { top: "composer.top" }; export const SIDEBAR_SECTIONS_AREA = "sidebar.sections";' +
    'export const SidebarSection = () => null; export const SidebarSectionRow = () => null; export const useValue = a => a.get();'), shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useState=v=>(globalThis.__useState?globalThis.__useState(v):[v,()=>{}]);export const jsx=(type,props,key)=>({type,props,key});export const jsxs=(type,props,key)=>({type,props,key})', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const host = globalThis.__host

test('ago / posterShort', () => {
  const now = 1_000_000_000
  assert.equal(m.ago(now / 1000 - 30, now), 'now')
  assert.equal(m.ago(now / 1000 - 720, now), '12m')
  assert.equal(m.ago(now / 1000 - 3 * 3600, now), '3h')
  assert.equal(m.ago(now / 1000 - 2 * 86400, now), '2d')
  assert.equal(m.ago(null, now), '')
  assert.equal(m.posterShort('cron · Vast idle audit'), 'cron')
  assert.equal(m.posterShort(null), '')
})

test('mergeItems: per-gateway attribution, unread first then newest, dedupe per gateway', () => {
  const out = m.mergeItems([
    { gateway: 'local', profile: 'default', items: [{ session_id: 'a', updated_at: 10 }, { session_id: 'b', updated_at: 30, unread: true }] },
    { gateway: 'mini', profile: 'bb', items: [{ session_id: 'c', updated_at: 20 }, { session_id: 'c', updated_at: 20 }] },
    { gateway: 'nous', items: null }
  ])
  assert.deepEqual(out.map(i => `${i.gateway}:${i.session_id}`), ['local:b', 'mini:c', 'local:a'])
  assert.equal(out[1].profile, 'bb')
  assert.equal(out[2].profile, 'default')
})

test('routeFor / restOpts: plain on the routed gateway, owner route elsewhere', () => {
  const gws = [{ id: 'local', kind: 'local' }, { id: 'mini', kind: 'ssh' }]
  assert.equal(m.routeFor({ gateway: 'local' }, 'local', gws), null)
  assert.deepEqual(m.routeFor({ gateway: 'mini', profile: 'bb' }, 'local', gws),
    { connectionId: 'mini', mode: 'remote', profile: 'bb', targetProfile: 'bb' })
  assert.deepEqual(m.restOpts({ gateway: 'local' }, 'local', { method: 'POST' }), { method: 'POST' })
  assert.deepEqual(m.restOpts({ gateway: 'mini' }, 'local', { method: 'POST' }), { method: 'POST', connectionId: 'mini' })
})

test('profile items: item.profile wins, routes even on the routed gateway, REST paths carry ?profile', () => {
  const out = m.mergeItems([{ gateway: 'nous', profile: 'default', items: [{ session_id: 'x', profile: 'alpha-follow' }, { session_id: 'y' }] }])
  assert.deepEqual(out.map(i => i.profile), ['alpha-follow', 'default'])
  const gws = [{ id: 'local', kind: 'local' }, { id: 'nous', kind: 'cloud' }]
  assert.deepEqual(m.routeFor({ gateway: 'local', profile: 'alpha-follow' }, 'local', gws),
    { connectionId: 'local', mode: 'local', profile: 'alpha-follow', targetProfile: 'alpha-follow' })
  assert.equal(m.routeFor({ gateway: 'local', profile: 'default' }, 'local', gws), null)
  assert.equal(m.itemPath({ session_id: 'x', profile: 'alpha-follow' }, 'accept'), '/items/x/accept?profile=alpha-follow')
  assert.equal(m.itemPath({ session_id: 'a b', profile: 'default' }, 'archive'), '/items/a%20b/archive')
  assert.equal(m.sessionPath({ session_id: 'x', profile: 'alpha-follow' }), '/api/sessions/x?profile=alpha-follow')
  assert.equal(m.sessionPath({ session_id: 'x' }), '/api/sessions/x')
})

function fakeCtx(answers) {
  const calls = []
  return {
    calls,
    storage: { get: (k, f) => f, set() {} },
    rest: async (path, opts = {}) => {
      calls.push([path, opts])
      const r = answers(path, opts)
      if (r instanceof Error) throw r
      return r
    }
  }
}

test('refresh reads every gateway; a failing gateway does not drop the others', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [{ id: 'local', kind: 'local' }, { id: 'mini', kind: 'ssh' }, { id: 'old', kind: 'ssh' }] }
  const ctx = fakeCtx((path, opts) =>
    opts.connectionId === 'old' ? new Error('404') :
    { profile: 'default', items: [{ session_id: opts.connectionId ? 'remote1' : 'local1', updated_at: 1 }] })
  const store = m.createStore(ctx)
  await store.refresh()
  assert.deepEqual(ctx.calls.map(c => c[1].connectionId ?? 'routed'), ['routed', 'mini', 'old'])
  assert.deepEqual(store.$items.get().map(i => i.gateway).sort(), ['local', 'mini'])
})

test('accept posts to the owning gateway, then submits the prompt into that chat', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [{ id: 'local' }, { id: 'mini' }] }
  const submitted = []
  host.composer = { submit: (sid, text) => (submitted.push([sid, text]), true) }
  const ctx = fakeCtx(path => (path.endsWith('/accept') ? { accept_prompt: 'destroy box' } : { items: [] }))
  const store = m.createStore(ctx)
  const item = { session_id: 's1', gateway: 'mini', accept_prompt: 'stale' }
  store.$items.set([item])
  await store.accept(item)
  assert.deepEqual(ctx.calls[0], ['/items/s1/accept', { method: 'POST', connectionId: 'mini' }])
  assert.deepEqual(submitted, [['s1', 'destroy box']])
  assert.equal(store.$items.get().length, 0)
})

test('accept falls back to the draft when no composer can submit', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  const drafts = []
  const notes = []
  host.composer = { submit: () => false, setDraft: async (sid, t) => drafts.push([sid, t]) }
  host.notify = n => notes.push(n)
  const store = m.createStore(fakeCtx(path => (path.endsWith('/accept') ? {} : { items: [] })))
  await store.accept({ session_id: 's2', gateway: 'local', accept_prompt: 'do x' })
  assert.deepEqual(drafts, [['s2', 'do x']])
  assert.equal(notes.length, 1)
})

test('archive removes optimistically and posts to the owning gateway', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  const ctx = fakeCtx(() => ({ ok: true, items: [] }))
  const store = m.createStore(ctx)
  const item = { session_id: 's3', gateway: 'local' }
  store.$items.set([item, { session_id: 's4', gateway: 'local' }])
  const p = store.archive(item)
  assert.deepEqual(store.$items.get().map(i => i.session_id), ['s4'])
  await p
  assert.deepEqual(ctx.calls[0], ['/items/s3/archive', { method: 'POST' }])
})

test('markRead clears unread once and posts to the owning gateway', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  const apiCalls = []
  globalThis.window = { hermesDesktop: { api: async req => (apiCalls.push(req), { ok: true }) } }
  const store = m.createStore(fakeCtx(() => ({ ok: true })))
  const item = { session_id: 's5', gateway: 'mini', unread: true }
  store.$items.set([item])
  store.markRead(item)
  assert.equal(store.$items.get()[0].unread, false)
  store.markRead(store.$items.get()[0])
  assert.deepEqual(apiCalls, [{ connectionId: 'mini', path: '/api/sessions/s5', method: 'PATCH', body: { unread: false } }])
  delete globalThis.window
})

test('nextItemAfter: the one below, else the top of what is left, null on the last', () => {
  const [a, b, c] = [{ session_id: 'a' }, { session_id: 'b' }, { session_id: 'c' }]
  assert.equal(m.nextItemAfter([a, b, c], a), b)
  assert.equal(m.nextItemAfter([a, b, c], c), a)
  assert.equal(m.nextItemAfter([a], a), null)
})

function hotkeyStore() {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [{ id: 'local', kind: 'local' }, { id: 'mini', kind: 'ssh' }] }
  const opened = []
  host.openSession = async (sid, opts) => { opened.push([sid, opts]) }
  const ctx = fakeCtx(() => ({ ok: true, items: [] }))
  const store = m.createStore(ctx)
  return { ctx, store, opened }
}

test('archiveHotkey on an inbox message: archives via the inbox on its gateway and opens the next message', async () => {
  const { ctx, store, opened } = hotkeyStore()
  store.$items.set([{ session_id: 's1', gateway: 'mini' }, { session_id: 's2', gateway: 'local' }])
  let coreNext = 0
  assert.equal(store.archiveHotkey({ sessionId: 's1', isFocused: true, openNextSession: () => coreNext++ }), true)
  assert.deepEqual(ctx.calls[0], ['/items/s1/archive', { method: 'POST', connectionId: 'mini' }])
  assert.deepEqual(opened, [['s2', {}]])
  assert.equal(coreNext, 0)
  assert.deepEqual(store.$items.get().map(i => i.session_id), ['s2'])
})

test('archiveHotkey on the last message: archives it, then core steps to the next session', () => {
  const { ctx, store, opened } = hotkeyStore()
  store.$items.set([{ session_id: 's9', gateway: 'local' }])
  let coreNext = 0
  assert.equal(store.archiveHotkey({ sessionId: 's9', isFocused: true, openNextSession: () => coreNext++ }), true)
  assert.deepEqual(ctx.calls[0], ['/items/s9/archive', { method: 'POST' }])
  assert.deepEqual(opened, [])
  assert.equal(coreNext, 1)
})

test('archiveHotkey on a selected-but-not-open message archives without navigating', () => {
  const { ctx, store, opened } = hotkeyStore()
  store.$items.set([{ session_id: 's1', gateway: 'local' }, { session_id: 's2', gateway: 'local' }])
  let coreNext = 0
  assert.equal(store.archiveHotkey({ sessionId: 's1', isFocused: false, openNextSession: () => coreNext++ }), true)
  assert.equal(ctx.calls[0][0], '/items/s1/archive')
  assert.deepEqual(opened, [])
  assert.equal(coreNext, 0)
})

test('archiveHotkey leaves non-inbox sessions to core', () => {
  const { ctx, store } = hotkeyStore()
  store.$items.set([{ session_id: 's1', gateway: 'local' }])
  assert.equal(store.archiveHotkey({ sessionId: 'other', isFocused: true, openNextSession: () => {} }), false)
  assert.equal(ctx.calls.length, 0)
})

test('register hooks the archive hotkey when the core offers it, and tolerates older cores', () => {
  const handlers = []
  const base = { storage: { get: (k, f) => f, set() {} }, rest: async () => ({ items: [] }), setInterval() {}, register() {}, onDispose() {} }
  host.state.focusedStoredSessionId = { get: () => null, listen: () => () => {} }
  m.default.register({ ...base, registerArchiveHotkeyHandler: h => (handlers.push(h), () => {}) })
  assert.equal(handlers.length, 1)
  assert.equal(handlers[0]({ sessionId: 'nope', isFocused: true, openNextSession() {} }), false)
  m.default.register(base)
})

// ── card types ──────────────────────────────────────────────────────────────

const XPOST = {
  session_id: 'x1', gateway: 'local', type: 'x-post', type_label: 'X post', poster: 'cron · x',
  fields: { id: 'Q1', text: 'Cells are agents.' },
  actions: [
    { id: 'approve', label: 'Approve', icon: 'check', style: 'primary', kind: 'resolve', row: true },
    { id: 'edit', label: 'Edit', icon: 'edit', kind: 'resolve', input: { field: 'text', multiline: true, max: 280 } },
    { id: 'skip', label: 'Skip', icon: 'close', kind: 'resolve' },
    { id: 'rewrite', label: 'Rewrite…', kind: 'prompt', input: { placeholder: 'What to change' } }
  ]
}

/** Depth-first search of a jsx tree for nodes matching `pred`. */
function find(node, pred, out = []) {
  if (!node || typeof node !== 'object') return out
  if (Array.isArray(node)) {
    for (const n of node) find(n, pred, out)
    return out
  }
  if (pred(node)) out.push(node)
  if (node.props) find(node.props.children, pred, out)
  return out
}

test('barActions: type actions with primary first and Archive last; api<3 items keep Do it / Archive', () => {
  const ids = m.barActions({ actions: [{ id: 'skip', kind: 'resolve' }, { id: 'ok', kind: 'resolve', style: 'primary' }] }).map(a => a.id)
  assert.deepEqual(ids, ['ok', 'skip', 'archive'])
  assert.deepEqual(m.barActions({ session_id: 'old' }).map(a => [a.id, a.kind]), [['do_it', 'accept'], ['archive', 'archive']])
  assert.deepEqual(m.barActions({ actions: [{ id: 'archive', kind: 'resolve' }] }).map(a => a.id), ['archive'])
})

test('Bar renders the focused card type actions in order', () => {
  host.state.focusedStoredSessionId = { get: () => 'x1', listen: () => () => {} }
  const store = m.createStore(fakeCtx(() => ({ items: [] })))
  store.$items.set([XPOST])
  const tree = m.Bar({ store })
  const buttons = find(tree, n => n.props && n.props.action).map(n => n.props.action.id)
  assert.deepEqual(buttons, ['approve', 'edit', 'skip', 'rewrite', 'archive'])
  assert.match(JSON.stringify(find(tree, n => n.type === 'span' && typeof n.props.children === 'string')), /X post from cron · x/)
})

test('Bar on an api<3 item renders exactly Do it / Archive', () => {
  host.state.focusedStoredSessionId = { get: () => 'old1', listen: () => () => {} }
  const store = m.createStore(fakeCtx(() => ({ items: [] })))
  store.$items.set([{ session_id: 'old1', gateway: 'local', accept_prompt: 'go' }])
  const tree = m.Bar({ store })
  const btns = find(tree, n => n.props && n.props.action)
  assert.deepEqual(btns.map(n => n.props.action.label), ['Do it', 'Archive'])
  assert.equal(btns[0].props.title, 'go')
})

test('Bar: an input action opens the editor prefilled from its field; Save runs act with the text', async () => {
  host.state.focusedStoredSessionId = { get: () => 'x1', listen: () => () => {} }
  let state = null
  globalThis.__useState = v => [state ?? v, n => { state = n }]
  const ctx = fakeCtx(() => ({ ok: true, items: [] }))
  const store = m.createStore(ctx)
  store.$items.set([XPOST])
  const edit = find(m.Bar({ store }), n => n.props && n.props.action && n.props.action.id === 'edit')[0]
  edit.props.onClick()
  assert.equal(state.value, 'Cells are agents.')
  const editor = m.Bar({ store })
  assert.equal(editor.type, m.Editor)
  editor.props.onChange('Cells are agents. So are you.')
  const tree = m.Editor(m.Bar({ store }).props)
  const input = find(tree, n => n.type === 'textarea')[0]
  assert.equal(input.props.value, 'Cells are agents. So are you.')
  assert.equal(find(tree, n => n.props && n.props['data-slot'] === 'inbox-editor-count')[0].props.children, '29/280')
  find(tree, n => n.props && n.props.action && n.props.action.id === 'save')[0].props.onClick()
  await new Promise(r => setTimeout(r, 0))
  assert.deepEqual(ctx.calls[0], ['/items/x1/act', { method: 'POST', body: { action: 'edit', input: 'Cells are agents. So are you.' } }])
  globalThis.__useState = null
})

test('Editor: Esc cancels, ⌘Enter saves, over-max disables Save', () => {
  const calls = []
  const props = {
    item: XPOST, action: XPOST.actions[1], value: 'x'.repeat(281), disabled: false,
    onChange() {}, onSave: v => calls.push(['save', v]), onCancel: () => calls.push(['cancel'])
  }
  let tree = m.Editor(props)
  const save = () => find(tree, n => n.props && n.props.action && n.props.action.id === 'save')[0]
  assert.equal(save().props.disabled, true)
  const ta = () => find(tree, n => n.type === 'textarea')[0]
  ta().props.onKeyDown({ key: 'Enter', metaKey: true, preventDefault() {} })
  assert.deepEqual(calls, [])
  tree = m.Editor({ ...props, value: 'short' })
  ta().props.onKeyDown({ key: 'Enter', metaKey: false, preventDefault() {} })  // plain Enter = newline
  ta().props.onKeyDown({ key: 'Enter', metaKey: true, preventDefault() {} })
  ta().props.onKeyDown({ key: 'Escape', preventDefault() {} })
  assert.deepEqual(calls, [['save', 'short'], ['cancel']])
})

test('act(resolve) posts {action} to the owning gateway/profile, drops the card and opens the next one', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [{ id: 'local', kind: 'local' }, { id: 'mini', kind: 'ssh' }] }
  host.state.focusedStoredSessionId = { get: () => 'x1', listen: () => () => {} }
  const opened = []
  host.openSession = async (sid, opts) => { opened.push(sid) }
  const ctx = fakeCtx(() => ({ ok: true, items: [] }))
  const store = m.createStore(ctx)
  const item = { ...XPOST, gateway: 'mini', profile: 'bb' }
  store.$items.set([item, { session_id: 'n2', gateway: 'local' }])
  assert.equal(await store.act(item, XPOST.actions[0]), true)
  assert.deepEqual(ctx.calls[0], ['/items/x1/act?profile=bb', { method: 'POST', body: { action: 'approve' }, connectionId: 'mini' }])
  assert.deepEqual(opened, ['n2'])
})

test('act(prompt) submits the returned prompt into the chat', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  const submitted = []
  host.composer = { submit: (sid, text) => (submitted.push([sid, text]), true) }
  const ctx = fakeCtx(path => (path.includes('/act') ? { prompt: 'Rewrite X draft Q1: shorter' } : { items: [] }))
  const store = m.createStore(ctx)
  store.$items.set([XPOST])
  await store.act(XPOST, XPOST.actions[3], 'shorter')
  assert.deepEqual(ctx.calls[0][1].body, { action: 'rewrite', input: 'shorter' })
  assert.deepEqual(submitted, [['x1', 'Rewrite X draft Q1: shorter']])
})

test('act with confirm: cancelling sends nothing', async () => {
  const ctx = fakeCtx(() => ({ ok: true, items: [] }))
  const store = m.createStore(ctx)
  globalThis.confirm = () => false
  assert.equal(await store.act(XPOST, { id: 'nuke', label: 'Nuke', kind: 'resolve', confirm: 'Sure?' }), false)
  assert.equal(ctx.calls.length, 0)
  globalThis.confirm = () => true
  assert.equal(await store.act(XPOST, { id: 'nuke', label: 'Nuke', kind: 'resolve', confirm: 'Sure?' }), true)
  assert.equal(ctx.calls[0][1].body.action, 'nuke')
  delete globalThis.confirm
})

test('row actions: row:true actions without input sit next to Archive in the sidebar row', () => {
  assert.deepEqual(m.rowActions(XPOST).map(a => a.id), ['approve'])
  assert.deepEqual(m.rowActions({ session_id: 'old' }), [])
  assert.deepEqual(m.rowActions({ actions: [{ id: 'e', row: true, input: {} }] }), [])
  assert.equal(m.inputDefault(XPOST, XPOST.actions[1]), 'Cells are agents.')
  assert.equal(m.inputDefault(XPOST, XPOST.actions[3]), '')
  assert.equal(m.counterText('abc', 280), '3/280')
})

test('sidebar row: RowActions renders the row action buttons before Archive and runs act', async () => {
  const ctx = fakeCtx(() => ({ ok: true, items: [] }))
  const store = m.createStore(ctx)
  store.$items.set([XPOST])
  const tree = m.RowActions({ store, item: XPOST })
  const kids = tree.props.children
  assert.equal(kids.length, 2)
  assert.equal(kids[0].props.action.id, 'approve')
  kids[0].props.onClick()
  await new Promise(r => setTimeout(r, 0))
  assert.deepEqual(ctx.calls[0][1].body, { action: 'approve' })
  assert.equal(m.RowActions({ store, item: { session_id: 'old' } }).props.onClick != null, true)  // plain Archive
})

test('review fixes: prompt actions never become row buttons; counter counts code points; tooltip shows the template', () => {
  assert.deepEqual(m.rowActions({ actions: [{ id: 'p', kind: 'prompt', row: true, prompt: 'x' }] }), [])
  assert.equal(m.counterText('ab😀', 280), '3/280')
  host.state.focusedStoredSessionId = { get: () => 'x1', listen: () => () => {} }
  const store = m.createStore(fakeCtx(() => ({ items: [] })))
  store.$items.set([{ ...XPOST, actions: [{ id: 'rewrite', label: 'Rewrite', kind: 'prompt', prompt: 'Rewrite {{id}}' }] }])
  const btn = find(m.Bar({ store }), n => n.props && n.props.action && n.props.action.id === 'rewrite')[0]
  assert.equal(btn.props.title, 'Rewrite {{id}}')
})

test('Editor: Enter while an IME composition is open does not save', () => {
  const calls = []
  const tree = m.Editor({ item: XPOST, action: { id: 'e', label: 'E', kind: 'resolve', input: {} }, value: 'x',
    onChange() {}, onSave: v => calls.push(v), onCancel() {} })
  const input = find(tree, n => n.type === 'input')[0]
  input.props.onKeyDown({ key: 'Enter', isComposing: true, preventDefault() {} })
  input.props.onKeyDown({ key: 'Enter', preventDefault() {} })
  assert.deepEqual(calls, ['x'])
})
