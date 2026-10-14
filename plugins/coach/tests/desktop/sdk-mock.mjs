// Minimal @hermes/plugin-sdk stand-in for the coach desktop flow test.
export const calls = []
const sig = v => {
  const subs = new Set()
  return { v, get() { return this.v }, set(x) { this.v = x; subs.forEach(f => f(x)) }, subscribe(f) { subs.add(f); return () => subs.delete(f) } }
}
export const state = {
  focusedStoredSessionId: sig('S1'), focusedSessionId: sig('R1'), focusedSessionProfile: sig('default'),
  activeSessionId: sig('R1'), busyBySession: sig({ R1: false })
}
export const host = {
  state,
  notify: m => calls.push(['notify', m.message]),
  navigate: p => calls.push(['navigate', p]),
  openSession: id => calls.push(['openSession', id]),
  newChat: p => calls.push(['newChat', p]),
  request: async (m, p) => {
    calls.push(['request', m, p.title || p.text || p.session_id])
    if (m === 'session.create') return { session_id: 'R9', stored_session_id: 'S9' }
    if (m === 'prompt.submit') return { status: 'streaming' }
    return {}
  },
  composer: {
    submit: (t, x) => (calls.push(['submit', t, x]), true),
    getDraft: async () => '',
    setDraft: async (t, v) => calls.push(['setDraft', t, v])
  }
}
export const useValue = s => s.get()
export const COMPOSER_AREAS = { top: 'composer.top', middleware: 'composer.middleware' }
export const PALETTE_AREA = 'palette'
export const KEYBINDS_AREA = 'keybinds'
