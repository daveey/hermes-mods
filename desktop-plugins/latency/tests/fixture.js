import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as latency from '../plugin.js'
window.latency = latency
window.opened = []
window.testStore = new Map()
window.ctx = { storage: { get: (k, fallback) => window.testStore.get(k) ?? fallback, set: (k, v) => window.testStore.set(k, v) }, socket: () => () => {}, rest: async (path, options = {}) => {
  const response = await fetch('/api'+path, { ...options, headers: { 'Content-Type': 'application/json' }, body: options.body && JSON.stringify(options.body) })
  if (!response.ok) throw Error('Unavailable')
  return response.json()
}, os: { openExternal: async url => window.opened.push(url) } }
const style = document.createElement('style'); style.textContent = latency.CSS; document.head.appendChild(style)
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
window.renderWatch = (definition, streaming = false) => {
  let element = document.getElementById('watch-root')
  if (!element) { element = document.createElement('div'); element.id = 'watch-root'; document.body.appendChild(element); window.watchRoot = createRoot(element) }
  window.watchRoot.render(React.createElement(QueryClientProvider, { client }, React.createElement(latency.WatchProposal, { ctx: window.ctx, attrs: { definition }, streaming })))
}
createRoot(document.getElementById('bar')).render(React.createElement(QueryClientProvider, { client }, React.createElement(latency.LedgerSurface, { ctx: window.ctx })))
