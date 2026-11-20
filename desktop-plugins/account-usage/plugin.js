/**
 * Account Usage — status-bar chips showing every pooled Claude / ChatGPT (Codex)
 * subscription pool: a 7-day weekly-quota forecast sparkline (red where it runs dry), ready count and a
 * verdict. Click a provider for a popover: the 4-provider forecast table with
 * that provider highlighted, then its accounts with 5-hour session + weekly bars and reset times.
 * Forecast: REST POST /forecast (plugins/account-usage/forecast.py) from the weekly windows below plus
 * the backend's usage history; an older backend without it just shows no forecast.
 * The popover is a plain table of visual cues; why an account is limited and status
 * hints open from the row's chevron. Auth problems stay visible on the collapsed row and as a bar
 * aggregate, worded by what is known: "disconnected" (auth-benched, Reconnect may renew it) vs
 * "sign-in needed" (grant confirmed dead). Sign-in launches the provider's supported flow.
 *
 * Data: gateway JSON-RPC `model.options` (same payload the model picker uses).
 * Pooled providers carry `usage.accounts[]` = {label, state, windows[], resets_at} plus the
 * sanitized cause fields {limit_kind: quota|cooldown, reason, cooldown_until, model_cooldowns[]}.
 * Red = out of quota (waits for the provider reset); amber = a temporary pool cooldown. Both
 * stay unselectable; an older backend without limit_kind stays red (never masked as green).
 * Read-only: the call serves cached snapshots and kicks a background refresh,
 * so polling it never blocks a turn or rotates a credential.
 * Disable/Enable (row details) = `hermes auth disable|enable` for every pooled row of that account
 * (REST /disabled); a disabled account is grey, never counted ready and never selectable.
 */

import { atom, cn, host, Popover, PopoverContent, PopoverTrigger, STATUSBAR_AREAS, useValue } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'account-usage'
const POLL_MS = 30_000
const WARN_PCT = 80
// A credential that served a call within this window is "in use" (pulses); older = "last used" (ring).
const ACTIVE_MS = 10 * 60_000

const CSS = `
@keyframes hermes-au-pulse { 0%,100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--au-c) 70%, transparent) } 50% { box-shadow: 0 0 0 3px color-mix(in srgb, var(--au-c) 0%, transparent) } }
.hermes-au-dot { position: relative }
.hermes-au-dot[data-mark='active'] { animation: hermes-au-pulse 1.8s ease-in-out infinite; outline: 1.5px solid var(--au-c); outline-offset: 1px }
.hermes-au-dot[data-mark='last'] { outline: 1.5px solid currentColor; outline-offset: 1px; opacity: .95 }
.hermes-au-row { width: 100%; text-align: left; border: 1px solid transparent; border-radius: 4px; padding: 5px 3px; background: transparent; color: inherit; cursor: pointer }
.hermes-au-row:hover { background: var(--chrome-action-hover) }
.hermes-au-row:focus-visible { outline: 2px solid var(--ui-accent); outline-offset: 2px }
.hermes-au-row:disabled { cursor: wait }
.hermes-au-row[data-unavailable]:disabled { cursor: not-allowed }
.hermes-au-fix { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 0 3px 5px 21px; font-size: 10.5px }
.hermes-au-fix button { color: var(--ui-accent, var(--accent)); border: 1px solid currentColor; border-radius: 3px; padding: 1px 7px; background: transparent; cursor: pointer; font-size: 10.5px }
.hermes-au-fix button:disabled { cursor: wait; opacity: .6 }
.hermes-au-fix code { overflow-wrap: anywhere; opacity: .8 }
.hermes-au-line { display: flex; align-items: center; gap: 2px }
.hermes-au-line > .hermes-au-row { flex: 1; min-width: 0 }
.hermes-au-more { flex: none; width: 18px; height: 18px; border: 0; border-radius: 3px; padding: 0; background: transparent; color: var(--au-c, currentColor); cursor: pointer; font-size: 10px; line-height: 18px; opacity: .8; transition: transform .12s }
.hermes-au-more:hover { background: var(--chrome-action-hover); opacity: 1 }
.hermes-au-more[aria-expanded='true'] { transform: rotate(90deg) }
.hermes-au-more:focus-visible { outline: 2px solid var(--ui-accent); outline-offset: 1px }
.hermes-au-gap { flex: none; width: 18px }
.hermes-au-details { margin: 0 20px 4px 21px; padding: 4px 6px; border-left: 2px solid var(--au-c, var(--border)); font-size: 10.5px; display: flex; flex-direction: column; gap: 3px }
.hermes-au-details .hermes-au-fix { padding: 0 }
.hermes-au-pill { justify-self: start; font-size: 10px; padding: 0 5px; border-radius: 3px; border: 1px solid currentColor; color: var(--au-c); white-space: nowrap }
.hermes-au-next { display: inline-block; white-space: nowrap; color: var(--ui-accent); border: 1px solid currentColor; border-radius: 3px; padding: 0 4px; font-size: 10px; margin-left: 5px }
.hermes-au-signin { display: inline-block; white-space: nowrap; color: #f85149; border: 1px solid currentColor; border-radius: 3px; padding: 0 4px; font-size: 10px; margin-left: 5px }
.hermes-au-disc { display: inline-block; white-space: nowrap; color: #d29922; border: 1px solid currentColor; border-radius: 3px; padding: 0 4px; font-size: 10px; margin-left: 5px }
.hermes-au-chip { display: inline-flex; align-items: center; gap: 5px; margin-right: 4px; padding: 0 4px; border-radius: 3px; white-space: nowrap; flex: none }
.hermes-au-tag { font: 600 9.5px/1 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .04em; padding: 2px 3px; border-radius: 3px; border: 1px solid color-mix(in srgb, currentColor 35%, transparent) }
.hermes-au-chip[data-hl='true'] { background: var(--chrome-action-hover); color: var(--foreground) }
.hermes-au-fc { width: 100%; border-collapse: collapse; font-size: 11px }
.hermes-au-fc th { font-weight: 500; text-align: left; opacity: .6; font-size: 10px; padding: 0 6px 4px 3px }
.hermes-au-fc { table-layout: auto }
.hermes-au-fc td { padding: 4px 6px 4px 3px; border-top: 1px solid color-mix(in srgb, var(--border) 60%, transparent); white-space: nowrap }
.hermes-au-fc td.hermes-au-verdict { white-space: nowrap }
.hermes-au-fc tr[data-hl='true'] td { background: var(--chrome-action-hover) }
.hermes-au-fc tr[data-hl='true'] td:first-child { box-shadow: inset 2px 0 0 var(--ui-accent, var(--accent)) }
.hermes-au-fc tbody tr { cursor: pointer }
.hermes-au-fc button { all: unset; cursor: pointer; font-weight: 600 }
.hermes-au-fc button:focus-visible { outline: 2px solid var(--ui-accent); outline-offset: 1px }
@media (prefers-reduced-motion: reduce) { .hermes-au-dot[data-mark='active'] { animation: none } }
`

const PROVIDERS = [
  { slug: 'anthropic', name: 'Claude', tag: 'ANT', title: 'Claude Max' },
  { slug: 'openai-codex', name: 'GPT', tag: 'GPT', title: 'ChatGPT (Codex)' },
  // xAI publishes no subscription-usage API; the backend reports API access (team_blocked) only.
  { slug: 'xai-oauth', name: 'Grok', tag: 'GRK', title: 'Grok (xAI)', statusOnly: true },
  // Gemini AI Studio API keys: status only (key valid / rejected). A future Gemini subscription
  // provider gets its own entry here (e.g. slug 'gemini-oauth'), not a change to this one.
  { slug: 'gemini', name: 'Gemini', tag: 'GEM', title: 'Gemini (AI Studio key)', statusOnly: true, statusOk: 'API key valid · no usage windows (AI Studio has no usage API)', statusBad: 'key rejected', statusBadHint: 'key rejected · check the key at aistudio.google.com/apikey' }
]

const COLORS = { ready: '#3fb950', warn: '#d29922', cooldown: '#e3b341', limited: '#f85149', unknown: 'var(--ui-text-tertiary, #888)', unavailable: 'var(--ui-text-tertiary, #888)', disabled: 'var(--ui-text-tertiary, #888)' }

// Pooled providers whose accounts can be switched off (backend DISABLE_PROVIDERS).
const DISABLE_SLUGS = new Set(['anthropic', 'openai-codex', 'xai-oauth'])

const REASONS = { quota_window: 'quota used up', billing: 'billing / credits', usage_limit: 'usage limit reached', rate_limit: 'rate limited', auth: 'auth error', cooldown: 'cooling down' }

const BAR_CLASS =
  'inline-flex h-full items-center gap-1.5 rounded-none px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground'

// ── pure helpers (exported for tests) ─────────────────────────────────────

// Model-family windows (scope 'model', or a Fable label) cap one model, never the pool.
// The sparkline is the account-wide weekly forecast; a Fable week must not become that line.
const FABLE_WINDOW = /fable/i
export function poolWindow(w) {
  return !!(w && w.scope !== 'model' && !FABLE_WINDOW.test(String(w.label || '')))
}

export function pickWindow(windows, kind) {
  const re = kind === 'session' ? /session|5.?h|hour/i : /week/i
  return (windows || []).find(w => poolWindow(w) && re.test(String(w.label || ''))) || null
}

