// Minimal @hermes/plugin-sdk stand-in for the topic-shift desktop flow test.
const signal = v => {
  const subs = new Set()
  return { get: () => v, set: n => { v = n; subs.forEach(f => f(n)) }, subscribe: f => (subs.add(f), () => subs.delete(f)) }
}
export const calls = []
export const host = {
  state: {
    focusedStoredSessionId: signal('stored-1'),
    focusedSessionId: signal('rt-1'),
    activeSessionId: signal('rt-1'),
    focusedSessionProfile: signal('default'),
    busyBySession: signal({})
  },
  notify: n => calls.push(['notify', n]),
  request: async (m, p) => (calls.push(['request', m, p]), {}),
  composer: {
    submit: (target, text) => (calls.push(['submit', target, text]), true),
    getDraft: async () => '',
    setDraft: async (t, v) => calls.push(['setDraft', t, v])
  }
}
export const useValue = s => s.get()
export const COMPOSER_AREAS = { middleware: 'composer.middleware', top: 'composer.top' }
export const PALETTE_AREA = 'palette'
