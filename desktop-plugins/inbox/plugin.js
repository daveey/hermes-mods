/**
 * Hermes Inbox — a left-pane section (above Blocked) listing proposals of work
 * Hermes posted for you, plus a "Do it / Archive" bar above the composer when
 * the open chat is one of them.
 *
 * Each message is a real session (hidden from Recents) whose first message is
 * the proposal; see plugins/inbox/store.py. Data: GET /api/plugins/inbox/items
 * on EVERY gateway (`ctx.rest(path, { connectionId })`), polled every 30 s while
 * visible and after each action. Opening a row opens that session on its own
 * gateway (owner route), which marks it read. Do it = POST …/accept (the session
 * moves to Recents) then submit the proposal's prompt into the chat. Typing a
 * reply accepts it too (agent-side hook). Archive = POST …/archive.
 * ⌘⇧A on a message archives it and opens the next message; on the last one it
 * steps to the next session like core's ⌘⇧A (ctx.registerArchiveHotkeyHandler).
 * An empty inbox shows one muted "Nothing to review" row.
 *
 * Card types (gateway api 3): an item carries its type's `actions`; the bar
 * renders them (primary first, Archive last) and runs them via POST …/act
 * {action, input}. `prompt` actions submit the returned prompt into the chat
 * (like Do it); `resolve` actions close the card and open the next message.
 * Actions with `input` open an inline editor (prefilled from input.field,
 * char counter vs max, Esc cancels, ⌘Enter saves); `confirm` asks first;
 * `row: true` actions (without input) also show as row-hover buttons. Items
 * from older gateways (no `actions`) keep Do it / Archive.
 */
