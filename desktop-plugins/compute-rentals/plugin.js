// Compute Rentals — cloud inventory is read-only; bindings only edit the local registry.
import {
  host,
  useValue,
  useQuery,
  useQueryClient,
  STATUSBAR_AREAS,
  Button,
  StatusDot,
  Popover,
  PopoverTrigger,
  PopoverContent,
} from '@hermes/plugin-sdk'
import { createElement as h, useState, useRef, useEffect } from 'react'

const ID = 'compute-rentals'
const BAR =
  'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'

const C_GOOD = '#3fb950'
const C_WARN = '#d29922'
const C_BAD = '#f85149'
export const CSS = `
.hermes-cr { display: flex; flex-direction: column; max-height: min(70vh, 640px); font-size: 12px; line-height: 1.4; color: var(--foreground) }
.hermes-cr :is(.hermes-cr-icon, .hermes-cr-seg button, .hermes-cr-sechead, .hermes-cr-row, .hermes-cr-link, .hermes-cr-back, .hermes-cr-srcbtn, .hermes-cr-src, button.hermes-cr-chip) { font: inherit; color: inherit; background: transparent; border: 0; margin: 0; cursor: pointer }
.hermes-cr button:focus-visible { outline: 2px solid var(--ui-accent); outline-offset: 1px }
.hermes-cr button:disabled { cursor: default }
.hermes-cr-head { display: flex; align-items: center; gap: 6px; padding: 8px 8px 4px 12px }
.hermes-cr-title { font-weight: 600; font-size: 12.5px; margin-right: auto }
.hermes-cr-seg { display: inline-flex; border: 1px solid var(--ui-stroke-secondary); border-radius: 6px; padding: 1px; gap: 1px }
.hermes-cr .hermes-cr-seg button { font-size: 11px; padding: 1px 9px; border-radius: 4px; color: var(--ui-text-secondary) }
.hermes-cr .hermes-cr-seg button:hover { color: var(--foreground) }
.hermes-cr .hermes-cr-seg button[aria-pressed='true'] { background: var(--chrome-action-hover); color: var(--foreground) }
.hermes-cr .hermes-cr-icon { width: 22px; height: 22px; display: inline-grid; place-items: center; border-radius: 5px; color: var(--ui-text-secondary); font-size: 13px; line-height: 1 }
.hermes-cr .hermes-cr-icon:hover:not(:disabled) { background: var(--chrome-action-hover); color: var(--foreground) }
.hermes-cr .hermes-cr-icon:disabled { opacity: .5 }
.hermes-cr-spin { display: inline-block; animation: hermes-cr-spin 0.9s linear infinite }
@keyframes hermes-cr-spin { to { transform: rotate(360deg) } }
.hermes-cr-summary { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 2px 12px 8px; border-bottom: 1px solid var(--border) }
.hermes-cr-big { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; margin-right: 2px }
.hermes-cr-sr { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0 }
.hermes-cr-big span { color: var(--ui-text-tertiary); font-weight: 400; margin: 0 4px }
.hermes-cr-chip { display: inline-flex; align-items: center; gap: 3px; font-size: 10.5px; line-height: 16px; padding: 0 5px; border: 1px solid var(--ui-stroke-secondary); border-radius: 4px; color: var(--ui-text-secondary); white-space: nowrap }
.hermes-cr-chip[data-tone='warn'] { color: ${C_WARN}; border-color: color-mix(in srgb, ${C_WARN} 45%, transparent) }
.hermes-cr .hermes-cr-chip:is(button):hover { color: var(--foreground) }
.hermes-cr-updated { margin-left: auto; font-size: 10.5px; color: var(--ui-text-tertiary); white-space: nowrap }
.hermes-cr-body { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 4px 4px 6px; overscroll-behavior: contain }
.hermes-cr-msg { margin: 4px 8px; font-size: 11px; color: var(--ui-text-secondary); overflow-wrap: anywhere }
.hermes-cr-msg[role='alert'] { color: var(--foreground); border-left: 2px solid ${C_BAD}; padding: 2px 0 2px 7px }
.hermes-cr-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 22px 12px; text-align: center; color: var(--ui-text-secondary) }
.hermes-cr-empty strong { color: var(--foreground); font-weight: 500 }
.hermes-cr-sec { margin-top: 2px }
.hermes-cr-sec h3 { margin: 0; font: inherit }
.hermes-cr .hermes-cr-sechead { width: 100%; display: flex; align-items: center; gap: 6px; padding: 5px 8px; border-radius: 5px; text-align: left; font-size: 11.5px }
.hermes-cr .hermes-cr-sechead:hover { background: var(--chrome-action-hover) }
.hermes-cr-chev { flex: none; width: 8px; height: 8px; display: inline-grid; place-items: center; transition: transform .12s }
.hermes-cr-chev::before { content: ''; width: 4px; height: 4px; border-right: 1.5px solid var(--ui-text-tertiary); border-bottom: 1.5px solid var(--ui-text-tertiary); transform: translateY(-1px) rotate(45deg) }
.hermes-cr-sechead[aria-expanded='false'] .hermes-cr-chev { transform: rotate(-90deg) }
.hermes-cr-label { font-weight: 600 }
.hermes-cr-count { color: var(--ui-text-tertiary); font-variant-numeric: tabular-nums }
.hermes-cr-sub { margin-left: auto; color: var(--ui-text-secondary); font-variant-numeric: tabular-nums; white-space: nowrap }
.hermes-cr-reason { margin: 0 8px 3px 22px; font-size: 11px; color: var(--ui-text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
.hermes-cr-reason[data-tone='bad'] { color: ${C_BAD} }
.hermes-cr-reason[data-tone='warn'] { color: ${C_WARN} }
.hermes-cr .hermes-cr-row { width: 100%; display: grid; grid-template-columns: 8px minmax(0, 1fr) minmax(0, max-content) 54px 72px; align-items: center; column-gap: 8px; padding: 3px 8px 3px 22px; border-radius: 4px; text-align: left; font-size: 11.5px }
.hermes-cr .hermes-cr-row:hover { background: var(--chrome-action-hover) }
.hermes-cr-name { display: flex; align-items: center; gap: 6px; min-width: 0 }
.hermes-cr-trunc { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
.hermes-cr-hw { max-width: 132px; color: var(--ui-text-tertiary); font-size: 11px }
.hermes-cr-status { color: var(--ui-text-tertiary); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
.hermes-cr-rate { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--ui-text-secondary) }
.hermes-cr-rate[data-kind='paid'] { color: var(--foreground) }
.hermes-cr-badge { flex: none; font-size: 10px; line-height: 14px; padding: 0 4px; border-radius: 3px; border: 1px solid var(--ui-stroke-secondary); color: var(--ui-text-secondary); white-space: nowrap }
.hermes-cr-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; flex: none; background: color-mix(in srgb, currentColor 32%, transparent) }
.hermes-cr-dot[data-tone='good'] { background: ${C_GOOD} }
.hermes-cr-dot[data-tone='warn'] { background: ${C_WARN} }
.hermes-cr-dot[data-tone='bad'] { background: ${C_BAD} }
.hermes-cr-dot[data-hollow] { background: transparent; border: 1.5px solid ${C_GOOD}; box-sizing: border-box }
.hermes-cr-dot[data-hollow][data-tone='muted'] { border-color: color-mix(in srgb, currentColor 40%, transparent) }
.hermes-cr-dot[data-hollow][data-tone='bad'] { border-color: ${C_BAD} }
.hermes-cr-dot[data-hollow][data-tone='warn'] { border-color: ${C_WARN} }
.hermes-cr-wls { margin: 2px 8px 4px 22px; font-size: 11px; color: var(--ui-text-secondary) }
.hermes-cr-wls p { margin: 2px 0 }
.hermes-cr-wls strong, .hermes-cr-sub-h { display: block; margin: 6px 0 2px; font-size: 10.5px; font-weight: 600; color: var(--ui-text-secondary); text-transform: uppercase; letter-spacing: .03em }
.hermes-cr-wl { list-style: none; margin: 0; padding: 0 }
.hermes-cr-wl li { display: grid; grid-template-columns: minmax(0, 1fr) 120px 108px; column-gap: 8px; padding: 1px 0; font-size: 11px }
.hermes-cr-wl li > span:first-child { color: var(--foreground) }
.hermes-cr-wl li > span + span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
.hermes-cr-wl li > span:last-child { text-align: right }
.hermes-cr-more { display: flex; align-items: center; gap: 6px; margin: 2px 0; font-size: 11px; color: var(--ui-text-tertiary) }
.hermes-cr .hermes-cr-link { color: var(--ui-accent); font-size: 11px; padding: 0 }
.hermes-cr .hermes-cr-link:hover { text-decoration: underline }
.hermes-cr .hermes-cr-back { display: inline-flex; gap: 4px; align-items: center; margin: 2px 4px 6px; padding: 2px 6px; border-radius: 4px; font-size: 11px; color: var(--ui-text-secondary) }
.hermes-cr .hermes-cr-back:hover { background: var(--chrome-action-hover); color: var(--foreground) }
.hermes-cr-detail { padding: 0 8px 4px }
.hermes-cr-dhead { display: flex; align-items: center; gap: 7px; margin-bottom: 8px }
.hermes-cr-dhead b { font-size: 13px; font-weight: 600; overflow-wrap: anywhere }
.hermes-cr-dl { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 3px 12px; margin: 0; font-size: 11.5px; overflow-wrap: anywhere }
.hermes-cr-dl dt { color: var(--ui-text-tertiary) }
.hermes-cr-dl dd { margin: 0 }
.hermes-cr-dl code { font-size: 10.5px; padding: 0 3px; border-radius: 3px; background: color-mix(in srgb, currentColor 8%, transparent) }
.hermes-cr-actions { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 4px }
.hermes-cr-note { margin: 2px 0 0; font-size: 10.5px; color: var(--ui-text-tertiary) }
.hermes-cr-foot { border-top: 1px solid var(--border); padding: 4px 4px 6px; font-size: 11px }
.hermes-cr .hermes-cr-srcbtn { width: 100%; display: flex; align-items: center; gap: 6px; padding: 3px 8px; border-radius: 4px; color: var(--ui-text-secondary); text-align: left }
.hermes-cr .hermes-cr-srcbtn:hover { background: var(--chrome-action-hover); color: var(--foreground) }
.hermes-cr-srcbtn[aria-expanded='false'] .hermes-cr-chev { transform: rotate(-90deg) }
.hermes-cr-tally { display: inline-flex; align-items: center; gap: 4px; color: var(--ui-text-tertiary) }
.hermes-cr-issue { display: flex; align-items: center; gap: 6px; margin: 1px 8px; color: var(--ui-text-secondary); white-space: nowrap; overflow: hidden }
.hermes-cr-sources { max-height: 38vh; overflow: auto }
.hermes-cr .hermes-cr-src { width: 100%; display: grid; grid-template-columns: 8px 8px max-content minmax(0, 1fr) auto; align-items: center; column-gap: 6px; padding: 2px 8px; border-radius: 4px; text-align: left }
.hermes-cr .hermes-cr-src:hover { background: var(--chrome-action-hover) }
.hermes-cr-src[aria-expanded='false'] .hermes-cr-chev { transform: rotate(-90deg) }
.hermes-cr-src .hermes-cr-chev { width: 8px }
.hermes-cr-when { color: var(--ui-text-tertiary); white-space: nowrap; font-variant-numeric: tabular-nums }
.hermes-cr-srcdetail { margin: 2px 8px 6px 36px }
.hermes-cr-srcdetail .hermes-cr-dl { grid-template-columns: 76px minmax(0, 1fr); font-size: 11px; color: var(--ui-text-secondary) }
@media (prefers-reduced-motion: reduce) { .hermes-cr-spin { animation: none } .hermes-cr-chev { transition: none } }
`

