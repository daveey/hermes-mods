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
  const ctx = fakeCtx(() => ({ ok: true, items: [] }))
  const store = m.createStore(ctx)
  store.$items.set([XPOST])
  const edit = find(m.Bar({ store }), n => n.props && n.props.action && n.props.action.id === 'edit')[0]
  edit.props.onClick()
  assert.equal(store.$editing.get().value, 'Cells are agents.')
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
  assert.equal(store.$editing.get(), null)  // a successful save closes the editor
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

// ── task actions ────────────────────────────────────────────────────────────

const TASK_ITEM = {
  session_id: 't1', gateway: 'local', type: 'x-post', type_label: 'X post', fields: { id: 'Q9', text: 'hi' },
  actions: [
    { id: 'approve', label: 'Approve', kind: 'resolve', style: 'primary' },
    { id: 'skip', label: 'Skip', icon: 'close', kind: 'task', row: true, prompt: 'Mark {{id}} skipped in QUEUE.md' },
    { id: 'later', label: 'Later', kind: 'task', prompt: 'p', input: { placeholder: 'when' } }
  ]
}

test('task act: closes the card like resolve, opens the next one and toasts the queued task id', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  host.state.focusedStoredSessionId = { get: () => 't1', listen: () => () => {} }
  const opened = []
  const notes = []
  host.openSession = async sid => { opened.push(sid) }
  host.notify = n => notes.push(n)
  const submitted = []
  host.composer = { submit: (sid, text) => (submitted.push([sid, text]), true) }
  const ctx = fakeCtx(path => (path.includes('/act') ? { state: 'resolved', outcome: 'skip', outcome_task: 't_abc123' } : { items: [] }))
  const store = m.createStore(ctx)
  store.$items.set([TASK_ITEM, { session_id: 'n2', gateway: 'local' }])
  assert.equal(await store.act(TASK_ITEM, TASK_ITEM.actions[1]), true)
  assert.equal(ctx.calls[0][0], '/items/t1/act')
  assert.deepEqual(ctx.calls[0][1].body, { action: 'skip' })
  assert.deepEqual(opened, ['n2'])
  assert.deepEqual(submitted, [])  // no agent turn in the card's chat
  assert.deepEqual(notes.map(n => n.message), ['Queued t_abc123'])
  assert.equal(store.$items.get().some(i => i.session_id === 't1'), false)
})

test('task act failure keeps the card in the list after refresh and shows the error, no toast', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  const notes = []
  const errors = []
  host.notify = n => notes.push(n)
  host.notifyError = (e, msg) => errors.push(msg)
  const ctx = {
    calls: [],
    rest: async (path, opts) => {
      ctx.calls.push([path, opts])
      if (path.includes('/act')) throw new Error('500 could not queue')
      return { items: [TASK_ITEM] }
    },
    setInterval() {}, storage: { get: () => true, set() {} }
  }
  const store = m.createStore(ctx)
  store.$items.set([TASK_ITEM])
  assert.equal(await store.act(TASK_ITEM, TASK_ITEM.actions[1]), false)
  assert.deepEqual(notes, [])
  assert.equal(errors.length, 1)
  assert.deepEqual(store.$items.get().map(i => i.session_id), ['t1'])
})

test('task buttons: in the bar with the template as tooltip; row button only without input', () => {
  assert.deepEqual(m.barActions(TASK_ITEM).map(a => a.id), ['approve', 'skip', 'later', 'archive'])
  assert.deepEqual(m.rowActions(TASK_ITEM).map(a => a.id), ['skip'])
  assert.deepEqual(m.rowActions({ actions: [{ id: 'l', kind: 'task', row: true, input: {} }] }), [])
})

test('task act without a returned task id shows no toast', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  host.state.focusedStoredSessionId = { get: () => 'zz', listen: () => () => {} }
  const notes = []
  host.notify = n => notes.push(n)
  const store = m.createStore(fakeCtx(path => (path.includes('/act') ? { state: 'resolved' } : { items: [] })))
  store.$items.set([TASK_ITEM])
  assert.equal(await store.act(TASK_ITEM, TASK_ITEM.actions[1]), true)
  assert.deepEqual(notes, [])
})

// ── action keys (⌥1…⌥9) and leaving a closed card ──────────────────────────

