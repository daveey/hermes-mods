/**
 * Session Transfer — copy or move Hermes sessions between gateways
 * (This device <-> a Nous-hosted / remote gateway, or between any two
 * registered connections).
 *
 * How it works: every gateway already serves `GET /api/sessions/<id>/export`
 * and `POST /api/sessions/import`. The desktop's own REST bridge
 * (`window.hermesDesktop.api`) can address ANY registered connection by
 * `connectionId`, with the auth the app already holds — so no extra login and
 * no backend code is needed.
 *
 * Transfer = export the whole compression chain from the source -> import on
 * the target -> re-export from the target and compare message counts per
 * segment -> (move only) delete on the source, only after verification passed.
 *
 * Not transferred: the session's working folder / files, attachments, and live
 * runtime state (the import resets routing + activity, by design).
 */

import {
  Button,
  Codicon,
  cn,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  host,
  PALETTE_AREA,
  queryClient,
  ROUTES_AREA,
  SESSION_ROW_AREAS,
  SIDEBAR_NAV_AREA,
  useQuery
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'session-transfer'
const ROUTE = '/session-transfer'
const LOCAL = 'local'
const MAX_SESSION_BYTES = 5 * 1024 * 1024 // server-side import cap per session
const MAX_TOTAL_BYTES = 24 * 1024 * 1024 // server-side cap per import call is 25 MB

// ── Core (pure; `api(connectionId, path, {method, body})` is injected) ──────

const enc = encodeURIComponent

export function desktopApi(connectionId, path, opts = {}) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null

  if (!bridge) {
    throw new Error('Hermes desktop bridge unavailable')
  }

  return bridge({ path, method: opts.method, body: opts.body, connectionId: connectionId || LOCAL, timeoutMs: 120000 })
}

function isNotFound(error) {
  return /^404\b|not found/i.test(String(error?.message ?? error))
}

async function tryGet(api, conn, path) {
  try {
    return await api(conn, path)
  } catch (error) {
    if (isNotFound(error)) {
      return null
    }

    throw error
  }
}

/** Export a session's full compression chain (oldest -> newest) from `conn`. */
export async function exportChain(api, conn, sessionId) {
  const first = await api(conn, `/api/sessions/${enc(sessionId)}/export`)
  const chain = [first]

  // Walk UP while the parent ended by compression (this row continues it).
  let cur = first
  const seen = new Set([first.id])

  while (cur.parent_session_id && !seen.has(cur.parent_session_id)) {
    const parent = await tryGet(api, conn, `/api/sessions/${enc(cur.parent_session_id)}/export`)

    if (!parent || parent.end_reason !== 'compression') {
      break
    }

    chain.unshift(parent)
    seen.add(parent.id)
    cur = parent
  }

  // Walk DOWN the newest-descendant path, keeping only compression continuations.
  const ld = await tryGet(api, conn, `/api/sessions/${enc(first.id)}/latest-descendant`)
  const path = Array.isArray(ld?.path) ? ld.path : []
  const startAt = path.indexOf(first.id)

  for (const nextId of startAt >= 0 ? path.slice(startAt + 1) : []) {
    const tail = chain[chain.length - 1]

    if (tail.end_reason !== 'compression' || seen.has(nextId)) {
      break
    }

    const next = await tryGet(api, conn, `/api/sessions/${enc(nextId)}/export`)

    if (!next || next.parent_session_id !== tail.id) {
      break
    }

    chain.push(next)
    seen.add(next.id)
  }

  return chain
}

function sizeOf(obj) {
  return new TextEncoder().encode(JSON.stringify(obj)).length
}

/**
 * Copy (or move) one session from `from` to `to`.
 * Returns { ids, imported, skipped, verified, deleted, messages }.
 * Throws with a readable message on any failure; never deletes unless verified.
 */