export function ComputeRentals({ ctx }) {
  const session = useValue(host.state.focusedStoredSessionId)
  const owner = useValue(host.state.focusedSessionOwner)
  const connection = useValue(host.state.connectionId)
  const profile = useValue(host.state.profile)
  const identity = {
    connection,
    ambientProfile: profile,
    profile: owner?.profile || profile,
    session_id: session || '',
    ownerConnection: owner?.connectionId || '',
  }
  // A keyed mount clears popover filters and query observer on any identity change.
  return h(Inventory, { key: JSON.stringify(identity), ctx, identity })
}

function Inventory({ ctx, identity }) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState('Session')
  const [provider, setProvider] = useState(null)
  const [selected, setSelected] = useState(null)
  const [actionError, setActionError] = useState('')
  const [collapsed, setCollapsed] = useState({})
  const [, setTick] = useState(0)
  const forceRefresh = useRef(false)
  const client = useQueryClient()
  // Relative times ("12s ago") re-render while the popover is open.
  useEffect(() => {
    if (!open) return undefined
    const timer = setInterval(() => setTick((n) => n + 1), 15_000)
    return () => clearInterval(timer)
  }, [open])
  // ctx.rest routes by the ambient connection/profile, not the query's profile.
  const available = Boolean(
    identity.connection &&
      (!identity.session_id ||
        (identity.ownerConnection === identity.connection && identity.profile === identity.ambientProfile)),
  )
  const query = useQuery({
    queryKey: [
      ID,
      identity.connection,
      identity.ambientProfile,
      identity.ownerConnection,
      identity.profile,
      identity.session_id,
    ],
    queryFn: async () => {
      if (!available) throw Error('Focused session gateway/profile is unavailable through this connection.')
      if (!identityCurrent(identity)) throw Error('Focus changed; discard the old inventory request.')
      const refresh = forceRefresh.current
      forceRefresh.current = false
      return ctx.rest(
        `/inventory?${new URLSearchParams({ session_id: identity.session_id, profile: identity.profile, gateway_id: '', refresh: String(refresh) })}`,
        { method: 'GET' },
      )
    },
    enabled: available,
    refetchInterval: 30_000,
    retry: false,
    placeholderData: undefined,
  })
  const data = query.data
  const now = Date.now()
  const scope = {
    gateway_id: data?.gateway_id || '',
    profile: identity.profile,
    session_id: identity.session_id,
  }
  const sessionRows = sessionMachines(data?.machines, scope)
  const providerRows = Array.isArray(data?.providers) ? data.providers : []
  registerProviderLabels(providerRows)
  const complete =
    Boolean(data?.summary?.complete) && providerRows.every((row) => row.status === 'ok') && !query.isError
  const sessionKnown = available && Boolean(scope.gateway_id && scope.profile && scope.session_id)
  const sessionSummary = data && sessionKnown ? summarizeMachines(sessionRows, complete) : undefined
  const globalSummary = data && { ...data.summary, complete }
  const groups = [...new Set(sessionRows.map((row) => row.provider))].sort()
  const workloads = Array.isArray(data?.workloads) ? data.workloads : []
  const machineKeys = new Set((data?.machines || []).map((row) => row.key))
  const children = childWorkloads(workloads)
  const sessionKeys = new Set(sessionRows.map((row) => row.key))
  const show = (vendor = null, key = null) => {
    setMode('Session')
    setProvider(vendor)
    setSelected(key)
    setOpen(true)
  }
  // Session Tasks' machine chips open a machine here. A chip can name a machine
  // bound to an ancestor of the focused chat (lineage), which Session mode lists
  // only for the exact id, so fall back to Mine for those.
  const showRef = useRef(null)
  showRef.current = (key) => {
    if (sessionKeys.has(key)) return show(null, key)
    setMode(mineMachines(data?.machines).some((row) => row.key === key) ? 'Mine' : 'Global')
    setProvider(null)
    setSelected(key)
    setOpen(true)
  }
  useEffect(() => {
    const open = (key) => showRef.current?.(String(key))
    window.__computeRentalsShow = open
    return () => {
      if (window.__computeRentalsShow === open) delete window.__computeRentalsShow
    }
  }, [])
  const mineRows = mineMachines(data?.machines)
  const rows = (mode === 'Session' ? sessionRows : mode === 'Mine' ? mineRows : data?.machines || []).filter(
    (row) => !provider || row.provider === provider,
  )
  const detail = rows.find((row) => row.key === selected)
  const vendors = [
    ...new Set([
      ...rows.map((row) => row.provider),
      ...(mode === 'Global' ? workloads.map((w) => w.provider).filter((p) => !provider || p === provider) : []),
    ]),
  ].sort((a, b) => providerLabel(a).localeCompare(providerLabel(b)))
  const viewSummary = provider
    ? data && (mode === 'Global' || sessionKnown)
      ? summarizeMachines(rows, complete)
      : undefined
    : mode === 'Session'
      ? sessionSummary
      : mode === 'Mine'
        ? data && summarizeMachines(mineRows, complete)
        : globalSummary
  const sessionItems =
    mode === 'Session' && sessionKnown
      ? (Array.isArray(data?.session_workloads) ? data.session_workloads : []).filter((w) =>
          (w.association?.parent_machine_keys || w.parent_machine_keys || []).some(
            (key) => sessionKeys.has(key) && rows.some((row) => row.key === key),
          ),
        )
      : []
  const listView = () => {
    if (!data) return null
    if (mode === 'Session' && !sessionKnown)
      return h(
        'div',
        { className: 'hermes-cr-empty' },
        h(
          'p',
          { role: 'status', style: { margin: 0 } },
          identity.session_id
            ? 'Session ownership unavailable; waiting for an unambiguous gateway identity.'
            : 'No saved session focused; use Global to browse machines.',
        ),
        h(Button, { variant: 'outline', size: 'sm', onClick: browseGlobal }, 'Browse global'),
      )
    if (!rows.length && mode === 'Session' && !sessionItems.length)
      return h(
        'div',
        { className: 'hermes-cr-empty' },
        h('strong', null, provider ? `No ${providerLabel(provider)} machines linked to this session` : 'No machines linked to this session'),
        h(
          'span',
          null,
          complete
            ? 'Machines rented with the rent-machine skill link here automatically.'
            : 'Coverage is incomplete; some providers could not be checked.',
        ),
        h(Button, { variant: 'outline', size: 'sm', onClick: browseGlobal }, 'Browse global'),
      )
    return [
      !rows.length &&
        !vendors.length &&
        h(
          'p',
          { key: 'none', className: 'hermes-cr-msg' },
          complete ? 'No machines in this view.' : 'No machines observed in this view; coverage is incomplete.',
        ),
      ...vendors.map((vendor) =>
        h(ProviderSection, {
          key: vendor,
          vendor,
          now,
          rows: rows.filter((row) => row.provider === vendor),
          statuses: providerRows.filter((row) => row.provider === vendor),
          workloads: mode === 'Global' ? workloads.filter((w) => w.provider === vendor) : [],
          children,
          machineKeys,
          machines: data?.machines || [],
          collapsed: Boolean(collapsed[vendor]),
          onToggle: () => setCollapsed({ ...collapsed, [vendor]: !collapsed[vendor] }),
          onSelect: setSelected,
        }),
      ),
      h(SessionWorkloads, { key: 'session-workloads', items: sessionItems, now }),
    ]
  }
  const browseGlobal = () => {
    setMode('Global')
    setProvider(null)
    setSelected(null)
  }
  return h(
    Popover,
    {
      open,
      onOpenChange: (value) => {
        setOpen(value)
        if (value) {
          setMode('Session')
          setProvider(null)
          setSelected(null)
        }
      },
    },
    h(
      'div',
      {
        style: { display: 'flex', alignItems: 'center', height: '100%', flexShrink: 0 },
        'aria-label': 'Compute rentals',
      },
      ...groups.map((vendor) =>
        h(
          'span',
          { key: vendor, style: { display: 'inline-flex', alignItems: 'center' } },
          h('button', { type: 'button', className: BAR, onClick: () => show(vendor) }, providerLabel(vendor)),
          ...sessionRows
            .filter((row) => row.provider === vendor)
            .map((row) => {
              const internal = isInternal(row)
              const name = `${row.display_name || row.provider_id}: ${row.status}${internal ? ' · internal prepaid' : ''}`
              return h(
                'button',
                {
                  type: 'button',
                  className: BAR,
                  key: row.key,
                  'data-machine-dot': row.key,
                  'data-billing': row.billing_class || 'unknown',
                  'aria-label': name,
                  title: name.replace(': ', ' · '),
                  onClick: () => show(vendor, row.key),
                },
                internal
                  ? h('span', {
                      'data-hollow-dot': '',
                      'aria-hidden': true,
                      style: {
                        display: 'inline-block',
                        width: 7,
                        height: 7,
                        borderRadius: '50%',
                        border: '1.5px solid currentColor',
                        boxSizing: 'border-box',
                        opacity: row.status === 'running' ? 1 : 0.6,
                      },
                    })
                  : h(StatusDot, { tone: row.status === 'running' ? 'good' : 'muted' }),
              )
            }),
        ),
      ),
      h(
        PopoverTrigger,
        { asChild: true },
        h(
          'button',
          {
            type: 'button',
            className: BAR,
            'aria-label': 'Compute rentals summary',
            title: `Session ${summaryLabel(sessionSummary, { exact: true })} | Global ${summaryLabel(globalSummary, { exact: true })}`,
            onClick: (event) => {
              event.preventDefault()
              show()
            },
          },
          h('span', { 'aria-hidden': true, 'data-bar-visible': '' }, `Session ${barLabel(sessionSummary)}  │  All ${barLabel(globalSummary)}`),
          h(
            'span',
            { className: 'hermes-cr-sr' },
            ` Session ${summaryLabel(sessionSummary)} | Global ${summaryLabel(globalSummary)}`,
          ),
        ),
      ),
    ),
    h(
      PopoverContent,
      {
        side: 'top',
        align: 'start',
        className: 'w-[480px]',
        'aria-label': 'Compute rentals inventory',
        style: { width: 'min(480px, calc(100vw - 24px))', padding: 0, overflow: 'hidden' },
      },
      h(
        'div',
        { className: 'hermes-cr' },
        h(
          'div',
          { className: 'hermes-cr-head' },
          h('span', { className: 'hermes-cr-title' }, 'Compute'),
          h(
            'div',
            { className: 'hermes-cr-seg', role: 'group', 'aria-label': 'Scope' },
            ...['Session', 'Mine', 'Global'].map((value) =>
              h(
                'button',
                {
                  key: value,
                  type: 'button',
                  'aria-pressed': mode === value,
                  onClick: () => {
                    setMode(value)
                    setSelected(null)
                  },
                },
                value,
              ),
            ),
          ),
          h(
            'button',
            {
              type: 'button',
              className: 'hermes-cr-icon',
              'aria-label': 'Refresh',
              title: query.isFetching ? 'Refreshing…' : 'Refresh inventory',
              disabled: !available || query.isFetching,
              onClick: () => {
                forceRefresh.current = true
                void query.refetch()
              },
            },
            h('span', { className: query.isFetching ? 'hermes-cr-spin' : undefined, 'aria-hidden': true }, '↻'),
          ),
          h(
            'button',
            {
              type: 'button',
              className: 'hermes-cr-icon',
              'aria-label': 'Close compute rentals',
              title: 'Close',
              onClick: () => setOpen(false),
            },
            h('span', { 'aria-hidden': true }, '×'),
          ),
        ),
        h(SummaryStrip, {
          summary: viewSummary,
          observedAt: data?.observed_at,
          now,
          provider,
          clearProvider: () => {
            setProvider(null)
            setSelected(null)
          },
        }),
        h(
          'div',
          { className: 'hermes-cr-body' },
          !available &&
            h(
              'p',
              { role: 'alert', className: 'hermes-cr-msg' },
              'Focused session gateway/profile is unavailable through this connection.',
            ),
          query.isError &&
            h(
              'p',
              { role: 'alert', className: 'hermes-cr-msg' },
              `Inventory unavailable${data ? ' · stale data' : ''}: ${String(query.error?.message || query.error)}`,
            ),
          !data && available && !query.isError && h('p', { className: 'hermes-cr-msg' }, 'Loading inventory…'),
          actionError && h('p', { role: 'alert', className: 'hermes-cr-msg' }, actionError),
          detail
            ? [
                h(
                  'button',
                  { key: 'back', type: 'button', className: 'hermes-cr-back', onClick: () => setSelected(null) },
                  '‹ All machines',
                ),
                h(
                  MachineDetail,
                  {
                    key: detail.key,
                    machine: detail,
                    ctx,
                    now,
                    onError: setActionError,
                    workloads: children.get(detail.key) || [],
                  }
                ),
              ]
            : listView(),
        ),
        h(Sources, {
          providers: providerRows,
          now,
          shown: new Set(detail ? [] : vendors),
          observedAt: data?.observed_at,
        }),
      ),
    ),
  )
}