const OPT = '¡™£¢∞§¶•ª'
const key = (code, extra = {}) => {
  const d = /^Digit([1-9])$/.exec(code)
  const ev = { type: 'keydown', code, key: d ? OPT[d[1] - 1] : '¡', altKey: true, metaKey: false, ctrlKey: false, shiftKey: false,
    target: null, defaultPrevented: false, prevented: 0, stopped: 0, ...extra }
  ev.preventDefault = () => { ev.prevented++; ev.defaultPrevented = true }
  ev.stopImmediatePropagation = () => { ev.stopped++ }
  return ev
}
// Minimal element: tagName / isContentEditable / closest(selector) over a fixed ancestor list.
const el = (tagName, ancestors = [], editable = false) => ({
  tagName, isContentEditable: editable,
  closest: sel => (sel.split(',').some(p => ancestors.some(a => a === p.trim())) ? {} : null)
})
const COMPOSER = el('DIV', ['[data-chat-surface]'], true)

test('actionKeyDigit: bare ⌥/Alt + digit by physical key; anything else is 0', () => {
  assert.equal(m.actionKeyDigit(key('Digit1')), 1)
  assert.equal(m.actionKeyDigit(key('Digit9')), 9)
  assert.equal(m.actionKeyDigit(key('Numpad3')), 0)  // Windows Alt+numpad codes type characters
  assert.equal(m.actionKeyDigit(key('Digit0')), 0)
  assert.equal(m.actionKeyDigit(key('KeyA')), 0)
  assert.equal(m.actionKeyDigit(key('Digit1', { altKey: false, key: '1' })), 0)  // bare digit types
  assert.equal(m.actionKeyDigit(key('Digit1', { metaKey: true })), 0)  // ⌘⌥1 = tab slot 10
  assert.equal(m.actionKeyDigit(key('Digit1', { ctrlKey: true })), 0)  // ^1 / AltGr
  assert.equal(m.actionKeyDigit(key('Digit1', { shiftKey: true })), 0)
  assert.equal(m.actionKeyDigit(key('Digit1', { repeat: true })), 0)
  assert.equal(m.actionKeyDigit(key('Digit1', { isComposing: true })), 0)
  assert.equal(m.actionKeyDigit(key('Digit1', { key: 'Process' })), 0)
  assert.equal(m.actionKeyDigit(key('Digit1', { keyCode: 229 })), 0)
})

test('keyTargetAllowed: composer and non-editables yes; other editables, dialogs, terminals, editor, claimed keys no', () => {
  assert.equal(m.keyTargetAllowed(key('Digit1', { target: COMPOSER })), true)
  assert.equal(m.keyTargetAllowed(key('Digit1', { target: el('BUTTON') })), true)
  assert.equal(m.keyTargetAllowed(key('Digit1', { target: null })), true)
  assert.equal(m.keyTargetAllowed(key('Digit1', { target: el('INPUT') })), false)  // e.g. sidebar search
  assert.equal(m.keyTargetAllowed(key('Digit1', { target: el('TEXTAREA', ['[data-chat-surface]', '[data-slot="inbox-editor"]']) })), false)
  assert.equal(m.keyTargetAllowed(key('Digit1', { target: el('INPUT', ['[data-chat-surface]', '[role="dialog"]']) })), false)
  assert.equal(m.keyTargetAllowed(key('Digit1', { target: el('TEXTAREA', ['[data-chat-surface]', '[data-terminal]']) })), false)
  assert.equal(m.keyTargetAllowed(key('Digit1', { target: COMPOSER, defaultPrevented: true })), false)
})

test('keyActions / keyHint: bar order minus Archive; Archive keeps ⌘⇧A', () => {
  assert.deepEqual(m.keyActions(XPOST).map(a => a.id), ['approve', 'edit', 'skip', 'rewrite'])
  assert.deepEqual(m.keyActions({ session_id: 'old' }).map(a => a.id), ['do_it'])
  const acts = m.barActions(XPOST)
  assert.deepEqual(acts.map(a => m.keyHint(XPOST, a, true)), ['⌥1', '⌥2', '⌥3', '⌥4', '⌘⇧A'])
  assert.deepEqual(acts.map(a => m.keyHint(XPOST, a, false)), ['Alt+1', 'Alt+2', 'Alt+3', 'Alt+4', 'Ctrl+Shift+A'])
  const many = { actions: Array.from({ length: 11 }, (_, i) => ({ id: `a${i}`, kind: 'resolve' })) }
  assert.equal(m.keyActions(many).length, 9)
  assert.equal(m.keyHint(many, m.barActions(many)[9], true), '')
})

