// Spin-out desktop flow: auto panel after a finished turn, Kanban button, dismiss memory, old-backend gate.
import { renderToStaticMarkup } from 'react-dom/server'
const store = {}
globalThis.window = { localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => (store[k] = v) } }
const { host, calls } = await import('./sdk-mock.mjs')
const plugin = (await import('./plugin.mjs')).default
const sleep = ms => new Promise(r => setTimeout(r, ms))
let fails = 0
const ok = (cond, msg) => { console.log(`${cond ? 'ok' : 'FAIL'} - ${msg}`); if (!cond) fails++ }

let api = 3, planReply = null
const rest = []
const PLAN = { tasks: [{ title: 'Fix sync', message: 'Fix the sync job @session:default/S1', prompt: 'Fix' },
                       { title: 'Add tests', message: 'Add tests @session:default/S1', prompt: 'Add' }],
               skipped: [], profile: 'default', cwd: '/code/app', reply_index: 7 }
const regs = {}
plugin.register({
  register: r => (regs[r.id] = r),
  onDispose() {},
  rest: async (path, opts = {}) => {
    rest.push([path, opts.body])
    if (path === '/ping') return api >= 2 ? { ok: true, api } : { ok: true }
    if (path === '/plan') return planReply
    if (path === '/spun') return { added: opts.body.items.length }
    if (path === '/kanban') return { board: 'babybear', cards: opts.body.tasks.map((t, i) => ({ title: t.title, task_id: `t_${i + 1}` })) }
    throw new Error('unexpected ' + path)
  }
})
const html = () => renderToStaticMarkup(regs.banner.render()).replace(/ class="[^"]*"/g, '')
const SO = window.__spinOut
const B = host.state.busyBySession
const finishTurn = async () => { B.set({ ...B.get(), R1: true }); B.set({ ...B.get(), R1: false }); await sleep(1700) }
await sleep(20)

// 1. a turn finishes, the reply has no follow-ups: the auto check runs, shows nothing
planReply = { tasks: [], skipped: [], auto_skip: 'no cues', reply_index: 6 }
await finishTurn()
const plans = () => rest.filter(r => r[0] === '/plan')
ok(plans().length === 1 && plans()[0][1].auto === true, 'auto /plan sent after the turn ended')
ok(html() === '', 'no panel when there is no follow-up work')

// 2. a turn finishes with follow-ups: the panel appears with Launch/All/Kanban
planReply = PLAN
await finishTurn()
let h = html()
ok(h.includes('left at the end of this turn') && h.includes('Kanban (2)') && h.includes('All (2)'), 'auto panel shows follow-ups + Kanban + All')

// 3. Kanban: both cards created, rows link to the board
await SO.toKanban()
const kb = rest.find(r => r[0] === '/kanban')
ok(kb && kb[1].session_id === 'S1' && kb[1].cwd === '/code/app' && kb[1].tasks.length === 2, '/kanban payload: parent, cwd, both tasks')
h = html()
ok(h.includes('card t_1') && h.includes('card t_2') && h.includes('2 on the board'), 'rows show their cards')
ok(calls.some(c => c[0] === 'notify' && /2 follow-ups on the babybear board/.test(c[1])), 'notified with the board name')
ok(!calls.some(c => c[0] === 'request' && c[1] === 'session.create'), 'no sessions were launched')

// 4. dismiss, then the same reply again: no re-show; a new reply: shows again
SO.dismiss(); SO.setJob(null)
planReply = { ...PLAN }
await finishTurn()
ok(html() === '' || !html().includes('Kanban'), 'a finished turn with a fresh plan for the same reply after a done panel')
SO.setJob(null)
await finishTurn()
SO.dismiss()
await finishTurn()
ok(html() === '', 'dismissed reply does not come back')
planReply = { ...PLAN, reply_index: 9 }
await finishTurn()
ok(html().includes('Kanban (2)'), 'a new reply with follow-ups shows the panel again')

// 5. switching sessions mid-turn is not a "turn finished"
SO.setJob(null)
const before = plans().length
// R1 busy, user focuses idle R2, then R1 finishes in the background: nothing fires for R2
B.set({ R1: true, R2: false })
host.state.focusedSessionId.set('R2'); host.state.focusedStoredSessionId.set('S2'); B.set({ ...B.get() })
B.set({ R1: false, R2: false }); await sleep(1700)
ok(plans().length === before, 'focus change during a turn does not trigger the check')
host.state.focusedSessionId.set('R1'); host.state.focusedStoredSessionId.set('S1')

// 6. toggle off: no check
regs['auto-toggle'].data.run()
await finishTurn()
ok(plans().length === before, 'auto toggle off stops the check')
regs['auto-toggle'].data.run()

// 7. launching a child tells the parent (ledger) where it went
SO.setJob(null)
planReply = { ...PLAN, reply_index: 11 }
await finishTurn()
await SO.launchOne(0); await sleep(20)
const sp = rest.find(r => r[0] === '/spun')
ok(sp && sp[1].session_id === 'S1' && sp[1].items[0].title === 'Fix sync' && sp[1].items[0].ref === '@session:default/S9', '/spun records the child with an @session pointer')

// 8. old backend (no api version): no auto check, no Kanban button on a manual run
api = 1
await SO.spinOut() // manual run re-probes
await sleep(20)
const n = plans().length
await finishTurn()
ok(plans().length === n, 'old backend: auto check skipped')
ok(!html().includes('Kanban') && html().includes('All (2)'), 'old backend: manual panel without the Kanban button')

console.log(fails ? `# fail ${fails}` : '# pass')
process.exit(fails ? 1 : 0)