import {
  atom,
  COMPOSER_AREAS,
  host,
  SIDEBAR_SECTIONS_AREA,
  SidebarSection,
  SidebarSectionRow,
  useValue
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'inbox'
const POLL_MS = 30_000
const FIRST = 5
const STEP = 10
const LOCAL = 'local'

const str = v => (typeof v === 'string' ? v : v == null ? '' : String(v))
const log = (...a) => console.info(`[${ID}]`, ...a)

// ── pure helpers (exported for tests) ───────────────────────────────────────

export function ago(sec, nowMs = Date.now()) {
  const t = Number(sec)
  if (!t) return ''
  const s = Math.max(0, Math.floor(nowMs / 1000 - t))
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

/** Short poster label: "cron · Vast idle audit" → "cron". */
export function posterShort(poster) {
  return str(poster).split('·')[0].trim()
}

/** Flatten per-gateway answers into one list: unread first, then newest. */
export function mergeItems(answers) {
  const seen = new Set()
  const out = []
  for (const { gateway, profile, items } of answers) {
    for (const item of Array.isArray(items) ? items : []) {
      const sid = str(item && item.session_id)
      const key = `${gateway}:${sid}`
      if (!sid || seen.has(key)) continue
      seen.add(key)
      out.push({ ...item, session_id: sid, gateway, profile: item.profile || profile || 'default' })
    }
  }
  return out.sort((a, b) => Number(!!b.unread) - Number(!!a.unread) || Number(b.updated_at) - Number(a.updated_at))
}

/** Owner route for an item on another gateway; null = the routed gateway. */
export function routeFor(item, routed, gateways) {
  const profile = item.profile || 'default'
  if ((!item.gateway || item.gateway === routed) && profile === 'default') return null
  const kind = (gateways || []).find(g => g.id === item.gateway)?.kind
  return { connectionId: item.gateway, mode: kind === 'local' ? 'local' : 'remote', profile, targetProfile: profile }
}

export function restOpts(item, routed, extra = {}) {
  return item.gateway && item.gateway !== routed ? { ...extra, connectionId: item.gateway } : extra
}

/** REST path for an item action on its own profile (the gateway's root dashboard serves all profiles). */
export function itemPath(item, action) {
  const p = item.profile && item.profile !== 'default' ? `?profile=${encodeURIComponent(item.profile)}` : ''
  return `/items/${encodeURIComponent(item.session_id)}/${action}${p}`
}

/** Core session path, scoped to the item's profile. */
export function sessionPath(item) {
  const p = item.profile && item.profile !== 'default' ? `?profile=${encodeURIComponent(item.profile)}` : ''
  return `/api/sessions/${encodeURIComponent(item.session_id)}${p}`
}

export function itemFor(items, sessionId) {
  if (!sessionId) return null
  return items.find(i => i.session_id === sessionId) || null
}

/** The message ⌘⇧A opens after archiving `item`: the one below it, else the
 *  top of what's left; null when it was the last one (core steps on). */
export function nextItemAfter(items, item) {
  const rest = items.filter(i => i !== item)
  if (!rest.length) return null
  const at = items.indexOf(item)
  return (at >= 0 && items[at + 1]) || rest[0]
}

const ARCHIVE = { id: 'archive', label: 'Archive', icon: 'archive', kind: 'archive' }
const LEGACY_DO_IT = { id: 'do_it', label: 'Do it', icon: 'play', style: 'primary', kind: 'accept' }

/** The bar's buttons for an item: the type's actions (primary first), then Archive.
 *  Items from gateways without card types (no `actions`) get Do it / Archive. */
export function barActions(item) {
  const acts = Array.isArray(item && item.actions) ? item.actions.filter(a => a && a.id && a.id !== 'archive') : null
  if (!acts) return [LEGACY_DO_IT, ARCHIVE]
  const primary = acts.filter(a => a.style === 'primary')
  return [...primary, ...acts.filter(a => a.style !== 'primary'), ARCHIVE]
}

/** Row-hover buttons besides Archive: `row: true` actions that need no input. */
export function rowActions(item) {
  return Array.isArray(item && item.actions)
    ? item.actions.filter(a => a && a.row && (a.kind === 'resolve' || a.kind === 'task') && !a.input && a.id !== 'archive')
    : []
}

/** Initial editor text for an input action: the field it edits, else empty. */
export function inputDefault(item, action) {
  const f = action && action.input && action.input.field
  return f ? str(item && item.fields && item.fields[f]) : ''
}

/** Length in code points (what the server's len() counts; emoji are 1, not 2). */
export const charCount = v => [...str(v)].length

export function counterText(value, max) {
  const n = charCount(value)
  return max ? `${n}/${max}` : `${n}`
}

// ── store ───────────────────────────────────────────────────────────────────

const routedGateway = () => {
  try {
    return host.activeConnectionId?.() || LOCAL
  } catch {
    return LOCAL
  }
}

function gatewayIds() {
  const routed = routedGateway()
  const ids = (host.state.gateways?.get?.() ?? []).map(g => g.id)
  return ids.length < 2 ? [routed] : [routed, ...ids.filter(id => id !== routed)]
}

export function createStore(ctx) {
  const $items = atom([])
  const $open = atom(ctx.storage?.get?.('open', true) ?? true)
  const $busy = atom('')
  let inflight = null
  let again = false

  const refresh = () => {
    if (inflight) {
      again = true
      return inflight
    }
    const routed = routedGateway()
    inflight = Promise.all(
      gatewayIds().map(gateway =>
        ctx
          .rest('/items', gateway === routed ? { timeoutMs: 8000 } : { connectionId: gateway, timeoutMs: 8000 })
          .then(r => ({ gateway, profile: r && r.profile, items: r && r.items }))
          .catch(() => ({ gateway, items: [] }))
      )
    )
      .then(answers => {
        $items.set(mergeItems(answers))
        markRead(itemFor($items.get(), host.state.focusedStoredSessionId?.get?.()))
      })
      .finally(() => {
        inflight = null
        if (again) {
          again = false
          void refresh()
        }
      })
    return inflight
  }

  // Desktop's own read state is client-side only, so the inbox keeps the
  // server watermark itself (any door that focuses the chat counts).
  const markRead = item => {
    if (!item || !item.unread) return
    $items.set($items.get().map(i => (i === item ? { ...i, unread: false } : i)))
    // Core's session PATCH (`unread: false` → SessionDB.set_session_read) on the owning gateway.
    const api = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
    if (!api) return
    const via = item.gateway && item.gateway !== routedGateway() ? { connectionId: item.gateway } : {}
    void Promise.resolve(
      api({ ...via, path: sessionPath(item), method: 'PATCH', body: { unread: false } })
    ).catch(() => {})
  }

  const open = item => {
    const route = routeFor(item, routedGateway(), host.state.gateways?.get?.() ?? [])
    markRead(item)
    void host.openSession(item.session_id, route ? { route } : {}).catch(e => host.notifyError?.(e, 'Could not open inbox message'))
  }

  const archive = async item => {
    $busy.set(item.session_id)
    $items.set($items.get().filter(i => i !== item))
    try {
      await ctx.rest(itemPath(item, 'archive'), restOpts(item, routedGateway(), { method: 'POST' }))
    } catch (e) {
      host.notifyError?.(e, 'Could not archive inbox message')
    } finally {
      $busy.set('')
      void refresh()
    }
  }

  const accept = async item => {
    $busy.set(item.session_id)
    try {
      const r = await ctx.rest(itemPath(item, 'accept'), restOpts(item, routedGateway(), { method: 'POST' }))
      const prompt = str((r && r.accept_prompt) || item.accept_prompt)
      $items.set($items.get().filter(i => i !== item))
      await submitPrompt(item, prompt)
    } catch (e) {
      host.notifyError?.(e, 'Could not start inbox proposal')
    } finally {
      $busy.set('')
      void refresh()
    }
  }

  const submitPrompt = async (item, prompt) => {
    if (!prompt) return
    const sent = prompt && host.composer?.submit?.(item.session_id, prompt)
    if (!sent) {
      await host.composer?.setDraft?.(item.session_id, prompt)
      host.notify?.({ kind: 'info', message: 'Proposal placed in the composer — press Enter to run it.' })
    }
  }

  const confirmFn = msg => {
    const c = typeof globalThis.confirm === 'function' ? globalThis.confirm : null
    return c ? c(msg) : true
  }

  // Run one of the item's card-type actions. Returns false when cancelled.
  const act = async (item, action, input) => {
    if (!action || action.kind === 'archive') return archive(item).then(() => true)
    if (action.kind === 'accept') return accept(item).then(() => true)
    if (action.confirm && !confirmFn(str(action.confirm))) return false
    const items = $items.get()
    const next = nextItemAfter(items, item)
    const wasFocused = host.state.focusedStoredSessionId?.get?.() === item.session_id
    $busy.set(item.session_id)
    try {
      const body = input == null ? { action: action.id } : { action: action.id, input: str(input) }
      const r = await ctx.rest(itemPath(item, 'act'), restOpts(item, routedGateway(), { method: 'POST', body }))
      $items.set($items.get().filter(i => i !== item))
      if (action.kind === 'prompt') await submitPrompt(item, str(r && r.prompt))
      else if (wasFocused && next) open(next)
      if (action.kind === 'task' && r && r.outcome_task) host.notify?.({ kind: 'success', message: `Queued ${str(r.outcome_task)}` })
      return true
    } catch (e) {
      host.notifyError?.(e, `Could not ${str(action.label || action.id).toLowerCase()} inbox message`)
      return false
    } finally {
      $busy.set('')
      void refresh()
    }
  }

  // ⌘⇧A on an inbox message (core `session.archive` hotkey → handler): archive
  // it through the inbox (owner gateway/profile) and open the next message;
  // on the last one, hand back to core's own next-session step.
  const archiveHotkey = ({ sessionId, isFocused, openNextSession }) => {
    const items = $items.get()
    const item = itemFor(items, sessionId)
    if (!item) return false
    const next = nextItemAfter(items, item)
    void archive(item)
    if (isFocused) {
      if (next) open(next)
      else openNextSession?.()
    }
    return true
  }

  const setOpen = v => {
    $open.set(v)
    ctx.storage?.set?.('open', v)
  }

  return { $items, $open, $busy, refresh, open, markRead, archive, archiveHotkey, accept, act, setOpen }
}

// ── UI ──────────────────────────────────────────────────────────────────────

function ArchiveButton({ onClick }) {
  return jsx('button', {
    type: 'button',
    'aria-label': 'Archive',
    title: 'Archive',
    className: 'grid size-5 place-items-center rounded text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
    onClick: e => {
      e.stopPropagation()
      onClick()
    },
    children: jsx('span', { className: 'codicon codicon-archive', style: { fontSize: '0.75rem' }, 'aria-hidden': true })
  })
}

function RowActionButton({ action, onClick }) {
  return jsx('button', {
    type: 'button',
    'aria-label': action.label,
    title: action.label,
    'data-inbox-action': action.id,
    className: 'grid size-5 place-items-center rounded text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
    onClick: e => {
      e.stopPropagation()
      onClick()
    },
    children: jsx('span', { className: `codicon codicon-${action.icon || 'check'}`, style: { fontSize: '0.75rem' }, 'aria-hidden': true })
  })
}

export function RowActions({ store, item }) {
  const extra = rowActions(item)
  const archive = jsx(ArchiveButton, { onClick: () => void store.archive(item) })
  if (!extra.length) return archive
  return jsxs('span', {
    className: 'flex items-center gap-0.5',
    children: [
      ...extra.map(a => jsx(RowActionButton, { action: a, onClick: () => void store.act(item, a) }, a.id)),
      archive
    ]
  })
}

function Section({ store }) {
  const items = useValue(store.$items)
  const open = useValue(store.$open)
  const focused = useValue(host.state.focusedStoredSessionId)
  const [visible, setVisible] = useState(FIRST)
  if (!items.length) {
    return jsx(SidebarSection, {
      label: 'Inbox',
      open,
      onToggle: () => store.setOpen(!open),
      children: jsx(SidebarSectionRow, {
        label: 'Nothing to review',
        muted: true,
        icon: 'inbox',
        tip: 'Hermes posts proposals of work here (inbox_post)',
        onSelect: () => void store.refresh()
      })
    })
  }
  const unread = items.filter(i => i.unread).length
  return jsx(SidebarSection, {
    label: 'Inbox',
    count: unread || items.length,
    open,
    onToggle: () => store.setOpen(!open),
    hiddenCount: Math.max(0, items.length - visible),
    loadMoreStep: STEP,
    onLoadMore: () => setVisible(n => n + STEP),
    children: items.slice(0, visible).map(item => {
      const who = posterShort(item.poster)
      return jsx(
        SidebarSectionRow,
        {
          label: item.title || 'Untitled proposal',
          tip: [item.title, item.poster && `from ${item.poster}`].filter(Boolean).join(' — '),
          accentColor: item.unread ? 'var(--accent)' : 'var(--ui-text-tertiary)',
          strong: !!item.unread,
          active: focused === item.session_id,
          meta: [ago(item.updated_at), who].filter(Boolean).join(' · '),
          actions: jsx(RowActions, { store, item }),
          onSelect: () => store.open(item)
        },
        `${item.gateway}:${item.session_id}`
      )
    })
  })
}

const BTN =
  'inline-flex h-6 items-center gap-1 rounded-md border border-(--border) px-2 text-[0.75rem] transition-colors hover:bg-(--chrome-action-hover) disabled:opacity-50'

const ACCENT = { background: 'var(--accent)', color: 'var(--accent-foreground, white)', borderColor: 'var(--accent)' }
const DANGER = { color: 'var(--destructive, #e5484d)', borderColor: 'var(--destructive, #e5484d)' }

function ActionButton({ action, disabled, title, onClick }) {
  return jsxs('button', {
    type: 'button',
    className: BTN,
    'data-inbox-action': action.id,
    style: action.style === 'primary' ? ACCENT : action.style === 'danger' ? DANGER : undefined,
    disabled,
    title: title || action.label,
    onClick,
    children: [
      action.icon ? jsx('span', { className: `codicon codicon-${action.icon}`, 'aria-hidden': true }) : null,
      action.label
    ]
  })
}

/** Inline editor for an action with `input` (controlled; Bar owns the text). */
export function Editor({ item, action, value, disabled, onChange, onSave, onCancel }) {
  const spec = action.input || {}
  const max = spec.max || 0
  const over = max && charCount(value) > max
  const empty = spec.required !== false && !str(value).trim()
  const save = () => {
    if (!over && !empty && !disabled) onSave(str(value))
  }
  const onKeyDown = e => {
    if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
    } else if (e.key === 'Enter' && !e.isComposing && (e.metaKey || e.ctrlKey || !spec.multiline)) {
      e.preventDefault()
      save()
    }
  }
  const field = spec.multiline
    ? jsx('textarea', {
        autoFocus: true,
        rows: 4,
        value: str(value),
        placeholder: spec.placeholder || '',
        'data-slot': 'inbox-editor-input',
        className: 'w-full resize-y rounded-md border border-(--border) bg-transparent px-2 py-1 text-[0.8125rem] leading-snug outline-none focus:border-(--accent)',
        onChange: e => onChange(e.target.value),
        onKeyDown
      })
    : jsx('input', {
        autoFocus: true,
        type: 'text',
        value: str(value),
        placeholder: spec.placeholder || '',
        'data-slot': 'inbox-editor-input',
        className: 'h-6 min-w-0 flex-1 rounded-md border border-(--border) bg-transparent px-2 text-[0.8125rem] outline-none focus:border-(--accent)',
        onChange: e => onChange(e.target.value),
        onKeyDown
      })
  return jsxs('div', {
    'data-slot': 'inbox-editor',
    className: 'flex w-full flex-col gap-1 px-2 pb-1.5 pt-1 text-[0.75rem]',
    children: [
      jsx('span', { className: 'text-(--ui-text-tertiary)', children: `${action.label}${item.type_label ? ` · ${item.type_label}` : ''}` }),
      field,
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('span', {
            'data-slot': 'inbox-editor-count',
            className: 'flex-1 tabular-nums',
            style: { color: over ? 'var(--destructive, #e5484d)' : 'var(--ui-text-tertiary)' },
            children: counterText(value, max)
          }),
          jsx('span', { className: 'text-(--ui-text-tertiary)', children: spec.multiline ? '⌘↵ save · esc cancel' : '↵ save · esc cancel' }),
          jsx(ActionButton, { action: { id: 'cancel', label: 'Cancel' }, onClick: onCancel }),
          jsx(ActionButton, {
            action: { id: 'save', label: 'Save', style: 'primary', icon: 'check' },
            disabled: disabled || over || empty,
            onClick: save
          })
        ]
      })
    ]
  })
}

