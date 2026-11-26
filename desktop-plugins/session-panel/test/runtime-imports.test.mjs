// Desktop's runtime loader (core apps/desktop/src/contrib/runtime-loader.ts `unsupportedImports`, map from
// sdk/runtime.ts `sdkImportMap`) refuses any plugin.js importing anything except these specifiers, and the whole
// plugin then fails to load ("runtime load failed (...) unsupported import"). Node tests resolve sibling files fine,
// so without this check a split-out helper module passes every suite and silently removes the plugin on Desktop.
//
// Everything from `importSpecifierRe` to `unsupportedImports` below is core's code with only the TypeScript types
// removed (rewriteSpecifiers dropped), so this test refuses exactly what Desktop refuses. When a core checkout is
// reachable, the drift test runs core's own source on the same inputs and fails if the two ever disagree.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import * as nodeModule from 'node:module'

const SDK_IMPORT_MAP = Object.fromEntries(['@hermes/plugin-sdk', 'react', 'react/jsx-runtime', 'react/jsx-dev-runtime'].map(k => [k, k]))
const ROOT = fileURLToPath(new URL('../../', import.meta.url))

// ---- core runtime-loader.ts (types stripped) ----
const importSpecifierRe = () => {
  const gap = String.raw`(?:\s|\/\*[\s\S]*?\*\/|\/\/[^\n]*\n)`

  return new RegExp(`(from${gap}*|import${gap}*\\(${gap}*|import${gap}+)(['"])([^'"]+)\\2`, 'g')
}

/** Character ranges of *source* that are code — string, template-literal and
 *  comment text excluded (template `${…}` interpolations ARE code). The
 *  specifier regex is not syntax-aware, so this is what keeps a plugin's own
 *  copy and comments — `const label = 'Copy keys from'`, `// import 'x'` —
 *  from being read as import syntax (rejected as "unsupported import") or
 *  rewritten in place (a mapped specifier inside a string must stay verbatim).
 *  Regex literals are excluded too: a quote or backtick inside a pattern
 *  (#120208) must not open a string/template state. */

/** Keywords after which a `/` opens a regex literal, never a division. */
const regexKeywordRe = /^(?:await|case|delete|do|else|in|instanceof|new|of|return|throw|typeof|void|yield)$/

/** True when the `/` at `slash` (already known not to start `//` or `/*`)
 *  opens a regex literal: the previous significant char cannot end a value.
 *  Standard division-vs-regex heuristic. */
function isRegexStart(source, slash) {
  let j = slash - 1

  while (j >= 0 && /\s/.test(source[j])) {
    j -= 1
  }

  if (j < 0) {
    return true
  }

  const prev = source[j]

  // Postfix `++`/`--` ends a value (division); a lone `+`/`-` cannot.
  if (prev === '+' || prev === '-') {
    return source[j - 1] !== prev
  }

  // Identifier, number, string/template end, `)` or `]` end a value.
  if (prev === ')' || prev === ']' || prev === "'" || prev === '"' || prev === '`') {
    return false
  }

  // Block-end `}` resolves toward regex — `} /re/` (statement-start
  // pattern) is real code, `} / 2` (dividing a block) is not. Revisit if a
  // plugin ever divides a block result.
  if (prev === '}') {
    return true
  }

  if (/[A-Za-z0-9_$]/.test(prev)) {
    let k = j

    while (k >= 0 && /[A-Za-z0-9_$]/.test(source[k])) {
      k -= 1
    }

    // `x.return / 2` divides a property, it is not `return /re/`.
    if (source[k] === '.') {
      return false
    }

    return regexKeywordRe.test(source.slice(k + 1, j + 1))
  }

  return true
}

/** End offset (exclusive) of the regex literal opened at `slash`, or -1 when
 *  the pattern never closes on this line (so the `/` was a division).
 *  Escapes and `[...]` classes are honored so a quote or backtick inside the
 *  pattern (#120208) cannot leak into the surrounding lex. */