test('Bar buttons carry their key hint', () => {
  host.state.focusedStoredSessionId = { get: () => 'x1', listen: () => () => {} }
  const store = m.createStore(fakeCtx(() => ({ items: [] })))
  store.$items.set([XPOST])
  const btns = find(m.Bar({ store }), n => n.props && n.props.action)
  assert.deepEqual(btns.map(n => n.props.hint.replace('Alt+', '⌥').replace('Ctrl+Shift+A', '⌘⇧A')), ['⌥1', '⌥2', '⌥3', '⌥4', '⌘⇧A'])
})

function keyStore(focused = 'x1', answers = () => ({ ok: true, items: [] })) {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  host.state.focusedStoredSessionId = { get: () => focused, listen: () => () => {} }
  const ctx = fakeCtx(answers)
  const store = m.createStore(ctx)
  store.$items.set([XPOST, { session_id: 'n2', gateway: 'local' }])
  return { ctx, store }
}

test('⌥1 in the composer on an open card runs the first button and swallows the key (no ¡ typed)', async () => {
  host.openSession = async () => {}
  const { ctx, store } = keyStore()
  const ev = key('Digit1', { target: COMPOSER })
  assert.equal(store.onKeyDown(ev), true)
  assert.equal(ev.prevented, 1)
  await new Promise(r => setTimeout(r, 0))
  assert.deepEqual(ctx.calls[0], ['/items/x1/act', { method: 'POST', body: { action: 'approve' } }])
})

test('⌥3 runs the third button; ⌥2 on an input action opens the editor instead of posting', () => {
  const { ctx, store } = keyStore()
  assert.equal(store.onKeyDown(key('Digit2', { target: COMPOSER })), true)
  assert.equal(ctx.calls.length, 0)
  assert.equal(store.$editing.get().action.id, 'edit')
  assert.equal(store.$editing.get().value, 'Cells are agents.')
})

test('action keys are off while the editor is open (swallowed, nothing runs) and off a non-inbox chat', () => {
  const { ctx, store } = keyStore()
  store.$editing.set({ sid: 'x1', action: XPOST.actions[1], value: 'x' })
  const ev = key('Digit1', { target: COMPOSER })
  assert.equal(store.onKeyDown(ev), false)
  assert.equal(ev.prevented, 1)  // swallowed, so no ¡ lands in the composer either
  assert.equal(ctx.calls.length, 0)
  const other = keyStore('not-inbox')
  const ev2 = key('Digit1', { target: COMPOSER })
  assert.equal(other.store.onKeyDown(ev2), false)
  assert.equal(ev2.prevented, 0)  // ⌥1 still types its character elsewhere
  assert.equal(other.ctx.calls.length, 0)
})

test('action keys ignore IME composition, other editables and digits with no button', () => {
  const { ctx, store } = keyStore()
  for (const ev of [
    key('Digit1', { target: COMPOSER, isComposing: true }),
    key('Digit1', { target: el('INPUT') }),
    key('Digit5', { target: COMPOSER }),  // XPOST has 4 keyed buttons
    key('Digit1', { target: COMPOSER, altKey: false, key: '1' })
  ]) {
    assert.equal(store.onKeyDown(ev), false)
    assert.equal(ev.prevented, 0)
  }
  assert.equal(ctx.calls.length, 0)
  assert.equal(store.$editing.get(), null)
})

test('action keys are swallowed and run nothing while the card is busy', () => {
  const { ctx, store } = keyStore()
  store.$busy.set('x1')
  const ev = key('Digit1', { target: COMPOSER })
  assert.equal(store.onKeyDown(ev), false)
  assert.equal(ev.prevented, 1)
  assert.equal(ctx.calls.length, 0)
})

