// Loads plugin.js against React + jsdom + react-query + radix (the Desktop SDK's own building blocks).
// Dependencies: HERMES_DESKTOP_PACKAGE (CI installs them), else ./package.json (npm ci here).
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
const require = createRequire(process.env.HERMES_DESKTOP_PACKAGE || new URL('./package.json', import.meta.url))
export const React = require('react')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<html><body></body></html>', { url: 'http://localhost/', pretendToBeVisual: true })
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Node', 'MutationObserver', 'getComputedStyle',
  'CustomEvent', 'NodeFilter', 'HTMLInputElement', 'HTMLButtonElement', 'MouseEvent', 'KeyboardEvent', 'DOMRect'])
  globalThis[key] = dom.window[key]
globalThis.localStorage = dom.window.localStorage
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0)
globalThis.cancelAnimationFrame = clearTimeout
const { createRoot } = require('react-dom/client')
const query = require('@tanstack/react-query')
const { Popover: P } = require('radix-ui')
const sdk = {
  useQuery: query.useQuery,
  useQueryClient: query.useQueryClient,
  STATUSBAR_AREAS: { right: 'statusBar.right' },
  PALETTE_AREA: 'palette',
  Popover: P.Root,
  PopoverTrigger: P.Trigger,
  PopoverContent: P.Content
}
globalThis.__throughputSdk = sdk
const sdkURL = `data:text/javascript;base64,${Buffer.from(
  Object.keys(sdk).map((k) => `export const ${k}=globalThis.__throughputSdk.${k}`).join(';')
).toString('base64')}`
export async function loadPlugin() {
  let source = await readFile(new URL('../plugin.js', import.meta.url), 'utf8')
  source = source.replace(/from ['"](@hermes\/plugin-sdk|react)['"]/g,
    (_, s) => `from '${s === 'react' ? pathToFileURL(require.resolve('react')).href : sdkURL}'`)
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
}
export async function mount(component, { gcTime = 0 } = {}) {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const client = new query.QueryClient({ defaultOptions: { queries: { retry: false, gcTime } } })
  const flush = async () => React.act(async () => { await new Promise((r) => setTimeout(r, 35)) })
  await React.act(async () => root.render(React.createElement(query.QueryClientProvider, { client }, component)))
  await flush()
  return {
    container,
    flush,
    async click(el, init = {}) {
      if (!el) throw Error('Missing element')
      await React.act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ...init })))
      await flush()
    },
    async event(el, type, init = {}) {
      await React.act(async () => el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, ...init })))
      await flush()
    },
    async close() {
      await React.act(async () => root.unmount())
      client.clear()
      container.remove()
    }
  }
}
export const button = (label) =>
  [...document.querySelectorAll('button')].find((e) => e.textContent === label || e.getAttribute('aria-label')?.startsWith(label))