export function tone(account) {
  if (account.disabled) return 'disabled'
  // Only an explicit backend 'cooldown' verdict turns a limited account amber.
  if (account.state === 'limited') return account.limit_kind === 'cooldown' ? 'cooldown' : 'limited'
  if (account.state !== 'ready') return account.state === 'unavailable' ? 'unavailable' : 'unknown'
  const hi = Math.max(...(account.windows || []).filter(poolWindow).map(w => Number(w.used_percent) || 0), 0)
  return hi >= WARN_PCT ? 'warn' : 'ready'
}

export function fmtReset(iso, now = Date.now()) {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return '—'
  const ms = t - now
  if (ms <= 0) return 'now'
  const mins = Math.round(ms / 60_000)
  if (mins < 60) return `${mins}m`
  if (mins < 24 * 60) return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, '0')}m`
  // Round to the minute: resets land a few ms either side of the hour (13:59:59.6Z), and the
  // hour-only format truncates, which would show the reset an hour early.
  return new Date(Math.round(t / 60_000) * 60_000).toLocaleString(undefined, { weekday: 'short', hour: 'numeric' })
}

// One line on why an account is not ready, from sanitized fields only (codes + timestamps).
export function describe(account, now = Date.now()) {
  if (!account) return ''
  if (account.disabled) return 'disabled · not used until you enable it'
  const parts = []
  if (account.state === 'limited' && account.status_bad) {
    parts.push(account.status_bad)
  } else if (account.state === 'limited') {
    parts.push(account.limit_kind === 'cooldown' ? 'cooling down' : 'out of quota')
    const why = REASONS[account.reason]
    if (why && why !== parts[0]) parts.push(why)
    const until = account.limit_kind === 'cooldown' ? account.cooldown_until || account.resets_at : account.resets_at || account.cooldown_until
    if (until) parts.push(`back ${fmtReset(until, now)}`)
  } else if (account.state === 'unavailable') {
    parts.push(account.signedOut ? 'signed out · removed from the pool' : account.reason === 'auth' ? 'sign-in needed' : 'unavailable')
  }
  const models = (account.model_cooldowns || []).filter(m => m && m.model && Date.parse(m.until) > now)
  if (models.length) parts.push(models.map(m => `${m.model} cooling ${fmtReset(m.until, now)}`).join(', '))
  return parts.join(' · ')
}

// Status-only providers (Grok, Gemini) say "rejected"/"blocked" ONLY for a real failed check (state limited).
// An account with no fresh check yet (state unknown: cold start, or the 15-min snapshot went stale and the
// background recheck hasn't landed) is "not checked yet", never "key rejected".
// Chip count colour: red only when something is actually limited; all-unchecked stays grey.
export function countColor(g) {
  if (g.ready > 0) return undefined
  if (g.limited) return COLORS.limited
  if (g.cooling) return COLORS.cooldown
  return COLORS.unknown
}

// Status-only chips (Grok, Gemini) stay out of the bar while healthy: they appear only when an enabled
// account is red (blocked) or yellow (warn / cooling down). Not-checked and sign-in states stay hidden too.
// The popover table always lists every provider. Usage providers (Claude, GPT) always show.
export function chipVisible(g) {
  if (!g.statusOnly) return true
  return (g.accounts || []).some(a => !a.disabled && ['limited', 'cooldown', 'warn'].includes(tone(a)))
}

// The one- or two-word status a status-only row shows in its table cell; statusHint is the detail.
export function statusLabel(g, a) {
  if (a.state === 'ready') return 'ok'
  if (a.state === 'limited') return g.statusBad || 'blocked'
  if (a.state === 'unavailable') return 'sign-in needed'
  return 'not checked'
}

// Whether a row has anything behind its disclosure chevron. Ready rows with nothing to say have none.
export function hasDetails(g, a) {
  return !!g.statusOnly || !!g.canDisable || !!describe(a) || needsReconnect(a, g.slug)
}

export function statusHint(g, a) {
  if (a.state === 'ready') return g.statusOk || 'API access ok · no usage windows (xAI has no usage API)'
  if (a.state === 'limited') return g.statusBadHint || 'API blocked · add credits at console.x.ai'
  if (a.state === 'unavailable') return 'credential unavailable · sign in again'
  return 'not checked yet · rechecks in a few seconds'
}

// `disabled` = the /disabled reply {supported, accounts: {provider: [account_id]}}; absent = unsupported.
export function summarize(payload, last = {}, now = Date.now(), disabled = null) {
  const rows = (payload && payload.providers) || []
  const off = (disabled && disabled.supported && disabled.accounts) || {}
  return PROVIDERS.map(p => {
    const row = rows.find(r => r && r.slug === p.slug)
    let accounts = row && row.usage && Array.isArray(row.usage.accounts) ? row.usage.accounts : null
    // Single-account provider: legacy gauge shape `usage.windows`.
    if (!accounts && row && row.usage && Array.isArray(row.usage.windows)) {
      const limited = row.usage.windows.some(w => poolWindow(w) && Number(w.used_percent) >= 100)
      // Status-only providers (xAI): 100% means "API blocked (no credits)", not a spent quota window.
      const reason = limited ? (p.statusBad ? 'auth' : p.statusOnly ? 'billing' : 'quota_window') : null
      accounts = [{ id: p.slug, selectable: false, label: row.name || p.title, windows: row.usage.windows, state: limited ? 'limited' : 'ready', resets_at: null, limit_kind: limited ? 'quota' : null, reason, ...(limited && p.statusBad ? { status_bad: p.statusBad } : {}) }]
    }
    if (!accounts || !accounts.length) return null
    // Status-only providers with their own wording (Gemini): a rejected key is not "out of quota".
    if (p.statusBad) accounts = accounts.map(a => (a.state === 'limited' ? { ...a, reason: 'auth', status_bad: p.statusBad } : a))
    const offIds = new Set(Array.isArray(off[p.slug]) ? off[p.slug] : [])
    if (offIds.size) accounts = accounts.map(a => (offIds.has(a.id) ? { ...a, disabled: true } : a))
    const canDisable = !!(disabled && disabled.supported) && DISABLE_SLUGS.has(p.slug)
    const ready = accounts.filter(a => a.state === 'ready' && !a.disabled).length
    const off_ = accounts.filter(a => a.disabled).length
    const cooling = accounts.filter(a => tone(a) === 'cooldown').length
    const limited = accounts.filter(a => tone(a) === 'limited').length
    const nextBack = accounts
      .filter(a => a.state === 'limited' && !a.disabled && a.resets_at)
      .map(a => a.resets_at)
      .sort()[0] || null
    const rec = last && last[p.slug]
    let markId = null
    let mark = null
    if (rec && rec.label) {
      const hit = accounts.find(a => a.label === rec.label)
      if (hit) {
        markId = hit.id
        mark = now - rec.at * 1000 < ACTIVE_MS ? 'active' : 'last'
      }
    }
    return { ...p, accounts, ready, cooling, limited, disabled: off_, canDisable, total: accounts.length, nextBack, markId, mark, markAt: rec ? rec.at * 1000 : null }
  }).filter(Boolean)
}

// Providers the backend can reconnect (plugin_api RECONNECT_PROVIDERS). A rejected Gemini key or an
// xAI block also carries reason 'auth' but is not a sign-in the panel can renew.
const RECONNECT_SLUGS = new Set(['anthropic', 'openai-codex'])
// Core forbids an in-app Anthropic login (web_routers/oauth.py: flow 'external'); Desktop's own
// supported sign-in for it lives in Settings › Accounts.
const EXTERNAL_SLUGS = new Set(['anthropic'])
const ACCOUNTS_SETTINGS = '/settings?tab=providers&pview=accounts'

// A disconnected account: dead grant, or benched for an auth error. These get a Reconnect/sign-in action.
// `slug` (when given) keeps providers the backend cannot reconnect out.
export function needsReconnect(account, slug) {
  if (slug !== undefined && !RECONNECT_SLUGS.has(slug)) return false
  return !!account && !account.disabled && (account.state === 'unavailable' || account.reason === 'auth')
}

// Sign-in is CONFIRMED needed only when something established the grant is dead: core marked the row
// DEAD (wire state 'unavailable'), core dropped it (signedOut), or a reconnect attempt came back
// needs_sign_in (`st`). An auth-benched row (limited + reason auth) may still renew by plain refresh:
// that one is only "disconnected".
export function signInConfirmed(account, st, slug) {
  if (!needsReconnect(account, slug)) return false
  return account.state === 'unavailable' || !!account.signedOut || !!(st && st.needsSignIn)
}

const reconnectKey = (provider, id) => `${provider}\u0000${id}`

// Gateway + profile a reconnect state belongs to. Account ids are only meaningful on the gateway
// that listed them, so state from one never shows, or acts, on another.
export const scopeOf = route => `${route?.connectionId ?? ''}\u0000${route?.profile ?? ''}`
export function scopedFix(reconnectState, route = ownerRoute()) {
  const scope = scopeOf(route)
  return Object.fromEntries(Object.entries(reconnectState || {}).filter(([, st]) => st && st.scope === scope))
}

// The reconnect state shown on a row. An Anthropic id is a token fingerprint, so a renewed or
// re-signed-in account comes back under a new id: a finished result follows it only to the exact
// row the readback resolved (resolvedId), never to any row that merely shares the label.
export function rowFix(reconnectState, slug, a) {
  const own = reconnectState[reconnectKey(slug, a.id)]
  if (own) return own
  return Object.values(reconnectState).find(st => st && st.done && st.provider === slug && st.resolvedId && st.resolvedId === a.id) || {}
}

// {signIn, disconnected} for one provider group. `reconnectState` = the reconnect store's atom value.
export function authCounts(g, reconnectState = {}) {
  let signIn = 0, disconnected = 0
  for (const a of (g && g.accounts) || []) {
    if (!needsReconnect(a, g.slug)) continue
    if (signInConfirmed(a, reconnectState[reconnectKey(g.slug, a.id)], g.slug)) signIn++
    else disconnected++
  }
  return { signIn, disconnected }
}

export function signInCount(groups, reconnectState = {}) {
  return (groups || []).reduce((n, g) => n + authCounts(g, reconnectState).signIn, 0)
}

// Closed-bar text for a provider's auth problems, worded by what is known.
export function authLabel({ signIn, disconnected }) {
  return [signIn > 1 ? `${signIn} sign-in` : signIn === 1 ? 'sign-in' : '',
    disconnected > 1 ? `${disconnected} disconnected` : disconnected === 1 ? 'disconnected' : ''].filter(Boolean).join(' · ')
}

// Core drops a Codex device_code row whose grant died, so the account vanishes from the next
// model.options read. Keep it on the panel (as a signed-out row) until sign-in is verified,
// otherwise the only place to sign in again disappears with it.
export function withSignedOut(groups, reconnectState = {}) {
  let out = groups || []
  for (const st of Object.values(reconnectState)) {
    if (!st || !st.removed || st.done || !st.provider || !RECONNECT_SLUGS.has(st.provider)) continue
    const g = out.find(row => row.slug === st.provider)
    const taken = g && g.accounts.some(a => a.id === st.id)
    if (taken) continue
    const ghost = { id: st.id, label: st.label || 'Signed-out account', state: 'unavailable', reason: 'auth', signedOut: true, selectable: false, windows: [] }
    if (g) out = out.map(row => (row === g ? { ...g, accounts: [...g.accounts, ghost], total: g.total + 1 } : row))
    else {
      const p = PROVIDERS.find(row => row.slug === st.provider)
      out = [...out, { ...p, accounts: [ghost], ready: 0, cooling: 0, limited: 0, disabled: 0, canDisable: false, total: 1, nextBack: null, markId: null, mark: null, markAt: null, forecast: null }]
        .sort((a, b) => PROVIDERS.findIndex(x => x.slug === a.slug) - PROVIDERS.findIndex(x => x.slug === b.slug))
    }
  }
  return out
}

export function ownerRoute() {
  return { connectionId: host.state.connectionId?.get(), profile: host.state.profile?.get() }
}

export function reconnectOutcome(r) {
  if (r && r.ok) return 'renewed'
  if (r && typeof r.status === 'string' && r.status) return r.status
  if (r && r.needs_sign_in) return 'needs_sign_in'
  return 'transient'
}

// Display labels are neither unique nor authenticated identity. If a token-fingerprint id changes,
// only an explicit backend identity mapping can certify its replacement; a matching label cannot.
export function findAccount(groups, provider, id) {
  const g = (groups || []).find(row => row.slug === provider)
  return (g?.accounts || []).find(row => row.id === id) || null
}
export function accountHealth(groups, provider, id, label) {
  const a = findAccount(groups, provider, id, label)
  if (!a) return 'missing'
  if (a.disabled) return 'disabled'
  if (a.state === 'unavailable') return 'needs_sign_in'
  if (a.reason === 'auth') return 'disconnected'
  if (a.state === 'limited' && a.limit_kind !== 'cooldown') return 'quota'
  if (a.state === 'ready' || a.state === 'limited') return 'ok'
  return 'unchecked'
}

// Status-bar text for a failed model.options read. After a core update the Desktop-owned backend refuses it
// with core's stale-code 503 ("Restart required: This process is running code from <old> but the checkout on
// disk is now <new> ...", hermes_cli/web_routers/models.py) until it restarts; a bare "usage ?" hid that.
const STALE_BACKEND_RE = /Restart required:|running code from \S+ but the checkout on disk is now/i
export const STALE_BACKEND_LABEL = 'backend on old code: restart'
export function unavailableLabel(error) {
  const text = String(error || '')
  if (STALE_BACKEND_RE.test(text)) {
    return { label: STALE_BACKEND_LABEL, title: `Account usage unavailable: ${STALE_BACKEND_LABEL} (use Restart backend in Hermes Desktop, or quit and reopen the app). ${text}` }
  }
  return { label: 'usage ?', title: `Account usage unavailable: ${text}` }
}

// ── forecast (pure, exported for tests) ──────────────────────────────────

const VERDICT_COLOR = { ok: COLORS.ready, tight: COLORS.warn, short: COLORS.limited }

// What the backend's /forecast needs per account: weekly used % and its reset, plus the spent account's back-time.
export function forecastRequest(groups) {
  return { providers: groups.filter(g => !g.statusOnly).map(g => ({ slug: g.slug, accounts: g.accounts.map(a => {
    const w = pickWindow(a.windows, 'week')
    return { id: String(a.id || ''), label: String(a.label || ''), state: a.state, disabled: !!a.disabled,
      week_used: w && Number.isFinite(Number(w.used_percent)) ? Number(w.used_percent) : null,
      week_resets_at: (w && w.resets_at) || null, resets_at: a.resets_at || null }
  }) })) }
}

export function fmtHours(h) {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`
  if (h < 48) return `${Math.round(h)}h`
  return `${Math.round(h / 24)}d`
}

