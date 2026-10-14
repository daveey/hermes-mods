export const calls = []
const sig = v => ({ v, get() { return this.v }, set(x) { this.v = x }, subscribe() { return () => {} } })
export const state = {
  focusedStoredSessionId: sig('S1'), focusedSessionId: sig('R1'), activeSessionId: sig('R1'),
  focusedSessionProfile: sig('default'), busyBySession: sig({ R1: false })
}
export const host = {
  state,
  notify: m => calls.push(['notify', m]),
  request: async (m, p) => { calls.push(['request', m, p]); if (m === 'session.interrupt') state.busyBySession.set({ R1: false }); return {} },
  composer: {
    submit: (target, text) => {
      calls.push(['submit', target, text.slice(0, 60)])
      if (text === '/new') { state.focusedStoredSessionId.set(null); state.activeSessionId.set(null) }
      return true
    },
    setDraft: async (t, x) => calls.push(['setDraft', t, x.slice(0, 40)]),
    getDraft: async () => ''
  }
}
export const useValue = s => s.get()
export const COMPOSER_AREAS = { middleware: 'mw', top: 'top' }
export const PALETTE_AREA = 'palette'