export async function transferSession(api, { from, to, sessionId, move = false, onStep = () => {} }) {
  if ((from || LOCAL) === (to || LOCAL)) {
    throw new Error('Source and target are the same gateway')
  }

  onStep('Exporting…')
  const chain = await exportChain(api, from, sessionId)
  const ids = chain.map(s => s.id)

  for (const seg of chain) {
    if (sizeOf(seg) > MAX_SESSION_BYTES) {
      throw new Error(`Session segment ${seg.id} is larger than the 5 MB import limit`)
    }
  }

  if (sizeOf(chain) > MAX_TOTAL_BYTES) {
    throw new Error('Session is larger than the 25 MB import limit')
  }

  // Refuse to overwrite: import skips existing ids, which would make a "move"
  // look verified against a DIFFERENT copy already on the target.
  const existing = []

  for (const id of ids) {
    if (await tryGet(api, to, `/api/sessions/${enc(id)}`)) {
      existing.push(id)
    }
  }

  if (existing.length === ids.length) {
    throw new Error('This session already exists on the target gateway')
  }

  if (existing.length) {
    throw new Error(`Part of this session already exists on the target (${existing.join(', ')}); delete it there first`)
  }

  onStep('Importing…')
  let result
  let renamed = false

  try {
    result = await api(to, '/api/sessions/import', { method: 'POST', body: { sessions: chain } })
  } catch (error) {
    // Titles are UNIQUE per gateway; a same-titled session on the target makes
    // the import fail (server 500, no detail). The import is one transaction,
    // so nothing landed — retry once with disambiguated titles.
    if (!/^5\d\d\b/.test(String(error?.message ?? error)) || !chain.some(s => s.title)) {
      throw error
    }

    renamed = true
    const suffix = ` (${String(sessionId).slice(-6)})`
    const retitled = chain.map(s => (s.title ? { ...s, title: `${s.title}${suffix}` } : s))
    result = await api(to, '/api/sessions/import', { method: 'POST', body: { sessions: retitled } })
  }

  if (!result?.ok) {
    throw new Error(`Import failed: ${JSON.stringify(result?.errors ?? result).slice(0, 300)}`)
  }

  onStep('Verifying…')
  let messages = 0

  for (const seg of chain) {
    const copy = await tryGet(api, to, `/api/sessions/${enc(seg.id)}/export`)
    const want = (seg.messages ?? []).length
    const got = (copy?.messages ?? []).length

    if (!copy || got !== want) {
      throw new Error(`Verification failed for ${seg.id}: expected ${want} messages, target has ${copy ? got : 'none'}`)
    }

    messages += got
  }

  let deleted = false

  if (move) {
    onStep('Removing original…')
    // Chain-aware on the server: deletes every compression segment.
    await api(from, `/api/sessions/${enc(chain[chain.length - 1].id)}`, { method: 'DELETE' })

    for (const id of ids) {
      if (await tryGet(api, from, `/api/sessions/${enc(id)}`)) {
        throw new Error(`Copied and verified, but the original (${id}) could not be removed`)
      }
    }

    deleted = true
  }

  return { ids, imported: result.imported, skipped: result.skipped, verified: true, deleted, messages, renamed }
}

// ── Connections ─────────────────────────────────────────────────────────────

export async function listConnections() {
  let rows = []

  try {
    rows = await host.connections()
  } catch {
    rows = []
  }

  const out = rows
    .filter(c => c && c.id)
    .map(c => ({ id: c.id, label: c.label || c.name || c.id, kind: c.kind || '' }))

  if (!out.some(c => c.id === LOCAL)) {
    out.unshift({ id: LOCAL, label: 'This device', kind: 'local' })
  }

  return out
}

function activeConnection() {
  try {
    return host.activeConnectionId() || LOCAL
  } catch {
    return LOCAL
  }
}

function useConnections() {
  return useQuery({ queryKey: [ID, 'connections'], queryFn: listConnections, staleTime: 30000 })
}

function refreshSessionLists() {
  void queryClient.invalidateQueries({ queryKey: ['sessions'] })
  void queryClient.invalidateQueries({ queryKey: [ID, 'list'] })
}

async function runTransfer({ from, to, toLabel, sessionId, title, move }) {
  const verb = move ? 'Move' : 'Copy'
  const name = title ? `"${String(title).slice(0, 40)}"` : sessionId

  try {
    const res = await transferSession(desktopApi, { from, to, sessionId, move })
    host.notify({
      kind: 'success',
      message: `${move ? 'Moved' : 'Copied'} ${name} to ${toLabel} (${res.messages} messages verified${res.ids.length > 1 ? `, ${res.ids.length} segments` : ''})`
    })
    refreshSessionLists()

    return res
  } catch (error) {
    host.notify({ kind: 'error', message: `${verb} to ${toLabel} failed: ${error?.message ?? error}` })
    throw error
  }
}

