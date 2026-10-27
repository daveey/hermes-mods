// Regression: `@the` did not offer the session "the runners pannel seems broken…" although it
// existed on this Mac and scored highest. provide() answers synchronously from an in-memory cache
// that register() empties, the fetch it kicks off never redrew the open popover, and a failed
// fetch was silent. Fix: provide() is paired with subscribe() (core bumps the popover's epoch),
// cold gateways show a "Loading…" row, failures show a row, and rows warm-start from localStorage.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
Error.stackTraceLimit = 1

const conns = [{ id: 'local', label: 'This device', kind: 'local', primary: true }]
const state = { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }
const runners = {
  id: '20261025_233046_d48ade',
  title: 'the runners pannel seems broken, not showing…',
  preview: 'x'.repeat(500),
  profile: 'default',
  source: 'desktop',
  last_active: 1_761_000_000,
  message_count: 68,
  system_prompt: 'large field that must not be persisted'
}
const sleep = ms => new Promise(r => setTimeout(r, ms))

function memoryStorage() {
  const m = new Map()
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k), _m: m }
}

/** Fresh module instance + register(); `routes(path)` answers the Desktop bridge. */
async function boot(routes, storage = memoryStorage()) {
  globalThis.__smHost = { state, connections: async () => conns, onEvent: () => () => {} }
  globalThis.window = { hermesDesktop: { api: async req => routes(req.path) } }
  globalThis.localStorage = storage
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = { middleware: "mw", atCompletions: "at" }')
  const m = await import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
  const regs = []
  const disposers = []
  m.default.register({ register: c => regs.push(c), onDispose: fn => disposers.push(fn), setInterval: () => 0 })
  const sessions = regs.find(r => r.id === 'session-mention-completions').data
  const tasks = regs.find(r => r.id === 'task-mention-completions').data
  return { m, sessions, tasks, storage, dispose: () => disposers.forEach(fn => fn()) }
}

const labels = items => items.map(i => i.display)
const deferred = () => {
  let resolve, reject
  const promise = new Promise((a, b) => ((resolve = a), (reject = b)))
  return { promise, resolve, reject }
}

test('cold cache: provide() shows Loading, then subscribe fires and the session appears', async () => {
  const gate = deferred()
  const { sessions, dispose } = await boot(path => {
    if (path.startsWith('/api/profiles/sessions')) return gate.promise
    if (path.startsWith('/api/plugins/kanban/boards')) return { boards: [] }
    return Promise.reject(new Error('unrouted ' + path))
  })
  let signals = 0
  const off = sessions.subscribe(() => signals++)

  await sleep(50)
  const before = labels(sessions.provide('the'))
  assert.ok(!before.some(l => l.startsWith('the runners')), 'nothing cached yet')
  assert.ok(before.includes('Loading sessions from This device…'), `loading row missing: ${before}`)

  const signalsBefore = signals
  gate.resolve({ sessions: [runners], total: 1 })
  await sleep(80)
  assert.ok(signals > signalsBefore, 'the landed fetch must signal subscribers')
  const after = labels(sessions.provide('the'))
  assert.equal(after[0], runners.title)
  assert.ok(!after.some(l => l.startsWith('Loading')), `loading row should be gone: ${after}`)

  off()
  dispose()
})

test('a failed session fetch is shown in the popover, not swallowed', async () => {
  const { sessions, dispose } = await boot(path => {
    if (path.startsWith('/api/profiles/sessions')) return Promise.reject(new Error('HTTP 502 from gateway'))
    if (path.startsWith('/api/plugins/kanban/boards')) return { boards: [] }
    return Promise.reject(new Error('unrouted ' + path))
  })
  let signals = 0
  sessions.subscribe(() => signals++)
  await sleep(80)
  assert.ok(signals > 0, 'the failure must signal subscribers')
  const items = sessions.provide('the')
  const row = items.find(i => i.display === "Couldn't load sessions from This device")
  assert.ok(row, `error row missing: ${labels(items)}`)
  assert.match(row.meta, /HTTP 502/)
  assert.equal(row.insert, '@session:')
  dispose()
})

test('a failed task fetch is shown in the popover', async () => {
  const { tasks, dispose } = await boot(path => {
    if (path.startsWith('/api/profiles/sessions')) return { sessions: [], total: 0 }
    if (path.startsWith('/api/plugins/kanban/boards')) return Promise.reject(new Error('kanban plugin not loaded'))
    return Promise.reject(new Error('unrouted ' + path))
  })
  await sleep(80)
  const row = tasks.provide('runners').find(i => i.display === "Couldn't load tasks from This device")
  assert.ok(row, 'task error row missing')
  assert.match(row.meta, /kanban plugin not loaded/)
  dispose()
})

test('warm start: rows saved by one run answer the first keystroke of the next, slimmed', async () => {
  const storage = memoryStorage()
  const first = await boot(path => {
    if (path.startsWith('/api/profiles/sessions')) return { sessions: [runners], total: 1 }
    if (path.startsWith('/api/plugins/kanban/boards')) return { boards: [] }
    return Promise.reject(new Error('unrouted ' + path))
  }, storage)
  await sleep(1_200) // the scheduled save (WARM_SAVE_MS) after the fetch lands
  first.dispose()
  const saved = JSON.parse(storage.getItem('hermes.session-mentions.warm.v1'))
  const row = saved.sessions.local[0]
  assert.equal(row.id, runners.id)
  assert.equal(row.system_prompt, undefined, 'only typeahead fields are persisted')
  assert.equal(row.preview.length, 120)

  // Next launch: the gateway never answers, yet the session is offered at once.
  const second = await boot(() => new Promise(() => {}), storage)
  const items = second.sessions.provide('the')
  assert.equal(items[0].display, runners.title)
  assert.ok(!labels(items).some(l => l.startsWith('Loading')), 'warm rows suppress the loading row')
  second.dispose()
})

test('corrupt or foreign warm data is ignored', async () => {
  const storage = memoryStorage()
  storage.setItem('hermes.session-mentions.warm.v1', '{not json')
  const { m } = await boot(() => new Promise(() => {}), storage)
  assert.equal(m.loadWarm(storage), 0)
  storage.setItem('hermes.session-mentions.warm.v1', JSON.stringify({ v: 99, sessions: { local: [runners] } }))
  assert.equal(m.loadWarm(storage), 0)
})
