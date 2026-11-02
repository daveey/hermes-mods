// t_01a199f0b0f218b0: a Desktop that missed a plugin reload must say which plugin.js it runs. The courier
// stamps git blob shas of the code it RUNS (read at register) and of its disk copy (re-read at most once a
// minute) onto every drain, and an `unknown action` refusal names both.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const pluginUrl = process.env.SM_PLUGIN_JS ? new URL(`file://${process.env.SM_PLUGIN_JS}`) : new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url)
const source = await fs.readFile(pluginUrl, 'utf8')
const blob = text => createHash('sha1').update(`blob ${Buffer.byteLength(text)}\0`).update(text).digest('hex')
async function load(desktop) {
  globalThis.__smHost = {}
  globalThis.window = { hermesDesktop: desktop }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
  return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}
const conns = [{ id: 'local', kind: 'local', key: 'macbook', primary: true, device: 'macbook', aliases: ['macbook'] }]

test('gitBlobSha equals `git hash-object`', async () => {
  const m = await load({})
  assert.equal(await m.gitBlobSha(''), 'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391')
  assert.equal(await m.gitBlobSha('héllo\n'), blob('héllo\n'))
})

test('drain query carries the loaded + disk sha; disk changes show as loaded != disk', async () => {
  let disk = 'export default { v: 1 }\n'
  const reads = []
  const m = await load({
    desktopPluginsRoot: async () => '/Users/d/.hermes/desktop-plugins/',
    readPluginSource: async path => (reads.push(path), { text: disk })
  })
  assert.equal(new URLSearchParams(m.outboxPath(conns[0], conns).split('?')[1]).get('plugin'), null) // before register
  await m.refreshPluginStamp({ loaded: true })
  assert.equal(reads[0], '/Users/d/.hermes/desktop-plugins/session-mentions/plugin.js')
  let q = new URLSearchParams(m.outboxPath(conns[0], conns).split('?')[1])
  assert.equal(q.get('plugin'), blob(disk))
  assert.equal(q.get('plugin_disk'), blob(disk))
  assert.match(q.get('plugin_at'), /^\d+$/)

  const v1 = blob(disk)
  disk = 'export default { v: 2, authority_read: true }\n' // git pull; this module was not reloaded
  await m.refreshPluginStamp() // within the minute: no re-read
  assert.equal(m.pluginStampOf().disk, v1)
  const realNow = Date.now
  Date.now = () => realNow() + 61_000
  try {
    await m.refreshPluginStamp()
  } finally {
    Date.now = realNow
  }
  q = new URLSearchParams(m.outboxPath(conns[0], conns).split('?')[1])
  assert.equal(q.get('plugin'), v1)
  assert.equal(q.get('plugin_disk'), blob(disk))
})

test('the shell lacking readPluginSource falls back to readFileText; no root means no stamp', async () => {
  const m = await load({ desktopPluginsRoot: async () => '/r', readFileText: async () => ({ text: 'x' }) })
  await m.refreshPluginStamp({ loaded: true })
  assert.equal(m.pluginStampOf().loaded, blob('x'))
  const bare = await load({})
  await bare.refreshPluginStamp({ loaded: true })
  assert.equal(bare.pluginStampOf().loaded, '')
  assert.equal(new URLSearchParams(bare.outboxPath(conns[0], conns).split('?')[1]).get('plugin'), null)
})

test('an unknown-action refusal names the loaded and on-disk plugin.js', () => {
  assert.equal((source.match(/unknown action \$\{action\}\$\{stampNote\(\)\}/g) || []).length, 2)
  assert.doesNotMatch(source, /unknown action \$\{action\}`/)
})