function atHour(h, now) {
  return new Date(now + h * 3600_000).toLocaleString(undefined, { weekday: 'short', hour: 'numeric' })
}

// Short verdict for the status bar.
export function verdictText(f) {
  if (!f) return ''
  if (f.verdict === 'learning') return 'learning…'
  // Short: hours until it runs dry. Dry this minute is "out" only when nothing comes back
  // inside the week; a session block with weekly quota left says when that quota returns.
  if (f.verdict === 'short') {
    if (f.runout_h !== 0) return fmtHours(f.runout_h)
    const end = f.gaps && f.gaps[0] && f.gaps[0].end_h
    if (Number.isFinite(end) && end > 0 && end < 168) return `back ${fmtHours(end)}`
    return 'out'
  }
  // tight / ok: the sparkline colour (yellow / green) already says it; no word in the bar.
  return ''
}

// Longer forecast sentence for the popover table.
export function forecastText(f, now = Date.now()) {
  if (!f) return 'no forecast (backend too old)'
  if (f.verdict === 'error') return 'forecast failed'
  if (f.verdict === 'learning') return `learning… ${Math.round((f.history_h || 0) * 10) / 10}h of 2h history`
  if (f.verdict === 'short') {
    const g = f.gaps[0]
    // Several dry spells: when it first runs out and how much of the week is dry in total.
    if (f.gaps.length > 1) return `out ${atHour(g.start_h, now)} · dry ${fmtHours(f.gaps.reduce((n, x) => n + (x.end_h - x.start_h), 0))} of 7d`
    return `out ${atHour(g.start_h, now)} → ${g.end_h >= 168 ? 'past 7d' : atHour(g.end_h, now)}`
  }
  const pace = f.pace == null ? '' : ` · ${Math.round(f.pace * 100)}% of supply`
  if (f.verdict !== 'tight') return `lasts the week${pace}`
  // Tight on timing (a near miss before a refill) rather than on the week's total.
  if (f.pace != null && f.pace <= 0.9 && f.burn_per_h > 0 && Array.isArray(f.curve) && f.curve.length)
    return `tight · ~${fmtHours(Math.min(...f.curve) / f.burn_per_h)} spare`
  return `tight${pace}`
}

export function leftText(f) {
  return f && Number.isFinite(f.left) ? `${(f.left / 100).toFixed(1)} acct` : '—'
}

// Fuel curve as SVG: line + fill, red bands where it is dry, ticks at resets.
// Centered moving average over `k` hours: hides the hourly refill sawtooth, keeps the trend.
export function smoothCurve(curve, k) {
  if (!Array.isArray(curve) || k <= 1) return curve
  const r = Math.floor(k / 2)
  return curve.map((_, i) => {
    let sum = 0, n = 0
    for (let j = Math.max(0, i - r); j <= Math.min(curve.length - 1, i + r); j++) { sum += Math.max(0, curve[j]); n++ }
    return sum / n
  })
}

// Smooth SVG path through points (Catmull-Rom as cubic Béziers), clamped to the box.
export function smoothPath(pts, h) {
  if (pts.length < 2) return ''
  const c = v => Math.min(h, Math.max(0, v))
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, c(p1[1] + (p2[1] - p0[1]) / 6)]
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, c(p2[1] - (p3[1] - p1[1]) / 6)]
    d += ` C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`
  }
  return d
}

