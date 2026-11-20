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

const { host, useQuery, PALETTE_AREA } = sdk
// Older Desktops do not export TOP_STRIP_AREA; a named import would throw at load and skip activation.
export const TOP_STRIP_AREA = sdk.TOP_STRIP_AREA || 'shell.topStrip'
export const STRIP_AREA = sdk.MAIN_PANE_TOP_AREA || TOP_STRIP_AREA

export const POLL_MS = 15000
export const IDLE_POLL_MS = 300000
export const REQUEST_TIMEOUT_MS = 20000
export const WINDOWS = [[1, '1h'], [6, '6h'], [24, '24h'], [168, '7d']]
export const STORE_KEY = 'hermes-lanes-window'
export const OPEN_KEY = 'hermes-lanes-open'
// The Desktop's status-bar task pill (board icon, bar of open cards, count) fires this cancellable event before
// it opens the board. Where lanes work, this strip takes the click over; its Board button opens the board.
export const KANBAN_CHIP_EVENT = 'hermes-kanban-chip'
const COLORS = ['#f0a43a', '#8fb0ff', '#5fd4e6', '#d982f0', '#b4bac6', '#3ddc97', '#ef7b7b', '#c9d36a']
export const METER_COLORS = { running: '#388bfd', notReady: '#d4a72c', blocked: '#d64545' }

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
  async function read(hours) {
    const path = `/v1/lanes?hours=${hours}`
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
  return { read, write, flow, target: () => chosen }
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
  const share = (name) => {
    const lane = snap.lanes[name]
    const paused = draft[name]?.paused ?? lane.paused
    return paused ? 0 : Number(draft[name]?.share ?? lane.share) || 0
  }
  const total = names.reduce((sum, name) => sum + share(name), 0) || 1
  return names.map((name, i) => {
    const lane = snap.lanes[name]
    return {
      ...lane,
      name,
      color: COLORS[i % COLORS.length],
      share: Number(draft[name]?.share ?? lane.share) || 0,
      paused: draft[name]?.paused ?? lane.paused,
      target: share(name) / total,
      dirty: draft[name] != null
    }
  })
}

/** Why a lane is off its target, in the user's terms; '' when it is within 10 points. */
export function drift(row) {
  if (row.paused) return 'paused'
  const gap = row.actual - row.target
  if (Math.abs(gap) <= 0.1) return ''
  if (gap > 0) return 'over: using idle slots'
  return row.ready === 0 ? 'under: nothing ready' : 'under: waiting for slots'
}

export function pct(value) { return `${Math.round((Number(value) || 0) * 100)}%` }

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

export function loadTab(storage = globalThis.localStorage) {
  try { const v = storage?.getItem(TAB_KEY); return TABS.some(([key]) => key === v) ? v : 'floor' } catch { return 'floor' }
}
export function saveTab(tab, storage = globalThis.localStorage) { try { storage?.setItem(TAB_KEY, tab) } catch {} }
export function loadBy(storage = globalThis.localStorage) {
  try { return storage?.getItem(BY_KEY) === 'source' ? 'source' : 'project' } catch { return 'project' }
}
export function saveBy(by, storage = globalThis.localStorage) { try { storage?.setItem(BY_KEY, by) } catch {} }

/** A card's column: any step core sent that this panel has no column for is shown as unclassified, never dropped. */
export const stepOf = (card) => (STEP_KEYS.includes(card?.step) ? card.step : UNCLASSIFIED)

export const hasUnclassified = (flow) => (flow?.cards || []).some((c) => stepOf(c) === UNCLASSIFIED)

/** Columns of the Floor: the ten steps, plus the unclassified column only while a card sits in it. */
export function floorColumns(flow) {
  const cols = STEPS.map(([key, label, hint]) => ({ key, label, hint }))
  if (hasUnclassified(flow)) cols.push({ key: UNCLASSIFIED, label: 'Unclassified', hint: 'landing stage not provable' })
  return cols
}

const reasonRank = (r) => { const i = REASON_ORDER.indexOf(r); return i < 0 ? REASON_ORDER.length : i }

/** { lane: { step: [cards] } }, a cell sorted by reason (you first) then oldest first. */
export function floorGrid(flow, names = []) {
  const grid = {}
  for (const n of names) grid[n] = {}
  for (const card of flow?.cards || []) ((grid[card.lane] ||= {})[stepOf(card)] ||= []).push(card)
  for (const cells of Object.values(grid)) {
    for (const list of Object.values(cells)) list.sort((a, b) => reasonRank(a.reason) - reasonRank(b.reason) || (b.age_h || 0) - (a.age_h || 0))
  }
  return grid
}

