// Lanes: how worker time is split between project lanes, and the controls for it.
// Lanes live on one gateway; the strip finds it (createSource below) and reads and writes there.
// Docked above the chat zone (older hosts: shell.topStrip). Collapsed by default: one line of per-lane pills.
// Expanded = two tabs, Floor (lanes × steps grid of cards, hatched where they pile up, with the lane valves) and
// Flow (Sankey of sources → lanes → flowing steps / dams); both read GET /v1/lanes/flow?window= only while open.
// The status-bar task pill (KANBAN_CHIP_EVENT) and the palette "Show lanes" command expand it.
// Data: GET /v1/lanes?hours=… (plugins/lanes): per lane target and actual share of run-hours, running,
// ready, held at the gate, blocked_on_you (human asks), open, done, tokens and estimated cost.
// Controls: PUT /v1/lanes/<lane> {share | paused}; the API also takes admit_per_day and has /v1/held,
// which this panel does not use yet. Shares are relative volumes: the target percentage is share / sum
// of shares. A draft survives polls; Reset, a successful Apply or collapsing the strip drops it, and
// the controls are locked while an Apply is in flight.
import * as sdk from '@hermes/plugin-sdk'
import { createElement as h, useState, useEffect, useMemo, useRef } from 'react'

const { host, PALETTE_AREA } = sdk
// Older Desktops do not export TOP_STRIP_AREA; a named import would throw at load and skip activation.
export const TOP_STRIP_AREA = sdk.TOP_STRIP_AREA || 'shell.topStrip'
export const STRIP_AREA = sdk.MAIN_PANE_TOP_AREA || TOP_STRIP_AREA

export const FALLBACK_MS = 60000
export const REQUEST_TIMEOUT_MS = 20000
export const WINDOWS = [[1, '1h'], [6, '6h'], [24, '24h'], [168, '7d']]
export const STORE_KEY = 'hermes-lanes-window'
export const OPEN_KEY = 'hermes-lanes-open'
// The Desktop's status-bar task pill (board icon, bar of open cards, count) fires this cancellable event before
// it opens the board. Where lanes work, this strip takes the click over; its Board button opens the board.
export const KANBAN_CHIP_EVENT = 'hermes-kanban-chip'
const COLORS = ['#f0a43a', '#8fb0ff', '#5fd4e6', '#d982f0', '#b4bac6', '#3ddc97', '#ef7b7b', '#c9d36a']
export const METER_COLORS = { running: '#388bfd', notReady: '#d4a72c', blocked: '#d64545' }
// Card-state colours shared with the sidebar's card squares (Tailwind red-500, pink-500, emerald-500, sky-500, zinc-500).
export const CARD_COLORS = { you: '#ef4444', ext: '#ec4899', gate: '#a855f7', worker: '#10b981', run: '#0ea5e9', dep: '#71717a' }
export const LANE_COUNT_COLORS = { blocked: CARD_COLORS.you, running: CARD_COLORS.run, other: '#a1a1aa' }
/** All-lanes strip counts: hub `status` key, words, colour (null = outlined, no fill). */
export const STATUS = [
  ['you', 'blocked on you', CARD_COLORS.you], ['ext', 'waiting on external (CI, review, timer)', CARD_COLORS.ext],
  ['gate', 'held at a gate', CARD_COLORS.gate], ['worker', 'waiting for a worker', CARD_COLORS.worker], ['run', 'running', CARD_COLORS.run],
  ['dep', 'waiting on other cards', CARD_COLORS.dep], ['triage', 'in triage', null], ['other', 'other', null]
]
/** The colour word a tooltip names for each count, so hovering says what the colour means. */
export const COLOR_WORDS = { you: 'Red', ext: 'Pink', gate: 'Purple', worker: 'Green', run: 'Blue', dep: 'Grey', triage: 'Outline', other: 'Outline' }
// Desktops with the session-list filter hook export these; older ones do not, and the strip then filters nothing.
export const SESSION_FILTER_AREA = sdk.SESSION_FILTER_AREA || ''
// Desktops with the Sessions tab-strip slot (beside Sessions / Bots) take the counts, "not live" and the expand
// toggle there; older ones keep them on the strip's first row.
export const SESSIONS_TAB_STRIP_AREA = sdk.SESSIONS_TAB_STRIP_AREA || ''
const useSidebarSessions = typeof sdk.useSidebarSessions === 'function' ? sdk.useSidebarSessions : () => null

// ── which gateway to talk to ──────────────────────────────────────────────
// Lanes are configured on one gateway (the one that holds the board and runs the dispatcher), which is often
// not the gateway this Desktop has in front. The source asks every connection once, by id (a request with a
// connectionId and no profile is answered by that connection's default profile), and settles on the one that
// steers lanes (see `steers`), else the first that answers `configured: true`; reads and writes then go to that connection by id, so they do not follow the
// window when another gateway comes to the front. Only when the Desktop lists no connections does it fall back
// to an untagged request, which goes to whichever gateway is in front.
// A settled gateway is kept through failures: one failed read is an error to show and retry (gateways restart
// several times a day), and only FAILURES_BEFORE_REDISCOVERY in a row, or an answer that says lanes are no
// longer configured there, starts a new search. While no gateway has lanes the search repeats every
// UNCONFIGURED_MS, and the reason shown names any gateway that could not be reached.
export const UNCONFIGURED_MS = 60000
export const FAILURES_BEFORE_REDISCOVERY = 3
// Every host can carry the lane definitions (so cards are classified where they are created), but lanes are
// steered on one gateway: the one whose config holds the gate and that runs the planner. Its shares are the ones
// that count and the only ones a shared-config pass does not overwrite, so that is where the panel must read
// and write. A gateway steers when a lane there is gated or it has planner notes.
export const steers = (body) => Object.values(body?.lanes || {}).some((l) => l?.gated === true) || Boolean(body?.planner)
const validRoute = (r) => r && ['connectionId', 'targetProfile'].every((k) => typeof r[k] === 'string' && r[k].trim())

export function createSource(ctx, hostRef = host, clock = () => Date.now()) {
  let chosen // { connectionId } | { connectionId: null } (no connections listed: whichever is in front) | undefined
  let failures = 0
  let sweptAt = -Infinity
  let unconfigured // the answer shown while no gateway has lanes
  const opts = (target, extra = {}) => ({ ...extra, timeoutMs: REQUEST_TIMEOUT_MS, ...(target.connectionId ? { connectionId: target.connectionId } : {}) })
  async function candidates() {
    let list = []
    try { list = ((await hostRef.profileRoutes()) || []).filter(validRoute) } catch { list = [] }
    const ids = [...new Set(list.map((r) => r.connectionId))]
    return ids.length ? ids.map((connectionId) => ({ connectionId })) : [{ connectionId: null }]
  }
  async function discover(path) {
    sweptAt = clock()
    failures = 0
    let first
    const unreachable = []
    let fallback // the first configured gateway, used when none of them steers
    for (const target of await candidates()) {
      try {
        const body = await ctx.rest(path, opts(target))
        if (body?.configured === true) {
          if (steers(body)) { chosen = target; unconfigured = undefined; return body }
          fallback ??= { target, body }
        } else first ??= body
      } catch (e) { unreachable.push(String(e?.message || e).slice(0, 80)) }
    }
    if (fallback) { chosen = fallback.target; unconfigured = undefined; return fallback.body }
    chosen = undefined
    unconfigured = undefined // nothing to show from an older search if this one throws
    if (!first) throw Error(unreachable[0] || 'No gateway answered')
    const missed = unreachable.length ? `; ${unreachable.length} other gateway(s) could not be reached (${unreachable[0]})` : ''
    unconfigured = { ...first, configured: false, reason: `${first?.reason || 'No lanes are configured.'}${missed}` }
    return unconfigured
  }
  async function read(hours, window, expanded) {
    const path = `/v1/lanes?hours=${hours}` + (window ? `&window=${window}&expanded=${Boolean(expanded)}` : '')
    if (!chosen) return unconfigured && clock() - sweptAt < UNCONFIGURED_MS ? unconfigured : discover(path)
    let body
    try {
      body = await ctx.rest(path, opts(chosen))
    } catch (e) {
      failures += 1
      if (failures < FAILURES_BEFORE_REDISCOVERY) throw e // keep the gateway and the last data; the next poll retries
      return discover(path)
    }
    failures = 0
    return body?.configured === true ? body : discover(path)
  }
  // `target` is the gateway chosen when Apply was pressed: every lane of that Apply goes there even if a poll
  // re-discovers meanwhile.
  async function write(lane, body, target = chosen) {
    if (!target) throw Error('No gateway with lanes configured')
    return ctx.rest(`/v1/lanes/${encodeURIComponent(lane)}`, opts(target, { method: 'PUT', body }))
  }
  // Open cards by step and reason, from the gateway that steers lanes; only asked while the panel is open.
  async function flow(window, target = chosen) {
    if (!target) throw Error('No gateway with lanes configured')
    const body = await ctx.rest(`/v1/lanes/flow?window=${encodeURIComponent(window)}`, opts(target))
    if (body?.configured === false) throw Error(body.reason || 'lanes flow is not available')
    return body
  }
  function subscribe(initial, onView, onLive, timers = globalThis) {
    let params = initial, disposed = false, socket, off, poll, watchdog, retry
    let epoch = 0, frames = 0, live = false, attempt = 0, connections = 0, started = false
    let warmingPoll
    const bounded = (promise) => new Promise((resolve, reject) => {
      const timer = timers.setTimeout(() => reject(Error('Lanes socket bridge timed out')), REQUEST_TIMEOUT_MS)
      Promise.resolve(promise).then(resolve, reject).finally(() => timers.clearTimeout(timer))
    })
    const path = () => `v1/lanes/stream?hours=${params.hours}&window=${params.window}&expanded=${params.expanded}`
    const fetch = async (redial = true) => {
      const at = epoch, before = frames, previousTarget = chosen?.connectionId
      try {
        const view = await read(params.hours, params.window, params.expanded)
        if (!disposed && at === epoch && before === frames) {
          onView(view)
          timers.clearTimeout(warmingPoll)
          if (view?.warming) warmingPoll = timers.setTimeout(() => { if (!disposed) void fetch() }, 1000)
          if (redial && started && previousTarget !== chosen?.connectionId) {
            epoch += 1; off?.(); off = undefined; socket?.close(); socket = undefined
            timers.clearTimeout(watchdog); timers.clearTimeout(retry)
            void dial()
          }
        }
      } catch (e) { if (!disposed) onView(undefined, e) }
    }
    const unavailable = () => {
      live = false; onLive(false)
      // Nothing is read while the window is hidden; the stream (or the next tick once shown) catches up.
      if (!poll && !disposed) poll = timers.setInterval(() => { if (!globalThis.document?.hidden) void fetch() }, FALLBACK_MS)
    }
    const receive = (frame) => {
      if (disposed) return
      if (frame?.params && (frame.params.hours !== params.hours || frame.params.window !== params.window || frame.params.expanded !== params.expanded)) return
      timers.clearTimeout(watchdog)
      const owner = socket, at = epoch
      watchdog = timers.setTimeout(() => {
        if (disposed || at !== epoch || owner !== socket) return
        if (owner) owner.close(); else unavailable()
      }, 45000)
      if (!live) { live = true; onLive(true); timers.clearInterval(poll); poll = undefined }
      attempt = 0
      if (frame?.view) {
        timers.clearTimeout(warmingPoll)
        frames += 1; onView(frame.view)
        // version 1 is a new connection: refresh once, not on ordinary updates.
        if (frame.version === 1 && connections++ > 0) void fetch()
        if (frame.view.configured === false) { unavailable(); void fetch() }
      }
    }
    const dial = async () => {
      timers.clearTimeout(watchdog)
      timers.clearTimeout(retry)
      const at = epoch
      const bridge = globalThis.window?.hermesDesktop
      // The legacy SDK cannot pin a connection or send updates. Use its socket only
      // in an unscoped host; never stream another gateway's counts into a pinned hub.
      if (!bridge?.getConnectionFor || !chosen?.connectionId) {
        if (typeof ctx.socket === 'function' && chosen?.connectionId === null) {
          off = ctx.socket(path(), receive)
          watchdog = timers.setTimeout(unavailable, 45000)
        } else unavailable()
        return
      }
      try {
        const connection = await bounded(bridge.getConnectionFor({ connectionId: chosen.connectionId, profile: null }))
        if (disposed || at !== epoch) return
        if (connection.authMode === 'oauth') { unavailable(); return }
        const suffix = '/' + path()
        const url = connection.authMode === 'native'
          ? await bounded(bridge.getPluginWsUrl({ connectionId: chosen.connectionId, profile: null, pluginId: 'lanes', path: suffix }))
          : connection.token ? `${connection.baseUrl.replace(/\/$/, '').replace(/^http/, 'ws')}/api/plugins/lanes${suffix}&token=${encodeURIComponent(connection.token)}` : null
        if (disposed || at !== epoch) return
        if (typeof url !== 'string') { unavailable(); return }
        const current = new WebSocket(url)
        socket = current
        current.onopen = () => { if (disposed || at !== epoch) { current.close(); return }; current.send(JSON.stringify(params)) }
        current.onmessage = (event) => { if (!disposed && at === epoch && socket === current) { try { receive(JSON.parse(event.data)) } catch {} } }
        current.onclose = () => {
          if (disposed || at !== epoch || socket !== current) return
          timers.clearTimeout(watchdog)
          unavailable()
          retry = timers.setTimeout(() => void dial(), Math.min(30000, 500 * 2 ** Math.min(attempt++, 6)))
        }
        watchdog = timers.setTimeout(() => {
          if (disposed || at !== epoch || socket !== current) return
          unavailable(); current.close()
        }, 45000)
      } catch {
        if (!disposed && at === epoch) { unavailable(); retry = timers.setTimeout(() => void dial(), 5000) }
      }
    }
    const start = () => {
      const at = ++epoch
      off?.(); off = undefined; socket?.close(); socket = undefined
      timers.clearTimeout(retry); timers.clearTimeout(watchdog)
      void fetch(false).then(() => {
        if (!disposed && at === epoch) { started = true; void dial() }
      })
    }
    start()
    const stop = () => {
      disposed = true; epoch += 1; off?.(); socket?.close()
      timers.clearInterval(poll); timers.clearTimeout(watchdog); timers.clearTimeout(retry); timers.clearTimeout(warmingPoll)
    }
    stop.update = (next) => {
      params = next
      if (socket?.readyState === 1) socket.send(JSON.stringify(params))
      else start()
    }
    stop.refresh = fetch
    return stop
  }
  return { read, write, flow, subscribe, target: () => chosen }
}

