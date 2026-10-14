import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
const require = createRequire(
  process.env.HERMES_DESKTOP_PACKAGE || `${process.env.HOME}/.hermes/hermes-agent/package.json`,
)
export const React = require('react')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' })
for (const key of [
  'window',
  'document',
  'HTMLElement',
  'Element',
  'Node',
  'MutationObserver',
  'getComputedStyle',
])
  globalThis[key] = dom.window[key]
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const { createRoot } = require('react-dom/client')
const query = require('@tanstack/react-query')
export const atom = (value) => {
  const listeners = new Set()
  return {
    get: () => value,
    set(next) {
      value = next
      for (const listener of listeners) listener()
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}
export const host = {
  state: {
    focusedStoredSessionId: atom('stored-1'),
    focusedSessionOwner: atom({ connectionId: 'local', profile: 'default' }),
    connectionId: atom('local'),
    profile: atom('default'),
  },
}
// Host atoms/REST are isolated; React, React Query and Radix Popover are real.
// Only themed Button/StatusDot wrappers and the portal are simplified.
// No fixture fetch ever reaches a cloud provider or the running desktop.
const NativePopover = require('radix-ui').Popover
for (const key of [
  'CustomEvent',
  'NodeFilter',
  'HTMLInputElement',
  'HTMLButtonElement',
  'HTMLSelectElement',
  'HTMLTextAreaElement',
])
  globalThis[key] = dom.window[key]
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0)
globalThis.cancelAnimationFrame = clearTimeout
export const sdk = {
  host,
  STATUSBAR_AREAS: { left: 'statusBar.left', right: 'statusBar.right' },
  useValue: (value) => React.useSyncExternalStore(value.subscribe, value.get, value.get),
  useQuery: query.useQuery,
  useQueryClient: query.useQueryClient,
  Button: ({ children, variant, size, ...props }) => React.createElement('button', props, children),
  StatusDot: ({ tone, ...props }) => React.createElement('span', { 'data-tone': tone, ...props }, '●'),
  Popover: NativePopover.Root,
  PopoverTrigger: NativePopover.Trigger,
  PopoverContent: ({ children, ...props }) =>
    React.createElement(
      NativePopover.Content,
      { asChild: true, ...props },
      React.createElement('section', null, children),
    ),
}
globalThis.__computeRentalsTestSdk = sdk
const sdkURL = `data:text/javascript;base64,${Buffer.from(
  Object.keys(sdk)
    .map((key) => `export const ${key} = globalThis.__computeRentalsTestSdk.${key};`)
    .join('\n'),
).toString('base64')}`
export async function loadPlugin(url) {
  let source = await readFile(url, 'utf8')
  source = source.replace(
    /from ['"](@hermes\/plugin-sdk|react)['"]/g,
    (_, specifier) =>
      `from '${specifier === 'react' ? pathToFileURL(require.resolve('react')).href : sdkURL}'`,
  )
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
}
export async function mount(component) {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const client = new query.QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  await React.act(async () => {
    root.render(React.createElement(query.QueryClientProvider, { client }, component))
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  return {
    container,
    client,
    async flush() {
      await React.act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 25))
      })
    },
    async click(element) {
      if (!element) throw Error('Missing click target')
      await React.act(async () => {
        element.click()
        await new Promise((resolve) => setTimeout(resolve, 25))
      })
    },
    async close() {
      await React.act(async () => root.unmount())
      client.clear()
      container.remove()
    },
  }
}