// ── Sidebar row action ──────────────────────────────────────────────────────

function RowTransferMenu({ sessionId }) {
  const connections = useConnections()
  const [busy, setBusy] = useState(false)
  const from = activeConnection()
  const targets = (connections.data ?? []).filter(c => c.id !== from)

  if (!sessionId || !targets.length) {
    return null
  }

  const go = (target, move) => {
    if (busy) {
      return
    }

    setBusy(true)
    runTransfer({ from, to: target.id, toLabel: target.label, sessionId, move })
      .catch(() => {})
      .finally(() => setBusy(false))
  }

  return jsxs(DropdownMenu, {
    children: [
      jsx(DropdownMenuTrigger, {
        asChild: true,
        children: jsx('button', {
          'aria-label': 'Copy or move to another gateway',
          className: cn(
            'inline-flex size-5 items-center justify-center rounded-[3px] text-(--ui-text-tertiary)',
            'hover:bg-(--chrome-action-hover) hover:text-foreground',
            busy ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100'
          ),
          onClick: e => e.stopPropagation(),
          onPointerDown: e => e.stopPropagation(),
          title: 'Copy or move to another gateway',
          type: 'button',
          children: jsx(Codicon, { name: busy ? 'loading' : 'arrow-swap', spinning: busy, size: '12px' })
        })
      }),
      jsx(DropdownMenuContent, {
        align: 'end',
        onClick: e => e.stopPropagation(),
        children: targets.flatMap((t, i) => [
          i > 0 ? jsx(DropdownMenuSeparator, {}, `sep-${t.id}`) : null,
          jsx(DropdownMenuItem, { onSelect: () => go(t, false), children: `Copy to ${t.label}` }, `copy-${t.id}`),
          jsx(DropdownMenuItem, { onSelect: () => go(t, true), children: `Move to ${t.label}` }, `move-${t.id}`)
        ])
      })
    ]
  })
}

// ── Page: browse any gateway, copy/move in either direction ─────────────────

function ConnSelect({ value, onChange, options, label }) {
  return jsxs('label', {
    className: 'flex items-center gap-2 text-xs text-(--ui-text-secondary)',
    children: [
      label,
      jsx('select', {
        className: 'rounded-[3px] border border-(--ui-stroke-secondary) bg-transparent px-2 py-1 text-xs text-foreground',
        onChange: e => onChange(e.target.value),
        value,
        children: options.map(o => jsx('option', { value: o.id, children: o.label }, o.id))
      })
    ]
  })
}

function SessionRow({ row, onCopy, onMove, state }) {
  const when = row.last_active || row.started_at
  const date = when ? new Date(when * 1000).toLocaleString() : ''

  return jsxs('div', {
    className: 'flex items-center gap-3 border-b border-(--ui-stroke-secondary) px-1 py-2 text-sm',
    children: [
      jsxs('div', {
        className: 'min-w-0 flex-1',
        children: [
          jsx('div', { className: 'truncate text-foreground', children: row.title || row.preview || row.id }),
          jsx('div', {
            className: 'truncate text-xs text-(--ui-text-tertiary)',
            children: `${date} · ${row.message_count ?? 0} messages · ${row.source || ''}`
          })
        ]
      }),
      state
        ? jsx('span', {
            className: cn('text-xs', state.error ? 'text-destructive' : 'text-(--ui-text-secondary)'),
            title: state.error || '',
            children: state.error ? 'Failed' : state.text
          })
        : null,
      jsx(Button, { disabled: Boolean(state?.running), onClick: onCopy, size: 'xs', variant: 'outline', children: 'Copy' }),
      jsx(Button, { disabled: Boolean(state?.running), onClick: onMove, size: 'xs', variant: 'outline', children: 'Move' })
    ]
  })
}

