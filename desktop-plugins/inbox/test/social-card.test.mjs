// node --test: ::social-card (thread rendering, option selection, bar, keys, untrusted text).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,' + encodeURIComponent(
    'export const host = globalThis.__host = { state: {} };' +
    'export const atom = v => { let x = v; return { get: () => x, set: n => { x = n } } };' +
    'export const COMPOSER_AREAS = { top: "composer.top" }; export const SIDEBAR_SECTIONS_AREA = "sidebar.sections";' +
    'export const TRANSCRIPT_DIRECTIVE_AREA = "transcript.directives";' +
    'export const SidebarSection = () => null; export const SidebarSectionRow = () => null; export const useValue = a => a.get();'), shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useState=v=>(globalThis.__useState?globalThis.__useState(v):[v,()=>{}]);export const useEffect=(f)=>{globalThis.__effects&&globalThis.__effects.push(f)};export const jsx=(type,props,key)=>({type,props,key});export const jsxs=(type,props,key)=>({type,props,key})', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const host = globalThis.__host

// Expand function components into host elements; returns the tree.
function render(node) {
  if (node == null || node === false || typeof node === 'string' || typeof node === 'number') return node
  if (Array.isArray(node)) return node.map(render)
  if (typeof node.type === 'function') return render(node.type(node.props || {}))
  const kids = node.props && node.props.children
  return { type: node.type, props: { ...node.props, children: Array.isArray(kids) ? kids.map(render) : render(kids) } }
}
function all(tree, pred, out = []) {
  if (tree == null || typeof tree !== 'object') return out
  if (Array.isArray(tree)) { for (const t of tree) all(t, pred, out); return out }
  if (pred(tree)) out.push(tree)
  all(tree.props && tree.props.children, pred, out)
  return out
}
function text(tree) {
  if (tree == null || tree === false) return ''
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree)
  if (Array.isArray(tree)) return tree.map(text).join('')
  return text(tree.props && tree.props.children)
}
const slot = (tree, s) => all(tree, n => n.props && n.props['data-slot'] === s)

const THREAD = [
  { author: '@daveeyb.bsky.social', self: true, text: '1/ We wrote a map of AI alignment', url: 'https://bsky.app/1' },
  { author: '@r64n.bsky.social', text: 'Wild how many camps', url: 'https://bsky.app/2' },
  { author: '@daveeyb.bsky.social', self: true, text: 'Keeping bargaining viable', url: 'https://bsky.app/3' },
  { author: '@r64n.bsky.social', text: 'Reciprocal vulnerability', url: 'https://bsky.app/4' },
  { author: '@daveeyb.bsky.social', self: true, text: 'Toddler and love', url: 'https://bsky.app/5' },
  { author: '@r64n.bsky.social', text: 'A toddler has leverage', url: 'javascript:alert(1)', target: true }
]
const act = (id, label, style) => ({ id, label, kind: 'prompt', prompt: `${id} {{option}}`, ...(style ? { style } : {}) })
const incoming = () => ({
  session_id: 's1', type: 'bsky-incoming', type_label: 'Bluesky reply / mention', created_at: 0,
  field_specs: { option_1: { max: 300 }, option_2: { max: 300 }, option_3: { max: 300 } },
  fields: {
    text: 'A toddler has leverage', thread: JSON.stringify(THREAD), summary: 'r64n is pushing on leverage.',
    option_1: 'Fair, better way to put it.', option_2: 'Then the design question.', option_3: 'Would you call it leverage?',
    option_1_angle: 'Concede', option_2_angle: 'Design question', option_3_angle: 'Ask the line',
    grounding: 'Read the thread.', sources: JSON.stringify([{ label: 'Paper', url: 'https://example.com/p.pdf' }, { label: 'Bad', url: 'javascript:x' }])
  },
  actions: [act('choose_1', 'Reply with option 1', 'primary'), act('choose_2', 'Reply with option 2', 'primary'), act('choose_3', 'Reply with option 3', 'primary'),
    { id: 'edit', label: 'Edit & reply…', kind: 'prompt', input: { multiline: true }, prompt: '{{input}}' }, act('draft', 'Three new options'), { id: 'done', label: 'Skip', kind: 'resolve' }]
})

test('parseThread: valid turns, untrusted urls dropped, bad JSON is null', () => {
  const t = m.parseThread(incoming())
  assert.equal(t.length, 6)
  assert.equal(t[5].url, '')
  assert.equal(t[0].url, 'https://bsky.app/1')
  assert.equal(m.parseThread({ fields: { thread: 'nope' } }), null)
  assert.equal(m.parseThread({ fields: { thread: '[]' } }), null)
  assert.equal(m.parseThread({ fields: {} }), null)
})

