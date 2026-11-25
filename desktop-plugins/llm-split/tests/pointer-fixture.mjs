// Owned-browser fixture: real Radix Tooltip -> titlebar button -> plugin Popover span,
// portaled Popover.Content, QueryClient and both plugins. Only ctx.rest is fake.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Tooltip as T } from 'radix-ui'
import * as L from '../plugin.js'
import * as P from '../../throughput/plugin.js'
const h = React.createElement
window.events = []
for (const type of ['pointerdown', 'pointerup', 'gotpointercapture', 'lostpointercapture', 'pointercancel', 'click', 'pointerout', 'focusin', 'focusout']) {
  document.addEventListener(type, (e) => window.events.push({ type, target: e.target.className, trusted: e.isTrusted }), true)
}
const split = {
  pools: [{ id: 'anthropic', label: 'Claude' }, { id: 'openai-codex', label: 'GPT' }],
  mode: 'manual', traffic: { share: { anthropic: 30, 'openai-codex': 70 } },
  quota: { share: { anthropic: 30, 'openai-codex': 70 }, dry: [] }
}
window.calls = []
const ctx = {
  rest: async (path, opts = {}) => {
    window.calls.push({ path, ...opts })
    if (path === '/v1/split') {
      if (opts.body?.mode) split.mode = opts.body.mode
      if (opts.body?.share) split.traffic.share = opts.body.share
      return { ...split }
    }
    if (path === '/v1/throttle') return { percent: 0, mode: 'manual', cap_per_s: 100, user_cap_per_s: 100 }
    if (path === '/v1/budget') return { pools: {}, history: {} }
    return { range: '6h', bucket_s: 60, gateways: [], series: {}, buckets: [] }
  },
  onDispose() {}, register() {}
}
L.default.register(ctx)
P.default.register(ctx)
function Tool({ tool }) {
  return h(T.Root, { disableHoverableContent: true },
    h(T.Trigger, { asChild: true }, h('button', {
      type: 'button', className: tool.className, 'aria-label': tool.label,
      onPointerDown: (e) => e.stopPropagation(), onClick: (e) => tool.onSelect?.(e),
      style: { width: 192, height: 28, padding: 0, border: 0, display: 'inline-flex', alignItems: 'center' }
    }, tool.icon)), h(T.Portal, null, h(T.Content, { side: 'bottom', style: { pointerEvents: 'none' } }, tool.title)))
}
createRoot(document.getElementById('root')).render(h(QueryClientProvider, {
  client: new QueryClient({ defaultOptions: { queries: { retry: false } } })
}, h(T.Provider, { delayDuration: 0 }, h('div', { style: { display: 'flex', justifyContent: 'flex-end' } },
  h(Tool, { tool: P.titlebarTool(ctx) }), h(Tool, { tool: L.tool(ctx) })))))