// The popover (ticks) draws dry spells as a red strip; the bar leaves them out so an empty provider reads as one line.
export function Sparkline({ f, w = 64, h = 14, ticks = false, k = 13 }) {
  if (!f || !Array.isArray(f.curve) || f.curve.length < 2) return null
  const n = f.curve.length - 1
  const curve = smoothCurve(f.curve, k)
  const max = Math.max(100, ...curve)
  const x = i => (i / n) * w
  const top = h - 3 // the bottom 2px carry the dry-spell strip
  const y = v => top - (Math.max(0, v) / max) * (top - 1)
  // ~1 point per 2px is plenty for a smooth line this size.
  const stride = Math.max(1, Math.round(n / (w / 2)))
  const idx = curve.map((_, i) => i).filter(i => i % stride === 0 || i === n)
  const line = smoothPath(idx.map(i => [x(i), y(curve[i])]), top)
  const col = VERDICT_COLOR[f.verdict] || 'currentColor'
  return jsxs('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}`, 'aria-hidden': true, style: { flex: 'none', overflow: 'visible' }, children: [
    jsx('path', { d: `${line} L${w},${top} L0,${top} Z`, fill: col, opacity: 0.16 }),
    ...(ticks ? f.gaps || [] : []).map((g, i) => jsx('rect', { key: `g${i}`, x: x(g.start_h), width: Math.max(1.5, x(g.end_h) - x(g.start_h)), y: h - 2, height: 2, rx: 1, fill: COLORS.limited })),
    ...(ticks ? (f.resets_h || []).filter(r => r >= 0 && r <= n).map((r, i) => jsx('line', { key: `t${i}`, x1: x(r), x2: x(r), y1: top - 2, y2: top, stroke: 'currentColor', opacity: 0.4 })) : []),
    jsx('path', { d: line, fill: 'none', stroke: col, strokeWidth: 1.3, strokeLinecap: 'round', strokeLinejoin: 'round', opacity: f.verdict === 'learning' ? 0.45 : 1 })
  ] })
}

function ForecastTable({ groups, hl, onPick }) {
  const now = Date.now()
  return jsxs('table', { className: 'hermes-au-fc', children: [
    jsx('thead', { children: jsxs('tr', { children: ['Provider', 'Ready', 'Left', 'Next 7 days', 'Forecast'].map(t => jsx('th', { key: t, children: t })) }) }),
    jsx('tbody', { children: groups.map(g => {
      const f = g.forecast
      return jsxs('tr', { key: g.slug, 'data-hl': g.slug === hl ? 'true' : undefined, 'data-provider': g.slug, onClick: () => onPick(g.slug), children: [
        jsx('td', { children: jsx('button', { type: 'button', 'aria-label': `Show ${g.title} accounts`, 'aria-pressed': g.slug === hl, onFocus: () => onPick(g.slug), children: g.name }) }),
        jsx('td', { style: { color: countColor(g) }, children: `${g.ready}/${g.total}` }),
        g.statusOnly
          ? jsx('td', { colSpan: 3, style: { opacity: 0.65 }, children: g.ready ? 'up · no usage API' : g.limited ? `down · ${g.statusBad || 'blocked'}` : 'not checked yet' })
          : null,
        g.statusOnly ? null : jsx('td', { children: leftText(f) }),
        g.statusOnly ? null : jsx('td', { children: jsx(Sparkline, { f, w: 96, h: 14, ticks: true, k: 13 }) }),
        g.statusOnly ? null : jsx('td', { className: 'hermes-au-verdict', style: { color: VERDICT_COLOR[f?.verdict], opacity: f && VERDICT_COLOR[f.verdict] ? 1 : 0.65 }, children: forecastText(f, now) })
      ].filter(Boolean) })
    }) })
  ] })
}

// ── state ─────────────────────────────────────────────────────────────────

// Provider-global observation, scoped to the ambient gateway/profile. Never fall
// back to a fixed local file: that would leak the default profile onto remotes.
async function readLast(ctx) {
  try { return await ctx.rest('/last') || {} } catch { return {} }
}

// Older plugin backends / cores have no /disabled: treat as unsupported (no toggle shown).
async function readDisabled(ctx) {
  try { return await ctx.rest('/disabled') || null } catch { return null }
}

// Older plugin backends have no /forecast: no forecast is shown (never a guessed one).
export async function attachForecast(ctx, groups) {
  const req = forecastRequest(groups)
  if (!req.providers.length) return groups
  let out = null
  try { out = await ctx.rest('/forecast', { method: 'POST', body: req }) } catch { out = null }
  return groups.map(g => ({ ...g, forecast: (out && out[g.slug]) || null }))
}

export function createStore(ctx) {
  const empty = () => ({ groups: [], fetchedAt: 0, loading: false, error: null })
  const $state = atom(empty())
  const key = () => JSON.stringify([host.state.connectionId?.get(), host.state.profile.get()])
  let scope = key(), epoch = 0, inflight = null, disposed = false
  const sync = () => {
    if (key() !== scope) { scope = key(); epoch++; inflight = null; $state.set(empty()) }
  }
  for (const name of ['connectionId', 'profile']) {
    const unsub = host.state[name]?.subscribe(sync)
    if (unsub) ctx.onDispose(unsub)
  }
  ctx.onDispose(() => { disposed = true; epoch++ })
  const refresh = () => {
    sync()
    if (disposed) return
    if (inflight) return inflight
    const e = epoch
    const current = () => { sync(); return !disposed && epoch === e }
    $state.set({ ...$state.get(), loading: true })
    inflight = Promise.all([host.request('model.options', {}, 30_000), readLast(ctx), readDisabled(ctx)])
      .then(([payload, last, disabled]) => attachForecast(ctx, summarize(payload, last || {}, Date.now(), disabled)))
      .then(groups => { if (current()) $state.set({ groups, fetchedAt: Date.now(), loading: false, error: null }) })
      .catch(err => { if (current()) $state.set({ ...$state.get(), loading: false, error: String(err?.message || err) }) })
      .finally(() => { if (current()) inflight = null })
    return inflight
  }
  return { $state, refresh }
}

// ctx.rest routes through the ambient gateway. Never retarget with only ?profile.
// Older SDKs without authoritative focused ownership fail closed for mutations.
function focusScope() {
  const get = name => host.state[name]?.get()
  const owner = get('focusedSessionOwner')
  const session = get('focusedSessionId')
  const connection = get('connectionId')
  const profile = get('profile')
  const key = JSON.stringify([session, owner, connection, profile, get('model')])
  const valid = !!session && !!owner?.connectionId && !!owner?.profile &&
    owner.connectionId === connection && owner.profile === profile
  return { key, session, profile, valid }
}

// Session selection is separate from provider-global last-used observations.
export function createSelectionStore(ctx) {
  const empty = () => ({ selection: null, loading: false, busy: false, error: null })
  const $state = atom(empty())
  let scope = focusScope(), epoch = 0, read = 0, disposed = false
  const locks = new Set()
  const sync = () => {
    const next = focusScope()
    if (scope.key !== next.key) { scope = next; epoch++; read++; $state.set(empty()) }
    return scope
  }
  for (const name of ['focusedSessionId', 'focusedSessionOwner', 'connectionId', 'profile', 'model']) {
    const unsub = host.state[name]?.subscribe(sync)
    if (unsub) ctx.onDispose(unsub)
  }
  ctx.onDispose(() => { disposed = true; epoch++; read++ })
  const current = (s, e) => !disposed && sync().key === s.key && epoch === e
  const path = s => `/selection?session_id=${encodeURIComponent(s.session)}&profile=${encodeURIComponent(s.profile)}`
  const check = (row, s) => {
    if (!row || row.session_id !== s.session || !Number.isSafeInteger(row.revision) || row.revision < 0 ||
        !row.provider || !row.model || !['none', 'pending', 'cancelled', 'consumed', 'rejected', 'empty'].includes(row.state))
      throw new Error('Invalid selection response; refresh before trying again')
    return row
  }
  const explain = e => String(e?.message || e || 'Account selection unavailable')
  const refresh = async () => {
    const s = sync(), e = epoch
    if (disposed || locks.has(s.key)) return
    if (!s.valid) { $state.set({ ...empty(), error: 'Focus a chat owned by the current gateway connection and profile to select an account.' }); return }
    const r = ++read
    $state.set({ ...$state.get(), loading: true })
    try {
      const row = check(await ctx.rest(path(s)), s)
      if (current(s, e) && read === r) $state.set({ ...empty(), selection: row, error: row.error || (row.state === 'rejected' ? 'Selection rejected; choose an available account again.' : null) })
    } catch (err) {
      if (current(s, e) && read === r) $state.set({ ...empty(), error: explain(err) })
    }
  }
  const mutate = async (provider, account) => {
    const s = sync(), e = epoch
    if (disposed || locks.has(s.key)) return
    const refuse = message => $state.set({ ...$state.get(), error: message })
    if (!s.valid) { refuse('Focus a chat owned by the current gateway connection and profile to select an account.'); return }
    const row = $state.get().selection
    if (!row || $state.get().loading) { refuse('Selection is loading; refresh before trying again.'); return }
    if (account && provider !== row.provider) { refuse(`This chat uses ${row.provider}; selecting another provider would change its route.`); return }
    if (account && account.disabled) { refuse('Account is disabled; enable it first.'); return }
    if (account && (account.state !== 'ready' || !account.id || account.selectable === false)) {
      const why = describe(account)
      refuse(`Account unavailable (${account.state || 'unknown'}${why ? `: ${why}` : ''}). Cooldowns are not reset.`); return
    }
    if (!account && row.state !== 'pending') { refuse('No pending selection to cancel.'); return }
    locks.add(s.key); ++read
    $state.set({ ...$state.get(), busy: true, error: null })
    try {
      // Capture target/revision before awaiting. Backend owns provider/model validation.
      if (account) await ctx.rest(`/selection?profile=${encodeURIComponent(s.profile)}`, { method: 'PUT', body: {
        session_id: s.session, provider, account_id: account.id, expected_revision: row.revision
      } })
      else await ctx.rest(`${path(s)}&expected_revision=${row.revision}`, { method: 'DELETE' })
      // No follow-up transport call once focus changes: ctx.rest is ambient-routed.
      if (!current(s, e)) return
      const verified = check(await ctx.rest(path(s)), s)
      if (!current(s, e)) return
      const expected = verified.revision === row.revision + 1 && verified.provider === row.provider && verified.model === row.model &&
        (account ? verified.state === 'pending' && verified.account_id === account.id : verified.state === 'cancelled' && verified.account_id === null)
      $state.set({ ...empty(), selection: verified, error: expected ? null : verified.error || 'Selection changed before confirmation; review the current state.' })
    } catch (err) {
      // Clear stale CAS metadata; explicit refresh required, never auto-retry a write.
      if (current(s, e)) $state.set({ ...empty(), error: `${explain(err)}. Refresh selection before retrying.` })
    } finally {
      locks.delete(s.key)
      if (current(s, e)) $state.set({ ...$state.get(), busy: false })
    }
  }
  return { $state, refresh, select: mutate, cancel: () => mutate() }
}