test('turns render in order, target marked, earlier turns folded past 4', () => {
  const tree = render(m.SocialCardView({ item: incoming(), selected: {} }))
  const turns = slot(tree, 'social-turn')
  assert.equal(turns.length, 3)
  assert.match(text(turns[0]), /Reciprocal vulnerability/)
  assert.match(text(turns[2]), /A toddler has leverage/)
  assert.equal(turns[2].props['data-target'], 'true')
  assert.match(text(turns[2]), /replying to this/)
  assert.equal(text(slot(tree, 'social-fold')[0]), 'Show 3 earlier posts')
  // unfolded: all six, oldest first
  globalThis.__useState = () => [true, () => {}]
  try {
    const open = slot(render(m.SocialCardView({ item: incoming(), selected: {} })), 'social-turn')
    assert.equal(open.length, 6)
    assert.match(text(open[0]), /map of AI alignment/)
    assert.match(text(open[5]), /A toddler has leverage/)
  } finally {
    globalThis.__useState = undefined
  }
  assert.match(text(slot(tree, 'social-summary')[0]), /r64n is pushing on leverage/)
  const why = slot(tree, 'social-why')[0]
  assert.match(text(why), /Read the thread\.Paper/)
  const links = all(why, n => n.type === 'a')
  assert.deepEqual(links.map(a => a.props.href), ['https://example.com/p.pdf'])
})

test('short threads are not folded', () => {
  const it = incoming()
  it.fields.thread = JSON.stringify(THREAD.slice(-2))
  const tree = render(m.SocialCardView({ item: it, selected: {} }))
  assert.equal(slot(tree, 'social-fold').length, 0)
  assert.equal(slot(tree, 'social-turn').length, 2)
})

test('options: draft bubble follows selection; counts; single-option drafts show no picker', () => {
  const it = incoming()
  let tree = render(m.SocialCardView({ item: it, selected: {} }))
  assert.match(text(slot(tree, 'social-draft')[0]), /Fair, better way to put it\./)
  const opts = slot(tree, 'social-option')
  assert.equal(opts.length, 3)
  assert.equal(opts[0].props['aria-pressed'], 'true')
  assert.match(text(opts[1]), /Design question.*Then the design question\..*25\/300/)
  let picked = 0
  render(m.SocialCardView({ item: it, selected: {}, onSelect: n => { picked = n } }))
  slot(render(m.SocialCardView({ item: it, selected: {}, onSelect: n => { picked = n } })), 'social-option')[1].props.onClick()
  assert.equal(picked, 2)
  tree = render(m.SocialCardView({ item: it, selected: { s1: 2 } }))
  assert.match(text(slot(tree, 'social-draft')[0]), /Then the design question\./)
  assert.equal(slot(tree, 'social-option')[1].props['aria-pressed'], 'true')
  const draft = { session_id: 'd', type: 'bsky-reply-draft', fields: { text: 'One draft', thread: JSON.stringify(THREAD.slice(-1)) }, actions: [act('post', 'Post', 'primary')] }
  const dt = render(m.SocialCardView({ item: draft, selected: {} }))
  assert.equal(slot(dt, 'social-options').length, 0)
  assert.match(text(slot(dt, 'social-draft')[0]), /One draft/)
})

test('socialOptions: x-reply-draft uses text as option 1; incoming text is never an option', () => {
  const x = { type: 'x-reply-draft', fields: { text: 'A', option_2: 'B', option_3: 'C' }, actions: [act('post', 'Post option 1', 'primary'), act('post_2', 'Post option 2'), act('post_3', 'Post option 3')] }
  assert.deepEqual(m.socialOptions(x).map(o => [o.n, o.text, o.action.id]), [[1, 'A', 'post'], [2, 'B', 'post_2'], [3, 'C', 'post_3']])
  const held = { type: 'bsky-incoming', fields: { text: 'their post' }, actions: [] }
  assert.deepEqual(m.socialOptions(held), [])
})

test('bar: option buttons collapse into the selected one, which runs that option', () => {
  const it = incoming()
  assert.deepEqual(m.socialBarActions(it, {}).map(a => a.id), ['choose_1', 'edit', 'draft', 'done', 'archive'])
  const bar = m.socialBarActions(it, { s1: 2 })
  assert.deepEqual(bar.map(a => [a.id, a.label, a.style]).slice(0, 1), [['choose_2', 'Reply with option 2', 'primary']])
  assert.deepEqual(m.keyActions(it, { s1: 3 }).map(a => a.id), ['choose_3', 'edit', 'draft', 'done'])
  assert.equal(m.keyHint(it, bar[0], true, { s1: 2 }), '⌥1')
  // single-option / non-social cards keep their bar
  const plain = { actions: [act('post', 'Post', 'primary'), { id: 'skip', label: 'Skip', kind: 'resolve' }] }
  assert.deepEqual(m.socialBarActions(plain, {}).map(a => a.id), ['post', 'skip', 'archive'])
})