// Read-only sidebar rows share the strip's pushed snapshot; no second poller.
const attentionStores = new WeakMap()
export function attentionStore(ctx) {
  let store = attentionStores.get(ctx)
  if (!store) {
    const listeners = new Set()
    let subscription
    const clients = new Map()
    store = { view: null, sessionMatch: () => true,
      setSessionMatch(match) {
        store.sessionMatch = match
        // Only attention listeners repaint. The filter effect depends on scalar
        // selection/map keys, never this notification, so it cannot re-register itself.
        store.publish(store.view && { ...store.view })
      },
      publish(view) { store.view = view; for (const fn of listeners) fn(view) },
      listen(fn) { listeners.add(fn); return () => listeners.delete(fn) },
      connect(params, onView, onLive = () => {}, primary = false) {
        const token = {}
        clients.set(token, { params, onView, onLive })
        if (store.view) onView(store.view)
        if (!subscription) subscription = sourceFor(ctx).subscribe(params,
          (view, err) => {
            if (view) store.publish(view)
            for (const client of clients.values()) client.onView(view, err)
          }, live => { for (const client of clients.values()) client.onLive(live) })
        else if (primary) subscription.update(params)
        const stop = () => {
          clients.delete(token)
          if (!clients.size) { subscription?.(); subscription = undefined }
        }
        stop.update = next => { clients.get(token).params = next; subscription?.update(next) }
        stop.refresh = () => subscription?.refresh()
        return stop
      } }
    attentionStores.set(ctx, store)
  }
  return store
}

// Match the sidebar's canonicalOutcomeStatus validation and live-row boundary.
// The SDK has no canonical selector export. Fail closed on unknown envelopes:
// needs_you is evidence of an ask, not proof that its session actually paints red.
export function hasRedAskCoverage(session) {
  if (session.archived || session.offline_cached) return false
  const status = session.outcome?.status
  const strings = value => Array.isArray(value) && value.every(item => typeof item === 'string')
  const count = value => Number.isSafeInteger(value) && value >= 0
  return Boolean(status && status.status_version === 1 &&
    ['done', 'unresolved', 'muted', 'decision', 'waiting', 'running'].includes(status.kind) &&
    count(status.revision) && count(status.generation) && status.identity &&
    ['gateway', 'profile', 'session_id', 'owner_epoch'].every(key =>
      typeof status.identity[key] === 'string' && status.identity[key].length > 0) &&
    ['complete', 'unknown'].includes(status.coverage?.state) && strings(status.coverage?.reasons) &&
    status.presentation?.stripe === 'stuck' && strings(status.presentation?.reason_codes))
}

export function uncoveredAsks(view, sessions, match = () => true) {
  if (!view?.attention?.available) return []
  // SDK rows precede plugin filters. Apply the very same registered matcher
  // before treating a chat as coverage; otherwise a lane-hidden ask disappears.
  return view.attention.rows.filter(row => sessions == null ? row.unlinked :
    !sessions.some(session => match(session) && hasRedAskCoverage(session) && (session.needs_you || []).some(ask =>
      ask.kind === 'card' && ask.ref === `${row.target?.board || 'default'}/${row.id}`)))
}

export function UnlinkedAsks({ ctx }) {
  const sessions = useSidebarSessions()
  const store = attentionStore(ctx)
  const [view, setView] = useState(store.view)
  const [open, setOpen] = useState(true)
  useEffect(() => {
    setView(store.view)
    const unlisten = store.listen(setView)
    const disconnect = store.connect({ hours: loadWindow(), window: flowWindow(loadWindow()), expanded: false }, () => {})
    return () => { unlisten(); disconnect() }
  }, [store])
  const rows = uncoveredAsks(view, sessions, store.sessionMatch)
  if (!rows.length || !sdk.SidebarSection || !sdk.SidebarSectionRow) return null
  return h('div', { 'data-unlinked-asks': '1', 'data-needs-you-count': rows.length },
    h(sdk.SidebarSection, { label: 'Unlinked asks', count: rows.length, open, onToggle: () => setOpen(v => !v) },
      rows.map(row => h(sdk.SidebarSectionRow, { key: row.key, label: row.title,
        accentColor: CARD_COLORS.you, meta: row.lane, tip: `${row.title}\n${row.id}`,
        onSelect: () => openCard(row.id, sourceFor(ctx).target()?.connectionId || undefined) }))))
}

// One source per plugin ctx, not per mount: the status bar remounts contributions often.
const sources = new WeakMap()
export function sourceFor(ctx) {
  let s = sources.get(ctx)
  if (!s) sources.set(ctx, (s = createSource(ctx)))
  return s
}

// ── pure helpers (exported for tests) ─────────────────────────────────────

export function loadWindow(storage = globalThis.localStorage) {
  try {
    const v = Number(storage?.getItem(STORE_KEY))
    return WINDOWS.some(([hours]) => hours === v) ? v : 6
  } catch { return 6 }
}

export function saveWindow(hours, storage = globalThis.localStorage) {
  try { storage?.setItem(STORE_KEY, String(hours)) } catch {}
}

export function loadOpen(storage = globalThis.localStorage) {
  try { return storage?.getItem(OPEN_KEY) === '1' } catch { return false }
}

export function saveOpen(open, storage = globalThis.localStorage) {
  try { storage?.setItem(OPEN_KEY, open ? '1' : '0') } catch {}
}

/** Rows in server order with a stable color, the target each lane WOULD get with `draft` shares applied. */
export function rows(snap, draft = {}) {
  const names = Object.keys(snap?.lanes || {})

  return names.map((name, i) => {
    const lane = snap.lanes[name]
    return {
      ...lane,
      name,
      color: COLORS[i % COLORS.length],
      share: Number(draft[name]?.share ?? lane.share) || 0,
      paused: draft[name]?.paused ?? lane.paused,
      target: lane.target,
      dirty: draft[name] != null
    }
  })
}

/** Why a lane is off its target, in the user's terms; '' when it is within 10 points. */
export function drift(row) {
  return row.drift || ''
}

export function summaryValue(value, format = String) { return value == null ? '—' : format(value) }
export function pct(value) { return summaryValue(value, v => `${Math.round((Number(v) || 0) * 100)}%`) }

export function compact(n) {
  n = Number(n) || 0
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(Math.round(n))
}

export function hours(seconds) {
  const v = (Number(seconds) || 0) / 3600
  return v >= 100 ? Math.round(v).toLocaleString('en-US') : v.toFixed(1)
}

export function age(seconds) {
  if (seconds == null) return ''
  if (seconds >= 86400) return `${Math.round(seconds / 86400)}d`
  if (seconds >= 3600) return `${Math.round(seconds / 3600)}h`
  return `${Math.max(1, Math.round(seconds / 60))}m`
}

/** "$12.40" plus how much of the lane's runs that covers: cost is only what workers reported. */
export function cost(row) {
  if (!row.usage_runs) return { text: '–', title: 'No run in this window reported cost' }
  return {
    text: `$${Number(row.cost_usd).toFixed(2)}`,
    title: `Estimated, from ${row.usage_runs} of ${row.runs} runs that reported usage · ${compact(row.tokens)} tokens in+out`
  }
}

/** The PUT bodies a draft needs, one per changed lane. `flow` supplies the saved cards/day and $/day to compare with. */
export function changes(snap, draft, flow) {
  return Object.entries(draft).map(([name, d]) => {
    const body = {}
    if (d.share != null && d.share !== snap.lanes[name]?.share) body.share = d.share
    if (d.paused != null && d.paused !== snap.lanes[name]?.paused) body.paused = d.paused
    for (const key of LIMIT_KEYS) if (d[key] !== undefined && d[key] !== (flow?.lanes?.[name]?.limits?.[key] ?? null)) body[key] = d[key]
    return [name, body]
  }).filter(([, body]) => Object.keys(body).length)
}

// ── Floor and Flow: pure helpers ──────────────────────────────────────────
// Data is `GET /v1/lanes/flow?window=` (core `hermes kanban lanes flow --json`): open cards
// {id,title,lane,step,reason,source{kind,id,project},age_h,pr} and per lane {done,run_hours,cost_usd,limits,usage}.