// Reconnect = renew the account's OAuth grant in place (backend force-refresh). A dead grant
// launches the provider's supported sign-in on the gateway that owns the chips: Codex device-code
// in the panel, Anthropic through Desktop's Settings › Accounts (core forbids an in-app login).
// Never a CLI command, never a typed code. "Reconnected" is only shown for a health the backend
// or a fresh panel read confirmed; a failed or empty readback says so.
const HEALTH = {
  ok: { done: true, message: 'Reconnected' },
  quota: { done: true, message: 'Signed in · out of quota until it resets' },
  disabled: { done: true, message: 'Signed in · account is disabled' },
  unchecked: { done: true, message: 'Signed in · usage not checked yet' },
  needs_sign_in: { error: true, needsSignIn: true, message: 'Still needs sign-in' },
  disconnected: { error: true, retry: true, message: 'Signed in, but the account is still disconnected — retry' },
  missing: { error: true, needsSignIn: true, unverified: true, message: 'Sign-in finished, but this account is not in the pool — another account may have signed in' }
}
const POLL_GAP_MS = 500

export function createReconnectStore(ctx, onDone = () => {}) {
  const $state = atom({})
  const key = reconnectKey
  const who = new Map() // key -> {provider, id, label}: lets a row core removed stay on the panel
  const flows = new Map() // key -> token of the one live sign-in flow; a superseded flow never writes state
  const track = (provider, account) => {
    const k = key(provider, account.id)
    who.set(k, { provider, id: account.id, label: account.label || '' })
    return k
  }
  const restOpts = route => route?.connectionId ? { connectionId: route.connectionId, profile: route.profile } : {}
  const desktopApi = () => (typeof window !== 'undefined' && window.hermesDesktop?.api) || ctx.api
  const drop = async (api, sessionId, route) => {
    try {
      await api({ path: `/api/providers/oauth/sessions/${encodeURIComponent(sessionId)}`, method: 'DELETE',
        connectionId: route?.connectionId, profile: route?.profile })
    } catch { /* cancelling a server session is best-effort */ }
  }
  const owners = new Map() // key -> {scope, route}: the gateway + profile whose actions own the key's state
  // The state of `k` as seen from `route`: another gateway's entry under the same id is not ours.
  const cur = (k, route = ownerRoute()) => {
    const st = $state.get()[k]
    return st && st.scope === scopeOf(route) ? st : null
  }
  // Start an action on `route`. Taking the key over from another gateway retires that gateway's
  // flow, deletes its server sign-in session and clears its state.
  const begin = (provider, account, route) => {
    const k = track(provider, account)
    const scope = scopeOf(route)
    const prev = owners.get(k)
    if (!prev || prev.scope !== scope) {
      const { [k]: old, ...rest } = $state.get()
      flows.delete(k)
      const api = desktopApi()
      if (prev && old?.sessionId && api) void drop(api, old.sessionId, prev.route)
      $state.set(rest)
      owners.set(k, { scope, route })
    }
    return k
  }
  // A writer bound to the key's owner at the time the action started: once another gateway takes
  // the key over, a late result from this action is dropped instead of landing in that gateway's state.
  const writer = k => {
    const me = owners.get(k)
    const put = v => {
      if (owners.get(k) !== me) return
      const prev = $state.get()[k]
      $state.set({ ...$state.get(), [k]: { ...who.get(k), scope: me.scope, ...(prev?.removed ? { removed: true } : {}), ...v } })
    }
    put.scope = me.scope
    put.owned = () => owners.get(k) === me
    return put
  }
  // Read the panel back and report the account's actual health. `keep` carries the flow fields
  // (external / opened) across a result that leaves the row needing action.
  const verify = async (put, provider, account, live = () => true, keep = {}) => {
    let groups = null, failed = false
    try { groups = await onDone() } catch { failed = true }
    if (!live()) return
    // If the panel now shows another gateway, its rows say nothing about this account.
    if (failed || !Array.isArray(groups) || scopeOf(ownerRoute()) !== put.scope) {
      put({ ...keep, error: true, unverified: true, check: true, message: 'Could not confirm the account — check again' })
      return
    }
    const row = findAccount(groups, provider, account.id, account.label)
    const health = accountHealth(groups, provider, account.id, account.label)
    const result = HEALTH[health]
    put(result.done ? { ...result, ...(row && row.id !== account.id ? { resolvedId: row.id } : {}) }
      : { ...keep, ...result, ...(health === 'missing' ? { removed: true } : {}) })
  }
  const reconnect = async (provider, account, route = ownerRoute()) => {
    if (cur(reconnectKey(provider, account.id), route)?.busy) return
    const put = writer(begin(provider, account, route))
    put({ busy: true })
    try {
      const r = await ctx.rest('/reconnect', { method: 'POST', body: { provider, account_id: account.id }, ...restOpts(route) })
      const status = reconnectOutcome(r)
      if (status === 'renewed') {
        // The backend re-read the pool row after the refresh: this renewal is confirmed there.
        // Refresh first, so the row does not briefly show both "Reconnected" and a stale auth state.
        try { await onDone() } catch { /* refresh is best-effort after a confirmed renew */ }
        put({ done: true, message: 'Reconnected' })
        return
      }
      if (status === 'transient') { put({ error: true, retry: true, message: 'Reconnect failed — retry' }); return }
      if (status === 'quota') { put({ error: true, message: 'Out of quota — not a sign-in problem' }); return }
      if (status === 'disabled') { put({ error: true, message: 'Account is disabled' }); return }
      if (status === 'needs_sign_in') {
        const external = r?.auth_flow === 'external' || EXTERNAL_SLUGS.has(provider)
        put({ needsSignIn: true, authFlow: external ? 'external' : r?.auth_flow, ...(r?.removed ? { removed: true } : {}),
          message: external ? 'Saved sign-in expired — sign in from Settings › Accounts' : 'Saved sign-in expired — sign in again' })
        return
      }
      put({ error: true, message: 'Reconnect is not available for this account' })
    } catch (err) {
      put({ error: true, retry: true, message: `Reconnect failed: ${String(err?.message || err)}` })
    }
  }
  const pollAuth = async (put, provider, account, sessionId, route, expiresIn, live) => {
    const api = desktopApi()
    const deadline = Date.now() + Math.min(Math.max(Number(expiresIn) || 900, 30) * 1000, 15 * 60 * 1000)
    let errors = 0
    while (Date.now() < deadline) {
      if (!live()) return
      let poll = null
      try {
        poll = await api({
          path: `/api/providers/oauth/${encodeURIComponent(provider)}/poll/${encodeURIComponent(sessionId)}?wait=25`,
          timeoutMs: 45000,
          connectionId: route?.connectionId, profile: route?.profile
        })
        errors = 0
      } catch (err) {
        if (!live()) return
        // One dropped poll says nothing about the sign-in; three in a row ends the flow.
        if (++errors >= 3) throw err
      }
      if (!live()) return
      if (poll?.status === 'approved') {
        put({ busy: true, needsSignIn: true, message: 'Checking account…' })
        await verify(put, provider, account, live)
        return
      }
      if (poll?.status && poll.status !== 'pending') {
        put({ error: true, needsSignIn: true, message: poll.status === 'cancelled' ? 'Sign-in cancelled' : 'Sign-in did not complete' })
        return
      }
      await new Promise(ok => setTimeout(ok, POLL_GAP_MS))
    }
    if (!live()) return
    await drop(api, sessionId, route)
    if (live()) put({ error: true, needsSignIn: true, message: 'Sign-in timed out' })
  }
  // Anthropic: hand over to Desktop's own Accounts page; "Check sign-in" reads the result back.
  const openSettings = (provider, account) => {
    const put = writer(begin(provider, account, ownerRoute()))
    if (typeof host.navigate !== 'function') {
      put({ error: true, needsSignIn: true, authFlow: 'external', message: 'Open Settings › Accounts to sign in to this provider' })
      return false
    }
    host.navigate(ACCOUNTS_SETTINGS)
    put({ needsSignIn: true, authFlow: 'external', opened: true, message: 'Sign in under Settings › Accounts, then check here' })
    return true
  }
  const check = async (provider, account) => {
    const st = cur(reconnectKey(provider, account.id)) || {}
    if (st.busy) return
    const put = writer(begin(provider, account, ownerRoute()))
    const keep = { ...(st.needsSignIn ? { needsSignIn: true } : {}), ...(st.authFlow ? { authFlow: st.authFlow } : {}), ...(st.opened ? { opened: true } : {}) }
    put({ ...keep, busy: true, message: 'Checking account…' })
    await verify(put, provider, account, () => true, keep)
  }
  const signIn = async (provider, account, route = ownerRoute()) => {
    const seen = cur(reconnectKey(provider, account.id), route)
    if (seen?.busy) return
    if (EXTERNAL_SLUGS.has(provider) || seen?.authFlow === 'external') { openSettings(provider, account); return }
    const k = begin(provider, account, route)
    const put = writer(k)
    const api = desktopApi()
    if (!api) { put({ error: true, needsSignIn: true, message: 'Secure sign-in is unavailable on this Desktop' }); return }
    const token = {}
    flows.set(k, token)
    // Live = not cancelled, not superseded by a newer sign-in, and the key is still this gateway's.
    const live = () => flows.get(k) === token && put.owned()
    put({ busy: true, needsSignIn: true, message: 'Starting sign-in…' })
    let sessionId = null // a started server session: every path that abandons it deletes it
    let settled = false // the server session reached a terminal state (approved / denied / cancelled)
    try {
      const start = await api({
        path: `/api/providers/oauth/${encodeURIComponent(provider)}/start`,
        method: 'POST', body: {}, timeoutMs: 30000, connectionId: route?.connectionId, profile: route?.profile
      })
      sessionId = start?.session_id || null
      if (!live()) return
      if (start?.flow !== 'device_code' || !start.verification_url || !sessionId) {
        put({ error: true, needsSignIn: true, authFlow: 'external', message: 'Sign-in for this provider runs from Settings › Accounts' })
        return
      }
      // Recorded before the browser opens, so Cancel can always reach the server session.
      put({ busy: true, needsSignIn: true, launching: true, sessionId, userCode: start.user_code,
        verificationUrl: start.verification_url, message: 'Opening the sign-in page…' })
      let opened = false
      try { opened = ctx.os?.openExternal ? !!(await ctx.os.openExternal(start.verification_url)) : false } catch { opened = false }
      if (!live()) return
      put({ busy: true, needsSignIn: true, launching: true, sessionId, userCode: start.user_code,
        verificationUrl: start.verification_url, message: opened ? 'Complete sign-in in the browser' : 'Open the sign-in page to continue' })
      await pollAuth(put, provider, account, sessionId, route, start.expires_in, live)
      settled = live() && !$state.get()[k]?.launching
    } catch (err) {
      if (!live()) return
      const msg = String(err?.message || err)
      put(/external CLI|Unsupported flow/i.test(msg)
        ? { error: true, needsSignIn: true, authFlow: 'external', message: 'Sign-in for this provider runs from Settings › Accounts' }
        : { error: true, needsSignIn: true, message: `Sign-in failed: ${msg}` })
    } finally {
      // Whoever leaves a started session unfinished deletes it (cancelled, superseded, taken over by
      // another gateway, failed): core would otherwise keep polling and could still save a sign-in
      // the panel no longer shows. Deleting a session that already ended is a no-op.
      if (sessionId && !settled) await drop(api, sessionId, route)
      if (flows.get(k) === token) flows.delete(k)
    }
  }
  const cancelAuth = async (provider, account, route = ownerRoute()) => {
    const st = cur(reconnectKey(provider, account.id), route)
    if (!st) return
    const k = track(provider, account)
    // Retire the flow first: its poll loop may be parked in a 25 s long-poll and must not write
    // state when that returns, least of all over a sign-in started after this cancel.
    flows.delete(k)
    writer(k)({ needsSignIn: true, cancelled: true, message: 'Sign-in cancelled' })
    const api = desktopApi()
    if (api && st.sessionId) await drop(api, st.sessionId, route)
  }
  return { $state, key, reconnect, signIn, cancelAuth, openSettings, check, openPage: url => ctx.os?.openExternal && url ? ctx.os.openExternal(url) : Promise.resolve(false) }
}