function identityCurrent(identity) {
  const owner = host.state.focusedSessionOwner.get()
  return (
    identity.connection === host.state.connectionId.get() &&
    identity.ambientProfile === host.state.profile.get() &&
    identity.session_id === (host.state.focusedStoredSessionId.get() || '') &&
    identity.ownerConnection === (owner?.connectionId || '') &&
    identity.profile === (owner?.profile || host.state.profile.get())
  )
}

// ── Presentation helpers ────────────────────────────────────────────────────

// "12s ago" / "in 2d"; exact ISO belongs in a title attribute.
export function relativeTime(value, now = Date.now()) {
  const at = typeof value === 'string' && value.trim() ? Date.parse(value) : NaN
  if (!Number.isFinite(at)) return 'unknown'
  const diff = Math.trunc((now - at) / 1000)
  const abs = Math.abs(diff)
  if (abs < 5) return 'just now'
  const unit =
    abs < 60
      ? `${abs}s`
      : abs < 3600
        ? `${Math.floor(abs / 60)}m`
        : abs < 86_400
          ? `${Math.floor(abs / 3600)}h`
          : `${Math.floor(abs / 86_400)}d`
  return diff > 0 ? `${unit} ago` : `in ${unit}`
}

function Time({ value, now, prefix = '' }) {
  const valid = typeof value === 'string' && value.trim() && Number.isFinite(Date.parse(value))
  if (!valid) return h('span', null, value ? `${prefix}${String(value)}` : `${prefix}unknown`)
  return h('span', { title: value, 'data-time': value }, `${prefix}${relativeTime(value, now)}`)
}

