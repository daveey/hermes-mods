// Flow test for desktop-plugins/topic-shift/plugin.js against sdk-mock.mjs. Exits non-zero on failure.
import assert from 'node:assert/strict'
import { renderToStaticMarkup } from 'react-dom/server'

globalThis.window = { localStorage: { getItem: () => null, setItem() {} } }
const { host, calls } = await import('./sdk-mock.mjs')
const plugin = (await import('./plugin.mjs')).default

const regs = {}
const checks = []
let reply = { shift: true, label: 'Other task', reason: 'r' }
plugin.register({
  register: r => (regs[r.id] = r),
  rest: async (path, opts) => (checks.push([path, opts.body.text]), path === '/brief' ? { seeded: 'BRIEF+msg' } : reply),
  storage: { get: (_k, d) => d, set() {} },
  onEvent() {},
  onDispose() {}
})
const sleep = ms => new Promise(r => setTimeout(r, ms))
const send = text => regs['observe-send'].data.handler({ text })
const banner = () => renderToStaticMarkup(regs.banner.render()).replace(/ class="[^"]*"/g, '')
const ts = window.__topicShift

// 1) idle send: checked after the short delay, banner offers New / Fork / Send here.
send('help me plan the unicorn party')
await sleep(700)
assert.deepEqual(checks.at(-1), ['/check', 'help me plan the unicorn party'])
assert.equal(ts.flag.steered, false)
assert.match(banner(), /New session.*Fork.*Send here/)
ts.sendHere()

// 2) send while the turn is running: no check until the turn ends, then a banner without Fork.
host.state.busyBySession.set({ 'rt-1': true })
const before = checks.length
send('why is the mini CPU pegged')
await sleep(1500)
assert.equal(checks.length, before, 'must not check while the turn runs')
host.state.busyBySession.set({})
await sleep(1500)
assert.deepEqual(checks.at(-1), ['/check', 'why is the mini CPU pegged'])
assert.equal(ts.flag.steered, true)
const html = banner()
assert.doesNotMatch(html, /Fork/)
assert.match(html, /Keep here/)

// 3) New session for a steered message never /undo-es the earlier turn.
await ts.doNew()
assert.ok(!calls.some(c => c[0] === 'submit' && c[2] === '/undo'), 'steered message must not /undo')
assert.ok(calls.some(c => c[0] === 'submit' && c[2] === '/new'))
assert.ok(calls.some(c => c[0] === 'submit' && c[2] === 'BRIEF+msg'))

// 4) a busy wait is superseded by a later send (e.g. the queue drain re-sending it).
calls.length = 0
host.state.busyBySession.set({ 'rt-1': true })
send('first queued thing to do')
host.state.busyBySession.set({})
send('second message right after')
await sleep(1600)
assert.equal(checks.filter(c => c[1] === 'first queued thing to do').length, 0, 'superseded wait must not check')
assert.equal(checks.at(-1)[1], 'second message right after')

console.log('topic-shift desktop flow: ok')
process.exit(0)
