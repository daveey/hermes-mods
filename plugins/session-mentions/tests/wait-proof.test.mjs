import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = {}; const COMPOSER_AREAS = {}')
const { summarizeTask } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))

test('task summary preserves owner wait evidence without inferring absent evidence', () => {
  const proof = { board: 'example', human_request: false, parked_agent: true, orphan_reason: 'producer gone' }
  const task = { id: 't_123456789abcdef0', status: 'scheduled' }
  assert.deepEqual(summarizeTask({ task, source: 'live', wait_observation: proof }).wait_observation, proof)
  assert.equal(summarizeTask({ task, source: 'replica' }).wait_observation, undefined)
})
