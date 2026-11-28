// Real producer JSON -> real Hub store. Only unused SDK/UI imports are shimmed.
import { register } from 'node:module'
import assert from 'node:assert/strict'
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,' + encodeURIComponent(
    'export const host = {}; export const atom = v => ({ get: () => v, set: n => { v = n } });' +
    'export const cn = () => ""; export const useValue = a => a.get();' +
    'export const Popover = "", PopoverTrigger = "", PopoverContent = "";'), shortCircuit: true }
  if (spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const jsx=()=>null,jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}
export async function load(url, ctx, next) {
  if (url.endsWith('/hub-status/plugin.js')) return next(url, { ...ctx, format: 'module' })
  return next(url, ctx)
}`))
const { createVersionStore } = await import('../../../desktop-plugins/hub-status/plugin.js')
const view = JSON.parse(process.argv[2])
const store = createVersionStore(async o => o.path.endsWith('/ping') ? { api: 2 } : view)
await store.refresh()
assert.equal(store.$message.get(), 'Updated')
assert.ok(!store.$message.get().includes(view.target.slice(0, 8)))
console.log(store.$message.get())
