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
 * Offline gateways: open cards of a gateway whose live /items read failed (or
 * that this Desktop has no connection for) come from the routed gateway's
 * hermes-hub replica (GET /api/plugins/hub-sync/inbox), greyed with "<gw> ·
 * synced Xm ago". Their resolve/task actions and Archive become hub owner
 * requests (POST /api/plugins/hub-sync/request {coll:'inbox', action:'act'|
 * 'archive', base_version}); the card stays, marked pending, until the owner
 * applies it. Prompt / Do it actions need the chat and are not offered there.
 *
 * Card types (gateway api 3): an item carries its type's `actions`; the bar
 * renders them (primary first, Archive last) and runs them via POST …/act
 * {action, input}. `prompt` actions submit the returned prompt into the chat
 * (like Do it); `resolve` actions close the card and open the next message.
 * Actions with `input` open an inline editor (prefilled from input.field,
 * char counter vs max, Esc cancels, ⌘Enter saves); `confirm` asks first;
 * `row: true` actions (without input) also show as row-hover buttons. Items
 * from older gateways (no `actions`) keep Do it / Archive.
 *
 * Keys: on an open card, ⌥1…⌥9 (Alt+1…9 off macOS) run the bar's buttons in
 * order (Archive stays ⌘⇧A); each button shows its hint. Matched on
 * `event.code` (⌥digit types ¡™£… on macOS), from the composer too, never
 * while the inline editor is open, during IME composition, or from another
 * editable outside the chat (dialogs, terminals). Archive / resolve / task on
 * the open card step to the next message, and on the last one to the session
 * core's ⌘⇧A would open (`ctx.openNextSession`, fork core; older cores open a
 * fresh draft).
 *
 * Social cards: an item whose fields carry `thread` (JSON turns) is posted as
 * `::social-card{session}`; the directive draws it as a chat (their posts
 * left, yours right, the answered post outlined, older turns folded), the
 * selected option as a dashed draft bubble, then the options to pick. Click or
 * a bare 1…9 (not in an editable) selects; the bar's per-option buttons
 * collapse into the selected one, and Edit opens prefilled with it.
 */