// Disable / Enable one account (every pooled row of it). onDone refreshes the usage store.
export function createDisableStore(ctx, onDone = () => {}) {
  const $state = atom({})
  const key = (provider, id) => `${provider}\u0000${id}`
  const put = (k, v) => $state.set({ ...$state.get(), [k]: v })
  const toggle = async (provider, account) => {
    const k = key(provider, account.id)
    if ($state.get()[k]?.busy) return
    const want = !account.disabled
    put(k, { busy: true })
    try {
      const r = await ctx.rest('/disabled', { method: 'POST', body: { provider, account_id: account.id, disabled: want } })
      put(k, r && r.ok
        ? { message: want ? 'Disabled · not used until enabled' : 'Enabled' }
        : { error: true, message: want ? 'Not disabled; refresh and retry' : 'Still disabled by the main Hermes home (run hermes auth enable there)' })
      await onDone()
    } catch (err) {
      put(k, { error: true, message: `${want ? 'Disable' : 'Enable'} failed: ${String(err?.message || err)}` })
    }
  }
  return { $state, key, toggle }
}

// ── UI ────────────────────────────────────────────────────────────────────

function Dot({ t, size = 7, mark = null }) {
  return jsx('i', { 'aria-hidden': true, className: 'hermes-au-dot', 'data-mark': mark || undefined,
    style: { display: 'inline-block', width: size, height: size, borderRadius: '50%', background: COLORS[t], flex: 'none', '--au-c': COLORS[t] } })
}

function since(ts) {
  if (!ts) return ''
  const m = Math.round((Date.now() - ts) / 60_000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  if (m < 48 * 60) return `${Math.round(m / 60)}h ago`
  return `${Math.round(m / 1440)}d ago`
}

function MarkBadge({ g }) {
  const active = g.mark === 'active'
  return jsx('span', { style: { display: 'inline-block', whiteSpace: 'nowrap', fontSize: 9.5, padding: '0 4px', marginLeft: 5, borderRadius: 3, border: '1px solid var(--border)',
    color: active ? COLORS.ready : undefined, opacity: active ? 1 : 0.7 }, title: `${active ? 'in use' : 'last used'} ${since(g.markAt)}`, children: active ? 'in use' : 'last' })
}

function Meter({ win }) {
  if (!win) return jsx('span', { style: { fontSize: 10, opacity: 0.5 }, children: '—' })
  const pct = Math.max(0, Math.min(100, Number(win.used_percent) || 0))
  const t = pct >= 100 ? 'limited' : pct >= WARN_PCT ? 'warn' : 'ready'
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }, children: [
    jsxs('span', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 10, gap: 6 }, children: [
      jsx('b', { style: { color: pct >= 100 ? COLORS.limited : undefined, fontWeight: 600 }, children: `${Math.round(pct)}%` }),
      jsx('span', { style: { opacity: 0.65, whiteSpace: 'nowrap' }, children: pct >= 100 ? `back ${fmtReset(win.resets_at)}` : fmtReset(win.resets_at) })
    ] }),
    jsx('div', { style: { height: 4, borderRadius: 2, background: 'color-mix(in srgb, currentColor 18%, transparent)', overflow: 'hidden' }, children:
      jsx('div', { style: { height: '100%', width: `${pct}%`, background: COLORS[t] } }) })
  ] })
}

// minmax(0, …) lets long cause text wrap instead of widening the popover.
const GRID = { display: 'grid', gridTemplateColumns: '10px minmax(0,1.3fr) minmax(0,1fr) minmax(0,1fr)', gap: 8, alignItems: 'center' }
const PANEL_STYLE = { width: 'min(460px, calc(100vw - 24px))', maxWidth: 'calc(100vw - 24px)', maxHeight: 'min(80vh, 680px)', overflowY: 'auto', overflowX: 'hidden' }

function DisableLine({ g, a, disableStore, disableState }) {
  if (!g.canDisable || !disableStore) return null
  const st = disableState[disableStore.key(g.slug, a.id)] || {}
  const name = a.label || a.id
  return jsxs('div', { className: 'hermes-au-fix', children: [
    jsx('button', { type: 'button', 'aria-label': `${a.disabled ? 'Enable' : 'Disable'} ${name}`, disabled: !!st.busy,
      title: a.disabled ? 'Let Hermes use this account again' : 'Stop Hermes from using this account (it stays signed in)',
      onClick: () => disableStore.toggle(g.slug, a), children: st.busy ? 'Saving…' : a.disabled ? 'Enable account' : 'Disable account' }),
    st.message ? jsx('span', { style: { color: st.error ? COLORS.limited : undefined, opacity: st.error ? 1 : 0.8 }, children: st.message }) : null
  ] })
}