test('non-US layouts: ⌥digit that types another character ([ @ ~ …) in the composer is left alone', () => {
  const { ctx, store } = keyStore()
  for (const k of ['[', '@', '~', '{']) {
    const ev = key('Digit1', { target: COMPOSER, key: k })
    assert.equal(store.onKeyDown(ev), false)
    assert.equal(ev.prevented, 0)
  }
  assert.equal(m.keyCharAllowed(key('Digit1', { target: COMPOSER, key: '1' }), 1), true)  // Alt+1 off macOS
  assert.equal(m.keyCharAllowed(key('Digit5', { target: COMPOSER }), 5), true)  // US ⌥5 = ∞
  assert.equal(m.keyCharAllowed(key('Digit5', { target: el('BUTTON'), key: '[' }), 5), true)  // not typing anywhere
  assert.equal(ctx.calls.length, 0)
})

test('action keys need the card bar on screen (not on Settings with the card still focused)', () => {
  const { ctx, store } = keyStore()
  globalThis.document = { querySelector: () => null }
  try {
    const ev = key('Digit1', { target: null })
    assert.equal(store.onKeyDown(ev), false)
    assert.equal(ev.prevented, 0)
    globalThis.document = { querySelector: sel => (sel === '[data-slot="inbox-bar"]' ? {} : null) }
    assert.equal(store.onKeyDown(key('Digit1', { target: null })), true)
  } finally {
    delete globalThis.document
  }
  assert.equal(ctx.calls.length, 1)  // only the key with the bar on screen ran
})

test('a reload without onDispose (older core) replaces the keydown listener instead of stacking it', () => {
  const live = new Set()
  globalThis.window = { addEventListener: (t, fn) => live.add(fn), removeEventListener: (t, fn) => live.delete(fn) }
  try {
    host.state.focusedStoredSessionId = { get: () => null, listen: () => () => {} }
    const ctx = { storage: { get: (k, f) => f, set() {} }, rest: async () => ({ items: [] }), setInterval() {}, register() {} }
    m.default.register(ctx)
    m.default.register(ctx)
    assert.equal(live.size, 1)
  } finally {
    delete globalThis.window
  }
})

test('aria-keyshortcuts uses ARIA key names', () => {
  assert.equal(m.hintAria('⌥3'), 'Alt+3')
  assert.equal(m.hintAria('⌘⇧A'), 'Meta+Shift+A')
  assert.equal(m.hintAria('Ctrl+Shift+A'), 'Control+Shift+A')
})

test('a card closed after the user moved to another chat does not navigate', async () => {
  let focused = 'x1'
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  host.state.focusedStoredSessionId = { get: () => focused, listen: () => () => {} }
  const opened = []
  host.openSession = async sid => { opened.push(sid) }
  const ctx = fakeCtx(path => { if (path.includes('/act')) focused = 'elsewhere'; return { state: 'resolved', items: [] } })
  ctx.openNextSession = () => assert.fail('moved on')
  ctx.runAction = () => assert.fail('moved on')
  const store = m.createStore(ctx)
  store.$items.set([XPOST, { session_id: 'n2', gateway: 'local' }])
  assert.equal(await store.act(XPOST, XPOST.actions[0]), true)
  assert.deepEqual(opened, [])
})

test('register installs the keydown listener in capture phase and removes it on dispose', () => {
  const added = []
  const removed = []
  const disposers = []
  globalThis.window = { addEventListener: (...a) => added.push(a), removeEventListener: (...a) => removed.push(a) }
  try {
    host.state.focusedStoredSessionId = { get: () => null, listen: () => () => {} }
    m.default.register({ storage: { get: (k, f) => f, set() {} }, rest: async () => ({ items: [] }), setInterval() {},
      register() {}, onDispose: fn => disposers.push(fn) })
    assert.equal(added.length, 1)
    assert.equal(added[0][0], 'keydown')
    assert.equal(added[0][2], true)
    disposers.forEach(fn => fn())
    assert.deepEqual(removed, [added[0]])
  } finally {
    delete globalThis.window
  }
})

test('last card: resolve steps to the session core ⌘⇧A would open (ctx.openNextSession)', async () => {
  const { ctx, store } = keyStore()
  store.$items.set([XPOST])
  const stepped = []
  ctx.openNextSession = sid => (stepped.push(sid), true)
  ctx.runAction = () => assert.fail('no fallback when core stepped')
  assert.equal(await store.act(XPOST, XPOST.actions[0]), true)
  assert.deepEqual(stepped, ['x1'])
})

