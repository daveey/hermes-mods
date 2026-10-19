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
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
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
