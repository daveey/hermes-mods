// LLM split (design option B, ~/design/llm-split/board.html): a titlebar chip that shows the router's traffic split
// between the subscription pools next to their quota share, and a hover panel whose drag bar sets the split.
// Click (or Enter on the focused chip) opens the panel; the titlebar tooltip names it.
// Chip: mini stacked bar (traffic) with a tick at Claude's quota share, "13:86" (Claude:GPT traffic %), one word
// (auto · manual · ANT over · GPT over · ANT dry). Panel: per-pool accounts, Quota bar, draggable Traffic bar, Auto|Manual.
// Data: GET/PUT /api/plugins/llm-split/v1/split (plugins/llm-split/core.py). Dragging = manual (PUT on release);
// Auto = weights follow quota (the gateway re-applies every 5 min). Mounted as a titleBar.tools.right item so the app's
// own titlebar buttons stay on plugin pages.
import { useQuery, useQueryClient, Popover, PopoverTrigger, PopoverContent } from '@hermes/plugin-sdk'
import { createElement as h, useState, useEffect, useRef, useCallback } from 'react'

export const POLL_MS = 60000
export const KEY_COMMIT_MS = 400
export const OVER_PTS = 5 // |traffic - quota| below this reads as "manual" (matches quota)
export const CHIP_SPAN = 8 // the chip is 8 titlebar cells wide (8 x 24 px; the core's span cap)
export const QUERY_KEY = ['llm-split', 'split']
const ANT = 'anthropic', GPT = 'openai-codex', GRK = 'xai-oauth'
export const COLORS = { [ANT]: '#d97757', [GPT]: '#3fb8a0', [GRK]: '#9aa0a6' }
const SHORT = { [ANT]: 'ANT', [GPT]: 'GPT', [GRK]: 'GRK' }
const DRY = '#f85149', TIGHT = '#d29922', OK = '#7aa2f7'

// ── pure helpers (exported for tests) ─────────────────────────────────────

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
export const pct = (v) => Math.round(Number(v) || 0)

// A Claude share dragged to `ant` keeps Grok's share and gives GPT the rest.
export function splitAt(ant, grk = 0) {
  grk = clamp(Number(grk) || 0, 0, 100)
  const a = clamp(Number(ant) || 0, 0, 100 - grk)
  return { [ANT]: a, [GPT]: 100 - grk - a, [GRK]: grk }
}

export function validSnapshot(s) {
  return !!s && Array.isArray(s.pools) && !!s.traffic && typeof s.traffic.share === 'object' && !!s.quota
}

// What the chip says: the shares, and a verdict.
export function summarize(snap, draft) {
  const traffic = draft || snap.traffic.share || {}
  const quota = snap.quota.share || {}
  const hasQuota = Object.keys(quota).length > 0
  const mode = draft ? 'manual' : snap.mode
  const over = (traffic[ANT] || 0) - (quota[ANT] || 0)
  let word = mode, tone = 'mute'
  if (hasQuota && (snap.quota.dry || []).includes(ANT)) { word = 'ANT dry'; tone = 'bad' }
  else if (mode === 'auto') { word = 'auto'; tone = 'ok' }
  else if (hasQuota && Math.abs(over) >= OVER_PTS) { word = over > 0 ? 'ANT over' : 'GPT over'; tone = 'warn' }
  else word = 'manual'
  return { traffic, quota, hasQuota, mode, over, word, tone }
}

export function noteFor(sum) {
  if (sum.mode === 'auto') return 'Follows quota, re-checked every 5 min'
  if (!sum.hasQuota) return 'No quota reading on this host'
  if (Math.abs(sum.over) < OVER_PTS) return 'Matches quota'
  return `${sum.over > 0 ? '+' : ''}${pct(sum.over)}% Claude vs quota`
}

export function titleFor(sum) {
  const t = sum.traffic, q = sum.quota
  const traffic = `Traffic Claude ${pct(t[ANT])}% · GPT ${pct(t[GPT])}%${t[GRK] ? ` · Grok ${pct(t[GRK])}%` : ''}`
  const quota = sum.hasQuota ? `Quota Claude ${pct(q[ANT])}% · GPT ${pct(q[GPT])}%` : 'Quota unknown'
  return `${traffic}\n${quota}\n${sum.mode === 'auto' ? 'Auto: split follows quota' : 'Manual split'}`
}