function TransferPage() {
  const connections = useConnections()
  const conns = connections.data ?? []
  const [from, setFrom] = useState(activeConnection())
  const fallbackTo = conns.find(c => c.id !== from)?.id ?? ''
  const [toPick, setTo] = useState('')
  const to = toPick && toPick !== from ? toPick : fallbackTo
  const [states, setStates] = useState({})

  const list = useQuery({
    enabled: Boolean(from),
    queryKey: [ID, 'list', from],
    queryFn: () => desktopApi(from, '/api/sessions?limit=50&order=recent&exclude_sources=cron'),
    staleTime: 10000
  })

  const toLabel = conns.find(c => c.id === to)?.label ?? to
  const rows = list.data?.sessions ?? []

  const go = (row, move) => {
    if (!to) {
      return
    }

    setStates(s => ({ ...s, [row.id]: { running: true, text: move ? 'Moving…' : 'Copying…' } }))
    transferSession(desktopApi, {
      from,
      to,
      sessionId: row.id,
      move,
      onStep: text => setStates(s => ({ ...s, [row.id]: { running: true, text } }))
    })
      .then(res => {
        setStates(s => ({ ...s, [row.id]: { text: `${move ? 'Moved' : 'Copied'} ✓ (${res.messages})` } }))
        host.notify({ kind: 'success', message: `${move ? 'Moved' : 'Copied'} to ${toLabel}` })
        refreshSessionLists()
      })
      .catch(error => {
        const message = String(error?.message ?? error)
        setStates(s => ({ ...s, [row.id]: { error: message } }))
        host.notify({ kind: 'error', message: `Transfer failed: ${message}` })
      })
  }

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-auto p-6',
    children: [
      jsx('div', { className: 'text-base font-medium text-foreground', children: 'Session Transfer' }),
      jsx('div', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children:
          'Copy or move conversations between gateways. Only the conversation moves — working folders and files stay where they are. "Move" removes the original only after the copy is verified.'
      }),
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-4',
        children: [
          jsx(ConnSelect, { label: 'From', onChange: setFrom, options: conns, value: from }),
          jsx(Codicon, { name: 'arrow-right', className: 'text-(--ui-text-tertiary)' }),
          jsx(ConnSelect, { label: 'To', onChange: setTo, options: conns.filter(c => c.id !== from), value: to }),
          jsx(Button, { onClick: () => list.refetch(), size: 'xs', variant: 'ghost', children: 'Refresh' })
        ]
      }),
      list.isLoading
        ? jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'Loading sessions…' })
        : list.error
          ? jsx('div', { className: 'text-xs text-destructive', children: String(list.error?.message ?? list.error) })
          : rows.length
            ? jsx('div', {
                children: rows.map(row =>
                  jsx(
                    SessionRow,
                    { onCopy: () => go(row, false), onMove: () => go(row, true), row, state: states[row.id] },
                    row.id
                  )
                )
              })
            : jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'No sessions on this gateway.' })
    ]
  })
}

// ── Registration ────────────────────────────────────────────────────────────

export default {
  id: ID,
  name: 'Session Transfer',
  register(ctx) {
    ctx.register({
      id: 'row-action',
      area: SESSION_ROW_AREAS.trailing,
      data: { render: ({ sessionId }) => jsx(RowTransferMenu, { sessionId }) }
    })

    ctx.register({
      id: 'page',
      area: ROUTES_AREA,
      data: { path: ROUTE },
      render: () => jsx(TransferPage, {})
    })

    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV_AREA,
      data: { path: ROUTE, label: 'Session Transfer', codicon: 'arrow-swap' }
    })

    ctx.register({
      id: 'open-page',
      area: PALETTE_AREA,
      data: {
        id: 'open-page',
        label: 'Session Transfer: copy/move sessions between gateways',
        keywords: ['move', 'copy', 'gateway', 'nous', 'cloud', 'transfer', 'session'],
        run: () => host.navigate(ROUTE)
      }
    })

    ctx.register({
      id: 'move-current',
      area: PALETTE_AREA,
      data: {
        id: 'move-current',
        label: 'Session Transfer: move current session to another gateway…',
        keywords: ['move', 'gateway', 'nous', 'transfer'],
        run: async () => {
          const sessionId = host.state.focusedStoredSessionId.get()

          if (!sessionId) {
            host.notify({ kind: 'info', message: 'Open a session first' })

            return
          }

          if (host.state.busy.get()) {
            host.notify({ kind: 'error', message: 'Wait for the current reply to finish before moving this session' })

            return
          }

          const from = activeConnection()
          const target = (await listConnections()).find(c => c.id !== from)

          if (!target) {
            host.notify({ kind: 'info', message: 'No other gateway registered' })

            return
          }

          await runTransfer({ from, to: target.id, toLabel: target.label, sessionId, move: true }).catch(() => {})
        }
      }
    })
  }
}
