// Coach desktop flow: every user rule's one-button panel, the shared gate (claim/report), spin-out hand-over and
// coordination, the screenshot middleware, dismiss/expired outcomes, toggle. Mocked SDK; the real spin-out plugin.
import { renderToStaticMarkup } from 'react-dom/server'
const store = {}
globalThis.window = { localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => (store[k] = v) } }
const { host, calls } = await import('./sdk-mock.mjs')
const coach = (await import('./coach.mjs')).default
const spin = (await import('./spin-out.mjs')).default
const sleep = ms => new Promise(r => setTimeout(r, ms))
let fails = 0
const ok = (cond, msg) => { console.log(`${cond ? 'ok' : 'FAIL'} - ${msg}`); if (!cond) fails++ }

const N = (rule, button, action, data = {}) => ({ nudge: { id: `n_${rule}`, rule, key: rule, title: `T ${rule}`, text: `why ${rule}`, button, action, data }, muted: [], demoted: [], turn: 7 })
const EMPTY = { nudge: null, muted: [], demoted: [], turn: 7 }
let stateReply = EMPTY, offerReply = EMPTY
const crest = [], srest = []
const cregs = {}, sregs = {}
const mk = (regs, log, handler) => ({ register: r => (regs[r.id] = r), onDispose() {}, onEvent() {}, rest: async (p, o = {}) => (log.push([p, o.body]), handler(p, o.body)) })
coach.register(mk(cregs, crest, (p, b) => {
  if (p === '/state') return stateReply
  if (p === '/offer') return offerReply
  if (p === '/event') return { ok: true }
  if (p === '/restart') return { title: 'Fix login', message: 'BRIEF @session:default/S1', cwd: '/code/app', profile: 'default' }
  if (p === '/board') return { board: 'default', cards: [{ title: 'Fix login', task_id: 't_42' }] }
  if (p === '/file-bug') return { board: 'hermes', cards: [{ title: 'Hermes bug', task_id: 't_7' }] }
  throw new Error('unexpected ' + p)
}))
let planReply = { tasks: [], skipped: [], reply_index: 1 }
spin.register(mk(sregs, srest, p => {
  if (p === '/ping') return { ok: true, api: 2 }
  if (p === '/plan') return planReply
  throw new Error('unexpected ' + p)
}))
const C = window.__coach, SO = window.__spinOut
const panel = () => renderToStaticMarkup(cregs.panel.render()).replace(/ class="[^"]*"/g, '')
const soBanner = () => renderToStaticMarkup(sregs.banner.render()).replace(/ class="[^"]*"/g, '')
const B = host.state.busyBySession
const finishTurn = async () => { B.set({ ...B.get(), R1: true }); B.set({ ...B.get(), R1: false }); await sleep(1950) }
const n = p => crest.filter(r => r[0] === p)
const events = () => n('/event').map(r => `${r[1].rule}:${r[1].event}`)
const send = draft => cregs['observe-send'].data.handler(draft)
await sleep(20)

// 1. a finished turn asks /state; nothing applies -> no panel
await finishTurn()
ok(n('/state').length === 1 && n('/state')[0][1].session_id === 'S1', 'turn end -> /state for the focused session')
ok(panel() === '', 'no nudge -> no panel')

// 2. still_broken -> Restart clean: brief, new session, opened, accepted
stateReply = N('still_broken', 'Restart clean', 'restart')
await finishTurn()
let h = panel()
ok(h.includes('data-rule="still_broken"') && (h.match(/<button/g) || []).length === 2 && h.includes('Restart clean') && h.includes('Dismiss'), 'still_broken panel: one primary button + Dismiss')
await C.act()
ok(calls.some(c => c[0] === 'request' && c[1] === 'session.create' && c[2] === 'Fix login'), 'restart: session.create with the brief title')
ok(calls.some(c => c[0] === 'request' && c[1] === 'prompt.submit' && /BRIEF @session/.test(c[2])), 'restart: brief + @session submitted')
ok(calls.some(c => c[0] === 'openSession' && c[1] === 'S9'), 'restart: clean session opened')
ok(events().at(-1) === 'still_broken:accepted' && panel().includes('Clean session started'), 'restart: accepted logged, panel done')
C.dismiss()
ok(events().at(-1) === 'still_broken:accepted', 'closing a done panel is not a dismissal')

// 3. move_to_board: dismiss -> muted for the session (claim says no)
stateReply = N('move_to_board', 'Move to board', 'board')
await finishTurn()
ok(panel().includes('Move to board'), 'move_to_board panel')
ok(C.claim('S1', 'topic-shift') === false, 'peers cannot claim while a coach panel is up')
C.dismiss()
ok(events().at(-1) === 'move_to_board:dismissed' && panel() === '', 'dismiss -> logged, panel gone')
ok(C.claim('S1', 'move_to_board') === false && C.claim('S1', 'topic-shift') === true, 'dismissed rule muted in the session; others free')

// 4. Move to board -> /board card, notify, done
await finishTurn()
await C.act()
ok(n('/board').length === 1 && n('/board')[0][1].session_id === 'S1', '/board for the session')
ok(calls.some(c => c[0] === 'notify' && /Moved to the default board: t_42/.test(c[1])) && panel().includes('Open board'), 'board: notified, Open board shown')
C.dismiss()

