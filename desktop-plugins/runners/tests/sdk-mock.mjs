import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
const require = createRequire(
  process.env.HERMES_DESKTOP_PACKAGE || new URL('./package.json', import.meta.url)
)
export const React = require('react')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<html><body></body></html>', {
  url: 'http://localhost/',
  pretendToBeVisual: true
})
for (const key of [
  'window',
  'document',
  'HTMLElement',
  'Element',
  'Node',
  'MutationObserver',
  'getComputedStyle',
  'CustomEvent',
  'NodeFilter',
  'HTMLInputElement',
  'HTMLButtonElement',
  'HTMLSelectElement',
  'HTMLTextAreaElement',
  'MouseEvent',
  'KeyboardEvent',
  'DOMRect'
])
  globalThis[key] = dom.window[key]
globalThis.localStorage = dom.window.localStorage
Object.defineProperty(globalThis, 'navigator', {
  value: dom.window.navigator,
  configurable: true
})
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0)
globalThis.cancelAnimationFrame = clearTimeout
const { createRoot } = require('react-dom/client')
const query = require('@tanstack/react-query')
const { Popover: P, ContextMenu: C } = require('radix-ui')
export const atom = (value) => {
  const listeners = new Set()
  return {
    get: () => value,
    set(v) {
      value = v
      listeners.forEach((f) => f())
    },
    subscribe(f) {
      listeners.add(f)
      return () => listeners.delete(f)
    }
  }
}
export const route = {
  connectionId: 'example',
  mode: 'remote',
  profile: 'alias',
  targetProfile: 'example'
}
// Matches the authenticated backend /v1/enrollment response in test_api.py.
export const enrollment = (profile = 'example') => ({
  contract_version: 1, profile, enrolled: true,
  capabilities: ['inventory', 'inspect', 'refresh']
})
export const host = {
  state: {
    gateways: atom([]),
    connectionId: atom('example'),
    profile: atom('example')
  },
  profileRoutes: async () => [route]
}
const sdk = {
  host,
  useValue: (a) => React.useSyncExternalStore(a.subscribe, a.get, a.get),
  useQuery: query.useQuery,
  useQueryClient: query.useQueryClient,
  STATUSBAR_AREAS: { right: 'statusBar.right' },
  PALETTE_AREA: 'palette',
  StatusDot: ({ tone, ...p }) => React.createElement('span', { 'data-tone': tone, ...p }),
  Popover: P.Root,
  PopoverTrigger: P.Trigger,
  PopoverContent: P.Content,
  ContextMenu: C.Root,
  ContextMenuTrigger: C.Trigger,
  ContextMenuContent: C.Content,
  ContextMenuItem: C.Item,
  ContextMenuSeparator: C.Separator
}
globalThis.__runnersSdk = sdk
const sdkURL = `data:text/javascript;base64,${Buffer.from(
  Object.keys(sdk)
    .map((k) => `export const ${k}=globalThis.__runnersSdk.${k}`)
    .join(';')
).toString('base64')}`
export async function loadPlugin() {
  let source = await readFile(new URL('../plugin.js', import.meta.url), 'utf8')
  source = source.replace(
    /from ['"](@hermes\/plugin-sdk|react)['"]/g,
    (_, s) => `from '${s === 'react' ? pathToFileURL(require.resolve('react')).href : sdkURL}'`
  )
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
}
export const fixtures = JSON.parse(
  await readFile(
    new URL('../../../plugins/runners/contracts/fixtures.json', import.meta.url),
    'utf8'
  )
)
// Explicit simulated provider/probe observations; never inferred from fetch time.
export function observe(s, { runners = s.data.runners, hosts = [], now = Date.now() } = {}) {
  for (const source_id of [...runners.map(r => r.id), ...hosts.map(h => h.resource_id)]) {
    s.meta.provenance = s.meta.provenance.filter(p => p.source_id !== source_id)
    s.meta.provenance.push({ source_id, state: 'fresh', observed_at: new Date(now - 1000).toISOString(), expires_at: new Date(now + 60000).toISOString(), observation_ids: ['obs_0000000000000001'] })
  }
  return s
}
export function snapshot() {
  const s = structuredClone(fixtures.InventoryResponse)
  s.meta.observed_at = new Date().toISOString()
  s.meta.expires_at = new Date(Date.now() + 120000).toISOString()
  s.meta.freshness = 'fresh'
  s.meta.coverage = [
    {
      scope_id: fixtures.Scope.id,
      state: 'complete',
      pagination_complete: true,
      enumerated_count: 1,
      provider_total_count: 1
    }
  ]
  return observe(s)
}
export async function mount(component) {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const client = new query.QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } }
  })
  const flush = async () =>
    React.act(async () => {
      await new Promise((r) => setTimeout(r, 35))
    })
  await React.act(async () =>
    root.render(React.createElement(query.QueryClientProvider, { client }, component))
  )
  await flush()
  return {
    container,
    client,
    flush,
    async click(el) {
      assertElement(el)
      await React.act(async () => el.click())
      await flush()
    },
    async key(el, key, extras = {}) {
      await React.act(async () =>
        el.dispatchEvent(
          new KeyboardEvent('keydown', {
            key,
            bubbles: true,
            cancelable: true,
            ...extras
          })
        )
      )
      await flush()
    },
    async close() {
      await React.act(async () => root.unmount())
      client.clear()
      container.remove()
    }
  }
}
function assertElement(el) {
  if (!el) throw Error('Missing element')
}
export const button = (label) =>
  [...document.querySelectorAll('button')].find(
    (e) =>
      e.textContent === label ||
      e.getAttribute('aria-label') === label ||
      (label === 'Show runners' &&
        e.getAttribute('aria-label')?.startsWith('Show runners: Runners ·'))
  )
