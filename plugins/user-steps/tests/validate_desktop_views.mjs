// Exercise the actual renderer exports without installing React or starting Desktop.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { register } from 'node:module'

// Only UI dependencies are stubbed; no validator/projection source is copied.
register('data:text/javascript,' + encodeURIComponent(`
  export async function load(url, context, next) {
    // Desktop loads this ESM .js file without a package.json; do the same on
    // older Node releases that do not yet auto-detect module syntax.
    return next(url, url.endsWith('/desktop-plugins/user-steps/plugin.js')
      ? { ...context, format: 'module' } : context)
  }
  export async function resolve(specifier, context, next) {
    const modules = {
      '@hermes/plugin-sdk': 'export const COMPOSER_AREAS={}, TRANSCRIPT_DIRECTIVE_AREA="", host={}; export function useValue(){throw Error("UI used")}; export function MessageTextContent(){throw Error("UI used")}',
      'react': 'export function useEffect(){throw Error("UI used")}; export function useState(){throw Error("UI used")}; export function useRef(){throw Error("UI used")}',
      'react/jsx-runtime': 'export function jsx(){throw Error("UI used")}; export function jsxs(){throw Error("UI used")}'
    }
    return Object.hasOwn(modules, specifier)
      ? { url: 'data:text/javascript,' + encodeURIComponent(modules[specifier]), shortCircuit: true }
      : next(specifier, context)
  }
`))
const { validRequest, projectRequests } = await import('../../../desktop-plugins/user-steps/plugin.js')
const requests = JSON.parse(readFileSync(0, 'utf8'))
assert.ok(requests.length > 0, 'contract corpus must not be empty')
const rejected = requests.filter(request => !validRequest(request))
assert.deepEqual(rejected.map(r => r.title), [], 'Desktop rejected backend-produced views')
assert.deepEqual(projectRequests(requests), requests, 'projection must not silently drop backend views')
console.log(JSON.stringify({ validated: requests.length }))
