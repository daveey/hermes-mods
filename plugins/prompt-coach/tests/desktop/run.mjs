import { renderToStaticMarkup } from 'react-dom/server'
import { jsx } from 'react/jsx-runtime'
globalThis.window = {}
const { calls, state } = await import('./sdk-mock.mjs')
const plugin = (await import('./plugin.mjs')).default
const regs = {}, rest = []
const RES = { id: 'c1', coach: true, lens: 'altitude', critique: 'You asked for a fix; the goal is crash-free.', rewrite: 'Keep the app crash-free: pull crash reports, reproduce, fix, verify.' }
plugin.register({
  register: r => (regs[r.id] = r),
  rest: async (path, opts) => { rest.push([path, opts.body]); return path === '/check' ? RES : path === '/seed' ? { seeded: opts.body.rewrite + '\n---\n[prompt-coach] Rewritten from @session:default/S1' } : { ok: true } },
  storage: { get: (k, d) => d, set() {} }, onDispose() {}
})
const PC = window.__promptCoach
const html = () => renderToStaticMarkup(jsx(regs.panel.render, {})).replace(/ class="[^"]*"/g, '')
const sleep = ms => new Promise(r => setTimeout(r, ms))
let ok = true
const check = (name, cond) => { console.log((cond ? 'PASS ' : 'FAIL ') + name); ok &&= !!cond }

// middleware is pass-through
const d = { text: 'fix the crash in the app when you open settings please' }
check('middleware returns draft unchanged', regs['observe-send'].data.handler(d) === d)
await sleep(400)
check('check called with stored id + text', rest[0][0] === '/check' && rest[0][1].session_id === 'S1')
let h = html()
check('panel renders critique + rewrite + buttons', h.includes('Altitude: ') && h.includes('crash-free') && h.includes('Run in new session') && h.includes('Replace') && h.includes('<textarea'))
console.log(h.slice(0, 600))

// slash + busy sends are not checked
const n = rest.length
regs['observe-send'].data.handler({ text: '/new' })
state.busyBySession.set({ R1: true }); regs['observe-send'].data.handler({ text: 'another long message that is queued behind the turn' }); state.busyBySession.set({ R1: false })
await sleep(400)
check('slash and queued sends skipped', rest.length === n && PC.flag?.id === 'c1')

// edit, then Run in new session
PC.setFlag({ ...PC.flag, draft: PC.flag.draft + ' Target: zero crashes for 30 days.' })
calls.length = 0
state.busyBySession.set({ R1: true }) // original turn still running (the real-world case)
const t0 = Date.now()
const p = PC.doNewSession()
check('panel closes immediately on click', PC.flag === null && html() === '')
await p
const ms = Date.now() - t0
const subs = calls.filter(c => c[0] === 'submit')
check('new session: newChat(profile) then seeded send to "new"', calls[0][0] === 'newChat' && calls[0][1] === 'default' && subs.length === 1 && subs[0][1] === 'new' && subs[0][2].startsWith('Keep the app crash-free'))
check(`launch is fast while original is busy (${ms}ms)`, ms < 500)
check('no /new routed into the busy original', !subs.some(s => s[2] === '/new'))
check('no interrupt of the original turn', !calls.some(c => c[1] === 'session.interrupt'))
check('no error notify', !calls.some(c => c[0] === 'notify'))
check('choice logged new_session edited=true', JSON.stringify(rest.at(-1)) === JSON.stringify(['/choice', { id: 'c1', choice: 'new_session', edited: true }]))
state.busyBySession.set({ R1: false })

// Replace path
state.focusedStoredSessionId.set('S1'); state.activeSessionId.set('R1'); state.busyBySession.set({ R1: true })
PC.setFlag({ ...RES, stored: 'S1', runtime: 'R1', profile: 'default', text: d.text, draft: RES.rewrite, open: true, phase: 'shown' })
calls.length = 0
await PC.doReplace()
const seq = calls.map(c => c[0] === 'submit' ? `submit:${c[2].slice(0, 12)}` : c[0] === 'request' ? c[1] : c[0])
console.log(seq)
check('replace: interrupt → /undo → clear → send rewrite', JSON.stringify(seq) === JSON.stringify(['session.interrupt', 'submit:/undo', 'setDraft', 'submit:Keep the app']))
check('choice logged replace', rest.at(-1)[1].choice === 'replace')

// Keep mine
PC.setFlag({ ...RES, stored: 'S1', runtime: 'R1', text: d.text, draft: RES.rewrite, open: true, phase: 'shown' })
PC.keepMine()
check('keep logs + clears', rest.at(-1)[1].choice === 'keep' && PC.flag === null)

// non-focused session hides panel
PC.setFlag({ ...RES, stored: 'S2', text: 'x', draft: 'y', open: true, phase: 'shown' })
check('hidden when another session focused', html() === '')
console.log(ok ? 'ALL PASS' : 'SOME FAILED'); process.exit(ok ? 0 : 1)