/** A cell is piling up when it holds PILE_AT or more cards, or any card that needs you. */
export const piling = (cards) => (cards?.length || 0) >= PILE_AT || (cards || []).some((c) => c.reason === 'you')

export function ageHours(hours) {
  const h = Number(hours) || 0
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`
  if (h < 48) return `${Math.round(h)}h`
  return `${Math.round(h / 24)}d`
}

const prLabel = (url) => { const m = /\/pull\/(\d+)/.exec(String(url || '')); return m ? `#${m[1]}` : String(url || '').split('/').filter(Boolean).pop() || '' }

/** What clears a dam, without inferring blocker identity from a card's own PR. */
export function damHint(reason, cards) {
  const n = cards.length
  const plural = (word) => `${n} ${word}${n === 1 ? '' : 's'}`
  const oldest = ageHours(Math.max(0, ...cards.map((c) => Number(c.age_h) || 0)))
  let text
  if (reason === 'you') text = `answer ${plural('ask')}`
  else if (reason === 'dep') {
    const groups = new Map()
    let unknown = 0
    for (const card of cards) {
      const blockers = Array.isArray(card.blockers) ? card.blockers : card.blocker ? [card.blocker] : []
      const valid = [...new Map(blockers.filter((b) => b && typeof b.id === 'string' && b.id).map((b) => [b.id, b])).values()]
      if (!valid.length) unknown++
      for (const b of valid) {
        const key = b.pr || b.id
        const group = groups.get(key) || { blocker: b, cards: new Set(), partial: false }
        group.cards.add(card.id || card)
        group.partial ||= valid.length > 1
        groups.set(key, group)
      }
    }
    const hints = [...groups.values()].sort((a, b) => b.cards.size - a.cards.size).map(({ blocker, cards, partial }) =>
      `${blocker.pr ? `land PR ${prLabel(blocker.pr)}` : `finish ${blocker.id}`} → ${partial ? `unblocks ${cards.size} dependency waits` : `frees ${cards.size}`}`)
    if (unknown) hints.push(`${unknown} card${unknown === 1 ? ' waits' : 's wait'} on an open parent`)
    text = hints.join('; ') || `${plural('card')} ${n === 1 ? 'waits' : 'wait'} on an open parent`
  } else if (reason === 'gate') text = `admit or release ${n} held`
  else if (reason === 'limit:cards_per_day') text = 'raise cards/day, or wait for local midnight'
  else if (reason === 'limit:usd_per_day') text = 'raise $/day, or wait for local midnight'
  else if (reason === 'slot') text = `${plural('card')} ${n === 1 ? 'needs' : 'need'} a free worker`
  else if (reason === 'quota') text = 'waits for model quota'
  else if (reason === 'time') text = 'wakes on its schedule'
  else if (reason === 'triage') text = `specify ${plural('card')}`
  else text = `${plural('card')} held`
  return `${text} · oldest ${oldest}`
}

const FLOWING = new Set(['run', 'review'])
const shortId = (id) => (String(id).length > 22 ? `${String(id).slice(0, 21)}…` : String(id))

/** The source node a card is drawn from: its project, or the session / automation that created it. */
export function sourceLabel(card, by) {
  const s = card.source || {}
  if (by === 'project') return s.project || (s.kind === 'automation' ? 'automations' : s.kind === 'user' ? 'you (manual)' : 'no project')
  if (s.kind === 'user') return 'you (manual)'
  return `${s.kind === 'session' ? 'session' : 'auto'} ${shortId(s.id || '?')}`
}

const SOURCE_CAP = 6

/**
 * Sankey data: sources → lanes → sinks. A sink is a flowing step (running, review…) or a dam named by its reason.
 * Every card is in exactly one source, lane and sink, so each layer's totals equal the card count.
 */
export function flowGraph(flow, by = 'project') {
  const cards = flow?.cards || []
  const counts = new Map()
  for (const c of cards) counts.set(sourceLabel(c, by), (counts.get(sourceLabel(c, by)) || 0) + 1)
  const keep = new Set([...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, SOURCE_CAP).map(([k]) => k))
  const src = (c) => { const label = sourceLabel(c, by); return keep.has(label) ? label : 'other' }
  const sinkKey = (c) => (FLOWING.has(c.reason) ? `step:${stepOf(c)}` : `dam:${c.reason}`)
  const nodes = { source: new Map(), lane: new Map(), sink: new Map() }
  const edges = { sl: new Map(), lk: new Map() }
  const bump = (map, key, by1 = 1) => map.set(key, (map.get(key) || 0) + by1)
  for (const c of cards) {
    const s = src(c)
    const k = sinkKey(c)
    bump(nodes.source, s); bump(nodes.lane, c.lane); bump(nodes.sink, k)
    bump(edges.sl, `${s}\u0000${c.lane}`); bump(edges.lk, `${c.lane}\u0000${k}`)
  }
  const bySink = {}
  for (const c of cards) (bySink[sinkKey(c)] ||= []).push(c)
  const sinkOrder = (key) => (key.startsWith('step:') ? STEP_KEYS.indexOf(key.slice(5)) - 100 : reasonRank(key.slice(4)))
  const laneOrder = Object.keys(flow?.lanes || {})
  const list = (map, order) => [...map.entries()].map(([key, count]) => ({ key, count })).sort(order)
  const edgeList = (map) => [...map.entries()].map(([k, count]) => { const [from, to] = k.split('\u0000'); return { from, to, count } })
  return {
    total: cards.length,
    sources: list(nodes.source, (a, b) => b.count - a.count || a.key.localeCompare(b.key)),
    lanes: list(nodes.lane, (a, b) => laneOrder.indexOf(a.key) - laneOrder.indexOf(b.key) || a.key.localeCompare(b.key)),
    sinks: list(nodes.sink, (a, b) => sinkOrder(a.key) - sinkOrder(b.key)).map((n) => {
      const dam = n.key.startsWith('dam:')
      const reason = dam ? n.key.slice(4) : null
      return { ...n, dam, label: dam ? reasonInfo(reason).label : STEPS.find(([k]) => k === n.key.slice(5))?.[1] || n.key.slice(5),
        color: dam ? reasonInfo(reason).color : reasonInfo(n.key.slice(5) === 'running' ? 'run' : 'review').color,
        hint: dam ? damHint(reason, bySink[n.key]) : '' }
    }),
    edges: { sourceLane: edgeList(edges.sl), laneSink: edgeList(edges.lk) }
  }
}

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
  const running = Number(row?.running) || 0
  const blocked = Number(row?.blocked_on_you) || 0
  const open = Number(row?.open) || 0
  const notReady = Math.max(0, open - running - blocked)
  return { running, notReady, blocked, total: running + notReady + blocked }
}