const ago = (at, now = Date.now() / 1000) => {
  if (!at) return ''
  const s = Math.max(0, now - at)
  return s < 90 ? 'just now' : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`
}

// ── components ─────────────────────────────────────────────────────────────

function Segments({ share, order, labels = false }) {
  return order.map((p) => {
    const v = share[p] || 0
    const text = !labels ? null : v >= 14 ? `${SHORT[p] || p} ${pct(v)}%` : v >= 7 ? String(pct(v)) : ''
    return h('span', { key: p, 'data-pool': p, style: { width: `${v}%`, background: COLORS[p] || '#888' } }, text)
  })
}

export function useSplit(ctx) {
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: async () => {
      const body = await ctx.rest('/v1/split', { timeoutMs: 20000 })
      if (!validSnapshot(body)) throw new Error('llm-split: bad /v1/split payload')
      return body
    },
    refetchInterval: POLL_MS,
    staleTime: 15000
  })
}

export function Panel({ ctx, snap, draft, setDraft, error, setError }) {
  const client = useQueryClient()
  const bar = useRef(null)
  const timer = useRef(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const sum = summarize(snap, draft)
  const order = [ANT, GPT, GRK].filter((p) => snap.pools.some((x) => x.id === p))
  const grk = order.includes(GRK) ? (sum.traffic[GRK] || 0) : 0

  const put = useCallback(async (body) => {
    setBusy(true); setError(null)
    try {
      const next = await ctx.rest('/v1/split', { method: 'PUT', body, timeoutMs: 20000 })
      if (validSnapshot(next)) client.setQueryData(QUERY_KEY, next)
      setDraft(null)
    } catch (e) {
      setError(String(e?.message || e)); setDraft(null)
      client.invalidateQueries({ queryKey: QUERY_KEY })
    } finally { setBusy(false) }
  }, [ctx, client, setDraft, setError])

  const commit = useCallback((share) => {
    const body = { mode: 'manual', share: Object.fromEntries(order.map((p) => [p, Math.round((share[p] || 0) * 10) / 10])) }
    return put(body)
  }, [put, order.join()])

  const at = (clientX) => {
    const r = bar.current?.getBoundingClientRect()
    if (!r || !r.width) return null
    return splitAt(((clientX - r.left) / r.width) * 100, grk)
  }
  const onPointerDown = (e) => {
    if (e.button !== 0) return
    e.preventDefault(); e.stopPropagation()
    const s = at(e.clientX); if (!s) return
    setDraft(s); setDragging(true)
    e.currentTarget.setPointerCapture?.(e.pointerId)
  }
  const onPointerMove = (e) => { if (dragging) { const s = at(e.clientX); if (s) setDraft(s) } }
  const onPointerUp = (e) => {
    if (!dragging) return
    setDragging(false)
    const s = at(e.clientX) || draft
    if (s) { setDraft(s); commit(s) }
  }
  const onKeyDown = (e) => {
    const step = e.shiftKey ? 10 : 1
    const d = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? step : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -step : 0
    if (!d && e.key !== 'Home' && e.key !== 'End') return
    e.preventDefault(); e.stopPropagation()
    const cur = sum.traffic[ANT] || 0
    const s = splitAt(e.key === 'Home' ? 0 : e.key === 'End' ? 100 : cur + d, grk)
    setDraft(s)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => commit(s), KEY_COMMIT_MS)
  }
  useEffect(() => () => clearTimeout(timer.current), [])

  const accounts = snap.quota.accounts || {}
  const rows = order.map((p) => {
    const a = accounts[p]
    const label = snap.pools.find((x) => x.id === p)?.label || p
    return h('tr', { key: p },
      h('td', null, h('span', { className: 'llms-sw', style: { background: COLORS[p] } }), label),
      h('td', null, a ? `${a.n - a.out}/${a.n} ready` : ''),
      h('td', { className: 'r' }, sum.hasQuota ? `quota ${pct(sum.quota[p])}%` : ''),
      h('td', { className: 'r' }, `traffic ${pct(sum.traffic[p])}%`))
  })
  const knobLeft = sum.traffic[ANT] || 0
  return h('div', { className: `llms-panel${dragging ? ' drag' : ''}`, role: 'group', 'aria-label': 'LLM split' },
    h('table', null, h('tbody', null, rows)),
    h('div', { className: 'llms-row' }, h('div', { className: 'llms-lab' }, 'Quota'),
      sum.hasQuota
        ? h('div', { className: 'llms-bar q', 'data-testid': 'quota-bar' }, h(Segments, { share: sum.quota, order, labels: true }))
        : h('div', { className: 'llms-empty' }, 'no reading')),
    h('div', { className: 'llms-row' }, h('div', { className: 'llms-lab' }, 'Traffic'),
      h('div', { className: 'llms-tr' },
        h('div', { ref: bar, className: 'llms-bar t', 'data-testid': 'traffic-bar', onPointerDown, onPointerMove, onPointerUp,
          onPointerCancel: () => { setDragging(false); setDraft(null) } }, h(Segments, { share: sum.traffic, order, labels: true })),
        sum.hasQuota && h('i', { className: 'llms-qtick', style: { left: `${sum.quota[ANT] || 0}%` }, title: 'Claude quota share' }),
        h('div', { className: 'llms-knob', tabIndex: 0, role: 'slider', 'aria-label': 'Claude share of traffic',
          'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': pct(knobLeft), 'aria-valuetext': `Claude ${pct(knobLeft)}%`,
          style: { left: `${knobLeft}%` }, onKeyDown, onPointerDown }))),
    h('div', { className: 'llms-foot' },
      h('div', { className: 'llms-seg', role: 'radiogroup', 'aria-label': 'Mode' },
        h('button', { type: 'button', role: 'radio', 'aria-checked': sum.mode === 'auto', className: sum.mode === 'auto' ? 'on' : '',
          disabled: busy || !sum.hasQuota, title: sum.hasQuota ? 'Weights follow quota' : 'No quota reading to follow',
          onClick: (e) => { e.stopPropagation(); put({ mode: 'auto' }) } }, 'Auto'),
        h('button', { type: 'button', role: 'radio', 'aria-checked': sum.mode !== 'auto', className: sum.mode !== 'auto' ? 'on' : '',
          disabled: busy, title: 'Freeze the split where it is now',
          onClick: (e) => { e.stopPropagation(); if (sum.mode === 'auto') commit(sum.traffic) } }, 'Manual')),
      h('div', { className: 'llms-note' }, error ? h('span', { className: 'bad' }, error) : busy ? 'Saving…' : noteFor(sum))),
    h('div', { className: 'llms-src' },
      `quota: ${snap.quota.source || 'none'}${snap.quota.at ? ` · ${ago(snap.quota.at)}` : ''} · weights ${order.map((p) => snap.traffic.weights?.[p] ?? '–').join(':')}`
      + (snap.mixed_homes?.length ? ' · profiles differ' : '')))
}

// The chip lives inside the titlebar's own <button>: a mouse click reaches the Radix trigger (the span); Enter/Space
// on the focused button arrive as a click with detail 0 and toggle through TOGGLE_EVENT instead.
export const TOGGLE_EVENT = 'hermes-toggle-llm-split'

export function Chip({ ctx }) {
  const q = useSplit(ctx)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    const toggle = () => setOpen((o) => !o)
    window.addEventListener(TOGGLE_EVENT, toggle)
    return () => window.removeEventListener(TOGGLE_EVENT, toggle)
  }, [])
  const snap = q.data
  if (!snap) {
    return h('span', { className: 'llms-chip dim', 'data-testid': 'llm-split-chip',
      'data-state-text': q.error ? `unavailable: ${q.error.message || q.error}` : 'loading' },
      h('span', { className: 'llms-mini' }), h('span', { className: 'llms-num' }, '–:–'),
      q.error && h('span', { className: 'llms-word bad' }, 'off'))
  }
  const sum = summarize(snap, draft)
  const order = [ANT, GPT, GRK].filter((p) => snap.pools.some((x) => x.id === p))
  // Events inside the portaled popover still bubble through React's tree to the titlebar button: stop them there.
  const stop = (e) => e.stopPropagation()
  return h(Popover, { open, onOpenChange: (o) => { setOpen(o); if (!o) { setDraft(null); setError(null) } } },
    h(PopoverTrigger, { asChild: true },
      h('span', { className: 'llms-chip', 'data-testid': 'llm-split-chip', 'aria-label': titleFor(sum) },
        h('span', { className: 'llms-mini' }, h(Segments, { share: sum.traffic, order }),
          sum.hasQuota && h('i', { className: 'llms-qm', style: { left: `${sum.quota[ANT] || 0}%` } })),
        h('span', { className: 'llms-num' }, `${pct(sum.traffic[ANT])}:${pct(sum.traffic[GPT])}`),
        h('span', { className: `llms-word ${sum.tone}` }, sum.word))),
    h(PopoverContent, { align: 'end', sideOffset: 6, className: 'llms-pop',
      onClick: stop, onPointerDown: stop, onPointerMove: stop, onPointerUp: stop,
      onKeyDown: (e) => { if (e.key !== 'Escape') e.stopPropagation() },
      onOpenAutoFocus: (e) => e.preventDefault() },
      h(Panel, { ctx, snap, draft, setDraft, error, setError })))
}

export const CSS = `
.llms-chip{display:inline-flex;align-items:center;justify-content:center;gap:7px;width:100%;height:100%;padding:0 6px;user-select:none;white-space:nowrap}
.llms-chip.dim{opacity:.55}
.llms-mini{position:relative;display:flex;width:84px;height:8px;border-radius:2px;overflow:hidden;background:var(--ui-stroke-tertiary,rgba(127,127,127,.25))}
.llms-mini>span{height:100%}
.llms-mini>span+span{box-shadow:inset 1px 0 0 var(--ui-chat-surface-background,#141517)}
.llms-qm{position:absolute;top:-2px;width:1.5px;height:12px;margin-left:-.75px;background:var(--foreground);opacity:.9}
.llms-num{font:500 10.5px/1 var(--font-mono,ui-monospace,monospace);color:var(--foreground);font-variant-numeric:tabular-nums}
.llms-word{font-size:10.5px;line-height:1;color:var(--muted-foreground)}
.llms-word.ok{color:${OK}} .llms-word.warn{color:${TIGHT}} .llms-word.bad{color:${DRY}}
.llms-pop{width:340px;padding:12px 12px 10px}
.llms-panel table{width:100%;border-collapse:collapse;font:10.5px/1.2 var(--font-mono,ui-monospace,monospace);margin:0 0 10px}
.llms-panel td{padding:3px 0;color:var(--muted-foreground)} .llms-panel td:first-child{color:var(--foreground)} .llms-panel td.r{text-align:right;padding-left:8px}
.llms-sw{display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:6px}
.llms-row{display:grid;grid-template-columns:52px 1fr;align-items:center;gap:8px;margin:0 0 10px}
.llms-lab{font:10px/1.2 var(--font-mono,ui-monospace,monospace);color:var(--muted-foreground);text-transform:uppercase;letter-spacing:.06em}
.llms-empty{font-size:11px;color:var(--muted-foreground)}
.llms-bar{position:relative;height:16px;display:flex;border-radius:3px;overflow:hidden}
.llms-bar>span{height:100%;display:flex;align-items:center;padding-left:5px;font:600 9.5px/1 var(--font-mono,ui-monospace,monospace);color:rgba(0,0,0,.72);white-space:nowrap;overflow:hidden;transition:width .25s}
.llms-bar>span+span{box-shadow:inset 1px 0 0 var(--popover,#17181b)}
.llms-bar.q>span{opacity:.55}
.llms-tr{position:relative}
.llms-bar.t{cursor:ew-resize;touch-action:none}
.llms-qtick{position:absolute;top:-3px;width:0;height:22px;margin-left:-.5px;border-left:1px dashed var(--foreground);opacity:.6;pointer-events:none}
.llms-knob{position:absolute;top:-3px;width:8px;height:22px;margin-left:-4px;border-radius:3px;background:var(--foreground);box-shadow:0 0 0 2px var(--popover,#17181b);cursor:ew-resize;transition:left .25s;touch-action:none}
.llms-knob:focus-visible{outline:2px solid ${OK};outline-offset:2px}
.llms-panel.drag .llms-knob,.llms-panel.drag .llms-bar>span{transition:none}
.llms-foot{display:flex;align-items:center;gap:10px;justify-content:space-between;border-top:1px solid var(--border);padding-top:8px;margin-top:2px}
.llms-seg{display:flex;border:1px solid var(--border);border-radius:4px;overflow:hidden;flex:none}
.llms-seg button{font-size:11px;color:var(--muted-foreground);background:transparent;border:0;padding:3px 10px;cursor:pointer}
.llms-seg button.on{background:var(--ui-control-hover-background,rgba(255,255,255,.09));color:var(--foreground)}
.llms-seg button:disabled{cursor:default;opacity:.6}
.llms-note{font-size:11px;color:var(--muted-foreground);text-align:right}
.llms-note .bad{color:${DRY}}
.llms-src{margin-top:6px;font:10px/1.2 var(--font-mono,ui-monospace,monospace);color:var(--muted-foreground);opacity:.75}
@media (prefers-reduced-motion:reduce){.llms-bar>span,.llms-knob{transition:none}}
`

export function tool(ctx) {
  return {
    id: 'llm-split',
    label: 'LLM split',
    title: 'Claude ⇄ GPT: traffic split vs quota. Click to adjust',
    span: CHIP_SPAN, // titlebar cells this tool takes (cores without `span` reserve one)
    className: 'llms-tool',
    icon: h(Chip, { ctx }),
    onSelect: (event) => { if (!event || event.detail === 0) window.dispatchEvent(new window.Event(TOGGLE_EVENT)) }
  }
}

export default {
  id: 'llm-split',
  name: 'LLM split',
  description: 'Titlebar chip: Claude/GPT quota share vs traffic split; drag to set it, or let it follow quota.',
  register(ctx) {
    const style = document.createElement('style')
    style.textContent = CSS + `.llms-tool{width:calc(${CHIP_SPAN}*var(--titlebar-control-size,24px))!important;padding:0!important}`
    document.head.append(style)
    ctx.onDispose(() => style.remove())
    ctx.register({ id: 'chip', area: 'titleBar.tools.right', order: 50, data: tool(ctx) })
  }
}