export const LIMIT_KEYS = ['max_workers', 'cards_per_day', 'usd_per_day']
export const TAB_KEY = 'hermes-lanes-tab'
export const BY_KEY = 'hermes-lanes-flow-by'
export const TABS = [['floor', 'Floor'], ['flow', 'Flow']]
export const UNCLASSIFIED = 'landing_unclassified'
export const PILE_AT = 4
export const STEPS = [
  ['intake', 'Intake', 'triage + lane gate'], ['waiting', 'Waiting', 'on a card, time or you'], ['ready', 'Ready', 'needs a worker'],
  ['running', 'Running', 'on a worker'], ['review', 'Review', 'PR review'], ['ci', 'CI', 'required checks'],
  ['merge', 'Merge', 'approved + green'], ['deploy', 'Deploy', 'merged, not live'], ['test', 'Test', 'live verification'],
  ['done', 'Done', 'this window']
]
const STEP_KEYS = STEPS.map(([key]) => key)
// Semantic colours (status, not chrome), so they do not follow the theme.
export const REASONS = {
  you: ['needs you', '#ff5d5d'], dep: ['waits on a card', '#f5c542'], gate: ['held at gate', '#c49bff'], time: ['scheduled', '#6b7280'],
  slot: ['no free worker', '#ff9f43'], quota: ['model quota', '#ff9f43'], triage: ['unspecified', '#9aa3b2'],
  run: ['running', '#4c9bff'], review: ['review / CI', '#3ddc97'],
  'limit:cards_per_day': ['over cards/day', '#e08bd0'], 'limit:usd_per_day': ['over $/day', '#e08bd0']
}
const REASON_ORDER = Object.keys(REASONS)
export function reasonInfo(reason) {
  const [label, color] = REASONS[reason] || (String(reason).startsWith('limit:') ? ['over limit', '#e08bd0'] : [String(reason || 'unknown'), '#9aa3b2'])
  return { label, color }
}

const SELECT_KEY = 'hermes.lanes.selected'
/** Lanes picked in the strip (empty = all lanes). */
export function loadSelected(storage = globalThis.localStorage) {
  try { const v = JSON.parse(storage?.getItem(SELECT_KEY) || '[]'); return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [] } catch { return [] }
}
export function saveSelected(names, storage = globalThis.localStorage) { try { storage?.setItem(SELECT_KEY, JSON.stringify(names)) } catch {} }
/** Totals for the selected lanes; with no selection (or an older hub without per-lane status) the all-lanes totals. */
export function selectedStatus(snap, selected) {
  const lanes = snap?.lanes || {}
  const picked = selected.filter((name) => lanes[name])
  if (!picked.length || picked.some((name) => !lanes[name].status)) return snap?.status
  const out = {}
  for (const name of picked) for (const [key, n] of Object.entries(lanes[name].status)) out[key] = (out[key] || 0) + (Number(n) || 0)
  return out
}

const STATUS_SELECT_KEY = 'hermes.lanes.statusSelected'
const NOCARDS_KEY = 'hermes.lanes.noCards'
/** Categories (`STATUS` keys) picked in the strip. */
export function loadStatusSelected(storage = globalThis.localStorage) {
  try {
    const v = JSON.parse(storage?.getItem(STATUS_SELECT_KEY) || '[]')
    return Array.isArray(v) ? v.filter((x) => STATUS.some(([key]) => key === x)) : []
  } catch { return [] }
}
export function saveStatusSelected(keys, storage = globalThis.localStorage) { try { storage?.setItem(STATUS_SELECT_KEY, JSON.stringify(keys)) } catch {} }
export function loadNoCards(storage = globalThis.localStorage) { try { return storage?.getItem(NOCARDS_KEY) === '1' } catch { return false } }
export function saveNoCards(on, storage = globalThis.localStorage) { try { storage?.setItem(NOCARDS_KEY, on ? '1' : '0') } catch {} }

/** The open-card map entries for a sidebar session (its id and every lineage id: a compressed chat keeps its cards). */
export function sessionCards(session, sessions) {
  if (!session || !sessions) return []
  const ids = new Set([session.id, session._lineage_root_id, ...(session._lineage_ids || [])].filter(Boolean))
  return [...ids].map((id) => sessions[id]).filter(Boolean)
}
/** Does a sidebar session pass the strip's selection? `sessions` is the hub's {session: {lane: {category: n}}}.
 *  Nothing selected: everything. Otherwise a session passes with an open card in a selected lane AND a selected
 *  category (none selected on either side = any), or, with no-cards picked, when it has no open cards. */
export function sessionMatches(session, sessions, { lanes = [], status = [], noCards = false } = {}) {
  if (!lanes.length && !status.length && !noCards) return true
  const entries = sessionCards(session, sessions)
  if (!entries.length) return noCards
  if (!lanes.length && !status.length) return false // only no-cards picked
  for (const byLane of entries) {
    for (const [lane, cats] of Object.entries(byLane)) {
      if (lanes.length && !lanes.includes(lane)) continue
      for (const [key, n] of Object.entries(cats || {})) if ((Number(n) || 0) > 0 && (!status.length || status.includes(key))) return true
    }
  }
  return false
}

export function loadTab(storage = globalThis.localStorage) {
  try { const v = storage?.getItem(TAB_KEY); return TABS.some(([key]) => key === v) ? v : 'floor' } catch { return 'floor' }
}
export function saveTab(tab, storage = globalThis.localStorage) { try { storage?.setItem(TAB_KEY, tab) } catch {} }
export function loadBy(storage = globalThis.localStorage) {
  try { return storage?.getItem(BY_KEY) === 'source' ? 'source' : 'project' } catch { return 'project' }
}
export function saveBy(by, storage = globalThis.localStorage) { try { storage?.setItem(BY_KEY, by) } catch {} }

/** Server projections only: no card grouping or counting in Desktop. */
export const floorColumns = (flow) => flow?.floor?.columns || []
export const floorGrid = (flow) => flow?.floor?.cells || []
export const piling = (cell) => Boolean(cell?.piling)

