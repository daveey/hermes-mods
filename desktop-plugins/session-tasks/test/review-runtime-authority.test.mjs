// node --test: a stale runtime must not clear the current core work-source failure.
// Composes the published plugin with core's actual negative store (not a fabricated outcome).
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

if (!process.env.HERMES_CORE_SOURCE) {
  test('runtime authority integration requires an explicit core checkout', { skip: 'core-free public CI; exercised by companion publication against landed core' }, () => {})
} else {
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))

function memoryStorage() {
  const data = new Map()
  return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k), data }
}

globalThis.localStorage = memoryStorage()
const m = await import('../plugin.js')
const { host } = await import('@hermes/plugin-sdk')

const coreRoot = process.env.HERMES_CORE_SOURCE
const storePath = coreRoot && resolve(coreRoot, 'apps/desktop/src/store/session-work-source.ts')
if (!storePath || !existsSync(storePath)) {
  throw new Error('HERMES_CORE_SOURCE must point at core with apps/desktop/src/store/session-work-source.ts')
}
const store = await import(pathToFileURL(storePath).href)

let focus
beforeEach(() => {
  store.$unavailableWorkSources.set({})
  focus = { runtime: 'rt-A', stored: 'chat-stale', owner: { connectionId: 'conn', profile: 'default' } }
  host.state = {
    focusedSessionId: { get: () => focus.runtime },
    focusedStoredSessionId: { get: () => focus.stored },
    focusedSessionOwner: { get: () => focus.owner }
  }
  globalThis.document = { hidden: false, getElementById: () => ({ remove() {} }), addEventListener() {}, removeEventListener() {} }
})

const deferred = () => {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}

const owner = { connectionId: 'conn', profile: 'default', storedSessionId: 'chat-stale' }
const otherScope = { connectionId: 'other-gw', profile: 'other-profile', storedSessionId: 'other-chat' }

test('stale runtime A success does not clear the current core work-source failure after the durable chat moves to B', async () => {
  const aRpc = deferred()
  host.sessions = { reportWorkSourceRead: store.reportWorkSourceRead }
  host.request = async (_method, params) => {
    if (params?.session_id === 'rt-A') return aRpc.promise
    throw new Error(`unexpected runtime RPC ${params?.session_id}`)
  }
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/sessions/')) return { id: focus.stored, parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [] }
    return { machines: [] }
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    const roundA = m.refresh()
    await new Promise(r => setTimeout(r, 0))

    focus.runtime = 'rt-B'
    store.reportWorkSourceRead({ ...owner, source: 'process', available: false })
    assert.equal(store.workSourceUnavailable(owner.connectionId, owner.profile, [owner.storedSessionId]), true,
      'current process failure is established on the actual core store')

    store.reportWorkSourceRead({ ...otherScope, source: 'process', available: false })
    assert.equal(store.workSourceUnavailable(otherScope.connectionId, otherScope.profile, [otherScope.storedSessionId]), true)
    assert.equal(store.workSourceUnavailable('other-gw', owner.profile, [owner.storedSessionId]), false)
    assert.equal(store.workSourceUnavailable(owner.connectionId, 'other-profile', [owner.storedSessionId]), false)
    assert.equal(store.workSourceUnavailable(owner.connectionId, owner.profile, ['other-chat']), false)

    aRpc.resolve({ processes: [], subagents: [] })
    await roundA

    assert.equal(store.workSourceUnavailable(owner.connectionId, owner.profile, [owner.storedSessionId]), true,
      'stale runtime A success must not clear the current work-source failure')
    assert.equal(store.workSourceUnavailable(otherScope.connectionId, otherScope.profile, [otherScope.storedSessionId]), true,
      'qualified other scope stays isolated')
    assert.equal(store.workSourceUnavailable('other-gw', owner.profile, [owner.storedSessionId]), false)
    assert.equal(store.workSourceUnavailable(owner.connectionId, 'other-profile', [owner.storedSessionId]), false)
    assert.equal(store.workSourceUnavailable(owner.connectionId, owner.profile, ['other-chat']), false)
  } finally {
    dispose()
    delete host.sessions
    delete host.request
    delete globalThis.window
    store.$unavailableWorkSources.set({})
  }
})
}
