// Desktop's runtime loader (core apps/desktop/src/contrib/runtime-loader.ts `unsupportedImports`, map from
// sdk/runtime.ts `sdkImportMap`) refuses any plugin.js importing anything except these specifiers, and the whole
// plugin then fails to load ("runtime load failed (...) unsupported import"). Node tests resolve sibling files fine,
// so without this check a split-out helper module passes every suite and silently removes the plugin on Desktop.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const ALLOWED = new Set(['@hermes/plugin-sdk', 'react', 'react/jsx-runtime', 'react/jsx-dev-runtime'])
const ROOT = fileURLToPath(new URL('../../', import.meta.url))

// Line-start static imports/re-exports and literal dynamic imports; block and line comments removed first.
export function importSpecifiers(source) {
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const specs = []
  for (const m of code.matchAll(/^\s*(?:import\s*(['"])([^'"]+)\1|(?:import|export)\b[^'"`;]*?\bfrom\s*(['"])([^'"]+)\3)/gm)) specs.push(m[2] || m[4])
  for (const m of code.matchAll(/\bimport\s*\(\s*(['"])([^'"]+)\1\s*\)/g)) specs.push(m[2])
  return specs
}

test('the import scanner sees the shapes the Desktop loader refuses', () => {
  assert.deepEqual(importSpecifiers("import { a } from './x.js'\nimport * as s from '@hermes/plugin-sdk'\nimport 'side'\nexport { b } from \"../y.js\"\nconst m = await import('./z.js')\n// import q from './comment.js'\n/* import r from './block.js' */\nexport const ID = 'not-an-import'\nimport {\n  c,\n  d\n} from './multi.js'"),
    ['./x.js', '@hermes/plugin-sdk', 'side', '../y.js', './multi.js', './z.js'])
})

test('every runtime-loaded desktop plugin.js imports only the SDK and react', () => {
  const bad = []
  for (const dir of readdirSync(ROOT, { withFileTypes: true })) {
    const file = join(ROOT, dir.name, 'plugin.js')
    if (!dir.isDirectory() || !existsSync(file)) continue
    for (const spec of importSpecifiers(readFileSync(file, 'utf8'))) if (!ALLOWED.has(spec)) bad.push(`${dir.name}/plugin.js: ${spec}`)
  }
  assert.deepEqual(bad, [], 'Desktop refuses these imports and drops the whole plugin; inline the module into plugin.js')
})