export function ageHours(hours) {
  const h = Number(hours) || 0
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`
  if (h < 48) return `${Math.round(h)}h`
  return `${Math.round(h / 24)}d`
}

const prLabel = (url) => { const m = /\/pull\/(\d+)/.exec(String(url || '')); return m ? `#${m[1]}` : String(url || '').split('/').filter(Boolean).pop() || '' }

/** Graphs and dam hints are counted and authored on the hub. */
export const flowGraph = (flow, by = 'project') => flow?.graphs?.[by] || { total: 0, sources: [], lanes: [], sinks: [], edges: { sourceLane: [], laneSink: [] } }

/** Geometry for the Sankey: node boxes per column and one stroked curve per edge. Pure, so tests can check it. */
export function sankey(graph, { width = 960, height = 300, nodeW = 14, gap = 6, minH = 10, cols = [0, 330, 640] } = {}) {
  const place = (list, x) => {
    const free = height - gap * Math.max(0, list.length - 1) - minH * list.length
    const total = list.reduce((s, n) => s + n.count, 0) || 1
    let y = 0
    return list.map((n) => {
      const h = minH + (Math.max(0, free) * n.count) / total
      const box = { ...n, x, y, h, outY: y, inY: y }
      y += h + gap
      return box
    })
  }
  const columns = [place(graph.sources, cols[0]), place(graph.lanes, cols[1]), place(graph.sinks, cols[2])]
  const find = (col, key) => columns[col].find((n) => n.key === key)
  const link = (from, to, count, lane) => {
    const w1 = (from.h * count) / from.count
    const w2 = (to.h * count) / to.count
    const y1 = from.outY + w1 / 2
    const y2 = to.inY + w2 / 2
    from.outY += w1; to.inY += w2
    const x1 = from.x + nodeW
    const x2 = to.x
    const mid = (x1 + x2) / 2
    return { from: from.key, to: to.key, count, lane, width: Math.max(1, (w1 + w2) / 2), d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}` }
  }
  const order = (edges, ci) => [...edges].sort((a, b) => columns[ci + 1].findIndex((n) => n.key === a.to) - columns[ci + 1].findIndex((n) => n.key === b.to))
  const links = [
    ...order(graph.edges.sourceLane, 0).map((e) => link(find(0, e.from), find(1, e.to), e.count, e.to)),
    ...order(graph.edges.laneSink, 1).map((e) => link(find(1, e.from), find(2, e.to), e.count, e.from))
  ]
  return { width, height, nodeW, columns, links }
}

const HOUR_WINDOW = new Map(WINDOWS.map(([hours, label]) => [hours, label]))
/** The flow API names windows `1h|6h|24h|7d`; the panel keeps hours. */
export const flowWindow = (hours) => HOUR_WINDOW.get(hours) || '6h'

/** Open the card's drawer where Desktop has the hook, then the task popup, then the Kanban page. */
export function openCard(id, connectionId, win = typeof window !== 'undefined' ? window : {}, nav = (p) => host.navigate?.(p)) {
  if (typeof win.__hermesKanbanOpenTask === 'function' && win.__hermesKanbanOpenTask(id, undefined, connectionId)) return 'drawer'
  if (typeof win.__taskMentionsOpen === 'function') { win.__taskMentionsOpen(id); return 'popup' }
  nav('/kanban')
  return 'page'
}

/** Open-card mix for a mini pill: blue running, yellow not ready, red human-blocked. Older cores omit blocked_on_you. */
export function meter(row) {
  return { running: row?.running === null ? null : row?.running ?? 0, notReady: row?.not_ready === null ? null : row?.not_ready ?? 0, blocked: row?.blocked_on_you === null ? null : row?.blocked_on_you ?? 0, total: row?.open === null ? null : row?.open ?? 0 }
}


// ── components ────────────────────────────────────────────────────────────

const num = (v) => (v == null ? '' : String(v))
const usd = (v) => `$${Number(v) >= 100 ? Math.round(v) : Number(v || 0).toFixed(Number(v) % 1 ? 2 : 0)}`

/** Four valves; older cores without max_workers keep the running-worker gauge. */
function Valves({ row, limits, usage, draft, saving, onShare, onPause, onLimit }) {
  const note = drift(row)
  const cap = (key, label, step, used, fmt) => {
    const value = draft?.[key] !== undefined ? draft[key] : limits?.[key]
    return h('label', { className: 'hermes-lanes-valve', 'data-valve': key },
      h('span', { className: 'hermes-lanes-valvename' }, label),
      h('input', { type: 'number', min: 0, step, placeholder: '∞', value: num(value), 'aria-label': `${row.name} ${label}`, disabled: saving,
        onChange: (e) => onLimit(row.name, key, e.target.value === '' ? null : Number(e.target.value)) }),
      h('span', { className: 'hermes-lanes-dim' }, usage?.[used] == null ? '' : `${fmt(usage[used])} today`))
  }
  return h('div', { className: 'hermes-lanes-valves' },
    h('div', { className: 'hermes-lanes-volume' },
      h('input', { type: 'range', min: 0, max: 100, step: 5, value: row.share, 'aria-label': `${row.name} volume`, disabled: saving, onChange: (e) => onShare(row.name, Number(e.target.value)) }),
      h('button', { type: 'button', className: row.paused ? 'is-paused' : '', 'aria-pressed': row.paused, disabled: saving, onClick: () => onPause(row.name, !row.paused) }, row.paused ? 'Paused' : 'Pause')),
    h('div', { className: 'hermes-lanes-dim' }, h('span', { className: 'hermes-lanes-big' }, pct(row.target)), ' target',
      h('span', { className: note ? 'hermes-lanes-warn' : '' }, ` · ${pct(row.actual)} actual${note ? ` · ${note}` : ''}`)),
    limits && Object.hasOwn(limits, 'max_workers')
      ? h('div', null, cap('max_workers', 'max workers', 1, 'running', String), h('span', { className: 'hermes-lanes-dim' }, `${summaryValue(row.running)} running`))
      : h('div', { className: 'hermes-lanes-dim', 'data-valve': 'workers' }, `workers ${summaryValue(row.running)} running`),
    cap('cards_per_day', 'cards/day', 1, 'cards_today', (v) => String(v)),
    cap('usd_per_day', '$/day', 0.5, 'usd_today', usd))
}

function Cell({ column, cell = {}, flowLane, onOpen }) {
  const pile = cell.piling
  const cards = cell.cards || []

  if (column.key === 'done') {
    return h('div', { className: 'hermes-lanes-cell', 'data-step': 'done', 'data-count': cell.count ?? 0 },
      h('div', { className: 'hermes-lanes-count' }, cell.count ?? 0),
      h('div', { className: 'hermes-lanes-dim' }, flowLane ? `${flowLane.run_hours ?? 0} run-h${flowLane.cost_usd ? ` · ${usd(flowLane.cost_usd)}` : ''}` : ''))
  }
  return h('div', { className: `hermes-lanes-cell${pile ? ' is-piling' : ''}`, 'data-step': column.key, 'data-count': cell.count ?? 0, 'data-piling': pile ? '1' : '0' },
    h('div', { className: 'hermes-lanes-count' }, cell.count || '·'),
    h('div', { className: 'hermes-lanes-boxes' }, cards.map((c) => {
      const info = reasonInfo(c.reason)
      return h('button', {
        key: c.id, type: 'button', className: 'hermes-lanes-box', 'data-card': c.id, 'data-reason': c.reason,
        title: `${c.title || c.id}\n${info.label} · ${ageHours(c.age_h)} old${c.pr ? ` · ${prLabel(c.pr)}` : ''}`, 'aria-label': `${c.title || c.id}, ${info.label}`,
        style: { background: info.color }, onClick: () => onOpen(c.id)
      })
    })),
    cell.summary ? h('div', { className: 'hermes-lanes-dim hermes-lanes-why' }, cell.summary) : null)
}

/** Mockup A: lanes are rows, steps are columns, one box per card coloured by why it waits; piled-up cells are hatched. */
export function Floor({ list, flow, flowError, draft, saving, edit, onOpen }) {
  const columns = floorColumns(flow)
  const names = flow?.floor?.names || list.map((r) => r.name)
  const grid = floorGrid(flow, names)
  const byName = Object.fromEntries(list.map((r) => [r.name, r]))
  const style = { gridTemplateColumns: `240px repeat(${columns.length}, minmax(86px, 1fr))` }
  return h('div', { className: 'hermes-lanes-floor', 'data-view': 'floor' },
    flowError ? h('div', { className: 'hermes-lanes-warn' }, `Cards unavailable: ${flowError}`) : null,
    h('div', { className: 'hermes-lanes-frow hermes-lanes-fhead', style },
      h('span', null, 'Lane'),
      columns.map((col) => h('span', { key: col.key, title: col.hint, 'data-column': col.key }, col.label))),
    names.map((name) => {
      const row = byName[name]
      const flowLane = flow?.lanes?.[name]
      return h('div', { key: name, className: 'hermes-lanes-frow', 'data-lane': name, style },
        h('div', { className: 'hermes-lanes-lanecell' },
          h('div', { className: 'hermes-lanes-name' },
            h('span', { className: 'hermes-lanes-dot', style: { background: row?.color || '#b4bac6' }, 'aria-hidden': 'true' }),
            h('div', null,
              h('div', { className: 'hermes-lanes-title' }, name),
              row ? h('div', { className: 'hermes-lanes-sub', title: row.describe || undefined },
                row.gated ? `gated · ${row.admitted_today}${row.admit_per_day == null ? '' : `/${row.admit_per_day}`} admitted today` : (row.describe || '')) : null)),
          row ? h(Valves, {
            row, limits: flowLane?.limits, usage: flowLane?.usage, draft: draft[name], saving,
            onShare: (n, share) => edit(n, { share }), onPause: (n, paused) => edit(n, { paused }), onLimit: (n, key, value) => edit(n, { [key]: value })
          }) : null),
        columns.map((col) => h(Cell, { key: col.key, column: col, cell: grid.find((c) => c.lane === name && c.step === col.key), flowLane, onOpen })))
    }),
    h('div', { className: 'hermes-lanes-legend' }, ['you', 'dep', 'gate', 'limit:usd_per_day', 'time', 'slot', 'triage', 'run', 'review'].map((r) => h('span', { key: r },
      h('i', { style: { background: reasonInfo(r).color } }), reasonInfo(r).label)), h('span', { className: 'hermes-lanes-dim' }, 'Hatched = piling up (4+ cards, or any that need you).')))
}

/** Mockup B: sources → lanes → flowing steps or dams, a dam labelled with what clears it. */
export function Flow({ flow, flowError, by, onBy, list, onEditLimits }) {
  if (!flow || flow.warming || flow.configured === false) return h('div', { className: 'hermes-lanes-note', 'data-view': 'flow' }, flow?.warming ? 'Loading…' : flowError ? `Flow unavailable: ${flowError}` : 'Loading…')
  const graph = flowGraph(flow, by)
  const geo = sankey(graph)
  const color = Object.fromEntries(list.map((r) => [r.name, r.color]))
  const laneColor = (name) => color[name] || '#b4bac6'
  const hints = Object.fromEntries(graph.sinks.map((s) => [s.key, s.hint]))
  const W = geo.width + 340
  return h('div', { className: 'hermes-lanes-flow', 'data-view': 'flow' },
    h('div', { className: 'hermes-lanes-head' },
      h('span', { className: 'hermes-lanes-dim' }, `${graph.total} open cards: where each came from, which lane took it, and what holds it (dashed = dam)`),
      h('span', { className: 'hermes-lanes-grow' }),
      h('div', { className: 'hermes-lanes-seg', role: 'group', 'aria-label': 'Group sources' },
        [['project', 'by project'], ['source', 'by source']].map(([value, label]) => h('button', { key: value, type: 'button', className: value === by ? 'is-on' : '', 'aria-pressed': value === by, onClick: () => onBy(value) }, label)))),
    graph.total === 0 ? h('div', { className: 'hermes-lanes-note' }, 'No open cards in any lane.') : h('svg', {
      className: 'hermes-lanes-sankey', viewBox: `0 0 ${W} ${geo.height + 16}`, width: '100%', role: 'img', 'aria-label': `${graph.total} open cards by source, lane and step`
    },
      h('g', { transform: 'translate(0,16)' },
        h('text', { x: 0, y: -4, className: 'hermes-lanes-svglabel' }, 'SOURCES'),
        h('text', { x: geo.columns[1][0]?.x ?? 330, y: -4, className: 'hermes-lanes-svglabel' }, 'LANES'),
        h('text', { x: geo.columns[2][0]?.x ?? 640, y: -4, className: 'hermes-lanes-svglabel' }, 'FLOWING / DAMMED BY'),
        geo.links.map((l, i) => h('path', { key: i, d: l.d, fill: 'none', stroke: laneColor(l.lane), strokeOpacity: 0.35, strokeWidth: l.width, 'data-edge': `${l.from}>${l.to}`, 'data-count': l.count })),
        geo.columns.map((col, ci) => col.map((n) => {
          const fill = ci === 1 ? laneColor(n.key) : ci === 2 ? n.color : 'var(--ui-stroke-secondary)'
          return h('g', { key: `${ci}:${n.key}`, 'data-flow-node': ci === 0 ? 'source' : ci === 1 ? 'lane' : 'sink', 'data-key': n.key, 'data-count': n.count },
            h('rect', { x: n.x, y: n.y, width: geo.nodeW, height: n.h, rx: 2, fill, stroke: ci === 2 && n.dam ? n.color : 'none', strokeDasharray: ci === 2 && n.dam ? '3 2' : undefined, fillOpacity: ci === 2 && n.dam ? 0.45 : 1 }),
            ci === 0
              ? h('text', { x: n.x + geo.nodeW + 6, y: n.y + Math.min(n.h, 14) - 3, className: 'hermes-lanes-svgtext' }, `${n.key} · ${n.count}`)
              : h('text', { x: n.x + geo.nodeW + 6, y: n.y + Math.min(n.h, 14) - 3, className: 'hermes-lanes-svgtext' }, ci === 1 ? `${n.key} ${n.count}` : `${n.dam ? '⤓' : '→'} ${n.label} ${n.count}`),
            ci === 2 && hints[n.key] ? h('text', { x: n.x + 190, y: n.y + Math.min(n.h, 14) - 3, className: 'hermes-lanes-svgtext hermes-lanes-dim', 'data-hint': n.key }, hints[n.key]) : null)
        })))),
    h('div', { className: 'hermes-lanes-limits' },
      list.map((r) => {
        const fl = flow.lanes?.[r.name]
        const lim = fl?.limits || {}
        const use = fl?.usage || {}
        const over = (cap, used) => cap != null && Number(used) >= cap
        return h('span', { key: r.name, className: 'hermes-lanes-limit', 'data-limit-lane': r.name },
          h('span', { className: 'hermes-lanes-dot', style: { background: r.color }, 'aria-hidden': 'true' }),
          h('b', null, r.name), h('span', { className: 'hermes-lanes-dim' }, pct(r.target)),
          lim.max_workers == null ? null : h('span', null, `${r.running}/${lim.max_workers}w`),
          lim.cards_per_day == null ? null : h('span', { className: over(lim.cards_per_day, use.cards_today) ? 'hermes-lanes-warn' : '' }, `${use.cards_today ?? 0}/${lim.cards_per_day} cards`),
          lim.usd_per_day == null ? null : h('span', { className: over(lim.usd_per_day, use.usd_today) ? 'hermes-lanes-warn' : '' }, `${usd(use.usd_today ?? 0)}/${usd(lim.usd_per_day)}`))
      }),
      h('span', { className: 'hermes-lanes-grow' }),
      h('button', { type: 'button', onClick: onEditLimits }, 'Edit limits')))
}

export function Panel({ snap, flow, flowError, windowHours, onWindow, onApply, saving, error, onBoard, onOpenCard, storage }) {
  flow = snap?.flow ? { ...snap.flow, floor: snap.floor } : flow
  flowError = snap?.flow_error || flowError
  const [draft, setDraft] = useState({})
  const [tab, setTabState] = useState(() => loadTab(storage))
  const [by, setByState] = useState(() => loadBy(storage))
  if (!snap || snap.warming) return h('div', { className: 'hermes-lanes-note' }, snap?.warming ? 'Loading…' : error ? `Unavailable: ${error}` : 'Loading…')
  if (!snap.configured) return h('div', { className: 'hermes-lanes-note' }, snap.reason || 'No lanes are configured.')
  const list = rows(snap, draft)
  const pending = changes(snap, draft, flow)
  const setTab = (value) => { setTabState(value); saveTab(value, storage) }
  const edit = (name, patch) => setDraft((d) => ({ ...d, [name]: { ...d[name], ...patch } }))
  const notes = snap.planner?.notes || []
  return h('div', { className: 'hermes-lanes' },
    h('div', { className: 'hermes-lanes-head' },
      h('strong', null, 'Lanes'),
      snap.flow_complete === false ? h('span', { role: 'status' }, 'Partial lanes data · unavailable counts shown as —') : null,
      h('span', { className: 'hermes-lanes-dim' }, `${summaryValue(snap.totals?.running)} running · ${summaryValue(snap.totals?.open)} open · ${snap.governors ? 'all hub boards' : snap.scope === 'fleet' ? 'whole fleet' : 'this board'}`),
      snap.shadow ? h('span', { className: 'hermes-lanes-warn', title: 'kanban.admission.lane_shadow is on: cards are classified and counted, but nothing is held, capped, reordered or paused' }, 'Shadow mode: reporting only') : null,
      h('div', { className: 'hermes-lanes-seg', role: 'tablist', 'aria-label': 'View' },
        TABS.map(([value, label]) => h('button', { key: value, type: 'button', role: 'tab', className: value === tab ? 'is-on' : '', 'aria-selected': value === tab, onClick: () => setTab(value) }, label))),
      h('span', { className: 'hermes-lanes-grow' }),
      onBoard ? h('button', { type: 'button', onClick: onBoard }, 'Board') : null,
      h('div', { className: 'hermes-lanes-seg', role: 'group', 'aria-label': 'Window' },
        WINDOWS.map(([value, label]) => h('button', { key: value, type: 'button', className: value === windowHours ? 'is-on' : '', 'aria-pressed': value === windowHours, onClick: () => onWindow(value) }, label)))),
    tab === 'flow'
      ? h(Flow, { flow, flowError, by, list, onBy: (value) => { setByState(value); saveBy(value, storage) }, onEditLimits: () => setTab('floor') })
      : h(Floor, { list, flow, flowError, draft, saving, edit, onOpen: onOpenCard || (() => {}) }),
    h('div', { className: 'hermes-lanes-foot' },
      h('div', { className: 'hermes-lanes-notes' },
        notes.length ? notes.map((n, i) => h('div', { key: i }, h('strong', null, `${n.lane}: `), n.text))
          : h('span', { className: 'hermes-lanes-dim' }, 'No planner notes yet.')),
      error ? h('span', { className: 'hermes-lanes-warn' }, error) : null,
      h('button', { type: 'button', disabled: !pending.length || saving, onClick: () => setDraft({}) }, 'Reset'),
      h('button', { type: 'button', className: 'is-primary', disabled: !pending.length || saving, onClick: async () => { if (await onApply(pending)) setDraft({}) } }, saving ? 'Applying…' : 'Apply')))
}

/** A lane's three strip counts: blocked on you (red), running (blue), everything else (grey). Zeros are left out. */
export function laneCounts(row) {
  const m = stripMeter(row)
  return [['blocked', m.blocked], ['running', m.running], ['other', m.notReady]].filter(([, n]) => n > 0)
}

/** The hub's per-lane `strip` counts (same card reasons as the totals); older hubs: the lane snapshot's counts. */
export function stripMeter(row) {
  const s = row?.strip
  if (!s) return meter(row)
  return { blocked: s.blocked === null ? null : s.blocked || 0, running: s.running === null ? null : s.running || 0, notReady: s.other === null ? null : s.other || 0, total: [s.blocked, s.running, s.other].includes(null) ? null : (s.blocked || 0) + (s.running || 0) + (s.other || 0) }
}

export function tokenScale(list) {
  return Math.max(1, ...list.flatMap((r) => r.strip?.tokens || []))
}

export function LaneSpark({ tokens, scale }) {
  if (!Array.isArray(tokens) || tokens.length < 2) return null
  const points = tokens.map((n, i) => `${(i * 34 / (tokens.length - 1)).toFixed(1)},${(13 - Math.min(1, Math.max(0, n) / Math.max(1, scale)) * 12).toFixed(1)}`).join(' ')
  return h('svg', { className: 'hermes-lanes-spark', width: 34, height: 14, viewBox: '0 0 34 14', 'aria-hidden': true },
    h('polygon', { points: `0,14 ${points} 34,14`, fill: '#3ddc97', fillOpacity: .18 }),
    h('polyline', { points, fill: 'none', stroke: '#3ddc97', strokeWidth: 1.3, strokeLinejoin: 'round' }))
}

export function laneTitle(row, selected) {
  const m = stripMeter(row), s = row.strip || {}
  const lines = [row.name]
  if (s.token_avg != null) lines[0] += `: ${compact(s.token_avg)} tok/m avg over 6 h`
  lines.push(`${summaryValue(m.blocked)} blocked on you`)
  const cap = s.cap == null ? '' : s.cap_kind === 'max' ? ` · max ${s.cap}` : ` · fair share ${s.cap} of ${s.fleet_running} busy slots (${pct(row.target)} target)`
  lines.push(`${summaryValue(m.running)} running${cap}`)
  if (s.over > 0) lines.push(`OVER by ${s.over}: borrowing idle slots — ${(s.idle_lanes || []).join(', ') || 'other lanes'} have nothing ready; ${s.actual == null ? 'actual' : '6 h actual'} ${pct(s.actual ?? row.actual)} vs ${pct(row.target)} target`)
  const mix = Object.entries(s.wait || {}).sort((a, b) => b[1] - a[1]).map(([r, n]) => `${n} ${reasonInfo(r).label}`).join(', ')
  lines.push(`${summaryValue(m.notReady)} waiting${mix ? ` (${mix})` : ''}`, `Click to ${selected ? 'deselect' : 'select'} this lane`)
  return lines.join('\n')
}

export function MiniPill({ row, onToggle, selected, dimmed, scale = 1 }) {
  const m = stripMeter(row), s = row.strip || {}
  // Older hubs have neither capacity nor series: retain their counts without inventing an overage.
  const over = s.over || 0
  return h('button', {
    type: 'button', className: `hermes-lanes-pill${over > 0 ? ' is-over' : ''}`, 'data-lane-pill': row.name,
    title: laneTitle(row, selected),
    'aria-label': `${row.name}: ${summaryValue(m.blocked)} blocked on you, ${summaryValue(m.running)} running, ${summaryValue(m.notReady)} waiting`,
    'aria-pressed': selected, 'data-selected': selected ? '1' : '0', 'data-dimmed': dimmed ? '1' : '0',
    onClick: (event) => { event.stopPropagation(); onToggle(row.name) }
  },
    h('span', { className: 'hermes-lanes-pill-name' }, row.name),
    m.total == null ? h('span', { className: 'hermes-lanes-dim' }, 'partial · ', summaryValue(m.total)) : null,
    h(LaneSpark, { tokens: s.tokens, scale }),
    m.blocked > 0 ? h('span', { className: 'hermes-lanes-bd sq', 'data-count': 'blocked', style: { background: CARD_COLORS.you } }, m.blocked) : null,
    m.running > 0 ? h('span', { className: 'hermes-lanes-bd cap', 'data-count': 'running' },
      h('span', { style: { background: CARD_COLORS.run }, 'data-part': 'total-running' }, m.running),
      over > 0 ? h('span', { style: { background: '#f59e0b' }, 'data-part': 'over', title: `${over} of ${m.running} running borrow idle capacity` }, `+${over} over`) : null) : null,
    m.notReady > 0 ? h('span', { className: 'hermes-lanes-bd sq hollow', 'data-count': 'other' }, m.notReady) : null)
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

const GATE_KIND = { lane_gate: 'lane gate', 'limit:cards_per_day': 'cards/day limit', 'limit:usd_per_day': '$/day limit' }
/** The pill word: the one gate holding cards ("held at hermes lane gate"), or a count of gates. */
export function gateWord(gates, scope = '') {
  const names = scope ? scope.split(', ') : []
  const shown = (gates || []).filter((g) => !names.length || names.includes(g.lane))
  if (shown.length === 1) return `held at ${shown[0].lane} ${GATE_KIND[shown[0].kind] || shown[0].kind}`
  return shown.length ? `held at ${shown.length} gates` : 'held at a gate'
}
/** Names each gate holding cards: "hermes lane gate: 46 (0/2 admitted today; 45 not linked to a chat: 30 ci-sweeper, …)". */
export function gateLines(gates, scope = []) {
  return (gates || []).filter((g) => !scope.length || scope.includes(g.lane)).map((g) => {
    const kind = GATE_KIND[g.kind] || g.kind
    const budget = g.kind === 'lane_gate' && g.admitted_today != null ? `${g.admitted_today}${g.admit_per_day == null ? '' : `/${g.admit_per_day}`} admitted today` : ''
    const top = Object.entries(g.unlinked || {}).slice(0, 3).map(([who, n]) => `${n} ${who}`).join(', ')
    const unlinked = Object.values(g.unlinked || {}).reduce((a, n) => a + n, 0)
    const orphans = unlinked ? `${unlinked} not linked to a chat${top ? `: ${top}` : ''}` : ''
    const extra = [budget, orphans].filter(Boolean).join('; ')
    return `${g.lane} ${kind}: ${g.count}${extra ? ` (${extra})` : ''}`
  })
}

/** The counts on the right, from the hub's `status` projection (older hubs send none: nothing shows): [all], one
 *  toggle per category with cards, and the no-cards toggle (only where the Desktop can list and filter sessions). */
function StatusTotals({ status, gates, scope, picked, noCards, noCardsCount, anySelected, onToggle, onNoCards, onAll }) {
  if (!status) return null
  const items = STATUS.filter(([key]) => (status[key] || 0) > 0 || picked.includes(key))
  const where = scope ? ` in ${scope}` : ''
  return h('span', { className: 'hermes-lanes-status', role: 'group', 'aria-label': 'Card categories', 'data-slot': 'lanes-status', 'data-scope': scope || 'all' },
    h('button', {
      type: 'button', className: 'hermes-lanes-cat hermes-lanes-all', 'data-status-all': '1', 'aria-pressed': !anySelected,
      'data-selected': anySelected ? '0' : '1', title: 'All: clear every lane and category selection',
      onClick: (event) => { event.stopPropagation(); onAll() }
    }, 'all'),
    items.map(([key, label, color]) => {
      const on = picked.includes(key)
      const n = status[key] || 0
      return h('button', {
        type: 'button', key, className: 'hermes-lanes-cat hermes-lanes-status-item', 'data-status': key,
        'aria-pressed': on, 'data-selected': on ? '1' : '0', 'data-dimmed': picked.length && !on ? '1' : '0',
        title: `${COLOR_WORDS[key]}: ${label}, ${plural(n, 'card')}${where}${key === 'gate' && gates?.length ? ` — ${gateLines(gates, scope ? scope.split(', ') : []).join('; ')}` : ''}. Click to ${on ? 'deselect' : 'select'}`,
        onClick: (event) => { event.stopPropagation(); onToggle(key) }
      },
        h('span', { className: color ? 'hermes-lanes-sw' : 'hermes-lanes-sw is-outline', style: color ? { background: color } : undefined, 'aria-hidden': 'true' }),
        h('span', { className: 'hermes-lanes-n' }, n),
        h('span', { className: 'hermes-lanes-status-word' }, key === 'gate' && gates?.length ? gateWord(gates, scope) : label))
    }),
    noCardsCount == null ? null : h('button', {
      type: 'button', className: 'hermes-lanes-cat hermes-lanes-status-item', 'data-status': 'nocards',
      'aria-pressed': noCards, 'data-selected': noCards ? '1' : '0',
      title: `Dashed: sessions with no open cards, ${plural(noCardsCount, 'session')} in your list. Click to ${noCards ? 'deselect' : 'select'}`,
      onClick: (event) => { event.stopPropagation(); onNoCards() }
    },
      h('span', { className: 'hermes-lanes-sw is-dashed', 'aria-hidden': 'true' }),
      h('span', { className: 'hermes-lanes-n' }, noCardsCount),
      h('span', { className: 'hermes-lanes-status-word' }, 'no cards')))
}

// Plugin-owned area: SDK versions before generic Slot export can still host a
// contribution. Availability follows a mounted configured strip, never activation.
export function stripArea() {
  const key = Symbol.for('hermes.lanes.stripRight.v1')
  return globalThis[key] ||= { hosts: new Set(), item: null, listeners: new Set(),
    notify() { for (const fn of [...this.listeners]) fn() } }
}
export function StripRight() {
  const area = stripArea(), token = useRef({}).current
  const [, update] = useState(0)
  useEffect(() => {
    const changed = () => update((n) => n + 1)
    area.listeners.add(changed); area.hosts.add(token); area.notify()
    return () => { area.listeners.delete(changed); area.hosts.delete(token); area.notify() }
  }, [area, token])
  return h('span', { className: 'hermes-lanes-strip-right', 'data-slot': 'lanes.stripRight' },
    area.hosts.values().next().value === token ? area.item?.render() : null)
}

// The strip owns the totals' state; the tab-strip contribution only paints what the strip hands it. Two listener
// sets so the strip re-renders on host mount/unmount only, never on its own item updates (no render loop).
export function tabTotalsArea() {
  const key = Symbol.for('hermes.lanes.sessionsTabTotals.v1')
  return globalThis[key] ||= { hosts: new Set(), item: null, hostListeners: new Set(), itemListeners: new Set(),
    hostsChanged() { for (const fn of [...this.hostListeners]) fn() },
    set(item) { this.item = item; for (const fn of [...this.itemListeners]) fn() } }
}
export function SessionsTabTotals() {
  const area = tabTotalsArea(), token = useRef({}).current
  const [, update] = useState(0)
  useEffect(() => {
    const changed = () => update((n) => n + 1)
    area.itemListeners.add(changed); area.hosts.add(token); area.hostsChanged()
    return () => { area.itemListeners.delete(changed); area.hosts.delete(token); area.hostsChanged() }
  }, [area, token])
  const mine = area.hosts.values().next().value === token
  return mine && area.item ? h('span', { className: 'hermes-lanes-tabtotals', 'data-slot': 'lanes-tab-totals' }, area.item.render()) : null
}
function useTabTotalsHosted() {
  const area = tabTotalsArea()
  const [hosted, setHosted] = useState(() => area.hosts.size > 0)
  useEffect(() => {
    const changed = () => setHosted(area.hosts.size > 0)
    area.hostListeners.add(changed); changed()
    return () => { area.hostListeners.delete(changed) }
  }, [area])
  return hosted
}

// One state/filter owner for both SDK layouts; modern Desktop paints totals only.
export function Lanes({ ctx, totalsOnly = false }) {
  const [open, setOpenState] = useState(loadOpen)
  // Legacy lane pills are absent in totals-only mode; their persisted selection
  // must not invisibly scope fleet totals or the Sessions filter.
  const [selected, setSelectedState] = useState(() => totalsOnly ? [] : loadSelected())
  const [statusPicked, setStatusPicked] = useState(loadStatusSelected)
  const [noCards, setNoCards] = useState(loadNoCards)
  const sidebarSessions = useSidebarSessions()
  const [windowHours, setWindowState] = useState(loadWindow)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const bodyRef = useRef(null)
  const stripRef = useRef(null)
  const [bodyMaxHeight, setBodyMaxHeight] = useState('60vh')
  const source = useMemo(() => sourceFor(ctx), [ctx])
  const setOpen = (value) => {
    setOpenState((prev) => {
      const next = typeof value === 'function' ? value(prev) : value
      saveOpen(next)
      return next
    })
  }
  const [snap, setSnap] = useState()
  const [live, setLive] = useState(false)
  const tabHosted = useTabTotalsHosted()
  const tabTotals = useRef(null)
  // Hand the latest totals to the Sessions tab strip after every render; clear them when the strip stops showing.
  useEffect(() => { tabTotalsArea().set(tabHosted ? tabTotals.current : null) })
  useEffect(() => () => tabTotalsArea().set(null), [])
  const subscription = useRef()
  useEffect(() => {
    // Sidebar owns modern request parameters. A totals-only subscriber must not
    // downgrade its expanded projection, regardless of mount order/remounts.
    // If totals mounts first, start with the sidebar's same complete defaults.
    const params = totalsOnly ? { hours: 6, window: '6h', expanded: true } :
      { hours: windowHours, window: flowWindow(windowHours), expanded: open }
    subscription.current = attentionStore(ctx).connect(params,
      (view, err) => { if (view) setSnap(view); setError(err ? String(err.message || err) : '') }, setLive, !totalsOnly)
    return () => subscription.current?.()
  }, [source, totalsOnly])
  const previous = useRef({ windowHours, open })
  useEffect(() => {
    if (!totalsOnly && (previous.current.windowHours !== windowHours || previous.current.open !== open)) {
      subscription.current?.update({ hours: windowHours, window: flowWindow(windowHours), expanded: open })
      previous.current = { windowHours, open }
    }
  }, [windowHours, open, totalsOnly])
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener('hermes-show-lanes', show)
    return () => window.removeEventListener('hermes-show-lanes', show)
  }, [])
  // The task pill expands this strip only where lanes are configured and the API answers; anywhere else its
  // click is left alone and opens the board as before.
  const working = snap?.configured === true
  useEffect(() => {
    const zone = stripRef.current?.closest('[data-tree-group]')
    if (!zone) return undefined // Older hosts use the full-window shell area.
    const measure = () => setBodyMaxHeight(`${Math.max(0, zone.getBoundingClientRect().height) * 0.6}px`)
    measure()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(measure)
    observer.observe(zone)
    return () => observer.disconnect()
  }, [working])

  useEffect(() => {
    if (!working || totalsOnly) return undefined
    const take = (event) => { event.preventDefault(); setOpen(true) }
    window.addEventListener(KANBAN_CHIP_EVENT, take)
    return () => window.removeEventListener(KANBAN_CHIP_EVENT, take)
  }, [working, totalsOnly])
  // The session-list filter: registered only while something is selected (and re-registered when the selection or
  // the hub's session map changes, so the list re-filters); the list's own "clear filters" calls onClear.
  const sessionMap = snap?.sessions
  const sessionKey = useMemo(() => JSON.stringify(sessionMap || null), [sessionMap])
  const filterLanes = selected.filter((name) => snap?.lanes?.[name])
  const filterKey = JSON.stringify([filterLanes, statusPicked, noCards])
  useEffect(() => {
    if (!SESSION_FILTER_AREA || !working || !sessionMap) return undefined
    const sel = { lanes: filterLanes, status: statusPicked, noCards }
    if (!sel.lanes.length && !sel.status.length && !sel.noCards) return undefined
    const parts = [...sel.lanes, ...sel.status.map((key) => STATUS.find(([k]) => k === key)?.[1] || key), ...(sel.noCards ? ['no cards'] : [])]
    const match = (session) => sessionMatches(session, sessionMap, sel)
    const store = attentionStore(ctx)
    store.setSessionMatch(match)
    const unregister = ctx.register({
      id: 'session-filter', area: SESSION_FILTER_AREA,
      data: { label: `Lanes: ${parts.join(', ')}`, match, onClear: () => clearAll() }
    })
    return () => { unregister(); store.setSessionMatch(() => true) }
  }, [filterKey, sessionKey, working])
  // One PUT per changed lane, in order. On a failure the lanes already written stay written: refetch so the panel
  // shows them, name the lane that failed, and keep the draft so the rest can be retried.
  const apply = async (pending) => {
    setSaving(true); setError('')
    let at = ''
    const target = source.target()
    try {
      for (const [name, body] of pending) {
        at = name
        await source.write(name, body, target)
      }
      if (!live) await subscription.current?.refresh()
      return true
    } catch (e) {
      if (!live) await subscription.current?.refresh()
      setError(`${at}: ${String(e?.message || e).slice(0, 180)}`)
      return false
    } finally { setSaving(false) }
  }
  tabTotals.current = null
  if (snap?.warming) return h('div', { className: 'hermes-lanes-note', 'data-view': 'warming' }, 'Loading…')
  if (!working) return null
  const list = rows(snap)
  // A lane pill selects/deselects that lane for the totals on the right; only the arrow expands or collapses.
  const shown = list.filter((row) => (row.strip ? stripMeter(row).total == null || stripMeter(row).total > 0 : row.open == null || Number(row.open) > 0)) // only known-empty lanes are hidden
  const picked = selected.filter((name) => shown.some((row) => row.name === name))
  const toggleLane = (name) => setSelectedState((prev) => {
    const next = prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]
    saveSelected(next)
    return next
  })
  function clearAll() {
    setSelectedState([]); saveSelected([])
    setStatusPicked([]); saveStatusSelected([])
    setNoCards(false); saveNoCards(false)
  }
  const toggleStatus = (key) => setStatusPicked((prev) => {
    const next = prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    saveStatusSelected(next)
    return next
  })
  const toggleNoCards = () => setNoCards((prev) => { saveNoCards(!prev); return !prev })
  const anySelected = picked.length > 0 || statusPicked.length > 0 || noCards
  const noCardsCount = sidebarSessions && snap?.sessions ? sidebarSessions.filter((s) => !sessionCards(s, snap.sessions).length).length : null
  const toggle = () => setOpen((value) => !value)
  const totals = [
    h(StatusTotals, {
      key: 'totals',
      status: selectedStatus(snap, picked), gates: snap?.gates, scope: picked.length ? picked.join(', ') : '', picked: statusPicked, noCards,
      noCardsCount: SESSION_FILTER_AREA ? noCardsCount : null, anySelected, onToggle: toggleStatus, onNoCards: toggleNoCards, onAll: clearAll
    }),
    !live ? h('small', { key: 'live', className: 'hermes-lanes-dim', title: 'Push unavailable; refreshing every 60 seconds' }, 'not live') : null,
    totalsOnly ? null : h('button', {
      key: 'toggle', type: 'button', className: 'hermes-lanes-toggle', 'aria-expanded': open,
      'aria-label': open ? 'Collapse lanes' : 'Expand lanes', onClick: (event) => { event.stopPropagation(); toggle() }
    }, open ? '▴' : '▾')
  ]
  tabTotals.current = { render: () => totals }
  if (totalsOnly) return h('div', { className: 'hermes-lanes-mini' }, h(StripRight), tabHosted ? null : totals)
  return h('div', { ref: stripRef, className: 'hermes-lanes-strip', 'data-slot': 'lanes-strip', 'data-open': open ? '1' : '0' },
    h('div', { className: 'hermes-lanes-mini', role: 'group', 'aria-label': 'Lanes' },
      // Lane pills wrap inside their own box (min width = widest pill) so the chip, global filters and
      // toggle sit top-right whenever they fit beside one pill, and wrap below it (never overlap) when not.
      h('div', { className: 'hermes-lanes-pills', 'data-slot': 'lanes-pills' },
        shown.map((row) => h(MiniPill, {
          key: row.name, row, onToggle: toggleLane, scale: tokenScale(list),
          selected: picked.includes(row.name), dimmed: picked.length > 0 && !picked.includes(row.name)
        }))),
      h(StripRight),
      tabHosted ? null : totals),
    open ? h('div', { ref: bodyRef, className: 'hermes-lanes-body', style: { maxHeight: bodyMaxHeight, overflow: 'auto' } },
      h(Panel, {
        snap, flowError: '',
        windowHours, saving, error,
        onOpenCard: (id) => openCard(id, source.target()?.connectionId || undefined),
        onWindow: (v) => { setWindowState(v); saveWindow(v) }, onApply: apply,
        onBoard: () => host.navigate('/kanban')
      })) : null)
}

export const SIDEBAR_OPEN_KEY = 'hermes-lanes-sidebar-open'
function storedSidebarOpen() { try { return localStorage.getItem(SIDEBAR_OPEN_KEY) === '1' } catch { return false } }
const SIDEBAR_LANE_KEY = 'hermes-lanes-sidebar-lane'
function storedSidebarLane() { try { return localStorage.getItem(SIDEBAR_LANE_KEY) || '' } catch { return '' } }
const measureText = value => value == null ? 'unknown' : typeof value === 'object'
  ? Object.entries(value).map(([key, n]) => `${key} ${measureText(n)}`).join(' · ') : String(value)

export function GovernorLine({ row }) {
  const pool = row.id === 'llm_pool'
  const reading = pool && row.reading ? `${row.reading.used_pct ?? '?'}% used · ${row.reading.accounts ?? '?'} accounts` : measureText(row.reading)
  return h('div', { className: 'lanes-governor', 'data-governor': row.id, 'data-state': row.state },
    h('div', { className: 'lanes-sb-line' }, h('span', { className: 'lanes-gov-dot', 'aria-hidden': true }),
      h('span', null, row.name), h('b', null, summaryValue(row.state === 'unknown' ? null : row.lane_count ?? row.cards_charged), ' held')),
    h('div', { className: 'lanes-sb-muted' }, row.state === 'unknown' ? 'unknown · missing or stale source' : row.state,
      ' · ', reading, ' / ', measureText(row.threshold)),
    h('details', null, h('summary', null, 'Readings & release'),
      h('div', null, row.clears),
      (row.readings || []).map((r, i) => h('div', { key: i }, `${r.board}${r.lane ? ' / ' + r.lane : ''}: ${measureText(r.reading)} / ${measureText(r.threshold)}`,
        r.detail ? h('div', null, measureText(r.detail)) : null)),
      (row.scope || []).map((s, i) => h('div', { key: i }, `${s.gateway}/${s.board}: ${s.available ? `${s.age_s}s ago` : 'unknown'}`))))
}

export function LaneControls({ name, row, write, disabled }) {
  const [draft, setDraft] = useState({})
  const [message, setMessage] = useState('')
  const [pending, setPending] = useState(false)
  disabled ||= pending
  const fields = [['share', 'share', row.share], ['max_workers', 'max workers', row.limits?.max_workers],
    ['cards_per_day', 'cards / day', row.limits?.cards_per_day], ['usd_per_day', '$ / day', row.limits?.usd_per_day]]
  return h('form', { className: 'lanes-controls', onSubmit: async e => {
    e.preventDefault(); setMessage('')
    if (disabled || !Object.keys(draft).length) return
    setPending(true)
    try { await write(name, draft); setDraft({}); setMessage('Saved') } catch (err) { setMessage(String(err.message || err)) }
    finally { setPending(false) }
  } }, h('label', null, 'share', h('input', { type: 'range', min: 0, max: Math.max(100, row.share || 0), step: 1,
    'aria-label': `${name} share slider`, disabled, value: draft.share ?? row.share ?? 0,
    onChange: e => setDraft(prev => ({ ...prev, share: Number(e.target.value) })) })), fields.map(([key, label, original]) => h('label', { key }, label,
    h('input', { 'aria-label': `${name} ${label}`, type: 'number', min: 0, step: key === 'usd_per_day' ? '.01' : '1',
      disabled, placeholder: '∞', value: key in draft ? draft[key] ?? '' : original ?? '',
      onChange: e => setDraft(prev => ({ ...prev, [key]: e.target.value === '' ? null : Number(e.target.value) })) }))),
    h('label', null, h('input', { type: 'checkbox', disabled, checked: draft.paused ?? Boolean(row.paused),
      onChange: e => setDraft(prev => ({ ...prev, paused: e.target.checked })) }), 'Pause lane'),
    h('button', { type: 'submit', disabled }, 'Apply'), h('span', { role: 'status' }, message))
}

export function SidebarView({ snap, error, open, setOpen, write, onHeld, heldAvailable = false, disabled = false }) {
  const [laneOpen, updateLaneOpen] = useState(storedSidebarLane)
  const setLaneOpen = value => { updateLaneOpen(value); try { localStorage.setItem(SIDEBAR_LANE_KEY, value) } catch {} }
  const [showIdle, setShowIdle] = useState(false)
  const [showOff, setShowOff] = useState(false)
  const gov = snap?.governors
  const idle = snap?.sidebar?.idle_lanes || []
  const status = snap?.status || {}
  return h('section', { className: 'lanes-sidebar', 'data-slot': 'lanes-sidebar', 'data-offline': error ? '1' : '0' },
    h('button', { className: 'lanes-sb-heading', 'aria-expanded': open, onClick: () => setOpen(!open) }, 'Ⅲ LANES', open ? ' ▾' : ' ▸'),
    error ? h('div', { role: 'status' }, 'Lanes offline - retrying') : null,
    snap?.flow_complete === false ? h('div', { role: 'status', className: 'lanes-unknown' }, 'Partial lanes data · unavailable counts shown as —') : null,
    !snap ? h('div', null, 'Loading lanes…') : h('div', { className: error ? 'lanes-last-known' : '' },
      h('div', { className: 'lanes-sb-line', 'data-sidebar-totals': true },
        h('span', { style: { color: CARD_COLORS.run } }, `${summaryValue(status.run)} running`),
        h('span', { className: 'lanes-held' }, `${gov?.idle_total ?? '—'} held`),
        h('span', { style: { color: CARD_COLORS.you } }, `${summaryValue(status.you)} you`),
        h('span', null, `${summaryValue(snap.totals?.not_ready)} waiting`)),
      h('div', { className: 'lanes-sb-muted' }, `$${snap.sidebar?.cost_today?.toFixed(2) ?? '—'} today`),
      snap.summary_complete === false ? h('div', { className: 'lanes-sb-muted' }, 'Worker share unknown') :
        h('div', { className: 'lanes-share-bar', 'aria-label': 'Worker share' }, Object.entries(snap.lanes || {}).map(([name, row]) =>
          h('span', { key: name, title: `${name} ${pct(row.actual)}`, style: { width: pct(row.actual) } }))),
      open ? Object.entries(snap.lanes || {}).filter(([name]) => showIdle || !idle.includes(name)).map(([name, row]) =>
        h('div', { key: name, className: 'lanes-sb-lane', 'data-sidebar-lane': name },
          h('button', { className: 'lanes-sb-line', 'aria-expanded': laneOpen === name, onClick: () => setLaneOpen(laneOpen === name ? '' : name) },
            h('strong', null, name), row.paused ? h('small', null, 'paused') : null,
            h('span', { style: { color: CARD_COLORS.you } }, summaryValue(row.status?.you)),
            h('span', { style: { color: CARD_COLORS.run } }, summaryValue(row.status?.run)),
            h('span', { className: 'lanes-held' }, summaryValue(row.governor_held)), h('span', null, summaryValue(row.not_ready))),
          h('div', { className: 'lanes-sb-muted', 'data-top-reason': name }, row.top_reason || 'Governor state unknown'),
          h('div', { className: 'lanes-target-share', title: `Actual ${row.actual == null ? 'unknown' : pct(row.actual)} / target ${pct(row.target)}` },
            h('span', null, row.actual == null ? '—' : pct(row.actual)),
            row.actual == null ? null : h('meter', { min: 0, max: 1, value: row.actual }), h('span', null, `target ${pct(row.target)}`)),
          laneOpen === name ? h('div', { className: 'lanes-sb-detail' },
            h('h4', null, 'WHERE CARDS ARE'),
            h('div', { className: 'lanes-steps' }, (row.steps || []).map(s => h('div', { key: s.key, 'data-step': s.key,
              'data-piling': s.piling ? '1' : '0' }, s.label, h('b', null, s.count)))),
            h('h4', null, 'GOVERNORS ON THIS LANE'), (row.governors || []).map(g => h(GovernorLine, { key: g.id, row: g })),
            h('h4', null, 'LANE'), h(LaneControls, { name, row, write, disabled: disabled || Boolean(error) }),
            h('div', { className: 'lanes-sb-muted' }, 'boards: ', (row.boards || []).join(', ')),
            row.device_footnote ? h('div', { 'data-device-footnote': row.device_footnote.state },
              row.device_footnote.label, ' · ', row.device_footnote.state, ' · ', row.device_footnote.age_s == null ? 'not read' : `${Math.round(row.device_footnote.age_s)}s ago`) : null,
            h('button', { disabled: !heldAvailable || Boolean(error) || row.governor_held == null,
              title: heldAvailable ? 'Open exact held-card set on its owning gateway' : 'Held-card filtering needs a newer Desktop',
              onClick: () => onHeld(name, row) }, `Show ${row.governor_held ?? '—'} held cards →`)) : null)) : null,
      open && idle.length ? h('button', { onClick: () => setShowIdle(!showIdle) }, `${idle.length} idle lanes ${showIdle ? '▾' : '▸'}`) : null,
      h('h4', null, 'GOVERNORS'),
      gov ? gov.rows.filter(g => g.state === 'on' || (open && g.state === 'at_cap')).map(g => h(GovernorLine, { key: g.id, row: g })) : h('div', null, 'Governor state unknown'),
      gov?.counts?.unknown ? h('details', { className: 'lanes-unknown' }, h('summary', null, `${gov.counts.unknown} unknown · missing or stale sources`),
        gov.rows.filter(g => g.state === 'unknown').map(g => h(GovernorLine, { key: g.id, row: g }))) : null,
      h('button', { onClick: () => setShowOff(!showOff) }, `${gov?.counts?.off ?? '—'} off · ${gov?.counts?.at_cap ?? '—'} at cap`),
      showOff ? gov?.rows.filter(g => g.state === 'off').map(g => h(GovernorLine, { key: g.id, row: g })) : null))
}

export function SidebarLanes({ ctx }) {
  const [snap, setSnap] = useState()
  const [error, setError] = useState('')
  const [open, updateOpen] = useState(storedSidebarOpen)
  const source = sourceFor(ctx)
  const subscription = useRef()
  useEffect(() => {
    subscription.current = attentionStore(ctx).connect({ hours: 6, window: '6h', expanded: true }, (view, err) => {
      if (view?.configured && !view.warming) { setSnap(view); setError('') }
      else if (err || view?.configured === false) setError(String(err?.message || view?.reason || 'Offline'))
    }, () => {}, true)
    const show = () => updateOpen(true)
    window.addEventListener('hermes-show-lanes', show)
    return () => { subscription.current?.(); window.removeEventListener('hermes-show-lanes', show) }
  }, [ctx])
  const setOpen = value => { updateOpen(value); try { localStorage.setItem(SIDEBAR_OPEN_KEY, value ? '1' : '0') } catch {} }
  const showCards = window.__hermesKanbanShowCards
  return h(SidebarView, { snap, error, open, setOpen, heldAvailable: typeof showCards === 'function',
    write: async (lane, body) => { await source.write(lane, body); await subscription.current?.refresh() },
    onHeld: (_name, row) => showCards?.({ cards: row.held_cards, connectionId: source.target()?.connectionId }) })
}

export function TopTotals({ ctx }) {
  return h(Lanes, { ctx, totalsOnly: true })
}

const CSS = `
.lanes-sidebar{padding:6px 10px;font-size:11px;color:var(--ui-text-primary);min-width:0}
.lanes-sidebar button{color:inherit;background:transparent;border:0;font:inherit;cursor:pointer;text-align:left}
.lanes-sidebar button:focus-visible{outline:1px solid var(--ui-text-secondary)}
.lanes-sidebar h4,.lanes-sb-heading{font-size:10px;letter-spacing:.1em;color:var(--ui-text-tertiary);margin:10px 0 6px}
.lanes-sidebar .lanes-sb-heading{color:#8f99ff;font-weight:600;width:100%}
.lanes-sb-line{display:flex;align-items:center;gap:7px;width:100%;font-variant-numeric:tabular-nums}
.lanes-sb-line strong,.lanes-sb-line>span:nth-child(2){flex:1}
.lanes-sb-line b{margin-left:auto;font-weight:500}
.lanes-sb-muted{color:var(--ui-text-tertiary);font-size:10px;margin:4px 0;overflow-wrap:anywhere}
.lanes-held{color:#bb98ff}
.lanes-last-known{opacity:.45;filter:grayscale(1)}
.lanes-sb-lane{padding:7px 0;border-bottom:1px solid var(--ui-stroke-secondary)}
.lanes-governor{margin:6px 0}
.lanes-gov-dot{height:6px;width:6px;flex:none;border-radius:50%;background:#52525b}
.lanes-governor[data-state=on] .lanes-gov-dot{background:#ae87ee;box-shadow:0 0 4px #ae87ee}
.lanes-governor[data-state=unknown]{color:#e9ba70}
.lanes-governor details{font-size:10px;color:var(--ui-text-tertiary)}
.lanes-share-bar{display:flex;height:4px;background:var(--ui-stroke-secondary);margin:6px 0;overflow:hidden}
.lanes-share-bar span{background:#7aa5ec}.lanes-share-bar span:nth-child(2n){background:#e58b91}
.lanes-target-share{display:flex;align-items:center;gap:6px;font-size:9px;color:var(--ui-text-tertiary)}
.lanes-target-share meter{flex:1;min-width:30px;height:6px}
.lanes-sb-detail{padding:4px 0 6px 10px}
.lanes-steps{display:flex;gap:3px;flex-wrap:wrap}
.lanes-steps>div{padding:3px;border:1px solid var(--ui-stroke-secondary);font-size:9px;text-align:center}
.lanes-steps b{display:block;font-size:11px}
.lanes-steps [data-piling="1"]{background:repeating-linear-gradient(135deg,#ae87ee33 0 3px,transparent 3px 6px)}
.lanes-controls{display:grid;gap:5px;margin:6px 0}
.lanes-controls label{display:flex;justify-content:space-between;align-items:center}
.lanes-controls input[type=number]{width:65px;background:transparent;color:inherit;border:1px solid var(--ui-stroke-secondary);border-radius:3px;font:inherit}
.lanes-controls button{border:1px solid var(--ui-stroke-secondary);border-radius:3px;padding:3px 6px}

.hermes-lanes-strip{display:flex;flex-direction:column;min-width:0;width:100%}
.hermes-lanes-mini{display:flex;align-items:flex-start;gap:4px 8px;flex-wrap:wrap;padding:4px 8px;min-height:28px}
.hermes-lanes-pills{display:flex;align-items:center;flex-wrap:wrap;gap:4px 8px;flex:1 1 0;min-width:min-content}
.hermes-lanes-pills>.hermes-lanes-pill{flex:none}
.hermes-lanes-mini>.hermes-lanes-strip-right,.hermes-lanes-mini>.hermes-lanes-status,.hermes-lanes-mini>.hermes-lanes-toggle,.hermes-lanes-mini>small{flex:none}
.hermes-lanes-pill{display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 8px;border:1px solid var(--ui-stroke-secondary);border-radius:999px;background:transparent;color:inherit;font:inherit;cursor:pointer}
.hermes-lanes-pill-name{font-weight:600;font-size:12px}
.hermes-lanes-pill{gap:7px;white-space:nowrap}
.hermes-lanes-pill.is-over,.hermes-lanes-pill.is-over[data-selected="1"]{border-color:rgba(245,158,11,.7)}
.hermes-lanes-spark{flex:none;opacity:.9}
.hermes-lanes-bd{display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;min-width:15px;height:15px;padding:0 3px;font-size:10.5px;font-weight:700;font-variant-numeric:tabular-nums;color:#fff;line-height:1}
.hermes-lanes-bd.sq{border-radius:3px}
.hermes-lanes-bd.hollow{background:transparent;border:1.3px solid #6b7079;color:#a1a1aa}
.hermes-lanes-bd.cap{padding:0;border-radius:999px;overflow:hidden}
.hermes-lanes-bd.cap span{display:inline-flex;align-items:center;height:15px;padding:0 4px}
.hermes-lanes-strip-right{margin-left:auto;display:inline-flex;align-items:center}
.hermes-lanes-strip-right+.hermes-lanes-status{margin-left:0}
.hermes-lanes-pill[data-selected="1"]{border-color:var(--ui-text-tertiary);background:var(--ui-stroke-secondary)}
.hermes-lanes-pill[data-dimmed="1"]{opacity:.45}
.hermes-lanes-n{font-variant-numeric:tabular-nums;font-size:11.5px;font-weight:600}
.hermes-lanes-status{margin-left:auto;display:inline-flex;align-items:center;gap:2px;padding-left:10px;border-left:1px solid var(--ui-stroke-secondary);white-space:nowrap}
.hermes-lanes-status-item{display:inline-flex;align-items:center;gap:4px}
.hermes-lanes-cat{height:22px;padding:0 6px;border:1px solid transparent;border-radius:999px;background:transparent;color:inherit;font:inherit;cursor:pointer}
.hermes-lanes-cat[data-selected="1"]{border-color:var(--ui-text-tertiary);background:var(--ui-stroke-secondary)}
.hermes-lanes-cat[data-dimmed="1"]{opacity:.45}
.hermes-lanes-all{font-size:11px;color:var(--ui-text-secondary)}
.hermes-lanes-sw.is-dashed{border:1px dashed var(--ui-text-tertiary)}
.hermes-lanes-status-word{display:none;color:var(--ui-text-tertiary);font-size:11px}
.hermes-lanes-sw{width:8px;height:8px;border-radius:2px;flex:none;box-sizing:border-box}
.hermes-lanes-sw.is-outline{border:1px solid var(--ui-text-tertiary)}
.hermes-lanes-status+.hermes-lanes-toggle,.hermes-lanes-status+small+.hermes-lanes-toggle{margin-left:0}
.hermes-lanes-toggle{margin-left:auto;height:22px;padding:0 8px;border:1px solid var(--ui-stroke-secondary);border-radius:6px;background:transparent;color:inherit;font:inherit;cursor:pointer}
.hermes-lanes-body{max-height:60vh;overflow:auto}
.hermes-lanes-tabtotals{display:inline-flex;align-items:center;gap:4px;padding:0 4px;font-size:12px;white-space:nowrap}
.hermes-lanes-tabtotals>.hermes-lanes-status{margin-left:0;padding-left:0;border-left:0}
.hermes-lanes-tabtotals .hermes-lanes-cat{height:20px;padding:0 4px}
.hermes-lanes-tabtotals>.hermes-lanes-toggle{margin-left:0;height:20px;padding:0 6px}
.hermes-lanes{display:flex;flex-direction:column;gap:8px;font-size:12px;padding:6px}
.hermes-lanes-head,.hermes-lanes-foot{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.hermes-lanes-head strong{font-size:14px}
.hermes-lanes-grow{flex:1 1 0}
.hermes-lanes-dim{color:var(--ui-text-tertiary)}
.hermes-lanes-warn{color:#f0a43a}
.hermes-lanes-big{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.hermes-lanes button{height:28px;padding:0 10px;border:1px solid var(--ui-stroke-secondary);border-radius:6px;background:transparent;color:inherit;font:inherit;cursor:pointer}
.hermes-lanes button:disabled{opacity:.45;cursor:default}
.hermes-lanes button.is-primary{background:#8fb0ff;border-color:#8fb0ff;color:#0f1115;font-weight:600}
.hermes-lanes button.is-paused{border-color:#f0a43a;color:#f6c477}
.hermes-lanes-seg{display:flex;gap:2px}
.hermes-lanes-seg button.is-on{background:var(--chrome-action-hover);border-color:var(--ui-text-tertiary)}
.hermes-lanes-name{display:flex;align-items:center;gap:8px;min-width:0}
.hermes-lanes-dot{width:9px;height:9px;border-radius:3px;flex:none}
.hermes-lanes-title{font-weight:600;font-size:13px}
.hermes-lanes-sub{color:var(--ui-text-tertiary);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:125px}
.hermes-lanes-volume{display:flex;align-items:center;gap:8px}
.hermes-lanes-volume input{flex:1 1 0;min-width:0;accent-color:#8fb0ff}
.hermes-lanes-notes{flex:1 1 320px;min-width:0}
.hermes-lanes-note{padding:10px;font-size:12px;color:var(--ui-text-secondary)}
.hermes-lanes-floor{display:flex;flex-direction:column;overflow-x:auto}
.hermes-lanes-frow{display:grid;column-gap:0;align-items:stretch;border-top:1px solid var(--ui-stroke-secondary);min-width:1120px}
.hermes-lanes-fhead{border-top:0;color:var(--ui-text-tertiary);font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;padding:2px 0}
.hermes-lanes-fhead>span{padding:0 6px}
.hermes-lanes-lanecell{display:flex;flex-direction:column;gap:6px;padding:8px 10px 8px 0}
.hermes-lanes-valves{display:flex;flex-direction:column;gap:4px}
.hermes-lanes-valve{display:grid;grid-template-columns:62px 62px 1fr;gap:6px;align-items:center;color:var(--ui-text-tertiary)}
.hermes-lanes-valve input{height:22px;width:62px;border:1px solid var(--ui-stroke-secondary);border-radius:5px;background:transparent;color:inherit;font:inherit;padding:0 4px;font-variant-numeric:tabular-nums}
.hermes-lanes-cell{padding:8px 6px;border-left:1px solid var(--ui-stroke-secondary);display:flex;flex-direction:column;gap:4px;min-width:0}
.hermes-lanes-cell.is-piling{background:repeating-linear-gradient(135deg,rgba(255,93,93,.14) 0 6px,transparent 6px 12px)}
.hermes-lanes-count{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.hermes-lanes-boxes{display:flex;flex-wrap:wrap;gap:3px;align-items:center}
.hermes-lanes .hermes-lanes-box{width:11px;height:11px;min-width:11px;padding:0;border:0;border-radius:3px}
.hermes-lanes .hermes-lanes-box:hover{outline:2px solid var(--ui-text-secondary)}
.hermes-lanes-why{font-size:10.5px}
.hermes-lanes-legend{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding-top:8px;border-top:1px solid var(--ui-stroke-secondary);color:var(--ui-text-tertiary);font-size:11px}
.hermes-lanes-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}
.hermes-lanes-sankey{display:block;max-height:420px}
.hermes-lanes-svgtext{font-size:11px;fill:var(--ui-text-secondary)}
.hermes-lanes-svglabel{font-size:9.5px;fill:var(--ui-text-tertiary);font-family:monospace}
.hermes-lanes-svgtext.hermes-lanes-dim{fill:var(--ui-text-tertiary)}
.hermes-lanes-limits{display:flex;flex-wrap:wrap;gap:14px;align-items:center}
.hermes-lanes-limit{display:inline-flex;gap:6px;align-items:center;font-variant-numeric:tabular-nums}
`

export default {
  id: 'lanes',
  name: 'Lanes',
  description: 'Top strip: the split of worker time between project lanes, backlog, throughput and cost, with volume controls.',
  register(ctx) {
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())
    if (sdk.SIDEBAR_SECTIONS_TOP_AREA) {
      ctx.register({ id: 'lanes-sidebar', area: sdk.SIDEBAR_SECTIONS_TOP_AREA, order: -20, render: () => h(SidebarLanes, { ctx }) })
      ctx.register({ id: 'strip', area: STRIP_AREA, order: 10, render: () => h(TopTotals, { ctx }) })
    } else {
      ctx.register({ id: 'strip', area: STRIP_AREA, order: 10, render: () => h(Lanes, { ctx }) })
    }
    if (SESSIONS_TAB_STRIP_AREA) ctx.register({ id: 'tab-totals', area: SESSIONS_TAB_STRIP_AREA, order: 10, render: () => h(SessionsTabTotals) })
    if (sdk.SIDEBAR_SECTIONS_AREA && sdk.SidebarSection && sdk.SidebarSectionRow) {
      ctx.register({ id: 'unlinked-asks', area: sdk.SIDEBAR_SECTIONS_AREA, order: 10, render: () => h(UnlinkedAsks, { ctx }) })
    }
    ctx.register({
      id: 'show-lanes',
      area: PALETTE_AREA,
      data: {
        id: 'show-lanes', label: 'Show lanes', keywords: ['lanes', 'priority', 'schedule', 'share', 'backlog', 'cost'],
        run: () => window.dispatchEvent(new window.Event('hermes-show-lanes'))
      }
    })
  }
}