export function ReconnectLine({ g, a, reconnectStore, reconnectState }) {
  const seen = rowFix(reconnectState, g.slug, a)
  const wanted = needsReconnect(a, g.slug)
  // A finished result is spent once the row needs action again (the grant died later in the session).
  const st = seen.done && wanted ? {} : seen
  if (!wanted && !st.message) return null
  const route = ownerRoute()
  const name = a.label || a.id
  const children = []
  const say = (text, color) => children.push(jsx('span', { className: 'hermes-au-fixmsg', style: color ? { color } : undefined, children: text }))
  if (st.launching && st.sessionId) {
    children.push(jsx('button', { type: 'button', 'aria-label': `Cancel sign-in for ${name}`,
      onClick: () => reconnectStore.cancelAuth(g.slug, a, route), children: 'Cancel sign-in' }))
    if (st.userCode) children.push(jsx('span', { children: `Code ${st.userCode}` }))
    if (st.verificationUrl) children.push(jsx('button', { type: 'button', 'aria-label': `Open sign-in page for ${name}`,
      onClick: () => reconnectStore.openPage(st.verificationUrl), children: 'Open sign-in page' }))
    if (st.message) say(st.message)
  } else if (st.done) {
    say(st.message, COLORS.ready)
  } else {
    const confirmed = signInConfirmed(a, st, g.slug) || (!!st.needsSignIn && RECONNECT_SLUGS.has(g.slug))
    const external = EXTERNAL_SLUGS.has(g.slug) || st.authFlow === 'external'
    if (confirmed && external) {
      // Anthropic: core forbids an in-app login, so the supported step is Desktop's Accounts page.
      children.push(jsx('button', { type: 'button', 'aria-label': `Open sign-in settings for ${name}`, disabled: !!st.busy,
        onClick: () => reconnectStore.openSettings(g.slug, a), children: 'Sign in from Settings…' }))
      if (st.opened || st.check) children.push(jsx('button', { type: 'button', 'aria-label': `Check sign-in for ${name}`, disabled: !!st.busy,
        onClick: () => reconnectStore.check(g.slug, a), children: st.busy ? 'Checking…' : 'Check sign-in' }))
    } else if (confirmed) {
      children.push(jsx('button', { type: 'button', 'aria-label': `Sign in to ${name} again`, disabled: !!st.busy,
        onClick: () => reconnectStore.signIn(g.slug, a, route), children: st.busy ? 'Starting…' : 'Sign in again…' }))
      if (st.check) children.push(jsx('button', { type: 'button', 'aria-label': `Check sign-in for ${name}`, disabled: !!st.busy,
        onClick: () => reconnectStore.check(g.slug, a), children: 'Check sign-in' }))
    } else if (st.check) {
      children.push(jsx('button', { type: 'button', 'aria-label': `Check sign-in for ${name}`, disabled: !!st.busy,
        onClick: () => reconnectStore.check(g.slug, a), children: st.busy ? 'Checking…' : 'Check again' }))
    } else if (wanted || st.retry) {
      children.push(jsx('button', { type: 'button', 'aria-label': `Reconnect ${name}`, disabled: !!st.busy,
        onClick: () => reconnectStore.reconnect(g.slug, a, route), children: st.busy ? 'Reconnecting…' : st.retry ? 'Retry' : 'Reconnect' }))
    }
    const text = st.message || (confirmed
      ? (external ? 'Saved sign-in expired — sign in from Settings › Accounts' : 'Saved sign-in expired — sign in again')
      : '')
    if (text) say(text, st.error ? COLORS.limited : undefined)
  }
  return jsxs('div', { className: 'hermes-au-fix', children })
}

// Quota/cooldown reasons stay behind the chevron. A confirmed sign-in-needed warning is visible
// even when the row is collapsed, and the reconnect action is shown with it.
export function AccountRow({ g, a, open, onToggle, selectionStore, selectionState, reconnectStore, reconnectState, disableStore = null, disableState = {} }) {
  const t = tone(a)
  const name = a.label || a.id
  const why = describe(a)
  // Backend says manual choice is refused (managed model routing); keep the row as a read-only status line.
  const refused = a.disabled ? 'disabled' : typeof selectionState.selection?.unavailable === 'string' ? selectionState.selection.unavailable : ''
  const fix = rowFix(reconnectState, g.slug, a)
  const details = hasDetails(g, a)
  const signIn = needsReconnect(a, g.slug)
  const confirmed = signInConfirmed(a, fix, g.slug)
  const shown = (details && (open || signIn)) || !!fix.message || !!(disableStore && disableState[disableStore.key(g.slug, a.id)]?.message)
  const cue = t === 'ready' || t === 'unknown' ? undefined : COLORS[t]
  return jsxs('div', { children: [
    jsxs('div', { className: 'hermes-au-line', children: [
      jsxs('button', { type: 'button', className: 'hermes-au-row',
        'aria-label': refused ? `${name}: ${refused}` : `Try ${name} next`, disabled: selectionState.busy || selectionState.loading || !!refused,
        'aria-disabled': refused ? 'true' : undefined, 'data-unavailable': refused ? 'true' : undefined,
        onClick: refused ? undefined : () => selectionStore.select(g.slug, a),
        style: { ...GRID, opacity: a.disabled ? 0.45 : a.state === 'ready' || a.id === g.markId ? 1 : 0.6, ...(refused ? { cursor: 'not-allowed' } : {}) },
        title: refused ? `${name} · ${why || a.state} · ${refused}` : `${name} · ${why || a.state} · Next request in the focused chat only`, children: [
        jsx(Dot, { t, mark: a.id === g.markId ? g.mark : null }),
        jsxs('span', { style: { display: 'flex', alignItems: 'center', minWidth: 0, fontSize: 11.5, fontWeight: a.id === g.markId ? 600 : undefined }, children: [
          jsx('span', { style: { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: name }), a.id === g.markId ? jsx(MarkBadge, { g }) : null,
          signIn ? jsx('span', { className: confirmed ? 'hermes-au-signin' : 'hermes-au-disc', 'data-auth': confirmed ? 'sign-in' : 'disconnected',
            title: confirmed ? 'The saved sign-in is dead: sign in again' : 'Benched for an auth error: Reconnect renews it if the saved sign-in is still good',
            children: confirmed ? 'sign-in needed' : 'disconnected' }) : null,
          selectionState.selection?.state === 'pending' && selectionState.selection.provider === g.slug && selectionState.selection.account_id === a.id
            ? jsx('span', { className: 'hermes-au-next', children: 'Next to try' }) : null] }),
        ...(g.statusOnly
          ? [jsx('span', { className: 'hermes-au-pill', style: { gridColumn: 'span 2', '--au-c': COLORS[t] }, children: statusLabel(g, a) })]
          : [jsx(Meter, { win: pickWindow(a.windows, 'session') }), jsx(Meter, { win: pickWindow(a.windows, 'week') })])
      ] }),
      details
        ? jsx('button', { type: 'button', className: 'hermes-au-more', 'aria-label': `Details for ${name}`, 'aria-expanded': shown,
          style: { '--au-c': cue }, onClick: onToggle, children: '▶' })
        : jsx('span', { className: 'hermes-au-gap' })
    ] }),
    shown ? jsxs('div', { className: 'hermes-au-details', 'data-account': name, style: { '--au-c': cue }, children: [
      why ? jsx('span', { className: 'hermes-au-why', 'data-tone': t, style: { color: cue }, children: why }) : null,
      g.statusOnly ? jsx('span', { style: { opacity: 0.8 }, children: statusHint(g, a) }) : null,
      a.disabled ? null : jsx(ReconnectLine, { g, a, reconnectStore, reconnectState }),
      a.signedOut ? null : jsx(DisableLine, { g, a, disableStore, disableState })
    ] }) : null
  ] })
}

function GroupPanel({ g, selectionStore, selectionState, reconnectStore, reconnectState, disableStore, disableState }) {
  const [open, setOpen] = useState(() => new Set())
  const toggle = id => setOpen(prev => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next })
  const limited = g.limited, cooling = g.cooling
  const off = g.disabled || 0
  const unknown = g.total - g.ready - limited - cooling - off
  const parts = [`${g.ready} ready`, limited && `${limited} ${g.statusBad || 'out of quota'}`, cooling && `${cooling} cooling down`, off && `${off} disabled`, unknown && `${unknown} unknown`].filter(Boolean)
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 4 }, children: [
    jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }, children: [
      jsx('b', { style: { fontSize: 12 }, children: g.title }),
      jsx('span', { style: { fontSize: 10.5, opacity: 0.65 }, children: parts.join(' · ') })
    ] }),
    g.statusOnly ? null : jsxs('div', { className: 'hermes-au-line', style: { fontSize: 10, opacity: 0.5 }, children: [
      jsxs('div', { style: { ...GRID, flex: 1, padding: '0 3px' }, children: [jsx('span', {}), jsx('span', { children: 'Account' }), jsx('span', { children: '5-hour' }), jsx('span', { children: 'Weekly' })] }),
      jsx('span', { className: 'hermes-au-gap' })
    ] }),
    ...g.accounts.map(a => jsx(AccountRow, { key: a.id, g, a, open: open.has(a.id), onToggle: () => toggle(a.id), selectionStore, selectionState, reconnectStore, reconnectState, disableStore, disableState }))
  ] })
}