test('last card: task and archive step too; no next session or an older core opens a fresh draft', async () => {
  const { ctx, store } = keyStore()
  store.$items.set([TASK_ITEM])
  host.state.focusedStoredSessionId = { get: () => 't1', listen: () => () => {} }
  const ran = []
  ctx.openNextSession = () => false
  ctx.runAction = id => ran.push(id)
  assert.equal(await store.act(TASK_ITEM, TASK_ITEM.actions[1]), true)
  assert.deepEqual(ran, ['session.new'])
  const older = keyStore()
  older.store.$items.set([XPOST])
  const ran2 = []
  older.ctx.runAction = id => ran2.push(id)
  assert.equal(await older.store.act(XPOST, { id: 'archive', kind: 'archive' }), true)
  assert.deepEqual(ran2, ['session.new'])
})

test('closing a card that is not the open chat never navigates; archive of the open card opens the next message', async () => {
  const opened = []
  host.openSession = async sid => { opened.push(sid) }
  const { ctx, store } = keyStore('elsewhere')
  ctx.openNextSession = () => assert.fail('not focused')
  ctx.runAction = () => assert.fail('not focused')
  assert.equal(await store.act(XPOST, XPOST.actions[0]), true)
  assert.deepEqual(opened, [])
  const b = keyStore('x1')
  b.ctx.openNextSession = () => assert.fail('a next message exists')
  assert.equal(await b.store.act(XPOST, { id: 'archive', kind: 'archive' }), true)
  assert.deepEqual(opened, ['n2'])
})

test('sidebar row Archive goes through act (steps when the row is the open chat)', async () => {
  const opened = []
  host.openSession = async sid => { opened.push(sid) }
  const { ctx, store } = keyStore('x1')
  const only = { session_id: 'n2', gateway: 'local' }
  store.$items.set([only])
  const archive = m.RowActions({ store, item: only })
  host.state.focusedStoredSessionId = { get: () => 'n2', listen: () => () => {} }
  const stepped = []
  ctx.openNextSession = sid => (stepped.push(sid), true)
  archive.props.onClick()
  await new Promise(r => setTimeout(r, 0))
  assert.equal(ctx.calls[0][0], '/items/n2/archive')
  assert.deepEqual(stepped, ['n2'])
})

// ── hermes-hub replica (hub-sync plugin): other gateways' open cards ─────────

const HUB_ROW = {
  gateway: 'nas', key: 'default/h1', version: 7, local: false, stale: false, synced_at: 100,
  session_id: 'h1', title: 'Remote card', state: 'open', profile: 'default', poster: 'cron', updated_at: 50,
  actions: [{ id: 'approve', label: 'Approve', kind: 'resolve', style: 'primary' }, { id: 'go', label: 'Go', kind: 'prompt' }],
  pending: []
}

test('hubInboxItems: only open cards of gateways not live, prompt actions dropped, routed by hub name', () => {
  const rows = [
    HUB_ROW,
    { ...HUB_ROW, gateway: 'mini', key: 'default/m1', session_id: 'm1' },
    { ...HUB_ROW, gateway: 'nas', key: 'default/c1', session_id: 'c1', state: 'resolved' },
    { ...HUB_ROW, gateway: 'laptop', key: 'default/l1', session_id: 'l1', local: true }
  ]
  const out = m.hubInboxItems(rows, new Set(['mini']), name => (name === 'nas' ? 'conn-nas' : null))
  assert.deepEqual(out.map(i => i.session_id), ['h1'])
  assert.equal(out[0].gateway, 'conn-nas')
  assert.deepEqual(out[0].hub, { gateway: 'nas', key: 'default/h1', version: 7, synced_at: 100 })
  assert.deepEqual(out[0].actions.map(a => a.id), ['approve'])
  assert.equal(m.hubInboxItems([{ ...HUB_ROW }], new Set(), () => null)[0].gateway, 'hub:nas')
  assert.deepEqual(m.hubInboxItems(null, new Set(), () => null), [])
})