function laneSelector(name) {
  const esc = globalThis.CSS?.escape ? globalThis.CSS.escape(name) : String(name).replace(/\\/g, '\\\\').replace(/"/g, '\\"')
  return `[data-lane="${esc}"]`
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
      ? h('div', null, cap('max_workers', 'max workers', 1, 'running', String), h('span', { className: 'hermes-lanes-dim' }, `${row.running} running`))
      : h('div', { className: 'hermes-lanes-dim', 'data-valve': 'workers' }, `workers ${row.running} running`),
    cap('cards_per_day', 'cards/day', 1, 'cards_today', (v) => String(v)),
    cap('usd_per_day', '$/day', 0.5, 'usd_today', usd))
}

function Cell({ column, cards, lane, flowLane, onOpen }) {
  const pile = piling(cards)

  if (column.key === 'done') {
    return h('div', { className: 'hermes-lanes-cell', 'data-step': 'done', 'data-count': flowLane?.done ?? 0 },
      h('div', { className: 'hermes-lanes-count' }, flowLane?.done ?? 0),
      h('div', { className: 'hermes-lanes-dim' }, flowLane ? `${flowLane.run_hours ?? 0} run-h${flowLane.cost_usd ? ` · ${usd(flowLane.cost_usd)}` : ''}` : ''))
  }
  const tally = {}
  for (const c of cards) tally[c.reason] = (tally[c.reason] || 0) + 1
  return h('div', { className: `hermes-lanes-cell${pile ? ' is-piling' : ''}`, 'data-step': column.key, 'data-count': cards.length, 'data-piling': pile ? '1' : '0' },
    h('div', { className: 'hermes-lanes-count' }, cards.length || '·'),
    h('div', { className: 'hermes-lanes-boxes' }, cards.map((c) => {
      const info = reasonInfo(c.reason)
      return h('button', {
        key: c.id, type: 'button', className: 'hermes-lanes-box', 'data-card': c.id, 'data-reason': c.reason,
        title: `${c.title || c.id}\n${info.label} · ${ageHours(c.age_h)} old${c.pr ? ` · ${prLabel(c.pr)}` : ''}`, 'aria-label': `${c.title || c.id}, ${info.label}`,
        style: { background: info.color }, onClick: () => onOpen(c.id)
      })
    })),
    cards.length ? h('div', { className: 'hermes-lanes-dim hermes-lanes-why' }, Object.entries(tally).sort((a, b) => reasonRank(a[0]) - reasonRank(b[0])).slice(0, 2)
      .map(([r, n]) => `${n} ${reasonInfo(r).label}`).join(' · ')) : null)
}