function Dot({ tone = 'muted', hollow = false, title }) {
  return h('span', {
    className: 'hermes-cr-dot',
    'data-tone': tone,
    'data-hollow': hollow ? '' : undefined,
    'aria-hidden': title ? undefined : true,
    title,
  })
}

const PROVIDER_TONE = { ok: 'good', partial: 'warn', error: 'bad', unconfigured: 'muted' }
const providerTone = (status) => PROVIDER_TONE[status] || 'muted'
const STATUS_SHORT = { ok: 'ok', partial: 'partial', error: 'error', unconfigured: 'not configured' }
function worstStatus(statuses) {
  for (const status of ['error', 'partial', 'unconfigured', 'ok'])
    if (statuses.some((row) => row.status === status)) return status
  return statuses[0]?.status || 'unknown'
}

function machineTone(row) {
  const status = String(row.status || '')
  if (status === 'running') return 'good'
  if (['error', 'failed'].includes(status)) return 'bad'
  if (['starting', 'pending', 'queued', 'stopping', 'suspended', 'provisioning'].includes(status)) return 'warn'
  return 'muted'
}

export function money(currency, amount) {
  if (currency === 'USD') return amount.startsWith('<') ? `<$${amount.slice(1)}` : `$${amount}`
  return `${amount} ${currency || ''}`.trim()
}

function ratesText(rates) {
  return Object.entries(rates || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([currency, rate]) => `${money(currency, formatRate(rate))}/hr`)
    .join(' + ')
}