test('refresh adds replica cards only for gateways whose live read failed', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [{ id: 'local', kind: 'local' }, { id: 'conn-mini', kind: 'ssh' }, { id: 'conn-nas', kind: 'ssh' }] }
  host.state.focusedStoredSessionId = { get: () => null, listen: () => () => {} }
  const names = { local: 'laptop', 'conn-mini': 'mini', 'conn-nas': 'nas' }
  const hubCalls = []
  const ctx = fakeCtx((path, opts) =>
    opts.connectionId === 'conn-nas' ? new Error('timed out') : { profile: 'default', items: [{ session_id: `live-${opts.connectionId || 'local'}`, updated_at: 1 }] })
  ctx.hubRest = async (path, opts = {}) => {
    hubCalls.push([path, opts])
    return { available: true, gateway: 'laptop', items: [HUB_ROW, { ...HUB_ROW, gateway: 'mini', key: 'default/m1', session_id: 'm1' }] }
  }
  ctx.hubGatewayName = cid => names[cid]
  const store = m.createStore(ctx)
  await store.refresh()
  assert.equal(hubCalls[0][0], '/inbox')
  const ids = store.$items.get().map(i => `${i.gateway}:${i.session_id}`).sort()
  assert.deepEqual(ids, ['conn-mini:live-conn-mini', 'conn-nas:h1', 'local:live-local'])
})

test('refresh without hub-sync (unavailable / error) keeps the live list', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  const ctx = fakeCtx(() => ({ items: [{ session_id: 'a', updated_at: 1 }] }))
  ctx.hubRest = async () => { throw new Error('404') }
  const store = m.createStore(ctx)
  await store.refresh()
  assert.deepEqual(store.$items.get().map(i => i.session_id), ['a'])
})

test('replica card: resolve and archive become hub requests with base_version and leave a pending marker', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  host.state.focusedStoredSessionId = { get: () => null, listen: () => () => {} }
  const ctx = fakeCtx(() => ({ items: [] }))
  const hubCalls = []
  ctx.hubRest = async (path, opts = {}) => {
    hubCalls.push([path, opts])
    return path === '/request' ? { id: 'hr-1', state: 'local', queued_locally: true } : { available: true, items: [] }
  }
  const store = m.createStore(ctx)
  const [item] = m.hubInboxItems([HUB_ROW], new Set(), () => 'conn-nas')
  store.$items.set([item])
  assert.equal(await store.act(item, item.actions[0]), true)
  assert.deepEqual(hubCalls[0], ['/request', { method: 'POST', body: { gateway: 'nas', coll: 'inbox', key: 'default/h1', action: 'act', args: { action: 'approve' }, base_version: 7 } }])
  assert.equal(ctx.calls.length, 0)  // nothing sent to the unreachable gateway
  assert.equal(m.isPending(store.$items.get()[0]), true)
  hubCalls.length = 0
  await store.act(store.$items.get()[0], { id: 'archive', kind: 'archive' })
  assert.deepEqual(hubCalls[0][1].body, { gateway: 'nas', coll: 'inbox', key: 'default/h1', action: 'archive', base_version: 7 })
  hubCalls.length = 0
  await store.act(item, { id: 'note', kind: 'resolve', input: true }, 'hi')
  assert.deepEqual(hubCalls[0][1].body.args, { action: 'note', input: 'hi' })
})

test('replica card row: greyed with the synced hint, pending shown once requested', () => {
  const [item] = m.hubInboxItems([HUB_ROW], new Set(), () => 'conn-nas')
  assert.match(m.rowMeta(item, 100_000 + 300_000), /synced 5m ago/)
  assert.equal(m.rowMeta({ ...item, pending: [{ state: 'local' }] }), 'pending')
  assert.equal(m.rowMeta({ updated_at: 0, poster: 'cron' }), 'cron')
})

test('archiveHotkey on a replica card queues a hub archive, never the unreachable gateway', async () => {
  host.activeConnectionId = () => 'local'
  host.state.gateways = { get: () => [] }
  const ctx = fakeCtx(() => ({ items: [] }))
  const hubCalls = []
  ctx.hubRest = async (path, opts = {}) => (hubCalls.push([path, opts]), { id: 'hr-2', state: 'queued' })
  const store = m.createStore(ctx)
  const [item] = m.hubInboxItems([HUB_ROW], new Set(), () => 'conn-nas')
  store.$items.set([item])
  assert.equal(store.archiveHotkey({ sessionId: 'h1', isFocused: false }), true)
  await new Promise(r => setTimeout(r, 0))
  assert.equal(hubCalls[0][1].body.action, 'archive')
  assert.equal(ctx.calls.length, 0)
})