/** Mockup A: lanes are rows, steps are columns, one box per card coloured by why it waits; piled-up cells are hatched. */
export function Floor({ list, flow, flowError, draft, saving, edit, onOpen }) {
  const columns = floorColumns(flow)
  const names = [...new Set([...list.map((r) => r.name), ...Object.keys(flow?.lanes || {}), ...(flow?.cards || []).map((c) => c.lane)])]
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
        columns.map((col) => h(Cell, { key: col.key, column: col, cards: grid[name]?.[col.key] || [], lane: name, flowLane, onOpen })))
    }),
    h('div', { className: 'hermes-lanes-legend' }, ['you', 'dep', 'gate', 'limit:usd_per_day', 'time', 'slot', 'triage', 'run', 'review'].map((r) => h('span', { key: r },
      h('i', { style: { background: reasonInfo(r).color } }), reasonInfo(r).label)), h('span', { className: 'hermes-lanes-dim' }, 'Hatched = piling up (4+ cards, or any that need you).')))
}

/** Mockup B: sources → lanes → flowing steps or dams, a dam labelled with what clears it. */
export function Flow({ flow, flowError, by, onBy, list, onEditLimits }) {
  if (!flow) return h('div', { className: 'hermes-lanes-note', 'data-view': 'flow' }, flowError ? `Flow unavailable: ${flowError}` : 'Loading…')
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
  const [draft, setDraft] = useState({})
  const [tab, setTabState] = useState(() => loadTab(storage))
  const [by, setByState] = useState(() => loadBy(storage))
  if (!snap) return h('div', { className: 'hermes-lanes-note' }, error ? `Unavailable: ${error}` : 'Loading…')
  if (!snap.configured) return h('div', { className: 'hermes-lanes-note' }, snap.reason || 'No lanes are configured.')
  const list = rows(snap, draft)
  const pending = changes(snap, draft, flow)
  const setTab = (value) => { setTabState(value); saveTab(value, storage) }
  const edit = (name, patch) => setDraft((d) => ({ ...d, [name]: { ...d[name], ...patch } }))
  const notes = snap.planner?.notes || []
  return h('div', { className: 'hermes-lanes' },
    h('div', { className: 'hermes-lanes-head' },
      h('strong', null, 'Lanes'),
      h('span', { className: 'hermes-lanes-dim' }, `${list.reduce((s, r) => s + r.running, 0)} running · ${list.reduce((s, r) => s + r.open, 0)} open · ${snap.scope === 'fleet' ? 'whole fleet' : 'this board'}`),
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

function MiniBar({ row }) {
  const m = meter(row)
  const total = m.total || 1
  const seg = (key, count) => count > 0
    ? h('span', { key, 'data-segment': key, style: { width: `${(count / total) * 100}%`, background: METER_COLORS[key] } })
    : null
  return h('span', {
    className: 'hermes-lanes-mini-bar', role: 'img',
    'aria-label': `${m.running} running, ${m.notReady} not ready, ${m.blocked} blocked on you`
  }, seg('running', m.running), seg('notReady', m.notReady), seg('blocked', m.blocked))
}

function MiniPill({ row, onOpen, expanded }) {
  return h('button', {
    type: 'button', className: 'hermes-lanes-pill', 'data-lane-pill': row.name,
    title: `${row.name}: ${row.running} running · ${row.ready} ready · ${Number(row.blocked_on_you) || 0} blocked on you · ${row.open} open`,
    'aria-expanded': expanded,
    onClick: (event) => { event.stopPropagation(); onOpen(row.name) }
  },
    h('span', { className: 'hermes-lanes-dot', style: { background: row.color }, 'aria-hidden': 'true' }),
    h('span', { className: 'hermes-lanes-pill-name' }, row.name),
    h(MiniBar, { row }),
    h('span', { className: 'hermes-lanes-pill-open' }, row.open))
}

export function Lanes({ ctx }) {
  const [open, setOpenState] = useState(loadOpen)
  const [focus, setFocus] = useState('')
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
  const key = ['lanes', windowHours]
  const query = useQuery({
    queryKey: key,
    queryFn: () => source.read(windowHours),
    // Collapsed still shows counts, so poll at the live interval once lanes are configured.
    refetchInterval: (q) => q.state.data?.configured ? POLL_MS : IDLE_POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
    placeholderData: (prev) => prev
  })
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener('hermes-show-lanes', show)
    return () => window.removeEventListener('hermes-show-lanes', show)
  }, [])
  // The task pill expands this strip only where lanes are configured and the API answers; anywhere else its
  // click is left alone and opens the board as before.
  const working = query.data?.configured === true
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
  // Cards for Floor and Flow: only while the strip is expanded, so a collapsed strip costs one request.
  const flowLabel = flowWindow(windowHours)
  const flowQuery = useQuery({
    queryKey: ['lanes-flow', flowLabel],
    queryFn: () => source.flow(flowLabel),
    enabled: open && working,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
    placeholderData: (prev) => prev
  })
  useEffect(() => {
    if (!working) return undefined
    const take = (event) => { event.preventDefault(); setOpen(true) }
    window.addEventListener(KANBAN_CHIP_EVENT, take)
    return () => window.removeEventListener(KANBAN_CHIP_EVENT, take)
  }, [working])
  useEffect(() => {
    if (!open || !focus) return undefined
    const el = (bodyRef.current || document).querySelector(laneSelector(focus))
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [open, focus])
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
      await query.refetch()
      if (open) await flowQuery.refetch().catch(() => {})
      return true
    } catch (e) {
      setError(`${at}: ${String(e?.message || e).slice(0, 180)}`)
      await query.refetch().catch(() => {})
      if (open) await flowQuery.refetch().catch(() => {})
      return false
    } finally { setSaving(false) }
  }
  if (!working) return null
  const list = rows(query.data)
  const openLane = (name) => { setFocus(name); setOpen(!open || name !== focus) }
  const toggle = () => setOpen((value) => !value)
  return h('div', { ref: stripRef, className: 'hermes-lanes-strip', 'data-slot': 'lanes-strip', 'data-open': open ? '1' : '0' },
    h('div', {
      className: 'hermes-lanes-mini', role: 'button', tabIndex: 0,
      'aria-label': 'Toggle lanes', 'aria-expanded': open, onClick: toggle,
      onKeyDown: (event) => {
        if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault(); toggle()
        }
      }
    },
      list.map((row) => h(MiniPill, { key: row.name, row, onOpen: openLane, expanded: open && focus === row.name })),
      h('button', {
        type: 'button', className: 'hermes-lanes-toggle', 'aria-expanded': open,
        'aria-label': open ? 'Collapse lanes' : 'Expand lanes', onClick: (event) => { event.stopPropagation(); toggle() }
      }, open ? '▴' : '▾')),
    open ? h('div', { ref: bodyRef, className: 'hermes-lanes-body', style: { maxHeight: bodyMaxHeight, overflow: 'auto' } },
      h(Panel, {
        snap: query.data, flow: flowQuery.data, flowError: flowQuery.error ? String(flowQuery.error.message).slice(0, 200) : '',
        windowHours, saving, error: error || (query.error ? String(query.error.message).slice(0, 200) : ''),
        onOpenCard: (id) => openCard(id, source.target()?.connectionId || undefined),
        onWindow: (v) => { setWindowState(v); saveWindow(v) }, onApply: apply,
        onBoard: () => host.navigate('/kanban')
      })) : null)
}

const CSS = `
.hermes-lanes-strip{display:flex;flex-direction:column;min-width:0;width:100%}
.hermes-lanes-mini{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:4px 8px;min-height:28px;cursor:pointer}
.hermes-lanes-pill{display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 8px;border:1px solid var(--ui-stroke-secondary);border-radius:999px;background:transparent;color:inherit;font:inherit;cursor:pointer}
.hermes-lanes-pill-name{font-weight:600;font-size:12px}
.hermes-lanes-pill-open{font-variant-numeric:tabular-nums;font-size:11px;color:var(--ui-text-tertiary)}
.hermes-lanes-mini-bar{display:inline-flex;width:28px;height:6px;border-radius:3px;overflow:hidden;background:var(--ui-stroke-secondary);flex:none}
.hermes-lanes-mini-bar>span{height:100%;display:block;min-width:0}
.hermes-lanes-toggle{margin-left:auto;height:22px;padding:0 8px;border:1px solid var(--ui-stroke-secondary);border-radius:6px;background:transparent;color:inherit;font:inherit;cursor:pointer}
.hermes-lanes-body{max-height:60vh;overflow:auto}
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
    ctx.register({ id: 'strip', area: STRIP_AREA, order: 10, render: () => h(Lanes, { ctx }) })
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
