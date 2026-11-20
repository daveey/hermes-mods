import { test, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

if (!process.env.HERMES_CORE_SOURCE) {
  test('captured authority integration requires an explicit core checkout', { skip: 'core-free public CI; exercised by companion publication against landed core' }, () => {})
} else {
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
const data = new Map()
globalThis.localStorage = { getItem: k => data.get(k) ?? null, setItem: (k,v) => data.set(k,String(v)), removeItem: k => data.delete(k) }
const plugin = await import('../plugin.js')
const { host } = await import('@hermes/plugin-sdk')
const store = await import(pathToFileURL(resolve(process.env.HERMES_CORE_SOURCE, 'apps/desktop/src/store/session-work-source.ts')).href)
function atom(value) {
  const listeners = new Set()
  return { get: () => value, set: next => { value = next; for (const cb of listeners) cb(next) },
    listen: cb => { listeners.add(cb); return () => listeners.delete(cb) } }
}
let dispose, pending, captured, reports
let sequence = 0
const scope = { connectionId: 'conn', profile: 'default', storedSessionId: 'captured-chat' }
const authority = { durable: 101, runtime: 202 }
beforeEach(() => {
  store.$unavailableWorkSources.set({})
  scope.storedSessionId = `captured-chat-${++sequence}`
  pending = []; captured = []; reports = []
  host.state = { focusedSessionId: atom('rt-A'), focusedStoredSessionId: atom(scope.storedSessionId),
    focusedSessionOwner: atom({ connectionId: scope.connectionId, profile: scope.profile }) }
  host.sessions = {
    beginWorkSourceRead: read => {
      const token = store.beginWorkSourceRead({ ...read, authorityEpoch: ['lineage','tasks'].includes(read.source) ? authority.durable : authority.runtime })
      captured.push(token)
      return token
    },
    reportWorkSourceRead: read => { reports.push(read); store.reportWorkSourceRead(read) }
  }
  host.request = method => new Promise(resolve => pending.push({ method, resolve }))
  globalThis.document = { hidden: false, getElementById: () => ({ remove() {} }), addEventListener() {}, removeEventListener() {} }
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/sessions/')) return { id: scope.storedSessionId, parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: [] }
    return { machines: [] }
  } } }
  dispose = plugin.default.register({ register() {} })
})
afterEach(() => { dispose(); delete globalThis.window; delete host.sessions; delete host.request; store.$unavailableWorkSources.set({}) })
const settle = () => { for (const read of pending.splice(0)) read.resolve(read.method === 'process.list' ? { processes: [] } : { subagents: [] }) }
const turn = () => new Promise(resolve => setImmediate(resolve))
const complete = () => store.workSourceAuthorityComplete(scope.connectionId, scope.profile, [scope.storedSessionId], authority)
function controlSuccess() {
  const token = store.beginWorkSourceRead({ ...scope, source: 'control', authorityEpoch: authority.runtime })
  store.reportWorkSourceRead({ ...token, available: true })
}
test('captures all dispatched plugin reads, keeps pending unknown, and composes a genuine five-source recovery', async () => {
  controlSuccess()
  const round = plugin.refresh(); await turn()
  assert.equal(complete(), false, 'pending runtime reads cannot certify complete')
  assert.deepEqual(new Set(captured.map(r => r.source)), new Set(['process','subagent','lineage','tasks']))
  assert.ok(captured.every(r => Number.isSafeInteger(r.readVersion) && r.available === false))
  settle(); await round
  assert.equal(complete(), true)
  for (const report of reports) {
    assert.ok(captured.some(token => token.source === report.source && token.readVersion === report.readVersion && token.authorityEpoch === report.authorityEpoch), 'settlement returns the exact captured authority')
  }
})
for (const transition of ['runtime', 'focus', 'owner']) {
  test(`${transition} ABA drops old successes, then fresh same-authority reads recover`, async () => {
    controlSuccess()
    const round = plugin.refresh(); await turn()
    const atom = transition === 'runtime' ? host.state.focusedSessionId : transition === 'focus' ? host.state.focusedStoredSessionId : host.state.focusedSessionOwner
    const original = atom.get()
    atom.set(transition === 'owner' ? { connectionId: 'other', profile: 'default' } : 'other')
    atom.set(original)
    settle(); await round
    assert.equal(reports.filter(r => ['process','subagent'].includes(r.source) && r.available).length, 0, 'old runtime read cannot publish success through ABA')
    assert.equal(complete(), false)
    const current = plugin.refresh(); await turn(); settle(); await current
    assert.equal(complete(), true, 'new reads under the restored authority recover')
  })
}
test('equal-valued owner publications do not invalidate current reads', async () => {
  controlSuccess()
  const round = plugin.refresh(); await turn()
  host.state.focusedSessionOwner.set({ connectionId: 'conn', profile: 'default' })
  settle(); await round
  assert.equal(complete(), true)
})
}