// 5. hermes_bug -> File it once (sig passed through)
stateReply = N('hermes_bug', 'File it once', 'file_bug', { sig: 'cross_session: open in a window' })
await finishTurn()
await C.act()
ok(n('/file-bug')[0][1].sig === 'cross_session: open in a window', '/file-bug carries the signature')
ok(calls.some(c => c[0] === 'notify' && /hermes board: t_7/.test(c[1])) && events().at(-1) === 'hermes_bug:accepted', 'bug filed on the hermes board, accepted')
C.dismiss()

// 6. long_session -> hands over to spin-out's planner; spin-out's panel then blocks coach
stateReply = N('long_session', 'Spin out', 'spin_out')
await finishTurn() // spin-out's own auto check finds nothing this turn; coach's long_session shows
ok(panel().includes('Spin out'), 'long_session panel')
planReply = { tasks: [{ title: 'Ship docs', message: 'Ship docs @session', prompt: 'Ship' }], skipped: [], profile: 'default', cwd: '/c', reply_index: 3 }
await C.act()
ok(srest.some(r => r[0] === '/plan' && !r[1].auto), 'spin_out: spin-out manual plan requested')
ok(panel() === '' && soBanner().includes('Ship docs'), 'coach panel replaced by the spin-out banner')
const before = n('/state').length
planReply = { ...planReply, tasks: [] } // spin-out's own auto check finds nothing new
SO.setJob({ ...SO.job }) // keep its banner up
await C.check()
ok(n('/state').length === before, 'no coach check while the spin-out banner is up')
SO.dismiss()
ok(!events().includes('spin-out:dismissed'), 'dismissing a manual spin-out run is not a bus dismissal')

// 7. spin-out auto panel goes through the gate: reports shown, blocked while a coach panel is up
planReply = { tasks: [{ title: 'Auto task', message: 'm', prompt: 'p' }], skipped: [], profile: 'default', cwd: '/c', reply_index: 11 }
stateReply = EMPTY
await finishTurn()
ok(soBanner().includes('Auto task') && events().at(-1) === 'spin-out:shown', 'spin-out auto panel reported as shown')
SO.dismiss()
ok(events().at(-1) === 'spin-out:dismissed' && C.claim('S1', 'spin-out') === false, 'auto spin-out dismissal -> muted on the bus')
C.facts.set('S1', { muted: [], demoted: [] })
C.setPanel({ stored: 'S1', runtime: 'R1', profile: 'default', nudge: N('still_broken', 'Restart clean', 'restart').nudge, phase: 'shown' })
planReply = { ...planReply, reply_index: 12 }
await SO.autoCheck('S1', 'default')
ok(!soBanner().includes('Auto task') || SO.job?.replyIndex !== 12, 'spin-out auto panel held back while a coach panel is up')
C.setPanel(null)

// 8. screenshot mid-turn: only image + <3 words + busy
offerReply = N('screenshot_midturn', 'Add a line', 'add_text')
B.set({ R1: true })
await send({ text: 'look', attachments: [{ kind: 'image', id: 'i1' }] })
ok(n('/offer').length === 1 && n('/offer')[0][1].rule === 'screenshot_midturn' && panel().includes('Add a line'), 'screenshot while busy -> Add a line')
await C.act()
ok(calls.some(c => c[0] === 'setDraft' && c[1] === 'S1' && /screenshot/.test(c[2])) && panel() === '', 'Add a line prefills the composer')
await send({ text: 'the button on the left is clipped', attachments: [{ kind: 'image', id: 'i2' }] })
B.set({ R1: false })
await send({ text: '', attachments: [{ kind: 'image', id: 'i3' }] })
await send({ text: 'hello', attachments: [] })
ok(n('/offer').length === 1, 'no offer with a real line of text, when idle, or without an image')
ok(send({ text: 'x' }) && true, 'middleware passes the draft through')

// 9. a peer panel (topic-shift) blocks the check; focus moved -> expired, not dismissed
window.__topicShift = { flag: { stored: 'S1', phase: 'shown' } }
const s0 = n('/state').length
await finishTurn()
ok(n('/state').length === s0, 'no coach check while topic-shift is showing')
delete window.__topicShift
stateReply = N('move_to_board', 'Move to board', 'board')
const origRest = crest.length
host.state.focusedStoredSessionId.set('S1')
const p = C.check('S1')
host.state.focusedStoredSessionId.set('S2')
await p
ok(panel() === '' && events().at(-1) === 'move_to_board:expired', 'focus moved before show -> expired (no mute)')
host.state.focusedStoredSessionId.set('S1')

// 10. report() from a peer -> /event with the session's turn; palette toggle stops checks
C.report('S1', 'topic-shift', 'shown')
ok(n('/event').at(-1)[1].turn === 7, 'peer report carries the turn for the shared budget')
cregs.toggle.data.run()
const s1 = n('/state').length
await finishTurn()
ok(n('/state').length === s1 && crest.length > origRest, 'toggle off: no checks')
cregs.toggle.data.run()

console.log(fails ? `# fail ${fails}` : '# pass')
process.exit(fails ? 1 : 0)
