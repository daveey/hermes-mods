import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(new URL('../plugin.js', import.meta.url), 'utf8')

test('titlebar spark keeps its chart dimensions against shell icon sizing', () => {
  // The titlebar button's descendant-svg utility overrides SVG width/height attributes.
  // Important dimensions are scoped to the chart, not every titlebar icon.
  const rule = source.match(/\.hermes-tp-tool \.hermes-tp-spark\{([^}]+)\}/)?.[1] || ''
  assert.match(rule, /width:34px!important/)
  assert.match(rule, /height:14px!important/)
})