export function shortHardware(machine) {
  const first = typeof machine.hardware === 'string' ? machine.hardware.split(';')[0].trim() : ''
  const gres = /^gpu:([^:(]+):(\d+)/i.exec(first)
  if (gres) return `${gres[2]} × ${gres[1].replace(/^nvidia_/i, '').replace(/_/g, ' ')}`
  if (first && !/^unknown$/i.test(first)) return first
  return machine.resource_kind ? machine.resource_kind.replace(/_/g, ' ') : ''
}

function SummaryStrip({ summary, observedAt, now, provider, clearProvider }) {
  const internal = Number.isInteger(summary?.internal_count) ? summary.internal_count : 0
  const count = summary
    ? Number.isInteger(summary.rental_count)
      ? summary.rental_count
      : summary.count - internal
    : null
  const rates = summary ? ratesText(summary.rates) : ''
  const money_ = !summary
    ? 'rate unknown'
    : rates || (count === 0 && summary.complete ? '$0.00/hr' : 'rate unknown')
  return h(
    'div',
    { className: 'hermes-cr-summary', 'aria-label': 'Inventory summary' },
    h(
      'span',
      { className: 'hermes-cr-big', title: summaryLabel(summary, { exact: true }) },
      summary ? plural(count, 'rental') : '— rentals',
      h('span', { 'aria-hidden': true }, '·'),
      money_,
    ),
    internal > 0 && h('span', { className: 'hermes-cr-chip', title: 'Internal prepaid nodes; not in paid totals' }, `+${internal} internal`),
    summary?.unknown_rates > 0 &&
      h('span', { className: 'hermes-cr-chip', title: 'Machines without a known hourly rate; not in totals' }, `${summary.unknown_rates} unknown rate`),
    summary &&
      !summary.complete &&
      h('span', { className: 'hermes-cr-chip', 'data-tone': 'warn', title: 'Some providers are partial, stale or unconfigured; totals are a lower bound' }, 'partial'),
    provider &&
      h(
        'button',
        { type: 'button', className: 'hermes-cr-chip', 'aria-label': 'Clear provider filter', title: 'Show all providers', onClick: clearProvider },
        `${providerLabel(provider)} ×`,
      ),
    h(
      'span',
      {
        className: 'hermes-cr-updated',
        title: `Last updated (response): ${observedAt || 'Never'}`,
      },
      observedAt ? `updated ${relativeTime(observedAt, now)}` : 'not updated yet',
    ),
  )
}

function rowRate(row) {
  if (isInternal(row)) return { text: 'prepaid', kind: 'internal' }
  if (row.hourly_rate === null || row.hourly_rate === undefined) return { text: 'unknown', kind: 'unknown' }
  return { text: `${money(row.currency, formatRate(String(row.hourly_rate)))}/hr`, kind: 'paid' }
}

function ProviderSection({ vendor, now, rows, statuses, workloads, children, machineKeys, machines, collapsed, onToggle, onSelect }) {
  const status = statuses.length ? worstStatus(statuses) : 'unknown'
  const sub = summarizeMachines(rows, true)
  const subText = rows.length
    ? sub.internal_count === rows.length
      ? 'internal prepaid'
      : `${ratesText(sub.rates) || 'rate unknown'}${sub.unknown_rates && Object.keys(sub.rates).length ? ` + ${sub.unknown_rates} unknown` : ''}`
    : ''
  const countText = rows.length ? String(rows.length) : plural(workloads.length, 'workload')
  const [limit, setLimit] = useState(ROW_CAP)
  const issues = statuses.filter((row) => row.status !== 'ok')
  return h(
    'div',
    { className: 'hermes-cr-sec', 'data-provider-section': vendor },
    h(
      'h3',
      null,
      h(
        'button',
        {
          type: 'button',
          className: 'hermes-cr-sechead',
          'aria-expanded': !collapsed,
          title: `${providerLabel(vendor)} · ${STATUS_SHORT[status] || status}`,
          onClick: onToggle,
        },
        h('span', { className: 'hermes-cr-chev', 'aria-hidden': true }),
        h(Dot, { tone: providerTone(status) }),
        h('span', { className: 'hermes-cr-label', 'data-section-label': '' }, providerLabel(vendor)),
        h('span', { className: 'hermes-cr-count' }, countText),
        subText && h('span', { className: 'hermes-cr-sub', title: summaryLabel(sub, { exact: true }) }, subText),
      ),
    ),
    ...issues.map((row, index) =>
      h(
        'p',
        {
          key: `status-${index}`,
          className: 'hermes-cr-reason',
          'data-tone': providerTone(row.status),
          title: `${row.status} · partial / possibly stale${row.error ? ` · ${row.error}` : ''}`,
        },
        `${STATUS_SHORT[row.status] || row.status}${row.error ? ` — ${row.error}` : ' — results may be stale'}`,
      ),
    ),
    !collapsed &&
      rows.slice(0, limit).map((row) => {
        const rate = rowRate(row)
        const name = row.display_name || row.provider_id
        const hardware = shortHardware(row)
        const kids = children.get(row.key)
        return h(
          'button',
          {
            key: row.key,
            type: 'button',
            className: 'hermes-cr-row',
            'data-machine-row': row.key,
            'aria-label': name,
            title: [
              name,
              row.resource_kind,
              row.hardware,
              row.status,
              rateText(row, true) + (isInternal(row) ? '' : ` (${row.rate_basis || 'unknown'})`),
              row.stale === true ? 'stale — last-known provider data' : '',
            ]
              .filter(Boolean)
              .join(' · '),
            onClick: () => onSelect(row.key),
          },
          h(Dot, { tone: machineTone(row), hollow: isInternal(row) }),
          h(
            'span',
            { className: 'hermes-cr-name' },
            h('span', { className: 'hermes-cr-trunc' }, name),
            kids?.length > 0 && h('span', { className: 'hermes-cr-badge', title: childCountText(kids) }, childBadge(kids)),
            row.stale === true && h('span', { className: 'hermes-cr-badge', title: 'Stale — last-known provider data' }, 'stale'),
          ),
          h('span', { className: 'hermes-cr-trunc hermes-cr-hw' }, hardware),
          h('span', { className: 'hermes-cr-status' }, row.status || 'unknown'),
          h('span', { className: 'hermes-cr-rate', 'data-kind': rate.kind }, rate.text),
        )
      }),
    !collapsed &&
      rows.length > limit &&
      h(
        'div',
        { className: 'hermes-cr-more', style: { marginLeft: 22 } },
        `${rows.length - limit} more`,
        h('button', { type: 'button', className: 'hermes-cr-link', onClick: () => setLimit(limit + ROW_CAP) }, `Show ${Math.min(ROW_CAP, rows.length - limit)} more`),
      ),
    !collapsed && h(ProviderWorkloads, { items: workloads, machineKeys, machines, now }),
  )
}

function Sources({ providers, now, shown, observedAt }) {
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState({})
  const tally = {}
  for (const row of providers) tally[row.status] = (tally[row.status] || 0) + 1
  // Error/partial reasons stay visible when no section shows them; unconfigured stays inside Sources.
  const issues = providers.filter((row) => (row.status === 'error' || row.status === 'partial') && !shown.has(row.provider))
  return h(
    'div',
    { className: 'hermes-cr-foot', 'aria-label': 'Provider coverage' },
    ...issues.map((row, index) =>
      h(
        'p',
        {
          key: `issue-${row.provider}-${index}`,
          className: 'hermes-cr-issue',
          role: row.status === 'error' ? 'alert' : undefined,
          title: `${providerLabel(row.provider)}: ${row.status} · partial / possibly stale${row.error ? ` · ${row.error}` : ''}`,
        },
        h(Dot, { tone: providerTone(row.status) }),
        h('span', { className: 'hermes-cr-label' }, providerLabel(row.provider)),
        h(
          'span',
          { className: 'hermes-cr-trunc' },
          `${STATUS_SHORT[row.status]}${row.error ? ` — ${row.error}` : ' — results may be stale'}`,
        ),
      ),
    ),
    h(
      'button',
      {
        type: 'button',
        className: 'hermes-cr-srcbtn',
        'aria-expanded': open,
        'aria-label': 'Sources',
        title: observedAt ? `Last updated (response): ${observedAt}` : undefined,
        onClick: () => setOpen(!open),
      },
      h('span', { className: 'hermes-cr-chev', 'aria-hidden': true }),
      h('span', null, 'Sources'),
      ...['ok', 'partial', 'error', 'unconfigured']
        .filter((status) => tally[status])
        .map((status) =>
          h('span', { key: status, className: 'hermes-cr-tally' }, h(Dot, { tone: providerTone(status) }), `${tally[status]} ${STATUS_SHORT[status]}`),
        ),
    ),
    open &&
      h(
        'div',
        { className: 'hermes-cr-sources' },
        ...providers.map((row, index) => {
          const id = `${row.provider}:${index}`
          const more = Boolean(expanded[id])
          const coverage = typeof row.scope === 'object' && row.scope ? JSON.stringify(row.scope) : row.scope || 'unknown'
          const coverageText = String(coverage).replace(/,(?=\S)/g, ', ')
          const lastSuccess = typeof row.last_success_at === 'string' && row.last_success_at.trim() ? row.last_success_at : null
          const persistence =
            typeof row.persistence_error === 'string' && row.persistence_error.trim() ? row.persistence_error : null
          return h(
            'div',
            { key: id, 'data-source': row.provider, 'data-status': row.status },
            h(
              'button',
              {
                type: 'button',
                className: 'hermes-cr-src',
                'aria-expanded': more,
                title: `${providerLabel(row.provider)} · coverage: ${coverage}${row.error ? ` · ${row.error}` : ''}`,
                onClick: () => setExpanded({ ...expanded, [id]: !more }),
              },
              h('span', { className: 'hermes-cr-chev', 'aria-hidden': true }),
              h(Dot, { tone: providerTone(row.status) }),
              h('span', null, h('span', { className: 'hermes-cr-label' }, providerLabel(row.provider)), ' ', h('span', { className: 'hermes-cr-count' }, STATUS_SHORT[row.status] || row.status)),
              h('span', { className: 'hermes-cr-trunc', style: { color: 'var(--ui-text-tertiary)' } }, row.status === 'ok' ? '' : row.error || 'results may be stale'),
              h('span', { className: 'hermes-cr-when' }, h(Time, { value: row.observed_at, now, prefix: 'checked ' })),
            ),
            more &&
              h(
                'div',
                { className: 'hermes-cr-srcdetail' },
                h(
                  'dl',
                  { className: 'hermes-cr-dl' },
                  ...[
                    ['Coverage', coverageText],
                    ['Observed', h(Time, { value: row.observed_at, now })],
                    lastSuccess && ['Last success', h(Time, { value: lastSuccess, now })],
                    row.status !== 'ok' && ['Freshness', 'partial / possibly stale'],
                    row.error && ['Error', row.error],
                    persistence && ['Persistence', persistence],
                  ]
                    .filter(Boolean)
                    .flatMap(([label, value]) => [
                      h('dt', { key: label }, label),
                      h('dd', { key: `${label}-value` }, value),
                    ]),
                ),
              ),
          )
        }),
      ),
  )
}

const TIME_FIELDS = new Set(['Started', 'Expires', 'Observed'])

function MachineDetail({ machine, ctx, onError, workloads = [], now, children }) {
  const metadata = {
    ID: machine.provider_id,
    Provider: providerLabel(machine.provider),
    Account: machine.account,
    Status: machine.status,
    Kind: machine.resource_kind,
    ...(typeof machine.billing_class === 'string' && machine.billing_class ? { Billing: machine.billing_class } : {}),
    ...(typeof machine.state_detail === 'string' && machine.state_detail.trim()
      ? { 'Provider state': machine.state_detail }
      : {}),
    Hardware: machine.hardware,
    Region: machine.region,
    Started: machine.started_at,
    Expires: machine.expires_at,
    Rate: isInternal(machine)
      ? 'Internal prepaid'
      : machine.hourly_rate == null
        ? 'Unknown'
        : `${machine.currency} ${machine.hourly_rate}/h`,
    'Rate basis': machine.rate_basis || 'unknown',
    ...(typeof machine.rate_note === 'string' && machine.rate_note.trim()
      ? { 'Rate details': machine.rate_note }
      : {}),
    Owners:
      (machine.ownership || [])
        .map((owner) => `${owner.gateway_id} / ${owner.profile} / ${owner.session_id}`)
        .join('; ') || 'Unlinked',
    ...(typeof machine.ssh === 'string' && machine.ssh ? { SSH: h('code', null, machine.ssh) } : {}),
    Observed: machine.observed_at,
    ...(machine.stale === true ? { Freshness: 'Stale — last-known provider data' } : {}),
  }
  const osAction = async (method, value) => {
    try {
      onError('')
      if (!(await ctx.os?.[method]?.(value))) throw Error('Desktop action unavailable')
    } catch (error) {
      onError(String(error.message || error))
    }
  }
  const rate = rowRate(machine)
  return h(
    'div',
    { className: 'hermes-cr-detail', 'aria-label': 'Machine details' },
    h(
      'div',
      { className: 'hermes-cr-dhead' },
      h(Dot, { tone: machineTone(machine), hollow: isInternal(machine) }),
      h('b', null, machine.display_name || machine.provider_id),
      h('span', { className: 'hermes-cr-chip' }, machine.status || 'unknown'),
      h('span', { className: 'hermes-cr-sub', title: rateText(machine, true) }, rate.text),
    ),
    h(
      'dl',
      { className: 'hermes-cr-dl' },
      ...Object.entries(metadata).flatMap(([label, value]) => [
        h('dt', { key: label }, label),
        h(
          'dd',
          { key: `${label}-value` },
          TIME_FIELDS.has(label) && value ? h(Time, { value, now }) : value || 'Unknown',
        ),
      ]),
    ),
    h(
      'div',
      { className: 'hermes-cr-actions' },
      h(
        Button,
        {
          variant: 'outline',
          size: 'sm',
          disabled: !/^https?:\/\//i.test(machine.console_url || ''),
          onClick: () => osAction('openExternal', machine.console_url),
        },
        'Open console',
      ),
      h(
        Button,
        {
          variant: 'outline',
          size: 'sm',
          disabled: !machine.ssh,
          onClick: () => osAction('writeClipboard', machine.ssh),
        },
        'Copy SSH',
      ),
      children,
    ),
    h('p', { className: 'hermes-cr-note' }, 'Registry only — does not modify or stop the cloud machine.'),
    workloads.length > 0 && h(ChildWorkloads, { items: workloads, now }),
  )
}

const ROW_CAP = 50
const ACTIVE = new Set(['running', 'starting', 'queued', 'pending', 'suspended', 'stopping'])
const STOPPED = new Set(['stopped', 'completed', 'failed', 'cancelled', 'terminated', 'exited'])
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
const KIND_WORD = { allocation: 'allocation', queued_job: 'queued job', container: 'container' }

export function childWorkloads(workloads) {
  const index = new Map()
  for (const w of workloads || [])
    for (const key of new Set(Array.isArray(w.parent_machine_keys) ? w.parent_machine_keys : [])) {
      if (!index.has(key)) index.set(key, [])
      index.get(key).push(w)
    }
  return index
}

// Exact counts (tooltip); the compact badge is childBadge.
function childCountText(items) {
  if (!items?.length) return ''
  const allocations = items.filter((w) => w.resource_kind === 'allocation').length
  const containers = items.filter((w) => w.resource_kind === 'container')
  const parts = []
  if (allocations) parts.push(plural(allocations, 'allocation'))
  if (containers.length)
    parts.push(`${containers.filter((w) => ACTIVE.has(w.status)).length} active / ${plural(containers.length, 'container')}`)
  return parts.join(' · ')
}

export function childBadge(items) {
  const allocations = (items || []).filter((w) => w.resource_kind === 'allocation').length
  const containers = (items || []).filter((w) => w.resource_kind === 'container').length
  const other = (items || []).length - allocations - containers
  return [
    allocations && plural(allocations, 'job'),
    containers && plural(containers, 'container'),
    other && plural(other, 'workload'),
  ]
    .filter(Boolean)
    .join(' · ')
}

function WorkloadLine({ w, now }) {
  return h(
    'li',
    {
      'data-workload': w.key,
      title: typeof w.state_detail === 'string' ? w.state_detail : undefined,
    },
    h('span', { className: 'hermes-cr-trunc' }, w.display_name || w.provider_id || w.key),
    h(
      'span',
      null,
      `${KIND_WORD[w.resource_kind] || w.resource_kind || 'workload'} · ${w.status || 'unknown'}${w.stale === true ? ' · stale' : ''}`,
    ),
    h(
      'span',
      { className: 'hermes-cr-when' },
      w.expires_at ? h(Time, { value: w.expires_at, now, prefix: w.resource_kind === 'allocation' ? 'deadline ' : 'expires ' }) : '',
    ),
  )
}

// Collapses stopped rows behind a toggle and caps rendered rows for large inventories.
function WorkloadRows({ items, label, now }) {
  const [showStopped, setShowStopped] = useState(false)
  const [limit, setLimit] = useState(ROW_CAP)
  const stopped = items.filter((w) => STOPPED.has(w.status))
  const visible = items.filter((w) => showStopped || !STOPPED.has(w.status))
  const shown = visible.slice(0, limit)
  const hidden = visible.length - shown.length
  return h(
    'div',
    { 'aria-label': label },
    h('ul', { className: 'hermes-cr-wl' }, ...shown.map((w) => h(WorkloadLine, { key: w.key, w, now }))),
    (hidden > 0 || stopped.length > 0) &&
      h(
        'div',
        { className: 'hermes-cr-more' },
        hidden > 0 && `${hidden} more `,
        hidden > 0 &&
          h('button', { type: 'button', className: 'hermes-cr-link', onClick: () => setLimit(limit + ROW_CAP) }, `Show ${Math.min(ROW_CAP, hidden)} more`),
        stopped.length > 0 &&
          h(
            'button',
            { type: 'button', className: 'hermes-cr-link', onClick: () => setShowStopped(!showStopped) },
            showStopped ? 'Hide stopped' : `Show ${stopped.length} stopped`,
          ),
      ),
  )
}

function containerLine(items) {
  const containers = items.filter((w) => w.resource_kind === 'container')
  if (!containers.length) return null
  const active = containers.filter((w) => ACTIVE.has(w.status)).length
  const stopped = containers.filter((w) => STOPPED.has(w.status)).length
  const unknown = containers.length - active - stopped
  return h('p', null, `Containers: ${active} active · ${stopped} stopped${unknown ? ` · ${unknown} unknown status` : ''}`)
}

function ChildWorkloads({ items, now }) {
  return h(
    'div',
    { 'aria-label': 'Child workloads', className: 'hermes-cr-wls', style: { margin: '10px 0 0' } },
    h('strong', null, `Workloads on this machine (${items.length})`),
    containerLine(items),
    h(WorkloadRows, { items, label: 'Child workload rows', now }),
  )
}

function SessionWorkloads({ items, now }) {
  if (!items.length) return null
  const labels = [...new Set(items.map((w) => w.association?.label).filter((x) => typeof x === 'string' && x.trim()))]
  return h(
    'div',
    { 'aria-label': 'Session workloads', className: 'hermes-cr-wls', style: { marginLeft: 8, marginTop: 8 } },
    h('strong', null, `Session workloads (${items.length}) · non-exclusive`),
    ...labels.map((text) => h('p', { key: text, style: { color: 'var(--ui-text-tertiary)' } }, text)),
    containerLine(items),
    h(WorkloadRows, { items, label: 'Session workload rows', now }),
  )
}

function ProviderWorkloads({ items, machineKeys, machines, now }) {
  if (!items.length) return null
  const queued = items.filter((w) => w.resource_kind === 'queued_job')
  const attached = items.filter(
    (w) => w.resource_kind !== 'queued_job' && (w.parent_machine_keys || []).some((key) => machineKeys.has(key)),
  )
  const attachedSet = new Set(attached)
  const loose = items.filter((w) => w.resource_kind !== 'queued_job' && !attachedSet.has(w))
  const parentProviders = [
    ...new Set(
      machines
        .filter((m) => attached.some((w) => (w.parent_machine_keys || []).includes(m.key)))
        .map((m) => providerLabel(m.provider)),
    ),
  ].sort()
  const counts = Object.keys(KIND_WORD)
    .map((kind) => {
      const rows = items.filter((w) => w.resource_kind === kind)
      if (!rows.length) return null
      const active = rows.filter((w) => ACTIVE.has(w.status)).length
      const stopped = rows.filter((w) => STOPPED.has(w.status)).length
      return `${plural(rows.length, KIND_WORD[kind])} · ${active} active · ${stopped} stopped`
    })
    .filter(Boolean)
  return h(
    'div',
    { 'aria-label': 'Provider workloads', className: 'hermes-cr-wls' },
    h('p', null, counts.join('; ')),
    attached.length > 0 &&
      h('p', null, `${attached.length} attached under ${parentProviders.join(', ')} hosts (see machine details)`),
    queued.length > 0 &&
      h(
        'div',
        { 'aria-label': 'Queued jobs' },
        h('strong', null, `Queued jobs (${queued.length})`),
        h(WorkloadRows, { items: queued, label: 'Queued job rows', now }),
      ),
    loose.length > 0 &&
      h(
        'div',
        { 'aria-label': 'Unattached workloads' },
        h('strong', null, `Not attached to a listed machine (${loose.length})`),
        h(WorkloadRows, { items: loose, label: 'Unattached workload rows', now }),
      ),
  )
}
// Built-in providers; extra (backend-loaded) providers send their own `label`
// in the inventory's provider rows, which registerProviderLabels records.
const PROVIDER_LABELS = {
  vast: 'Vast',
  sfcompute: 'SF Compute',
  ec2: 'EC2',
  runpod: 'RunPod',
  modal: 'Modal',
  primeintellect: 'Prime Intellect',
  slurm: 'Slurm',
}
const backendLabels = new Map()

export function registerProviderLabels(rows) {
  for (const row of Array.isArray(rows) ? rows : []) {
    if (typeof row?.provider === 'string' && typeof row.label === 'string' && row.label.trim()) {
      backendLabels.set(row.provider, row.label.trim())
    }
  }
}

export function providerLabel(provider) {
  if (backendLabels.has(provider)) return backendLabels.get(provider)
  return Object.hasOwn(PROVIDER_LABELS, provider) ? PROVIDER_LABELS[provider] : String(provider ?? '')
}

export function isInternal(machine) {
  return machine?.billing_class === 'internal_prepaid'
}

// Half-up rounding of a non-negative decimal string to cents, without float error.
export function formatRate(rate) {
  if (typeof rate !== 'string' || !/^\d+(\.\d+)?$/.test(rate)) return String(rate)
  const [whole, fraction = ''] = rate.split('.')
  let cents = BigInt(whole + fraction.slice(0, 2).padEnd(2, '0'))
  if ((fraction[2] || '0') >= '5') cents++
  if (cents === 0n && /[1-9]/.test(whole + fraction)) return '<0.01'
  const digits = cents.toString().padStart(3, '0')
  return `${digits.slice(0, -2)}.${digits.slice(-2)}`
}

function rateText(machine, exact = false) {
  if (isInternal(machine)) return 'internal prepaid'
  if (machine.hourly_rate === null || machine.hourly_rate === undefined) return 'rate unknown'
  return `${machine.currency} ${exact ? machine.hourly_rate : formatRate(machine.hourly_rate)}/h`
}

export default {
  id: ID,
  name: 'Compute Rentals',
  defaultEnabled: false,
  register(ctx) {
    if (typeof document !== 'undefined' && document.head) {
      const style = document.createElement('style')
      style.dataset.hermesPlugin = ID
      style.textContent = CSS
      document.head.append(style)
      ctx.onDispose?.(() => style.remove())
    }
    // Anchor-aware hosts place this after gateway health. Older hosts ignore
    // the metadata and retain the earliest left plugin slot via order.
    ctx.register({
      id: 'inventory',
      area: STATUSBAR_AREAS.left,
      order: -1000,
      data: { after: 'gateway-health' },
      render: () => h(ComputeRentals, { ctx }),
    })
  },
}

export function summarizeMachines(machines, complete) {
  const totals = new Map()
  let unknown_rates = 0
  let internal_count = 0
  const unique = [...new Map(machines.map((machine) => [machine.key, machine])).values()]
  for (const machine of unique) {
    if (isInternal(machine)) {
      internal_count++
      continue
    }
    const rate = machine.hourly_rate
    if (typeof rate !== 'string' || !/^\d+(\.\d+)?$/.test(rate) || !machine.currency) {
      unknown_rates++
      continue
    }
    const [whole, fraction = ''] = rate.split('.')
    const previous = totals.get(machine.currency) || { amount: 0n, scale: 2 }
    const scale = Math.max(previous.scale, fraction.length)
    const amount =
      previous.amount * 10n ** BigInt(scale - previous.scale) +
      BigInt(whole + fraction) * 10n ** BigInt(scale - fraction.length)
    totals.set(machine.currency, { amount, scale })
  }
  const rates = {}
  for (const [currency, { amount, scale }] of totals) {
    const digits = amount.toString().padStart(scale + 1, '0')
    rates[currency] = `${digits.slice(0, -scale)}.${digits.slice(-scale)}`
  }
  return {
    count: unique.length,
    rental_count: unique.length - internal_count,
    internal_count,
    rates,
    unknown_rates,
    complete: Boolean(complete),
  }
}

// Paid count excludes internal prepaid nodes; display rounds to cents unless exact.
/** Compact status-bar form: "40 · $21.71/h+" — "+" marks partial coverage or
 *  unknown rates (details live in the tooltip/popover). Internal prepaid nodes
 *  are excluded from the count, as in the popover. */
export function barLabel(summary) {
  if (!summary) return '— · ?'
  const internal = Number.isInteger(summary.internal_count) ? summary.internal_count : 0
  const count = Number.isInteger(summary.rental_count) ? summary.rental_count : summary.count - internal
  const rates = Object.entries(summary.rates || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([currency, rate]) => (currency === 'USD' ? `$${formatRate(rate)}/h` : `${formatRate(rate)} ${currency}/h`))
    .join(' + ')
  const caveat = !summary.complete || summary.unknown_rates ? '+' : ''
  return `${count} · ${rates || (count === 0 && summary.complete ? '$0/h' : '?')}${rates ? caveat : ''}`
}

export function summaryLabel(summary, { exact = false } = {}) {
  if (!summary) return '— · rate unknown'
  const internal = Number.isInteger(summary.internal_count) ? summary.internal_count : 0
  const count = Number.isInteger(summary.rental_count) ? summary.rental_count : summary.count - internal
  const rates = Object.entries(summary.rates || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([currency, rate]) => `${currency} ${exact ? rate : formatRate(rate)}/h`)
    .join(' + ')
  return `${count}${summary.complete ? '' : ' observed'} · ${rates || (count === 0 && summary.complete ? '0/h' : 'rate unknown')}${summary.unknown_rates ? ` + ${summary.unknown_rates} unknown` : ''}${internal ? ` + ${internal} internal` : ''}${summary.complete ? '' : ' · partial'}`
}

// "Mine": machines linked to any of this gateway's sessions (the rent-machine
// skill attaches every rental; evidence auto-link covers older ones).
export function mineMachines(machines) {
  return (machines || []).filter((machine) => Array.isArray(machine.ownership) && machine.ownership.length > 0)
}

export function sessionMachines(machines, scope) {
  if (!scope?.gateway_id || !scope?.profile || !scope?.session_id) return []
  return [
    ...new Map(
      (machines || [])
        .filter((machine) =>
          (machine.ownership || []).some(
            (owner) =>
              owner.gateway_id === scope.gateway_id &&
              owner.profile === scope.profile &&
              owner.session_id === scope.session_id,
          ),
        )
        .map((machine) => [machine.key, machine]),
    ).values(),
  ]
}
