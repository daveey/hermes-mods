// Regression: every `sessions.changed` (every ~1-2 s while any turn streams) forced a full session
// re-read of the active connection 400 ms later. On the primary connection Desktop main fans that
// read out to every connected gateway (up to 5 pages each): a 40 s audit on a 20-gateway Desktop saw
// 43 forced reads -> ~1,500 backend /api/sessions requests. Fix: a change only marks rows pending;
// forced refreshes are at least CHANGE_REFRESH_MIN_MS apart, and typing @ (provide) refreshes a
// pending change at once so the list is still fresh when it is used.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')

const conns = [{ id: 'local', label: 'This device', kind: 'local', primary: true }]
const state = { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function boot() {
  const handlers = {}
  let sessionReads = 0
  globalThis.__smHost = {
    state,
    connections: async () => conns,
    onEvent: (name, fn) => ((handlers[name] = fn), () => delete handlers[name])
  }
  globalThis.window = {
    hermesDesktop: {
      api: async req => {
        if (req.path.startsWith('/api/profiles/sessions')) {
          sessionReads++
          return { sessions: [], total: 0 }
        }
        if (req.path.startsWith('/api/plugins/kanban/boards')) return { boards: [] }
        throw new Error('unrouted ' + req.path)
      }
    }
  }
  const m = new Map()
  globalThis.localStorage = { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k) }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = { middleware: "mw", atCompletions: "at" }')
  const mod = await import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
  const regs = []
  const disposers = []
  mod.default.register({ register: c => regs.push(c), onDispose: fn => disposers.push(fn), setInterval: () => 0 })
  const sessions = regs.find(r => r.id === 'session-mention-completions').data
  return { mod, sessions, handlers, reads: () => sessionReads, dispose: () => disposers.forEach(fn => fn()) }
}

test('a stream of sessions.changed events forces at most one session re-read per window', async () => {
  const { handlers, reads, dispose } = await boot()
  await sleep(50) // register()'s initial stale fetch lands
  const baseline = reads()
  // One event every 450 ms for ~2.7 s: the old 400 ms debounce re-read after each of them.
  for (let i = 0; i < 6; i++) {
    handlers['sessions.changed']()
    await sleep(450)
  }
  const forced = reads() - baseline
  assert.ok(forced <= 1, `expected at most 1 forced re-read in the window, got ${forced}`)
  dispose()
})

test('typing @ after a pending change refreshes at once', async () => {
  const { sessions, handlers, reads, dispose } = await boot()
  await sleep(50)
  handlers['sessions.changed']()
  await sleep(450) // first change refreshes after the debounce
  const afterFirst = reads()
  handlers['sessions.changed']() // now throttled: no read for CHANGE_REFRESH_MIN_MS
  await sleep(450)
  assert.equal(reads(), afterFirst, 'a throttled change must not re-read on its own yet')
  sessions.provide('the')
  await sleep(30)
  assert.equal(reads(), afterFirst + 1, 'provide() must refresh the pending change immediately')
  sessions.provide('them')
  await sleep(30)
  assert.equal(reads(), afterFirst + 1, 'no pending change: further keystrokes must not re-read')
  dispose()
})

test('changeRefreshDelay keeps the debounce when idle and stretches it inside the window', async () => {
  const { mod } = await boot()
  const { changeRefreshDelay, CHANGE_REFRESH_MIN_MS, CHANGE_DEBOUNCE_MS } = mod
  assert.equal(changeRefreshDelay(1_000_000, 0), CHANGE_DEBOUNCE_MS)
  assert.equal(changeRefreshDelay(1_000_000, 1_000_000 - 5_000), CHANGE_REFRESH_MIN_MS - 5_000)
  assert.equal(changeRefreshDelay(1_000_000, 1_000_000 - CHANGE_REFRESH_MIN_MS - 1), CHANGE_DEBOUNCE_MS)
})
