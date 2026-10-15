// Regression: a week-old chat could not be @-mentioned. The typeahead
// fetched one 300-row page and hid kanban/oneshot rows client-side; those were ~93% of the page,
// so only ~20 real chats survived and a week-old one was never listed. Hidden sources must be
// excluded server-side, and every page fetched.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = {}; const COMPOSER_AREAS = {}')
const m = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))

test('session list excludes hidden sources server-side and pages until short', async () => {
  const paths = []
  const fake = async (_conn, path) => {
    paths.push(path)
    const off = Number(new URL(path, 'http://x').searchParams.get('offset'))
    const n = off === 0 ? 500 : 7
    return { total: 507, sessions: Array.from({ length: n }, (_, i) => ({ id: `s${off + i}` })) }
  }
  const rows = await m.fetchAllSessionPages('local', fake)
  assert.equal(rows.length, 507)
  assert.equal(paths.length, 2)
  const excl = new URL(paths[0], 'http://x').searchParams.get('exclude_sources').split(',').sort()
  assert.deepEqual(excl, ['cron', 'kanban', 'oneshot', 'subagent', 'tool'])
})

test('a failing later page keeps the rows already fetched', async () => {
  let calls = 0
  const fake = async () => {
    if (calls++) throw new Error('boom')
    return { total: 900, sessions: Array.from({ length: 500 }, (_, i) => ({ id: `s${i}` })) }
  }
  assert.equal((await m.fetchAllSessionPages('local', fake)).length, 500)
  await assert.rejects(m.fetchAllSessionPages('local', async () => Promise.reject(new Error('down'))), /down/)
})