import {
  atom,
  COMPOSER_AREAS,
  host,
  SIDEBAR_SECTIONS_AREA,
  SidebarSection,
  SidebarSectionRow,
  TRANSCRIPT_DIRECTIVE_AREA,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
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
// Owner reports a card as noise: archive + mute its poster family + file a five-whys Kanban card.
const JUNK = { id: 'junk', label: 'Junk (mute poster, file five-whys)', icon: 'trash', kind: 'junk' }
const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(str(navigator.platform || navigator.userAgent))
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

/** The buttons ⌥1…⌥9 run, in bar order: every bar action but Archive (⌘⇧A). */
export function keyActions(item, selected) {
  return socialBarActions(item, selected).filter(a => a.kind !== 'archive').slice(0, 9)
}

/** Key hint shown on a bar button; '' for buttons without one. */
export function keyHint(item, action, mac = IS_MAC, selected) {
  if (action.kind === 'archive') return mac ? '⌘⇧A' : 'Ctrl+Shift+A'
  const n = keyActions(item, selected).findIndex(a => a.id === action.id) + 1
  return n ? (mac ? `⌥${n}` : `Alt+${n}`) : ''
}

/** Digit 1–9 of a bare ⌥/Alt+digit chord (by physical key), else 0. */
export function actionKeyDigit(e) {
  if (!e || !e.altKey || e.metaKey || e.ctrlKey || e.shiftKey || e.repeat) return 0
  if (e.isComposing || e.key === 'Process' || e.keyCode === 229) return 0
  const m = /^Digit([1-9])$/.exec(str(e.code))  // not Numpad: Windows Alt-codes
  return m ? Number(m[1]) : 0
}

const isEditable = el =>
  !!el && (!!el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(str(el.tagName)))

/** Whether a keydown may run a card action: not already claimed, not typed
 *  into an editable outside the chat surface (the composer is inside it), not
 *  in a dialog or terminal. */
export function keyTargetAllowed(e) {
  if (!e || e.defaultPrevented) return false
  const t = e.target
  const closest = sel => (t && typeof t.closest === 'function' ? t.closest(sel) : null)
  if (closest('[role="dialog"], [role="alertdialog"], [data-terminal], [data-interactive-terminal], [data-slot="inbox-editor"]')) return false
  return !isEditable(t) || !!closest('[data-chat-surface]')
}

// What ⌥1…⌥9 type on a US Mac layout; Alt+digit elsewhere reports the digit.
const US_OPTION_DIGITS = '¡™£¢∞§¶•ª'

/** In an editable (the composer), claim ⌥n only when it would type the US
 *  Option character or the digit itself: on German/French/Nordic layouts ⌥5…⌥9
 *  type [ ] | { } @ ~, which must keep reaching the composer. */
export function keyCharAllowed(e, n) {
  if (!isEditable(e && e.target)) return true
  const k = str(e.key)
  return k === String(n) || k === US_OPTION_DIGITS[n - 1]
}

// The card bar is on screen (not on Settings or another non-chat route).
// Without a DOM (tests) there is nothing to check.
const barShown = () =>
  typeof document === 'undefined' || typeof document.querySelector !== 'function' || !!document.querySelector('[data-slot="inbox-bar"]')

/** Initial editor text for an input action: the field it edits, else empty. */
export function inputDefault(item, action, selected) {
  const f = action && action.input && action.input.field
  if (f) return str(item && item.fields && item.fields[f])
  // Edit & post on a social card starts from the selected option.
  const sel = action && action.input && socialOptions(item).length ? selectedOption(item, selected) : null
  return sel ? sel.text : ''
}

/** A bare 1–9 (no modifiers, not typed into an editable): select that option. */
export function optionKeyDigit(e) {
  if (!e || e.altKey || e.metaKey || e.ctrlKey || e.shiftKey || e.repeat || e.isComposing) return 0
  if (isEditable(e.target)) return 0
  const m = /^Digit([1-9])$/.exec(str(e.code))
  return m ? Number(m[1]) : 0
}

/** Length in code points (what the server's len() counts; emoji are 1, not 2). */
export const charCount = v => [...str(v)].length

export function counterText(value, max) {
  const n = charCount(value)
  return max ? `${n}/${max}` : `${n}`
}

// ── social cards (::social-card directive) ──────────────────────────────────
// Social cards (Bluesky/X replies, drafts, DMs) carry the conversation as a
// `thread` field (JSON turns, oldest first). The store posts `::social-card`
// for them and this plugin draws it as a chat: their posts left, yours right,
// the post being answered outlined, your draft as the next bubble, then the
// options to pick from. All text is untrusted and rendered as text only.

// Types whose `text` field is the (first) draft; elsewhere `text` is the incoming post.
const DRAFT_TEXT_TYPES = new Set(['bsky-reply-draft', 'x-reply-draft'])
const KEEP_SHOWN = 3

/** The card's turns, or null when it has no valid `thread` field. */
export function parseThread(item) {
  let turns
  try {
    turns = JSON.parse(str(item && item.fields && item.fields.thread))
  } catch {
    return null
  }
  if (!Array.isArray(turns) || !turns.length || !turns.every(t => t && typeof t === 'object' && typeof t.text === 'string')) return null
  return turns.map(t => ({ author: str(t.author), self: !!t.self, text: t.text, url: safeUrl(t.url), target: !!t.target }))
}

/** http(s) URLs only (thread data is untrusted). */
export function safeUrl(u) {
  const v = str(u).trim()
  return /^https?:\/\/[^\s"'<>]+$/i.test(v) ? v : ''
}

/** Earlier turns fold away once there are more than KEEP_SHOWN + 1. */
export function foldTurns(turns) {
  if (turns.length <= KEEP_SHOWN + 1) return { folded: [], shown: turns }
  return { folded: turns.slice(0, -KEEP_SHOWN), shown: turns.slice(-KEEP_SHOWN) }
}

/** The option number an action posts (`post` = 1, `post_2`/`choose_2`/`send_2` = 2), else 0. */
export function optionOfAction(action) {
  const id = str(action && action.id)
  if (id === 'post') return 1
  const m = /^(?:post|choose|send)_([1-9])$/.exec(id)
  return m ? Number(m[1]) : 0
}

/** [{n, text, angle, action}] for the card's reply options, in order. */
export function socialOptions(item) {
  const f = (item && item.fields) || {}
  const acts = Array.isArray(item && item.actions) ? item.actions : []
  const out = []
  for (let n = 1; n <= 9; n++) {
    let text = f[`option_${n}`]
    if (n === 1 && text == null && DRAFT_TEXT_TYPES.has(str(item && item.type))) text = f.text
    if (typeof text !== 'string' || !text.trim()) continue
    out.push({ n, text, angle: str(f[`option_${n}_angle`]), action: acts.find(a => optionOfAction(a) === n) || null })
  }
  return out
}

/** The selected option for a card (default the first). */
export function selectedOption(item, selected) {
  const opts = socialOptions(item)
  if (!opts.length) return null
  const want = selected && item ? selected[item.session_id] : undefined
  return opts.find(o => o.n === want) || opts[0]
}

/** Bar buttons with a selection: the per-option buttons collapse into the selected one (primary). */
export function socialBarActions(item, selected) {
  const base = barActions(item)
  const opts = socialOptions(item).filter(o => o.action)
  if (opts.length < 2) return base
  const sel = selectedOption(item, selected)
  const pick = sel && sel.action ? { ...sel.action, style: 'primary' } : null
  const rest = base.filter(a => !optionOfAction(a))
  return pick ? [pick, ...rest] : rest
}

/** Sources field: JSON [{label,url}] or "label: url" lines. */
export function parseSources(raw) {
  const v = str(raw).trim()
  if (!v) return []
  try {
    const j = JSON.parse(v)
    if (Array.isArray(j)) return j.filter(x => x && typeof x === 'object').map(x => ({ label: str(x.label || x.url), url: safeUrl(x.url) })).filter(x => x.label)
  } catch {}
  return v.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
    const m = /^(.*?):?\s*(https?:\/\/\S+)$/.exec(l)
    return m ? { label: m[1] || m[2], url: safeUrl(m[2]) } : { label: l, url: '' }
  })
}

