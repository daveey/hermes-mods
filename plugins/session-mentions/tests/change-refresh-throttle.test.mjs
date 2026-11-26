// Regression: `sessions.changed` re-read the whole session list of the active connection on a timer.
// First every 400 ms (a 40 s audit on a 20-gateway Desktop saw ~1,500 backend list requests), then, after
// the first fix, once per 30 s. On a gateway where some session changes every second that 30 s cap is a
// poll: the hub's request stats showed 74% of its `GET /api/profiles/sessions` traffic was this plugin
// re-reading 433 rows (one 500-row request, or five paged ones through a registry connection) with nobody
// typing `@`. Fix: a change only marks the rows pending. Typing `@` (provide) refreshes a pending change,
// at most once per TYPING_REFRESH_MIN_MS, and rows older than a minute refresh on provide() even when the
// change event was lost.
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

/** Run `fn` with Date.now() moved `ms` ahead: the plugin sees a later wall clock. */
async function later(ms, fn) {
  const real = Date.now
  Date.now = () => real() + ms
  try {
    return await fn()
  } finally {
    Date.now = real
  }
}

test('sessions.changed events alone never re-read the session list', async () => {
  const { handlers, reads, dispose } = await boot()
  await sleep(50) // register()'s initial stale fetch lands
  const baseline = reads()
  // One event every 450 ms for ~2.7 s: the 400 ms debounce re-read after the first of them.
  for (let i = 0; i < 6; i++) {
    handlers['sessions.changed']()
    await sleep(450)
  }
  assert.equal(reads() - baseline, 0, 'a change with nobody typing @ must not read the list')
  dispose()
})

test('typing @ after a change refreshes at once, and only once per change', async () => {
  const { sessions, handlers, reads, dispose } = await boot()
  await sleep(50)
  const baseline = reads()
  handlers['sessions.changed']()
  sessions.provide('the')
  await sleep(30)
  assert.equal(reads(), baseline + 1, 'provide() must refresh the pending change immediately')
  sessions.provide('them')
  await sleep(30)
  assert.equal(reads(), baseline + 1, 'no pending change: further keystrokes must not re-read')
  dispose()
})

test('changes that keep arriving while typing refresh at most once per TYPING_REFRESH_MIN_MS', async () => {
  const { mod, sessions, handlers, reads, dispose } = await boot()
  await sleep(50)
  const baseline = reads()
  handlers['sessions.changed']()
  sessions.provide('t')
  await sleep(30)
  for (const q of ['th', 'the', 'them']) {
    handlers['sessions.changed']()
    sessions.provide(q)
    await sleep(30)
  }
  assert.equal(reads(), baseline + 1, 'keystrokes inside the window share one refresh')
  assert.ok(mod.TYPING_REFRESH_MIN_MS >= 1_000 && mod.TYPING_REFRESH_MIN_MS < 60_000)
  await later(mod.TYPING_REFRESH_MIN_MS + 1, async () => {
    sessions.provide('theme')
    await sleep(30)
  })
  assert.equal(reads(), baseline + 2, 'the change left pending is refreshed by the first keystroke after the window')
  dispose()
})

test('a lost sessions.changed is covered: provide() re-reads rows older than a minute', async () => {
  const { sessions, reads, dispose } = await boot()
  await sleep(50)
  const baseline = reads()
  sessions.provide('the')
  await sleep(30)
  assert.equal(reads(), baseline, 'fresh rows and no change: no read')
  await later(61_000, async () => {
    sessions.provide('the')
    await sleep(30)
  })
  assert.equal(reads(), baseline + 1, 'stale rows must be re-read with no change event at all')
  dispose()
})

test('a pending change arms no timer: nothing reads after dispose()', async () => {
  const { handlers, reads, dispose } = await boot()
  await sleep(50)
  const baseline = reads()
  handlers['sessions.changed']()
  dispose()
  await sleep(500)
  assert.equal(reads(), baseline)
})
