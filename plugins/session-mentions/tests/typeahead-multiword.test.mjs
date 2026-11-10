// Regression: the `@` session typeahead closed at the first space (`@baby bear` never narrowed) and
// missed a short query landing mid-word in a title (`pe` → "typeahead"). Core keeps a `multiWord`
// source's popover open across a space while its rows match; these pin the plugin half: the sources
// opt in, multi-word queries rank by title words (not transcript previews), and 2-char mid-word hits.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')

async function load() {
  globalThis.__smHost = { state: {}, connections: async () => [], onEvent: () => () => {} }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = { middleware: "mw", atCompletions: "at" }')
  return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}

const m = await load()
const row = (id, title, preview = '') => ({ id, title, preview, _conn: 'local', _gwLabel: 'This device', profile: 'default', last_active: 1 })
const rows = [row('s1', 'Fix session typeahead bugs'), row('s2', 'BabyBear hub restart', 'please look at the logs'), row('s3', 'baby monitor')]
const titles = q => m.sessionItems(rows, q, { now: 2000 }).map(i => i.display)

test('a multi-word query narrows across the space', () => {
  assert.deepEqual(titles('baby mon'), ['baby monitor'])
  assert.deepEqual(titles('session typeahead'), ['Fix session typeahead bugs'])
  assert.deepEqual(titles('session:hub rest'), ['BabyBear hub restart'])
})

test('a short query matches mid-word in a title', () => {
  assert.deepEqual(titles('pe'), ['Fix session typeahead bugs'])
  assert.deepEqual(titles('ahead'), ['Fix session typeahead bugs'])
  assert.deepEqual(titles('bear'), ['BabyBear hub restart'])
})

test('later words of a multi-word query do not match the transcript preview', () => {
  // Single word: preview is still a fallback.
  assert.deepEqual(titles('logs'), ['BabyBear hub restart'])
  // `@hub please` is a mention followed by prose: "please" is only in the preview, so no row matches
  // and core closes the popover instead of offering the session on Enter.
  assert.deepEqual(titles('hub please'), [])
})

test('both @ sources declare multiWord so core keeps them open across a space', async () => {
  const regs = []
  const mm = await load()
  globalThis.window = { hermesDesktop: { api: async () => ({ sessions: [], boards: [] }) } }
  globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  mm.default.register({ register: c => regs.push(c), onDispose: () => {}, setInterval: () => 0 })
  for (const id of ['session-mention-completions', 'task-mention-completions']) {
    assert.equal(regs.find(r => r.id === id)?.data.multiWord, true, id)
  }
})
