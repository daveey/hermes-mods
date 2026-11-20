// node --test plugins/core-update/tests/test_chip.mjs — the chip's pure label logic
// (imports stripped: the SDK only exists inside Desktop).
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../../desktop-plugins/core-update/plugin.js'), 'utf8')
const stripped = src.replace(/^import .*$/gm, '').replace(/^export default[\s\S]*$/m, '')
const file = join(mkdtempSync(join(tmpdir(), 'core-update-')), 'chip.mjs')
writeFileSync(file, stripped)
const { chipFor } = await import(file)

test('hidden while idle, failed or unknown', () => {
  assert.equal(chipFor(null), null)
  assert.equal(chipFor({ state: 'idle' }), null)
  assert.equal(chipFor({ state: 'failed', sha: 'a'.repeat(40) }), null)
})

test('building shows a non-clickable progress chip', () => {
  const c = chipFor({ state: 'building', sha: 'abcdef0123456789', commits: 3 })
  assert.equal(c.label, 'Building update…')
  assert.equal(c.clickable, false)
  assert.match(c.title, /3 new commits \(abcdef0123\)/)
})

test('ready is clickable only when the backend can restart', () => {
  assert.equal(chipFor({ state: 'ready', sha: 'abc', commits: 1, can_restart: true }).label, 'Update ready — restart to apply')
  assert.equal(chipFor({ state: 'ready', sha: 'abc', can_restart: true }).clickable, true)
  assert.equal(chipFor({ state: 'ready', sha: 'abc', can_restart: false }).clickable, false)
  assert.equal(chipFor({ state: 'ready', sha: 'abc', can_restart: true }, true).kind, 'restarting')
})
