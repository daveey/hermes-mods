export const calls = []
const sig = v => {
  const subs = new Set()
  return { v, get() { return this.v }, set(x) { this.v = x; subs.forEach(f => f(x)) }, subscribe(f) { subs.add(f); f(this.v); return () => subs.delete(f) } }
}
export const state = {
  focusedStoredSessionId: sig('S1'), focusedSessionId: sig('R1'), focusedSessionProfile: sig('default'),
  busyBySession: sig({ R1: false, R2: false })
}
export const host = {
  state,
  notify: m => calls.push(['notify', m.message]),
  navigate: p => calls.push(['navigate', p]),
  openSession: id => calls.push(['openSession', id]),
  request: async (m, p) => {
    calls.push(['request', m, p.title || p.session_id])
    if (m === 'session.create') return { session_id: 'R9', stored_session_id: 'S9' }
    if (m === 'prompt.submit') return { status: 'streaming' }
    return {}
  }
}
export const useValue = s => s.get()
export const COMPOSER_AREAS = { top: 'top' }
export const PALETTE_AREA = 'palette'
export const KEYBINDS_AREA = 'keybinds'
