import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'
import * as sdk from '@hermes/plugin-sdk'

/** Resolve once per explicit open. Never fall back to the active backend for a named owner. */
export function processRoute(target, bridge, gateways) {
  if (!target?.id || !target.session || !target.gateway || !target.profile) throw Error('Process owner is unavailable.')
  const connectionId = bridge?.connectionForHubGateway?.(target.gateway)
  const gateway = gateways?.find(g => g?.id === connectionId)
  if (!connectionId || !gateway) throw Error('The gateway that holds this process is not connected.')
  return Object.freeze({ connectionId, mode: gateway.kind === 'local' ? 'local' : 'remote',
    profile: target.profile, targetProfile: target.profile })
}

/** Existing owner-scoped RPCs only; no shell, kill, log acknowledgement or ambient request. */
export async function readProcessView(target, route, request) {
  const attached = await request(route, 'session.resume', {session_id: target.session, lazy: true, omit_messages: true}, 30000, {spawnPriority: 'foreground'})
  if (attached?.stored_session_id !== target.session || !attached?.session_id) throw Error('The owning session changed. Refresh the panel before opening this process.')
  const result = await request(route, 'process.list', {session_id: attached.session_id}, 10000)
  if (!Array.isArray(result?.processes)) throw Error('Process status is unavailable.')
  const process = result.processes.find(p => p?.session_id === target.id)
  if (!process) throw Error('This process is no longer in the owning session’s live inventory. Its output may have expired.')
  return {id: target.id, command: String(process.command || target.id), status: String(process.status || 'unknown'),
    output: typeof process.output_tail === 'string' ? process.output_tail : '', exit_code: process.exit_code}
}

/** Read-only, explicit-refresh process viewer. Closing/switching owner cancels late presentation. */
export function ProcessView({target, route, onClose}) {
  const [value, setValue] = useState(null), [error, setError] = useState(null), [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setValue(null); setError(null)
    void readProcessView(target, route, (...args) => sdk.host.requestProfile(...args))
      .then(v => {if (active) setValue(v)}, e => {if (active) setError(String(e?.message || e))})
    return () => {active = false}
  }, [target, route, retry])
  const {Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription} = sdk
  if (!Dialog || !DialogContent || !DialogTitle) return null
  return jsx(Dialog, {open: true, onOpenChange: open => {if (!open) onClose()}, children:
    jsxs(DialogContent, {'data-process-view': target.id, children: [
      jsx(DialogHeader || 'div', {children: jsx(DialogTitle, {children: value?.command || target.title || target.id})}),
      jsx(DialogDescription || 'p', {children: `${target.gateway} / ${target.profile} · ${target.id}`}),
      target.detail && jsx('p', {'data-process-snapshot': '', children: `Panel observation at open: ${target.detail}`}),
      jsx('div', {role: 'status', children: error || (value ? value.status + (value.exit_code == null ? '' : ` · exit ${value.exit_code}`) : 'Loading process…')}),
      value && jsx('pre', {'data-process-output': '', style: {whiteSpace: 'pre-wrap', overflow: 'auto', maxHeight: '50vh'}, children: value.output || 'No output yet.'}),
      jsx('button', {type: 'button', onClick: () => setRetry(n => n + 1), children: 'Refresh'}),
      jsx('button', {type: 'button', onClick: onClose, children: 'Close'})
    ]})})
}
