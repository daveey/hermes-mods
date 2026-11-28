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
createRoot(document.getElementById('bar')).render(React.createElement(QueryClientProvider, { client }, React.createElement(latency.LedgerSurface, { ctx: window.ctx })))