const PLATFORM = t => (str(t).startsWith('bsky') ? 'Bluesky' : str(t).startsWith('x-') ? 'X' : '')

// ── hermes-hub replica (hub-sync plugin) ────────────────────────────────────
// Open cards of gateways whose live /items read failed (unreachable) or that
// this Desktop has no connection for, from the routed gateway's hub replica.
// Their actions become hub owner requests (queued locally while the hub is down).

const HUB_NAMES_KEY = 'hermes.desktop.hubGatewayNames.v1' // connection id -> hub gateway name (core store/hub-sync.ts)
const HUB_ACTION_KINDS = new Set(['resolve', 'task']) // prompt/accept need the chat on its own gateway

function storedHubNames() {
  try {
    return JSON.parse((typeof localStorage !== 'undefined' && localStorage.getItem(HUB_NAMES_KEY)) || '{}') || {}
  } catch {
    return {}
  }
}

export function isPending(item) {
  return Array.isArray(item && item.pending) && item.pending.some(p => p && (p.state === 'local' || p.state === 'queued'))
}

export function syncedAgo(sec, nowMs = Date.now()) {
  const a = ago(sec, nowMs)
  return !a ? 'never synced' : a === 'now' ? 'synced just now' : `synced ${a} ago`
}

/** Replica rows -> inbox items. `liveNames`: hub names of gateways answered live
 *  (their own list is primary); `connFor(name)`: registry connection id or null. */
export function hubInboxItems(rows, liveNames, connFor) {
  const out = []
  for (const r of Array.isArray(rows) ? rows : []) {
    if (!r || r.local || !r.session_id || !r.gateway || liveNames.has(r.gateway)) continue
    if (r.state !== 'open' && r.state !== 'queuing') continue
    out.push({
      ...r,
      gateway: connFor(r.gateway) || `hub:${r.gateway}`,
      profile: r.profile || 'default',
      unread: false,
      actions: (Array.isArray(r.actions) ? r.actions : []).filter(a => a && a.id && HUB_ACTION_KINDS.has(a.kind)),
      hub: { gateway: r.gateway, key: r.key, version: r.version ?? null, synced_at: r.synced_at ?? null }
    })
  }
  return out
}