function regexEnd(source, slash) {
  let j = slash + 1
  let inClass = false

  while (j < source.length) {
    const c = source[j]

    if (c === '\\') {
      j += 2
    } else if (c === '\n') {
      return -1
    } else if (c === '[') {
      inClass = true
      j += 1
    } else if (c === ']') {
      inClass = false
      j += 1
    } else if (c === '/' && !inClass) {
      j += 1

      while (j < source.length && /[A-Za-z]/.test(source[j])) {
        j += 1
      }

      return j
    } else {
      j += 1
    }
  }

  return -1
}

function codeRanges(source) {
  const ranges = []
  const stack = []
  let state = 'code'
  let codeStart = 0
  let i = 0

  const closeCode = (end) => {
    if (end > codeStart) {
      ranges.push([codeStart, end])
    }
  }

  while (i < source.length) {
    const ch = source[i]
    const next = i + 1 < source.length ? source[i + 1] : ''

    if (state === 'code') {
      if (ch === '/' && next === '/') {
        closeCode(i)
        state = 'line-comment'
        i += 2
      } else if (ch === '/' && next === '*') {
        closeCode(i)
        state = 'block-comment'
        i += 2
      } else if (ch === "'") {
        closeCode(i)
        state = 'single'
        i += 1
      } else if (ch === '"') {
        closeCode(i)
        state = 'double'
        i += 1
      } else if (ch === '`') {
        closeCode(i)
        stack.push('template')
        state = 'template'
        i += 1
      } else if (ch === '/') {
        // A lone `/` (not `//` or `/*`, handled above) opens a regex literal
        // when the previous significant token cannot end a value (#120208).
        // Otherwise it is a division and stays plain code.
        const end = isRegexStart(source, i) ? regexEnd(source, i) : -1

        if (end > 0) {
          // The pattern is not code: import-looking text inside it must
          // neither match nor be rewritten in place.
          closeCode(i)
          i = end
          codeStart = i
        } else {
          i += 1
        }
      } else if (ch === '}' && stack[stack.length - 1] === 'expr') {
        closeCode(i)
        stack.pop()
        state = 'template'
        i += 1
      } else {
        i += 1
      }

      continue
    }

    if (state === 'line-comment') {
      if (ch === '\n') {
        state = 'code'
        codeStart = i
      }

      i += 1

      continue
    }

    if (state === 'block-comment') {
      if (ch === '*' && next === '/') {
        i += 2
        state = 'code'
        codeStart = i
      } else {
        i += 1
      }

      continue
    }

    if (state === 'single' || state === 'double') {
      if (ch === '\\') {
        i += 2
      } else if (ch === (state === 'single' ? "'" : '"')) {
        i += 1
        state = 'code'
        codeStart = i
      } else if (ch === '\n') {
        // Unterminated literal — recover as code so one stray quote cannot
        // swallow the rest of the file.
        i += 1
        state = 'code'
        codeStart = i
      } else {
        i += 1
      }

      continue
    }

    // Template-literal text.
    if (ch === '\\') {
      i += 2
    } else if (ch === '$' && next === '{') {
      stack.push('expr')
      state = 'code'
      i += 2
      codeStart = i
    } else if (ch === '`') {
      stack.pop()
      state = 'code'
      i += 1
      codeStart = i
    } else {
      i += 1
    }
  }

  closeCode(source.length)

  return ranges
}

/** True when *at* sits inside a code range (ordered, non-overlapping). */
function inCode(ranges, at) {
  for (const [start, end] of ranges) {
    if (at < start) {
      return false
    }

    if (at < end) {
      return true
    }
  }

  return false
}

/** Import specifiers outside the SDK map. Everything that is not
 *  `@hermes/plugin-sdk` / `react*` is refused up-front: a bare package would
 *  only fail later as a cryptic native "Failed to resolve module specifier",
 *  a relative path cannot resolve against the blob: base the module is
 *  evaluated from, and a URL scheme (`import 'https://…'`) is a second stage
 *  the admission lint must never be able to wave through — the loader is the
 *  last tripwire for catalog installs. */
