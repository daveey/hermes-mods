/**
 * Account Usage — status-bar chips showing every pooled Claude / ChatGPT (Codex)
 * subscription account: ready count, a dot per account, and a click-open
 * popover with per-account 5-hour session + weekly bars and reset times.
 * The popover is a plain table of visual cues; why an account is limited, status
 * hints and Reconnect open from the row's chevron.
 *
 * Data: gateway JSON-RPC `model.options` (same payload the model picker uses).
 * Pooled providers carry `usage.accounts[]` = {label, state, windows[], resets_at} plus the
 * sanitized cause fields {limit_kind: quota|cooldown, reason, cooldown_until, model_cooldowns[]}.
 * Red = out of quota (waits for the provider reset); amber = a temporary pool cooldown. Both
 * stay unselectable; an older backend without limit_kind stays red (never masked as green).
 * Read-only: the call serves cached snapshots and kicks a background refresh,
 * so polling it never blocks a turn or rotates a credential.
 */

import { atom, cn, host, Popover, PopoverContent, PopoverTrigger, STATUSBAR_AREAS, useValue } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
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
@media (prefers-reduced-motion: reduce) { .hermes-au-dot[data-mark='active'] { animation: none } }
`

const PROVIDERS = [
  { slug: 'anthropic', name: 'Claude', title: 'Claude Max' },
  { slug: 'openai-codex', name: 'GPT', title: 'ChatGPT (Codex)' },
  // xAI publishes no subscription-usage API; the backend reports API access (team_blocked) only.
  { slug: 'xai-oauth', name: 'Grok', title: 'Grok (xAI)', statusOnly: true },
  // Gemini AI Studio API keys: status only (key valid / rejected). A future Gemini subscription
  // provider gets its own entry here (e.g. slug 'gemini-oauth'), not a change to this one.
  { slug: 'gemini', name: 'Gemini', title: 'Gemini (AI Studio key)', statusOnly: true, statusOk: 'API key valid · no usage windows (AI Studio has no usage API)', statusBad: 'key rejected', statusBadHint: 'key rejected · check the key at aistudio.google.com/apikey' }
]

const COLORS = { ready: '#3fb950', warn: '#d29922', cooldown: '#e3b341', limited: '#f85149', unknown: 'var(--ui-text-tertiary, #888)', unavailable: 'var(--ui-text-tertiary, #888)' }

const REASONS = { quota_window: 'quota used up', billing: 'billing / credits', usage_limit: 'usage limit reached', rate_limit: 'rate limited', auth: 'auth error', cooldown: 'cooling down' }

const BAR_CLASS =
  'inline-flex h-full items-center gap-1.5 rounded-none px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground'

// ── pure helpers (exported for tests) ─────────────────────────────────────

export function pickWindow(windows, kind) {
  const re = kind === 'session' ? /session|5.?h|hour/i : /week/i
  return (windows || []).find(w => w && w.scope !== 'model' && re.test(String(w.label || ''))) || null
}

export function tone(account) {
  // Only an explicit backend 'cooldown' verdict turns a limited account amber.
  if (account.state === 'limited') return account.limit_kind === 'cooldown' ? 'cooldown' : 'limited'
  if (account.state !== 'ready') return account.state === 'unavailable' ? 'unavailable' : 'unknown'
  const hi = Math.max(...(account.windows || []).filter(w => w.scope !== 'model').map(w => Number(w.used_percent) || 0), 0)
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
    parts.push(account.reason === 'auth' ? 'sign-in needed' : 'unavailable')
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

// The one- or two-word status a status-only row shows in its table cell; statusHint is the detail.
export function statusLabel(g, a) {
  if (a.state === 'ready') return 'ok'
  if (a.state === 'limited') return g.statusBad || 'blocked'
  if (a.state === 'unavailable') return 'sign-in needed'
  return 'not checked'
}

// Whether a row has anything behind its disclosure chevron. Ready rows with nothing to say have none.
export function hasDetails(g, a) {
  return !!g.statusOnly || !!describe(a) || needsReconnect(a)
}

export function statusHint(g, a) {
  if (a.state === 'ready') return g.statusOk || 'API access ok · no usage windows (xAI has no usage API)'
  if (a.state === 'limited') return g.statusBadHint || 'API blocked · add credits at console.x.ai'
  if (a.state === 'unavailable') return 'credential unavailable · sign in again'
  return 'not checked yet · rechecks in a few seconds'
}

export function summarize(payload, last = {}, now = Date.now()) {
  const rows = (payload && payload.providers) || []
  return PROVIDERS.map(p => {
    const row = rows.find(r => r && r.slug === p.slug)
    let accounts = row && row.usage && Array.isArray(row.usage.accounts) ? row.usage.accounts : null
    // Single-account provider: legacy gauge shape `usage.windows`.
    if (!accounts && row && row.usage && Array.isArray(row.usage.windows)) {
      const limited = row.usage.windows.some(w => w.scope !== 'model' && Number(w.used_percent) >= 100)
      // Status-only providers (xAI): 100% means "API blocked (no credits)", not a spent quota window.
      const reason = limited ? (p.statusBad ? 'auth' : p.statusOnly ? 'billing' : 'quota_window') : null
      accounts = [{ id: p.slug, selectable: false, label: row.name || p.title, windows: row.usage.windows, state: limited ? 'limited' : 'ready', resets_at: null, limit_kind: limited ? 'quota' : null, reason, ...(limited && p.statusBad ? { status_bad: p.statusBad } : {}) }]
    }
    if (!accounts || !accounts.length) return null
    // Status-only providers with their own wording (Gemini): a rejected key is not "out of quota".
    if (p.statusBad) accounts = accounts.map(a => (a.state === 'limited' ? { ...a, reason: 'auth', status_bad: p.statusBad } : a))
    const ready = accounts.filter(a => a.state === 'ready').length
    const cooling = accounts.filter(a => tone(a) === 'cooldown').length
    const limited = accounts.filter(a => tone(a) === 'limited').length
    const nextBack = accounts
      .filter(a => a.state === 'limited' && a.resets_at)
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
    return { ...p, accounts, ready, cooling, limited, total: accounts.length, nextBack, markId, mark, markAt: rec ? rec.at * 1000 : null }
  }).filter(Boolean)
}

// A disconnected account: dead grant, or benched for an auth error. These get a Reconnect button.
export function needsReconnect(account) {
  return !!account && (account.state === 'unavailable' || account.reason === 'auth')
}

// ── state ─────────────────────────────────────────────────────────────────

// Provider-global observation, scoped to the ambient gateway/profile. Never fall
// back to a fixed local file: that would leak the default profile onto remotes.
async function readLast(ctx) {
  try { return await ctx.rest('/last') || {} } catch { return {} }
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
    inflight = Promise.all([host.request('model.options', {}, 30_000), readLast(ctx)])
      .then(([payload, last]) => { if (current()) $state.set({ groups: summarize(payload, last || {}), fetchedAt: Date.now(), loading: false, error: null }) })
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

// Reconnect = renew the account's OAuth grant in place (backend force-refresh). When the grant is
// dead the reply says needs_sign_in and the row offers the provider sign-in flow instead.
export function createReconnectStore(ctx, onDone = () => {}) {
  const $state = atom({})
  const key = (provider, id) => `${provider}\u0000${id}`
  const put = (k, v) => $state.set({ ...$state.get(), [k]: v })
  const reconnect = async (provider, account) => {
    const k = key(provider, account.id)
    if ($state.get()[k]?.busy) return
    put(k, { busy: true })
    try {
      const r = await ctx.rest('/reconnect', { method: 'POST', body: { provider, account_id: account.id } })
      put(k, r && r.ok
        ? { done: true, message: 'Reconnected' }
        : { needsSignIn: true, command: r?.sign_in_command || `hermes auth add ${provider} --type oauth`, message: 'Saved sign-in expired — sign in again' })
      if (r && r.ok) onDone()
    } catch (err) {
      put(k, { error: true, message: `Reconnect failed: ${String(err?.message || err)}` })
    }
  }
  return { $state, key, reconnect }
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
      jsx('span', { style: { opacity: 0.65 }, children: pct >= 100 ? `back ${fmtReset(win.resets_at)}` : fmtReset(win.resets_at) })
    ] }),
    jsx('div', { style: { height: 4, borderRadius: 2, background: 'color-mix(in srgb, currentColor 18%, transparent)', overflow: 'hidden' }, children:
      jsx('div', { style: { height: '100%', width: `${pct}%`, background: COLORS[t] } }) })
  ] })
}

// minmax(0, …) lets long cause text wrap instead of widening the popover.
const GRID = { display: 'grid', gridTemplateColumns: '10px minmax(0,1.3fr) minmax(0,1fr) minmax(0,1fr)', gap: 8, alignItems: 'center' }
const PANEL_STYLE = { width: 'min(460px, calc(100vw - 24px))', maxWidth: 'calc(100vw - 24px)', maxHeight: 'min(80vh, 680px)', overflowY: 'auto', overflowX: 'hidden' }

function ReconnectLine({ g, a, reconnectStore, reconnectState }) {
  const st = reconnectState[reconnectStore.key(g.slug, a.id)] || {}
  if (!needsReconnect(a) && !st.message) return null
  const children = []
  if (st.needsSignIn) {
    children.push(jsx('button', { type: 'button', 'aria-label': `Sign in to ${a.label || a.id} again`,
      onClick: () => host.navigate('/settings?tab=providers'), children: 'Sign in again…' }))
    children.push(jsxs('span', { children: [st.message, ' · or run ', jsx('code', { children: st.command })] }))
  } else {
    if (!st.done) children.push(jsx('button', { type: 'button', 'aria-label': `Reconnect ${a.label || a.id}`, disabled: !!st.busy,
      onClick: () => reconnectStore.reconnect(g.slug, a), children: st.busy ? 'Reconnecting…' : 'Reconnect' }))
    if (st.message) children.push(jsx('span', { style: { color: st.error ? COLORS.limited : COLORS.ready }, children: st.message }))
  }
  return jsxs('div', { className: 'hermes-au-fix', children })
}

// The table shows only visual cues (dot colour, bars, dimmed rows, a tinted chevron). Why an
// account is limited, status hints and the Reconnect action sit behind the row's chevron.
export function AccountRow({ g, a, open, onToggle, selectionStore, selectionState, reconnectStore, reconnectState }) {
  const t = tone(a)
  const name = a.label || a.id
  const why = describe(a)
  // Backend says manual choice is refused (managed model routing); keep the row as a read-only status line.
  const refused = typeof selectionState.selection?.unavailable === 'string' ? selectionState.selection.unavailable : ''
  const fix = reconnectState[reconnectStore.key(g.slug, a.id)] || {}
  const details = hasDetails(g, a)
  // A Reconnect outcome stays visible after the click even if the row is collapsed again.
  const shown = details && (open || !!fix.message)
  const cue = t === 'ready' || t === 'unknown' ? undefined : COLORS[t]
  return jsxs('div', { children: [
    jsxs('div', { className: 'hermes-au-line', children: [
      jsxs('button', { type: 'button', className: 'hermes-au-row',
        'aria-label': refused ? `${name}: ${refused}` : `Try ${name} next`, disabled: selectionState.busy || selectionState.loading || !!refused,
        'aria-disabled': refused ? 'true' : undefined, 'data-unavailable': refused ? 'true' : undefined,
        onClick: refused ? undefined : () => selectionStore.select(g.slug, a),
        style: { ...GRID, opacity: a.state === 'ready' || a.id === g.markId ? 1 : 0.6, ...(refused ? { cursor: 'not-allowed' } : {}) },
        title: refused ? `${name} · ${why || a.state} · ${refused}` : `${name} · ${why || a.state} · Next request in the focused chat only`, children: [
        jsx(Dot, { t, mark: a.id === g.markId ? g.mark : null }),
        jsxs('span', { style: { display: 'flex', alignItems: 'center', minWidth: 0, fontSize: 11.5, fontWeight: a.id === g.markId ? 600 : undefined }, children: [
          jsx('span', { style: { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: name }), a.id === g.markId ? jsx(MarkBadge, { g }) : null,
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
      jsx(ReconnectLine, { g, a, reconnectStore, reconnectState })
    ] }) : null
  ] })
}

function GroupPanel({ g, selectionStore, selectionState, reconnectStore, reconnectState }) {
  const [open, setOpen] = useState(() => new Set())
  const toggle = id => setOpen(prev => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next })
  const limited = g.limited, cooling = g.cooling
  const unknown = g.total - g.ready - limited - cooling
  const parts = [`${g.ready} ready`, limited && `${limited} ${g.statusBad || 'out of quota'}`, cooling && `${cooling} cooling down`, unknown && `${unknown} unknown`].filter(Boolean)
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 4 }, children: [
    jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }, children: [
      jsx('b', { style: { fontSize: 12 }, children: g.title }),
      jsx('span', { style: { fontSize: 10.5, opacity: 0.65 }, children: parts.join(' · ') })
    ] }),
    g.statusOnly ? null : jsxs('div', { className: 'hermes-au-line', style: { fontSize: 10, opacity: 0.5 }, children: [
      jsxs('div', { style: { ...GRID, flex: 1, padding: '0 3px' }, children: [jsx('span', {}), jsx('span', { children: 'Account' }), jsx('span', { children: '5-hour' }), jsx('span', { children: 'Weekly' })] }),
      jsx('span', { className: 'hermes-au-gap' })
    ] }),
    ...g.accounts.map(a => jsx(AccountRow, { key: a.id, g, a, open: open.has(a.id), onToggle: () => toggle(a.id), selectionStore, selectionState, reconnectStore, reconnectState }))
  ] })
}

function ago(ts) {
  if (!ts) return 'never'
  const s = Math.round((Date.now() - ts) / 1000)
  return s < 60 ? `${s}s ago` : `${Math.round(s / 60)}m ago`
}

function UsageBar({ store, selectionStore, reconnectStore }) {
  const { groups, fetchedAt, loading, error } = useValue(store.$state)
  const reconnectState = useValue(reconnectStore.$state)
  const selectionState = useValue(selectionStore.$state)
  // Subscriptions invalidate immediately; effect hydrates the newly focused route.
  const focused = useValue(host.state.focusedSessionId)
  const owner = useValue(host.state.focusedSessionOwner || host.state.profile)
  const connection = useValue(host.state.connectionId || host.state.profile)
  const profile = useValue(host.state.profile)
  const model = useValue(host.state.model)
  const [open, setOpen] = useState(false)
  useEffect(() => { store.refresh() }, [connection, profile, store])
  useEffect(() => { if (open) selectionStore.refresh() }, [open, focused, owner, connection, profile, model, selectionStore])
  if (!groups.length) {
    if (!error) return null
    return jsx('span', { className: BAR_CLASS, title: `Account usage unavailable: ${error}`, children: 'usage ?' })
  }
  return jsxs(Popover, { open, onOpenChange: v => { setOpen(v); if (v) store.refresh() }, children: [
    jsx(PopoverTrigger, { asChild: true, children: jsx('button', { type: 'button', className: cn(BAR_CLASS, open && 'text-foreground'), 'aria-label': 'Account usage', children:
      groups.map(g => jsxs('span', { key: g.slug, style: { display: 'inline-flex', alignItems: 'center', gap: 5, marginRight: 6 },
        title: `${g.title}: ${g.ready} of ${g.total} ready${g.limited ? ` · ${g.limited} ${g.statusBad || 'out of quota'}` : ''}${g.cooling ? ` · ${g.cooling} cooling down` : ''}${g.markId ? ` · ${g.mark === 'active' ? 'in use' : 'last used'}: ${(g.accounts.find(a => a.id === g.markId) || {}).label} (${since(g.markAt)})` : ''}${g.nextBack ? ` · next back ${fmtReset(g.nextBack)}` : ''}`, children: [
          jsx('b', { style: { fontWeight: 600 }, children: g.name }),
          jsx('span', { style: { display: 'inline-flex', gap: 2.5 }, children: g.accounts.map(a => jsx(Dot, { key: a.id, t: tone(a), size: 6, mark: a.id === g.markId ? g.mark : null })) }),
          jsx('span', { style: { color: countColor(g) }, children: `${g.ready}/${g.total}` })
        ] }))
    }) }),
    jsx(PopoverContent, { side: 'top', align: 'end', className: 'w-[460px]', style: PANEL_STYLE, children:
      jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 10, padding: 4, fontSize: 12 }, children: [
        jsxs('div', { role: 'status', 'aria-live': 'polite', style: { fontSize: 10.5, opacity: 0.75 },
          title: 'Click an account for this chat’s next request only. Provider and model stay unchanged; other chats and global priority are unaffected. In use / last used is a provider-global observation, not this chat’s queued account.', children: [
          selectionState.busy ? 'Saving selection…' : selectionState.loading ? 'Loading selection…' : selectionState.selection ? `${selectionState.selection.provider} · ${selectionState.selection.model}` : 'Selection not loaded.',
          selectionState.selection?.state === 'pending' ? jsx('button', { type: 'button', 'aria-label': 'Cancel next account', disabled: selectionState.busy || selectionState.loading, onClick: () => selectionStore.cancel(), style: { marginLeft: 8, textDecoration: 'underline' }, children: 'Cancel next account' }) : null,
          jsx('button', { type: 'button', disabled: selectionState.busy || selectionState.loading, onClick: () => selectionStore.refresh(), style: { marginLeft: 8, textDecoration: 'underline' }, children: 'Refresh selection' })
        ] }),
        selectionState.error ? jsx('div', { role: 'alert', style: { color: 'var(--ui-text-primary)', border: '1px solid var(--ui-stroke-secondary)', padding: 7, overflowWrap: 'anywhere' }, children: selectionState.error }) : null,
        ...groups.flatMap((g, i) => [i ? jsx('div', { key: `sep-${g.slug}`, style: { borderTop: '1px solid var(--border)' } }) : null, jsx(GroupPanel, { key: g.slug, g, selectionStore, selectionState, reconnectStore, reconnectState })]).filter(Boolean),
        jsx('div', { style: { borderTop: '1px solid var(--border)' } }),
        jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 10.5, opacity: 0.7 }, children: [
          jsx('span', { children: error ? `Refresh failed: ${error}` : `Updated ${ago(fetchedAt)} · auto every 30s` }),
          jsx('button', { type: 'button', onClick: () => store.refresh(), style: { color: 'var(--accent)', cursor: 'pointer' }, children: loading ? 'Refreshing…' : '↻ Refresh' })
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
    const reconnectStore = createReconnectStore(ctx, () => store.refresh())
    store.refresh()
    // Second read shortly after: the first call kicks background usage fetches.
    ctx.setTimeout(() => store.refresh(), 8_000)
    ctx.setInterval(() => { store.refresh(); selectionStore.refresh() }, POLL_MS)
    ctx.register({ id: 'chips', area: STATUSBAR_AREAS.right, order: 5, render: () => jsx(UsageBar, { store, selectionStore, reconnectStore }) })
  }
}
