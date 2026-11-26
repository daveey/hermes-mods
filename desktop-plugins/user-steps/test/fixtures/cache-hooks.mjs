// Cache inspection/reset is a test seam, not part of the runtime-loaded plugin's exports.
// Append it only in Node's test loader so every shipped helper remains registration-reachable.
import { register } from 'node:module'
register('data:text/javascript,' + encodeURIComponent(`
export async function load(url, context, next) {
  const loaded = await next(url, context)
  if (!new URL(url).pathname.endsWith('/user-steps/plugin.js')) return loaded
  return { ...loaded, source: String(loaded.source) + ${JSON.stringify(`
export const resetLedgerCacheForTests = () => ledgerCache.clear()
export const getSnapshot = () => snapshot
export const clearStepCache = () => stepCache.clear()
export const resetCanonicalCacheForTests = () => { canonicalCache.clear(); canonical = { scope: null, requests: [], error: null } }
export const clearFeatureCache = () => { pings.clear(); pinged.clear() }
export const cachedRequests = scope => canonicalCache.get(scope) || null
`)} }
}
`))