export function Bar({ store }) {
  const items = useValue(store.$items)
  const busy = useValue(store.$busy)
  const focused = useValue(host.state.focusedStoredSessionId)
  const [editing, setEditing] = useState(null)
  const item = itemFor(items, focused)
  if (!item) return null
  const disabled = busy === item.session_id
  const edit = editing && editing.sid === item.session_id ? editing : null
  if (edit) {
    return jsx(Editor, {
      item,
      action: edit.action,
      value: edit.value,
      disabled,
      onChange: value => setEditing({ ...edit, value }),
      onCancel: () => setEditing(null),
      onSave: value =>
        void store.act(item, edit.action, value).then(ok => {
          if (ok) setEditing(null)
        })
    })
  }
  const run = action => {
    if (action.input) setEditing({ sid: item.session_id, action, value: inputDefault(item, action) })
    else void store.act(item, action)
  }
  const label = item.type_label && item.type !== 'default' ? item.type_label : 'Inbox proposal'
  return jsxs('div', {
    'data-slot': 'inbox-bar',
    className: 'flex w-full items-center gap-2 px-2 pb-1.5 pt-1 text-[0.75rem]',
    children: [
      jsx('span', { className: 'codicon codicon-inbox', style: { color: 'var(--accent)' }, 'aria-hidden': true }),
      jsx('span', {
        className: 'min-w-0 flex-1 truncate text-(--ui-text-tertiary)',
        title: item.accept_prompt,
        children: `${label}${item.poster ? ` from ${item.poster}` : ''}`
      }),
      ...barActions(item).map(action =>
        jsx(ActionButton, {
          action,
          disabled,
          // Prompt actions show what they will send (the template), so nothing runs unseen.
          title: action.kind === 'accept' || action.id === 'do_it' ? item.accept_prompt : action.kind === 'prompt' || action.kind === 'task' ? str(action.prompt) || action.label : action.label,
          onClick: () => run(action)
        }, action.id)
      )
    ]
  })
}

export default {
  id: ID,
  name: 'Inbox',
  description: 'Left-pane Inbox of work Hermes proposes: read, Do it, or archive.',
  register(ctx) {
    const store = createStore(ctx)
    if (typeof window !== 'undefined') window.__inbox = { store, refresh: store.refresh }
    void store.refresh()
    ctx.setInterval(() => {
      if (typeof document === 'undefined' || !document.hidden) void store.refresh()
    }, POLL_MS)
    // Focusing a message (from any door) marks it read.
    const unsub = host.state.focusedStoredSessionId?.listen?.(sid => store.markRead(itemFor(store.$items.get(), sid)))
    if (unsub) ctx.onDispose?.(unsub)
    // ⌘⇧A: archive + next inbox message (fork core; older cores archive via core).
    ctx.registerArchiveHotkeyHandler?.(store.archiveHotkey)
    ctx.register({ id: 'section', area: SIDEBAR_SECTIONS_AREA, order: 5, render: () => jsx(Section, { store }) })
    ctx.register({ id: 'bar', area: COMPOSER_AREAS.top, order: -30, render: () => jsx(Bar, { store }) })
    log('registered')
  }
}