test('Edit & post opens with the selected option', () => {
  const it = incoming()
  const edit = it.actions.find(a => a.id === 'edit')
  assert.equal(m.inputDefault(it, edit, { s1: 3 }), 'Would you call it leverage?')
  assert.equal(m.inputDefault(it, edit, {}), 'Fair, better way to put it.')
  assert.equal(m.inputDefault({ fields: { text: 'x' } }, { input: { field: 'text' } }, {}), 'x')
})

function storeWith(items, posts) {
  const rest = async (path, opts) => {
    posts.push({ path, body: opts && opts.body })
    if (path.endsWith('/act')) return { prompt: `PROMPT:${opts.body.action}` }
    return { items }
  }
  host.state = { focusedStoredSessionId: { get: () => 's1' } }
  host.composer = { submit: (sid, p) => { posts.push({ submitted: p }); return true } }
  globalThis.confirm = () => true
  return m.createStore({ rest, storage: null })
}

test('store: digit 2 selects, then the primary bar button runs option 2 exactly', async () => {
  const posts = []
  const it = incoming()
  const store = storeWith([it], posts)
  store.$items.set([it])
  const ev = (o) => ({ code: 'Digit2', key: '2', target: { tagName: 'DIV' }, preventDefault() { this.prevented = true }, ...o })
  const e = ev()
  assert.equal(store.onKeyDown(e), true)
  assert.equal(e.prevented, true)
  assert.deepEqual(store.$selected.get(), { s1: 2 })
  // typing a digit into the composer never selects
  assert.equal(store.onKeyDown(ev({ code: 'Digit3', key: '3', target: { tagName: 'TEXTAREA', closest: () => ({}) } })), false)
  assert.deepEqual(store.$selected.get(), { s1: 2 })
  const primary = m.socialBarActions(it, store.$selected.get())[0]
  await store.act(it, primary)
  const actPost = posts.find(p => p.path && p.path.endsWith('/act'))
  assert.deepEqual(actPost.body, { action: 'choose_2' })
  assert.ok(posts.some(p => p.submitted === 'PROMPT:choose_2'))
})

test('store: click selection is per card and Edit opens prefilled', () => {
  const posts = []
  const it = incoming()
  const store = storeWith([it], posts)
  store.$items.set([it])
  store.select('s1', 3)
  store.run(it, it.actions.find(a => a.id === 'edit'))
  assert.equal(store.$editing.get().value, 'Would you call it leverage?')
})

test('untrusted text renders as plain text, never markup or directives', () => {
  const it = incoming()
  const evil = '<img src=x onerror=alert(1)> **bold** ::preview{file="/etc/passwd"} [x](javascript:alert(1))'
  it.fields.option_1 = evil
  it.fields.thread = JSON.stringify([{ author: '<b>@x</b>', text: evil, target: true }])
  const tree = render(m.SocialCardView({ item: it, selected: {} }))
  const bodies = slot(tree, 'social-text')
  assert.ok(bodies.length >= 2)
  for (const b of bodies) assert.equal(b.props.children, evil)  // a single string child: React escapes it
  assert.equal(all(tree, n => n.type === 'img' || n.props.dangerouslySetInnerHTML).length, 0)
})

test('SocialCard: falls back to fetched copy for a non-open card', async () => {
  const it = incoming()
  const calls = []
  host.state = { focusedStoredSessionId: { get: () => null }, gateways: { get: () => [] } }
  host.activeConnectionId = () => 'local'
  const store = m.createStore({ rest: async (path) => { calls.push(path); if (path === '/items/s1') return { ...it, state: 'accepted' }; return { items: [] } } })
  globalThis.__effects = []
  let tree = render(m.SocialCard({ store, attrs: {}, sessionId: 's1' }))
  assert.equal(slot(tree, 'social-card-missing').length, 1)
  for (const f of globalThis.__effects) f()
  await new Promise(r => setTimeout(r, 0))
  await new Promise(r => setTimeout(r, 0))
  assert.ok(calls.includes('/items/s1'))
  tree = render(m.SocialCard({ store, attrs: { session: 's1' }, sessionId: null }))
  assert.equal(slot(tree, 'social-card').length, 1)
  globalThis.__effects = undefined
})

test('plugin registers the social-card directive', () => {
  const regs = []
  host.state = { focusedStoredSessionId: { get: () => null, listen: () => () => {} }, gateways: { get: () => [] } }
  m.default.register({ register: r => regs.push(r), rest: async () => ({ items: [] }), setInterval: () => {}, onDispose: () => {} })
  const d = regs.find(r => r.area === 'transcript.directives')
  assert.equal(d.data.name, 'social-card')
})