function ago(ts) {
  if (!ts) return 'never'
  const s = Math.round((Date.now() - ts) / 1000)
  return s < 60 ? `${s}s ago` : `${Math.round(s / 60)}m ago`
}

function UsageBar({ store, selectionStore, reconnectStore, disableStore }) {
  const { groups: polled, fetchedAt, loading, error } = useValue(store.$state)
  // Only this gateway + profile's reconnect state: another gateway's must not show or act here.
  const reconnectState = scopedFix(useValue(reconnectStore.$state))
  // An account core dropped for a dead grant stays listed until its sign-in is verified.
  const groups = withSignedOut(polled, reconnectState)
  const disableState = useValue(disableStore.$state)
  const selectionState = useValue(selectionStore.$state)
  // Subscriptions invalidate immediately; effect hydrates the newly focused route.
  const focused = useValue(host.state.focusedSessionId)
  const owner = useValue(host.state.focusedSessionOwner || host.state.profile)
  const connection = useValue(host.state.connectionId || host.state.profile)
  const profile = useValue(host.state.profile)
  const model = useValue(host.state.model)
  const [open, setOpen] = useState(false)
  // Opens on click (or Enter/Space/arrows); stays until Esc, an outside click, or clicking the same provider again.
  const [hl, setHl] = useState(null)
  useEffect(() => { store.refresh() }, [connection, profile, store])
  useEffect(() => { if (open) selectionStore.refresh() }, [open, focused, owner, connection, profile, model, selectionStore])
  if (!groups.length) {
    if (!error) return null
    const { label, title } = unavailableLabel(error)
    return jsx('span', { className: BAR_CLASS, title, children: label })
  }
  const shown = groups.find(g => g.slug === hl) || groups[0]
  const show = slug => {
    if (slug) setHl(slug)
    if (!open) { setOpen(true); store.refresh() }
  }
  const close = () => setOpen(false)
  const step = d => { const i = groups.findIndex(g => g.slug === shown.slug); setHl(groups[(i + d + groups.length) % groups.length].slug) }
  return jsxs(Popover, { open, onOpenChange: v => { if (v) show(null); else close() }, children: [
    jsx(PopoverTrigger, { asChild: true, children: jsx('button', { type: 'button', className: cn(BAR_CLASS, open && 'text-foreground'), 'aria-label': 'Account usage',
      'aria-expanded': open,
      // Click opens on the clicked provider; any click on the bar while open closes. Radix skips its
      // own toggle when the click is default-prevented.
      onClick: e => {
        e.preventDefault()
        if (open) close(); else show(e.target.closest?.('.hermes-au-chip')?.dataset.provider || null)
      },
      onKeyDown: e => {
        if (e.key === 'Escape' && open) { e.preventDefault(); close() }
        else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); if (!open) show(null); step(e.key === 'ArrowRight' ? 1 : -1) }
      },
      children:
      groups.filter(chipVisible).map(g => {
        const f = g.forecast
        const auth = authCounts(g, reconnectState)
        const authText = authLabel(auth)
        const tip = `${g.title}: ${g.ready} of ${g.total} ready${auth.signIn ? ` · ${auth.signIn} need sign-in (click to sign in)` : ''}${auth.disconnected ? ` · ${auth.disconnected} disconnected (click to reconnect)` : ''}${g.limited ? ` · ${g.limited} ${g.statusBad || 'out of quota'}` : ''}${g.cooling ? ` · ${g.cooling} cooling down` : ''}${g.disabled ? ` · ${g.disabled} disabled` : ''}${f && !g.statusOnly ? ` · ${leftText(f)} left · ${forecastText(f)}` : ''}${g.markId ? ` · ${g.mark === 'active' ? 'in use' : 'last used'}: ${(g.accounts.find(a => a.id === g.markId) || {}).label} (${since(g.markAt)})` : ''}${g.nextBack ? ` · next back ${fmtReset(g.nextBack)}` : ''}`
        return jsxs('span', { key: g.slug, className: 'hermes-au-chip', 'data-provider': g.slug, 'data-hl': open && g.slug === shown.slug ? 'true' : undefined,
          title: open ? undefined : tip, children: [
          // Compact provider tag in the bar; the popover table keeps the full name.
          jsx('b', { className: 'hermes-au-tag', 'aria-label': g.name, children: g.tag || g.name }),
          f && !g.statusOnly ? jsx(Sparkline, { f })
            // No forecast (status-only provider, or a backend without /forecast): the per-account dots.
            : jsx('span', { style: { display: 'inline-flex', gap: 2.5 }, children: g.accounts.map(a => jsx(Dot, { key: a.id, t: tone(a), size: 6, mark: a.id === g.markId ? g.mark : null })) }),
          jsx('span', { style: { color: countColor(g) }, children: `${g.ready}/${g.total}` }),
          authText ? jsx('span', { className: 'hermes-au-vb', 'data-signin': auth.signIn ? 'true' : undefined, 'data-disconnected': auth.disconnected ? 'true' : undefined,
            style: { color: auth.signIn ? COLORS.limited : COLORS.warn }, children: authText })
            : (f && !g.statusOnly && verdictText(f) ? jsx('span', { className: 'hermes-au-vb', style: { color: VERDICT_COLOR[f.verdict], opacity: VERDICT_COLOR[f.verdict] ? 1 : 0.6 }, children: verdictText(f) }) : null)
        ] })
      })
    }) }),
    jsx(PopoverContent, { side: 'top', align: 'end', className: 'w-[480px]', style: PANEL_STYLE,
      // Never steal focus from the composer on open; closing never refocuses the bar.
      onOpenAutoFocus: e => e.preventDefault(), onCloseAutoFocus: e => e.preventDefault(),
      // Focus moving elsewhere (e.g. back to the composer) never closes it: only a click (bar or outside) or Esc.
      onFocusOutside: e => e.preventDefault(),
      children:
      jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 10, padding: 4, fontSize: 12 }, children: [
        jsx(ForecastTable, { groups, hl: shown.slug, onPick: setHl }),
        jsx('div', { style: { borderTop: '1px solid var(--border)' } }),
        jsx(GroupPanel, { key: shown.slug, g: shown, selectionStore, selectionState, reconnectStore, reconnectState, disableStore, disableState }),
        jsx('div', { style: { borderTop: '1px solid var(--border)' } }),
        jsxs('div', { role: 'status', 'aria-live': 'polite', style: { fontSize: 10.5, opacity: 0.75 },
          title: 'Click an account for this chat’s next request only. Provider and model stay unchanged; other chats and global priority are unaffected. In use / last used is a provider-global observation, not this chat’s queued account.', children: [
          selectionState.busy ? 'Saving selection…' : selectionState.loading ? 'Loading selection…' : selectionState.selection ? `${selectionState.selection.provider} · ${selectionState.selection.model}` : 'Selection not loaded.',
          selectionState.selection?.state === 'pending' ? jsx('button', { type: 'button', 'aria-label': 'Cancel next account', disabled: selectionState.busy || selectionState.loading, onClick: () => selectionStore.cancel(), style: { marginLeft: 8, textDecoration: 'underline' }, children: 'Cancel next account' }) : null,
          jsx('button', { type: 'button', disabled: selectionState.busy || selectionState.loading, onClick: () => selectionStore.refresh(), style: { marginLeft: 8, textDecoration: 'underline' }, children: 'Refresh selection' })
        ] }),
        selectionState.error ? jsx('div', { role: 'alert', style: { color: 'var(--ui-text-primary)', border: '1px solid var(--ui-stroke-secondary)', padding: 7, overflowWrap: 'anywhere' }, children: selectionState.error }) : null,
        jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 10.5, opacity: 0.7 }, children: [
          jsx('span', { children: error ? `Refresh failed: ${error}` : `Updated ${ago(fetchedAt)} · forecast = weekly quota at the last-24h pace` }),
          jsx('button', { type: 'button', onClick: () => store.refresh(), style: { color: 'var(--accent)', cursor: 'pointer', whiteSpace: 'nowrap' }, children: loading ? 'Refreshing…' : '↻ Refresh' })
        ] })
      ] })
    })
  ] })
}

export default {
  id: ID,
  name: 'Account Usage',
  description: 'Status-bar chips with per-account Claude / ChatGPT subscription usage, plus Grok (xAI) API status.',
  register(ctx) {
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())
    const store = createStore(ctx)
    const selectionStore = createSelectionStore(ctx)
    // Twice: a poll already in flight resolves with the pre-sign-in state; the second read is fresh.
    // A failed read throws, so the readback reports "could not confirm" instead of judging stale rows.
    const reconnectStore = createReconnectStore(ctx, async () => {
      await store.refresh(); await store.refresh()
      const now = store.$state.get()
      if (now.error) throw new Error(now.error)
      return now.groups
    })
    // Twice: a poll already in flight resolves with the pre-toggle state; the second read is fresh.
    const disableStore = createDisableStore(ctx, async () => { await store.refresh(); await store.refresh() })
    store.refresh()
    // Second read shortly after: the first call kicks background usage fetches.
    ctx.setTimeout(() => store.refresh(), 8_000)
    ctx.setInterval(() => { store.refresh(); selectionStore.refresh() }, POLL_MS)
    ctx.register({ id: 'chips', area: STATUSBAR_AREAS.right, order: 5, render: () => jsx(UsageBar, { store, selectionStore, reconnectStore, disableStore }) })
  }
}
