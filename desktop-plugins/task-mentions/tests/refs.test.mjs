// Gateway-qualified refs: `@task:<board>@<gateway>/<id>` must be looked up on that gateway.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../plugin.js', import.meta.url), 'utf8')
const m = await import(
  'data:text/javascript;base64,' + Buffer.from(source.replace("import { host as sdkHost } from '@hermes/plugin-sdk'", 'const sdkHost = {}')).toString('base64')
)

test('refQualifier reads the board/gateway prefix right before an id', () => {
  assert.deepEqual(m.refQualifier('see @task:bb@mac-mini/'), { board: 'bb', gateway: 'mac-mini' })
  assert.deepEqual(m.refQualifier('`@task:default/'), { board: 'default', gateway: '' })
  assert.equal(m.refQualifier('plain text '), null)
  assert.equal(m.refQualifier('@task:bb@mac-mini/ then '), null)
})

test('findConnection matches slugged labels and ids', () => {
  const conns = [
    { id: 'local', label: 'This device' },
    { id: 'skint-polycentrism', label: 'nous-1' }
  ]
  assert.equal(m.findConnection(conns, 'nous-1'), 'skint-polycentrism')
  assert.equal(m.findConnection(conns, 'this-device'), 'local')
  assert.equal(m.findConnection(conns, 'skint-polycentrism'), 'skint-polycentrism')
  assert.equal(m.findConnection(conns, 'nope'), null)
})