export function unsupportedImports(source) {
  const map = SDK_IMPORT_MAP
  const unsupported = new Set()
  const ranges = codeRanges(source)

  for (const m of source.matchAll(importSpecifierRe())) {
    const spec = m[3]

    // Strings and comments can quote any text; only real code counts.
    if (!spec || !inCode(ranges, m.index ?? 0)) {
      continue
    }

    if (!map[spec]) {
      unsupported.add(spec)
    }
  }

  return [...unsupported]
}
// ---- end core ----

const plugins = () => readdirSync(ROOT, { withFileTypes: true })
  .map(d => ({ name: d.name, file: join(ROOT, d.name, 'plugin.js') }))
  .filter(p => existsSync(p.file))
  .map(p => ({ ...p, source: readFileSync(p.file, 'utf8') }))

// [source, what Desktop refuses]. Includes the cases where the earlier line-anchored scanner disagreed with core.
const CASES = [
  ["import { a } from './x.js'\nimport * as s from '@hermes/plugin-sdk'\nimport 'side'\nexport { b } from \"../y.js\"\nconst m = await import('./z.js')", ['./x.js', 'side', '../y.js', './z.js']],
  ["import {\n  c,\n  d\n} from './multi.js'", ['./multi.js']],
  ["// import q from './comment.js'\n/* import r from './block.js' */\nexport const ID = 'not-an-import'", []],
  ["const example = \"import('./x.js')\"", []],
  ["const t = `\nimport './x.js'\n`", []],
  ["const label = 'Copy keys from'\nconst re = /import '.\\/x.js'/", []],
  ["const n=1; import {x} from './x.js'", ['./x.js']],
  ["const n=1; import './x.js'", ['./x.js']],
  ["const n=1; export {x} from './x.js'", ['./x.js']],
  ["const m=import('./x.js', {with:{type:'json'}})", ['./x.js']],
  ["const t = `${await import('./x.js')}`", ['./x.js']],
  ["import { useState } from 'react'\nimport { jsx } from 'react/jsx-runtime'", []],
]

test('scanner refuses exactly what the Desktop loader refuses', () => {
  for (const [source, want] of CASES) assert.deepEqual(unsupportedImports(source), want, source)
})

test('every runtime-loaded desktop plugin.js imports only the SDK and react', () => {
  const bad = plugins().flatMap(p => unsupportedImports(p.source).map(s => `${p.name}/plugin.js: ${s}`))
  assert.deepEqual(bad, [], 'Desktop refuses these imports and drops the whole plugin; inline the module into plugin.js')
})

const CORE = [process.env.HERMES_CORE_DIR, join(process.env.HOME || '', '.hermes/hermes-agent')]
  .filter(Boolean).map(d => join(d, 'apps/desktop/src/contrib/runtime-loader.ts')).find(f => existsSync(f))

test('the ported scanner matches core runtime-loader.ts', { skip: !CORE || !nodeModule.stripTypeScriptTypes ? 'no core checkout or Node without stripTypeScriptTypes' : false }, async () => {
  const src = readFileSync(CORE, 'utf8')
  const start = src.indexOf('const importSpecifierRe'), end = src.indexOf('\nfunction unsupportedImports')
  assert.ok(start > 0 && end > start, 'core layout changed: update this port')
  const fn = src.slice(end, src.indexOf('\n}\n', end) + 3)
  const ts = `const sdkImportMap = () => (${JSON.stringify(SDK_IMPORT_MAP)})\n${src.slice(start, end)}\nexport ${fn.trim()}\n`
  const core = await import('data:text/javascript,' + encodeURIComponent(nodeModule.stripTypeScriptTypes(ts)))
  for (const [source] of CASES) assert.deepEqual(unsupportedImports(source), core.unsupportedImports(source), source)
  for (const p of plugins()) assert.deepEqual(unsupportedImports(p.source), core.unsupportedImports(p.source), p.name)
})