/** Sidebar row meta: replica cards show their sync age (or pending). */
export function rowMeta(item, nowMs = Date.now()) {
  if (item && item.hub) return isPending(item) ? 'pending' : `${item.hub.gateway} · ${syncedAgo(item.hub.synced_at, nowMs)}`
  return [ago(item.updated_at, nowMs), posterShort(item.poster)].filter(Boolean).join(' · ')
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
  // The inline editor ({sid, action, value}); in the store so the action keys
  // can open it and stay off while it is open.
  const $editing = atom(null)
  // Social cards: selected option per session id, and items fetched for cards
  // that are no longer open (accepted/resolved chats still draw their card).
  const $selected = atom({})
  const $cards = atom({})
  const fetching = new Set()
  let inflight = null
  let again = false

  // hub-sync REST on the routed gateway (ctx.hubRest: test seam).
  const hubRest = (path, opts = {}) => {
    if (ctx.hubRest) return ctx.hubRest(path, opts)
    const api = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
    if (!api) return Promise.reject(new Error('Hermes desktop bridge unavailable'))
    return api({ path: `/api/plugins/hub-sync${path}`, timeoutMs: 6000, ...opts })
  }
  const safeHub = path => Promise.resolve().then(() => hubRest(path)).catch(() => null)
  const hubName = cid => (ctx.hubGatewayName ? ctx.hubGatewayName(cid) : storedHubNames()[cid]) || ''
  const connForHub = name => {
    const ids = new Set((host.state.gateways?.get?.() ?? []).map(g => g.id))
    ids.add(routedGateway())
    for (const id of ids) if (hubName(id) === name) return id
    return null
  }

  // Owner request for a replica card; the card stays, marked pending.
  const hubSend = async (item, action, args) => {
    $busy.set(item.session_id)
    try {
      const body = { gateway: item.hub.gateway, coll: 'inbox', key: item.hub.key, action, ...(args ? { args } : {}), base_version: item.hub.version }
      const r = await hubRest('/request', { method: 'POST', body })
      const mark = { id: str(r && r.id), action, state: str(r && r.state) || 'local' }
      $items.set($items.get().map(i => (i.hub && i.hub.key === item.hub.key && i.hub.gateway === item.hub.gateway ? { ...i, pending: [...(i.pending || []), mark] } : i)))
      return true
    } catch (e) {
      host.notifyError?.(e, `Could not queue the change for ${item.hub.gateway}`)
      return false
    } finally {
      $busy.set('')
    }
  }

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
          .catch(() => ({ gateway, items: [], failed: true }))
      )
    )
      .then(async answers => {
        const live = mergeItems(answers)
        const hub = await safeHub('/inbox')
        let extra = []
        if (hub && hub.available !== false && Array.isArray(hub.items)) {
          const liveNames = new Set(answers.filter(a => !a.failed).map(a => hubName(a.gateway)).filter(Boolean))
          if (hub.gateway) liveNames.add(hub.gateway)
          const seen = new Set(live.map(i => `${hubName(i.gateway) || i.gateway}:${i.session_id}`))
          extra = hubInboxItems(hub.items, liveNames, connForHub).filter(i => !seen.has(`${i.hub.gateway}:${i.session_id}`))
        }
        $items.set(extra.length ? [...live, ...extra] : live)
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
    if (!item || !item.unread || item.hub) return
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
    if (item.hub && str(item.gateway).startsWith('hub:')) {
      host.notify?.({ kind: 'info', message: `${item.hub.gateway} is not registered on this Desktop; acting on it through hermes-hub.` })
      return
    }
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

  // The open card just closed: the next message, else the session core's ⌘⇧A
  // would open (fork core), else a fresh draft (older cores).
  const leave = (item, next) => {
    if (next) return open(next)
    if (ctx.openNextSession?.(item.session_id)) return
    void ctx.runAction?.('session.new')
  }

  const confirmFn = msg => {
    const c = typeof globalThis.confirm === 'function' ? globalThis.confirm : null
    return c ? c(msg) : true
  }

  // Run one of the item's card-type actions. Returns false when cancelled.
  const act = async (item, action, input) => {
    if (action && action.kind === 'junk') {
      if (item && item.hub) return hubSend(item, 'junk')
      $busy.set(item.session_id)
      $items.set($items.get().filter(i => i !== item))
      try {
        await ctx.rest(itemPath(item, 'junk'), restOpts(item, routedGateway(), { method: 'POST' }))
        return true
      } catch (e) {
        host.notifyError?.(e, 'Could not mark inbox message as junk')
        return false
      } finally {
        $busy.set('')
        void refresh()
      }
    }
    if (item && item.hub) {
      if (!action || action.kind === 'archive') return hubSend(item, 'archive')
      if (!HUB_ACTION_KINDS.has(action.kind)) return false
      if (action.confirm && !confirmFn(str(action.confirm))) return false
      return hubSend(item, 'act', input == null ? { action: action.id } : { action: action.id, input: str(input) })
    }
    if (action && action.kind === 'accept') return accept(item).then(() => true)
    const next = nextItemAfter($items.get(), item)
    const wasFocused = host.state.focusedStoredSessionId?.get?.() === item.session_id
    if (!action || action.kind === 'archive') {
      const done = archive(item)
      if (wasFocused) leave(item, next)
      return done.then(() => true)
    }
    if (action.confirm && !confirmFn(str(action.confirm))) return false
    $busy.set(item.session_id)
    try {
      const body = input == null ? { action: action.id } : { action: action.id, input: str(input) }
      const r = await ctx.rest(itemPath(item, 'act'), restOpts(item, routedGateway(), { method: 'POST', body }))
      $items.set($items.get().filter(i => i !== item))
      // Only navigate if the card is still the open chat after the request
      // (the user may have moved on meanwhile); pick the next from now.
      const stillFocused = wasFocused && host.state.focusedStoredSessionId?.get?.() === item.session_id
      if (action.kind === 'prompt') await submitPrompt(item, str(r && r.prompt))
      else if (stillFocused) {
        const now = $items.get().filter(i => i.session_id !== item.session_id)
        leave(item, (next && now.find(i => i.session_id === next.session_id)) || now[0] || null)
      }
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
    void (item.hub ? hubSend(item, 'archive') : archive(item))
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

  // Run (or open the editor for) a bar button.
  const run = (item, action) => {
    if (action.input) $editing.set({ sid: item.session_id, action, value: inputDefault(item, action, $selected.get()) })
    else void store.act(item, action)
  }

  const select = (sid, n) => {
    if (!sid || !n) return
    $selected.set({ ...$selected.get(), [sid]: n })
  }

  // The item behind a ::social-card: the open list, else the fetched copy.
  const cardItem = sid => itemFor($items.get(), sid) || $cards.get()[sid] || null

  // Fetch a card that is not in the open list: routed gateway first, then the rest.
  const fetchCard = async sid => {
    if (!sid || fetching.has(sid) || cardItem(sid)) return cardItem(sid)
    fetching.add(sid)
    try {
      const routed = routedGateway()
      for (const gateway of gatewayIds()) {
        try {
          const r = await ctx.rest(`/items/${encodeURIComponent(sid)}`, gateway === routed ? { timeoutMs: 8000 } : { connectionId: gateway, timeoutMs: 8000 })
          if (r && r.session_id) {
            const item = { ...r, gateway, profile: r.profile || 'default' }
            $cards.set({ ...$cards.get(), [sid]: item })
            return item
          }
        } catch {}
      }
      $cards.set({ ...$cards.get(), [sid]: { session_id: sid, missing: true } })
      return null
    } finally {
      fetching.delete(sid)
    }
  }

  // ⌥1…⌥9 on the open card. Returns true when it ran something.
  const onKeyDown = e => {
    const d = optionKeyDigit(e)
    if (d && !e.defaultPrevented) {
      const it = itemFor($items.get(), host.state.focusedStoredSessionId?.get?.())
      if (it && socialOptions(it).some(o => o.n === d) && !($editing.get() && $editing.get().sid === it.session_id)) {
        e.preventDefault()
        select(it.session_id, d)
        return true
      }
      return false
    }
    const n = actionKeyDigit(e)
    if (!n || !keyTargetAllowed(e) || !keyCharAllowed(e, n)) return false
    const item = itemFor($items.get(), host.state.focusedStoredSessionId?.get?.())
    if (!item || !barShown()) return false
    const edit = $editing.get()
    if ((edit && edit.sid === item.session_id) || $busy.get() === item.session_id) {
      e.preventDefault()
      return false
    }
    const action = keyActions(item, $selected.get())[n - 1]
    if (!action) return false
    e.preventDefault()
    e.stopImmediatePropagation?.()
    run(item, action)
    return true
  }

  const store = { $items, $open, $busy, $editing, $selected, $cards, refresh, open, markRead, archive, archiveHotkey, accept, act, run, onKeyDown, setOpen, select, cardItem, fetchCard }
  return store
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
  const archive = jsx(ArchiveButton, { onClick: () => void store.act(item, ARCHIVE) })
  const junk = jsx(RowActionButton, { action: JUNK, onClick: () => void store.act(item, JUNK) }, 'junk')
  if (!extra.length) return jsxs('span', { className: 'flex items-center gap-0.5', children: [junk, archive] })
  return jsxs('span', {
    className: 'flex items-center gap-0.5',
    children: [
      ...extra.map(a => jsx(RowActionButton, { action: a, onClick: () => void store.act(item, a) }, a.id)),
      junk,
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
          tip: [item.title, item.poster && `from ${item.poster}`, item.hub && `on ${item.hub.gateway} (offline) · ${syncedAgo(item.hub.synced_at)} · actions go through hermes-hub`].filter(Boolean).join(' — '),
          accentColor: item.unread ? 'var(--accent)' : 'var(--ui-text-tertiary)',
          strong: !!item.unread,
          muted: !!item.hub,
          icon: item.hub ? (isPending(item) ? 'sync' : 'cloud') : undefined,
          active: focused === item.session_id,
          meta: item.hub ? rowMeta(item) : [ago(item.updated_at), who].filter(Boolean).join(' · '),
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

function ActionButton({ action, disabled, title, hint, onClick }) {
  return jsxs('button', {
    type: 'button',
    className: BTN,
    'data-inbox-action': action.id,
    style: action.style === 'primary' ? ACCENT : action.style === 'danger' ? DANGER : undefined,
    disabled,
    title: title || action.label,
    'aria-keyshortcuts': hint ? hintAria(hint) : undefined,
    onClick,
    children: [
      action.icon ? jsx('span', { className: `codicon codicon-${action.icon}`, 'aria-hidden': true }) : null,
      action.label,
      hint ? jsx('kbd', { 'data-slot': 'inbox-key-hint', className: 'ml-0.5 font-sans text-[0.6875rem] opacity-60', children: hint }) : null
    ]
  })
}

export const hintAria = h => h.replace('⌥', 'Alt+').replace('⌘⇧', 'Meta+Shift+').replace('Ctrl+', 'Control+')

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
  const editing = useValue(store.$editing)
  const selected = useValue(store.$selected)
  const setEditing = v => store.$editing.set(v)
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
  const run = action => store.run(item, action)
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
      ...socialBarActions(item, selected).map(action =>
        jsx(ActionButton, {
          action,
          disabled,
          // Prompt actions show what they will send (the template), so nothing runs unseen.
          title: action.kind === 'accept' || action.id === 'do_it' ? item.accept_prompt : action.kind === 'prompt' || action.kind === 'task' ? str(action.prompt) || action.label : action.label,
          hint: keyHint(item, action, IS_MAC, selected),
          onClick: () => run(action)
        }, action.id)
      )
    ]
  })
}

// ── ::social-card ───────────────────────────────────────────────────────────

const C = {
  muted: 'var(--ui-text-tertiary, var(--muted-foreground))',
  border: 'var(--border)',
  accent: 'var(--accent)',
  target: '#f0b429'
}
const SC = {
  card: { maxWidth: 680, margin: '4px 0 8px', fontSize: '0.875rem', lineHeight: 1.45 },
  head: { display: 'flex', gap: 6, alignItems: 'baseline', fontSize: '0.75rem', color: C.muted, marginBottom: 8 },
  kind: { color: 'var(--foreground)', fontWeight: 600 },
  summary: { margin: '0 0 10px' },
  label: { fontSize: '0.6875rem', textTransform: 'uppercase', letterSpacing: '0.07em', color: C.muted, marginRight: 8 },
  chat: { display: 'flex', flexDirection: 'column', gap: 8, padding: 12, border: `1px solid ${C.border}`, borderRadius: 12 },
  fold: { alignSelf: 'center', font: 'inherit', fontSize: '0.75rem', color: C.muted, background: 'none', border: `1px dashed ${C.border}`, borderRadius: 12, padding: '2px 12px', cursor: 'pointer' },
  row: me => ({ display: 'flex', gap: 8, maxWidth: '84%', alignSelf: me ? 'flex-end' : 'flex-start' }),
  av: me => ({ flex: 'none', width: 24, height: 24, borderRadius: '50%', display: 'grid', placeItems: 'center', fontSize: '0.6875rem', fontWeight: 700, background: me ? C.accent : 'color-mix(in srgb, var(--foreground) 14%, transparent)', color: me ? 'var(--accent-foreground, white)' : 'var(--foreground)' }),
  bubble: (me, target, draft) => ({
    padding: '6px 11px',
    borderRadius: me ? '14px 14px 4px 14px' : '14px 14px 14px 4px',
    border: draft ? `1px dashed ${C.accent}` : `1px solid ${target ? C.target : me ? 'color-mix(in srgb, var(--accent) 45%, transparent)' : C.border}`,
    boxShadow: target ? `inset 0 0 0 1px ${C.target}` : undefined,
    background: draft ? 'transparent' : me ? 'color-mix(in srgb, var(--accent) 16%, transparent)' : 'color-mix(in srgb, var(--foreground) 5%, transparent)',
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere'
  }),
  bh: { display: 'flex', gap: 8, alignItems: 'center', fontSize: '0.6875rem', color: C.muted, marginBottom: 2 },
  link: { marginLeft: 'auto', color: C.muted, textDecoration: 'none' },
  sec: { margin: '14px 0 6px', fontSize: '0.6875rem', letterSpacing: '0.07em', textTransform: 'uppercase', color: C.muted },
  opt: on => ({ display: 'flex', width: '100%', gap: 10, alignItems: 'flex-start', textAlign: 'left', font: 'inherit', color: 'inherit', border: `1px solid ${on ? C.accent : C.border}`, background: on ? 'color-mix(in srgb, var(--accent) 8%, transparent)' : 'transparent', borderRadius: 10, padding: '8px 12px', cursor: 'pointer', marginBottom: 6 }),
  num: on => ({ flex: 'none', width: 20, height: 20, borderRadius: '50%', display: 'grid', placeItems: 'center', fontSize: '0.6875rem', border: `1px solid ${on ? C.accent : C.muted}`, background: on ? C.accent : 'transparent', color: on ? 'var(--accent-foreground, white)' : C.muted }),
  angle: on => ({ display: 'block', fontSize: '0.75rem', fontWeight: 600, color: on ? C.accent : C.muted }),
  count: { flex: 'none', fontSize: '0.6875rem', color: C.muted, fontVariantNumeric: 'tabular-nums' },
  why: { marginTop: 12, borderTop: `1px solid ${C.border}`, paddingTop: 8, fontSize: '0.75rem', color: C.muted }
}

const initial = a => (str(a).replace(/^@/, '')[0] || '?').toUpperCase()
const shortHandle = a => str(a).replace(/\.bsky\.social$/, '')

function Turn({ turn, draft }) {
  const me = turn.self || draft
  const who = draft ? 'You · draft, not posted' : me ? 'You' : shortHandle(turn.author)
  return jsxs('div', {
    'data-slot': draft ? 'social-draft' : 'social-turn',
    'data-target': turn.target ? 'true' : undefined,
    style: SC.row(me),
    children: [
      me ? null : jsx('span', { style: SC.av(false), 'aria-hidden': true, children: initial(turn.author) }),
      jsxs('div', {
        style: SC.bubble(me, turn.target, draft),
        children: [
          jsxs('div', {
            style: SC.bh,
            children: [
              jsx('span', { children: who }),
              turn.target ? jsx('span', { style: { color: C.target }, children: 'replying to this' }) : null,
              turn.url ? jsx('a', { href: turn.url, target: '_blank', rel: 'noopener noreferrer', style: SC.link, title: 'Open post', children: '↗' }) : null
            ]
          }),
          jsx('div', { 'data-slot': 'social-text', children: turn.text })
        ]
      })
    ]
  })
}

export function SocialCardView({ item, selected, onSelect, maxChars }) {
  const [unfold, setUnfold] = useState(false)
  const turns = parseThread(item) || []
  const f = item.fields || {}
  const { folded, shown } = foldTurns(turns)
  const opts = socialOptions(item)
  const sel = selectedOption(item, selected)
  const specs = item.field_specs || {}
  const maxFor = n => maxChars || (specs[`option_${n}`] && specs[`option_${n}`].max) || (specs.text && specs.text.max) || 0
  const sources = parseSources(f.sources)
  const target = turns.find(t => t.target) || turns[turns.length - 1]
  const children = [
    jsxs('div', {
      style: SC.head,
      children: [
        jsx('span', { style: SC.kind, children: [PLATFORM(item.type), item.type_label || ''].filter(Boolean).join(' · ') }),
        target && !target.self ? jsx('span', { children: `· ${shortHandle(target.author)}` }) : null,
        jsx('span', { style: { marginLeft: 'auto' }, children: ago(item.created_at) })
      ]
    }),
    f.summary ? jsxs('p', { 'data-slot': 'social-summary', style: SC.summary, children: [jsx('span', { style: SC.label, children: 'Thread' }), f.summary] }) : null,
    jsxs('div', {
      style: SC.chat,
      children: [
        folded.length
          ? jsx('button', { type: 'button', 'data-slot': 'social-fold', style: SC.fold, onClick: () => setUnfold(!unfold), children: unfold ? 'Hide earlier posts' : `Show ${folded.length} earlier post${folded.length === 1 ? '' : 's'}` })
          : null,
        ...(unfold ? folded : []).map((t, i) => jsx(Turn, { turn: t }, `f${i}`)),
        ...shown.map((t, i) => jsx(Turn, { turn: t }, `s${i}`)),
        sel ? jsx(Turn, { turn: { self: true, text: sel.text }, draft: true }, 'draft') : null
      ]
    }),
    opts.length > 1
      ? jsxs('div', {
          'data-slot': 'social-options',
          children: [
            jsx('div', { style: SC.sec, children: 'Pick a reply' }),
            ...opts.map(o => {
              const on = sel && sel.n === o.n
              return jsxs('button', {
                type: 'button',
                'data-slot': 'social-option',
                'data-option': String(o.n),
                'aria-pressed': on ? 'true' : 'false',
                style: SC.opt(on),
                onClick: () => onSelect && onSelect(o.n),
                children: [
                  jsx('span', { style: SC.num(on), children: String(o.n) }),
                  jsxs('span', { style: { flex: 1 }, children: [o.angle ? jsx('span', { style: SC.angle(on), children: o.angle }) : null, jsx('span', { children: o.text })] }),
                  jsx('span', { style: SC.count, children: counterText(o.text, maxFor(o.n)) })
                ]
              }, o.n)
            })
          ]
        })
      : null,
    f.grounding || sources.length
      ? jsxs('details', {
          'data-slot': 'social-why',
          style: SC.why,
          children: [
            jsx('summary', { style: { cursor: 'pointer', color: 'var(--foreground)' }, children: 'Why this reply' }),
            f.grounding ? jsx('p', { style: { margin: '6px 0 0', whiteSpace: 'pre-wrap' }, children: f.grounding }) : null,
            sources.length
              ? jsx('p', {
                  style: { margin: '6px 0 0' },
                  children: sources.map((x, i) =>
                    x.url
                      ? jsx('a', { href: x.url, target: '_blank', rel: 'noopener noreferrer', style: { color: C.accent, marginRight: 12 }, children: x.label }, i)
                      : jsx('span', { style: { marginRight: 12 }, children: x.label }, i)
                  )
                })
              : null
          ]
        })
      : null
  ]
  return jsx('div', { 'data-slot': 'social-card', style: SC.card, children })
}

export function SocialCard({ store, attrs, sessionId }) {
  const sid = str(sessionId) || str(attrs && attrs.session)
  const items = useValue(store.$items)
  const cards = useValue(store.$cards)
  const selected = useValue(store.$selected)
  const item = itemFor(items, sid) || cards[sid] || null
  useEffect(() => {
    if (sid && !item) void store.fetchCard(sid)
  }, [sid, !!item])
  if (!item || item.missing || !parseThread(item)) {
    return jsx('div', { 'data-slot': 'social-card-missing', style: { fontSize: '0.75rem', color: C.muted }, children: item && item.missing ? 'Card details unavailable.' : item ? '' : 'Loading card…' })
  }
  return jsx(SocialCardView, { item, selected, onSelect: n => store.select(sid, n) })
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
    // ⌥1…⌥9: card actions. Capture phase, so the composer never types ¡™£ for them.
    if (typeof window !== 'undefined' && window.addEventListener) {
      // A reload without onDispose (older cores) must not stack listeners.
      if (window.__inboxKeyDown) window.removeEventListener('keydown', window.__inboxKeyDown, true)
      window.__inboxKeyDown = store.onKeyDown
      window.addEventListener('keydown', store.onKeyDown, true)
      ctx.onDispose?.(() => {
        window.removeEventListener('keydown', store.onKeyDown, true)
        if (window.__inboxKeyDown === store.onKeyDown) window.__inboxKeyDown = null
      })
    }
    ctx.register({ id: 'section', area: SIDEBAR_SECTIONS_AREA, order: 5, render: () => jsx(Section, { store }) })
    ctx.register({ id: 'bar', area: COMPOSER_AREAS.top, order: -30, render: () => jsx(Bar, { store }) })
    if (TRANSCRIPT_DIRECTIVE_AREA) {
      ctx.register({ id: 'social-card', area: TRANSCRIPT_DIRECTIVE_AREA, data: { name: 'social-card', render: props => jsx(SocialCard, { store, ...props }) } })
    }
    log('registered')
  }
}
