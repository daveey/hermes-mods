// node --test: pure helpers + actions (the SDK and React are stubbed by the loader below).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={state:{}};export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const request = (action, interaction = 'approval') => ({ action, interaction, why_human: 'Only the owner can use this control.', scope: action, evidence: 'Checked the documented owner-only control; owner interaction is required.' })

const ev = (id, kind, payload) => ({ id, kind, payload, created_at: 1000 + id })

test('latestBlock takes the newest block after the newest unblock', () => {
  const events = [
    ev(1, 'blocked', { reason: 'old ask', kind: 'needs_input' }),
    ev(2, 'unblocked', {}),
    ev(3, 'commented', {}),
    ev(4, 'blocked', JSON.stringify({ reason: 'Plug the phone in.', kind: 'capability' }))
  ]
  assert.deepEqual(m.latestBlock(events), { reason: 'Plug the phone in.', kind: 'capability', human_request: undefined, at: 1004 })
  assert.equal(m.latestBlock([ev(1, 'blocked', { reason: 'x' }), ev(2, 'unblocked', {})]), null)
  assert.equal(m.latestBlock([ev(1, 'blocked', { reason: 'initial_status' })]).reason, '')
})

test('needsHuman requires evidence, not a kind or missing history', () => {
  assert.equal(m.needsHuman({ kind: 'transient', reason: 'flaky' }), false)
  assert.equal(m.needsHuman({ kind: 'needs_input', reason: 'No human action needed. Waiting on time: two nightly runs.' }), false)
  assert.equal(m.needsHuman({ kind: 'needs_input', human_request: request('Approve the design doc.') }), true)
  assert.equal(m.needsHuman({ kind: 'capability', human_request: request('Restore the API login.', 'external') }), true)
  assert.equal(m.needsHuman(null), false)
})

test('stepsOf splits inline "1) … 2) …" runs and keeps the intro', () => {
  const r = m.stepsOf('Code is shipped. The rest needs you, in order: 1) plug the phone in; 2) open the app and lock it; 3) say done.')
  assert.equal(r.intro, 'Code is shipped. The rest needs you, in order:')
  assert.deepEqual(r.steps, ['plug the phone in', 'open the app and lock it', 'say done.'])
  const zero = m.stepsOf('Needs the phone. 0) Unlock it and plug it in. 1) Record PiP. 2) Hold the mic.')
  assert.deepEqual(zero.steps, ['Unlock it and plug it in.', 'Record PiP.', 'Hold the mic.'])
  assert.equal(zero.start, 0)
  assert.equal(r.start, 1)
})

test('stepsOf splits line-leading markers and leaves plain prose alone', () => {
  const r = m.stepsOf('I need 2 things:\n1) Fix billing\n   at the settings page.\n2) Reply "go".')
  assert.equal(r.intro, 'I need 2 things:')
  assert.deepEqual(r.steps, ['Fix billing at the settings page.', 'Reply "go".'])
  assert.deepEqual(m.stepsOf('Approve the design (see doc 3) before Phase 2.'), {
    intro: 'Approve the design (see doc 3) before Phase 2.',
    steps: [],
    start: 1
  })
  assert.deepEqual(m.stepsOf(''), { intro: '', steps: [], start: 1 })
})

test('blockedItems keeps evidenced requests only, newest first', () => {
  const tasks = [
    { id: 't_a', title: 'A', status: 'blocked', board: 'default' },
    { id: 't_b', title: 'B', status: 'running', board: 'default' },
    { id: 't_c', title: 'C', status: 'blocked', board: 'ops' },
    { id: 't_d', title: 'D', status: 'blocked', board: 'default' },
    { id: 't_e', title: 'E', status: 'blocked', board: 'default' },
    { id: 't_a', title: 'A dup', status: 'blocked', board: 'default' }
  ]
  const details = new Map([
    ['default:t_a', { task: {}, events: [ev(5, 'blocked', { reason: 'Turn on UI Automation.', kind: 'needs_input', human_request: request('Turn on UI Automation.', 'external') })] }],
    ['ops:t_c', { task: { body: 'Unrelated coordination prose.' }, events: [ev(1, 'blocked', { reason: 'initial_status', human_request: request('Human op: rotate the key.', 'external') })] }],
    ['default:t_d', { task: {}, events: [ev(9, 'blocked', { reason: 'retrying', kind: 'transient' })] }]
    // t_e: detail not loaded yet
  ])
  const items = m.blockedItems(tasks, details)
  assert.deepEqual(items.map(i => i.id), ['t_a', 't_c'])
  assert.equal(items[0].kindLabel, 'Needs your input')
  assert.equal(items[0].text, 'Turn on UI Automation.')
  assert.equal(items[1].fromBody, false)
  assert.equal(items[1].board, 'ops')
  assert.equal(items[1].text, 'Human op: rotate the key.')
  assert.ok(m.agentIssues(tasks, details).some(i => i.id === 't_e'))
})

test('parked (⌘B) matches any lineage id; junk storage is empty', () => {
  assert.deepEqual(m.parseParkedIds('["s1","",3,"s2"]'), ['s1', 's2'])
  assert.deepEqual(m.parseParkedIds('not json'), [])
  assert.deepEqual(m.parseParkedIds(null), [])
  assert.equal(m.isParked(new Set(['tip', 'root']), ['root']), true)
  assert.equal(m.isParked(new Set(['tip']), ['other']), false)
})

test('unblockCard: Done → PATCH ready only; Reply → comment first, then PATCH, on the card board', async () => {
  const calls = []
  const call = async (path, opts) => (calls.push([path, opts.method, opts.body]), {})
  await m.unblockCard({ id: 't_a', board: 'default' }, '   ', call)
  assert.deepEqual(calls, [['/api/plugins/kanban/tasks/t_a', 'PATCH', { status: 'ready' }]])
  calls.length = 0
  await m.unblockCard({ id: 't_c', board: 'ops' }, 'Done, key rotated', call)
  assert.deepEqual(calls, [
    ['/api/plugins/kanban/tasks/t_c/comments?board=ops', 'POST', { body: 'Done, key rotated', author: 'user' }],
    ['/api/plugins/kanban/tasks/t_c?board=ops', 'PATCH', { status: 'ready' }]
  ])
})

test('unblockCard: a failed comment does not unblock', async () => {
  const calls = []
  const call = async (path, opts) => {
    calls.push(opts.method)
    if (opts.method === 'POST') throw new Error('409')
  }
  await assert.rejects(m.unblockCard({ id: 't_a', board: 'default' }, 'note', call))
  assert.deepEqual(calls, ['POST'])
})

test('openCard: core drawer hook, else task-mentions popup, else the Kanban page', () => {
  const seen = []
  assert.equal(m.openCard({ id: 't_a', board: 'ops' }, { __hermesKanbanOpenTask: (id, b) => (seen.push([id, b]), true) }), 'drawer')
  assert.deepEqual(seen, [['t_a', 'ops']])
  assert.equal(m.openCard({ id: 't_a' }, { __taskMentionsOpen: id => seen.push(id) }), 'popup')
  let nav = null
  assert.equal(m.openCard({ id: 't_a' }, {}, p => (nav = p)), 'page')
  assert.equal(nav, '/kanban')
})

test('unescapeText turns literal \\uXXXX escapes into characters', () => {
  assert.equal(m.unescapeText('panel host \\u2014 video'), 'panel host \u2014 video')
  assert.equal(m.unescapeText(null), '')
})

test('checklist: model rewrite wins and gets a hand-back step only when missing', () => {
  const item = { id: 't_x', board: 'default', text: 'Code shipped. Plug the phone in.', steps: [], start: 1, intro: '' }
  const a = m.checklist(item, ['Plug the phone into the Mac.', 'Click Done → unblock.'])
  assert.equal(a.source, 'rewritten')
  assert.deepEqual(a.steps, ['Plug the phone into the Mac.', 'Click Done → unblock.'])
  const b = m.checklist(item, ['Plug the phone into the Mac.'])
  assert.deepEqual(b.steps, ['Plug the phone into the Mac.', m.HANDBACK])
})

test('checklist: falls back to the note numbering, then one step per sentence, always numbered', () => {
  const parsed = m.checklist({ text: 'x', steps: ['plug in', 'open app'], start: 0, intro: 'Do:' }, null)
  assert.equal(parsed.source, 'parsed')
  assert.equal(parsed.start, 0)
  assert.equal(parsed.intro, 'Do:')
  assert.deepEqual(parsed.steps, ['plug in', 'open app', m.HANDBACK])
  const prose = m.checklist({ text: 'Code is on main; the watch is on 295. After 07:00 PT, update the watch to 316. Then reply with what you heard.', steps: [], start: 1 }, [])
  assert.equal(prose.source, 'sentences')
  assert.deepEqual(prose.steps, ['Code is on main; the watch is on 295.', 'After 07:00 PT, update the watch to 316.', 'Then reply with what you heard.', m.HANDBACK])
  assert.deepEqual(m.checklist({ text: '', steps: [] }, null).steps, ['Open the card to see what it needs.', m.HANDBACK])
})

test('sentenceSteps keeps decimals and abbreviations-free prose intact', () => {
  assert.deepEqual(m.sentenceSteps('Median was 0.99 s. Restart the app!  Then wait'), ['Median was 0.99 s.', 'Restart the app!', 'Then wait'])
  assert.deepEqual(m.sentenceSteps(''), [])
})

test('parked chats get numbered steps too', () => {
  assert.equal(m.PARKED_STEPS.length, 2)
  assert.match(m.PARKED_STEPS[1], /⌘B/)
})

test('linkSegments: web URLs and local paths become links; fractions, and/or and trailing punctuation do not', () => {
  const t = 'On your MacBook, open /Users/me/.hermes/kanban/evidence/blind-sheet-v2.html in the browser. See https://example.com/x?a=1. Score 7/10 and/or ~/notes/a.md, then done.'
  const links = m.linkSegments(t).filter(s => s.kind !== 'text')
  assert.deepEqual(links, [
    { kind: 'path', value: '/Users/me/.hermes/kanban/evidence/blind-sheet-v2.html' },
    { kind: 'url', value: 'https://example.com/x?a=1' },
    { kind: 'path', value: '~/notes/a.md' }
  ])
  assert.equal(m.linkSegments(t).map(s => s.value).join(''), t)
  assert.deepEqual(m.linkSegments('(see `/tmp/x.log`)').filter(s => s.kind !== 'text'), [{ kind: 'path', value: '/tmp/x.log' }])
  assert.deepEqual(m.linkSegments('no links here, 1/2 done'), [{ kind: 'text', value: 'no links here, 1/2 done' }])
  assert.deepEqual(m.linkSegments(''), [])
})

test('linkSegments: a path with spaces links whole (\u201c~/d/Morning Digest Board.html\u201d), prose after it does not', () => {
  const t = 'Open ~/design/bb-digest/Morning Digest Board.html and review it. Also `/Users/me/My Notes/todo list.md` here.'
  const links = m.linkSegments(t).filter(x => x.kind !== 'text')
  assert.deepEqual(links, [
    { kind: 'path', value: '~/design/bb-digest/Morning Digest Board.html' },
    { kind: 'path', value: '/Users/me/My Notes/todo list.md' }
  ])
  assert.equal(m.linkSegments(t).map(x => x.value).join(''), t)
  // Lowercase prose after a directory never joins; a path that already has an extension stays as is.
  assert.deepEqual(m.linkSegments('see ~/code/foo and bar.txt').filter(x => x.kind !== 'text'), [{ kind: 'path', value: '~/code/foo' }])
  assert.deepEqual(m.linkSegments('ran /tmp/a.log Then Board.html').filter(x => x.kind !== 'text'), [{ kind: 'path', value: '/tmp/a.log' }])
  // No extension within 5 words: keep the short match.
  assert.deepEqual(m.linkSegments('in ~/design/Big Project Folder.').filter(x => x.kind !== 'text'), [{ kind: 'path', value: '~/design/Big' }])
})

test('linkSegments: scheme-less web addresses link with an https href; file names and emails do not', () => {
  const t = 'Open github.com/settings/billing while signed in. Edit plugin.js, README.md; mail someone@example.com.'
  const links = m.linkSegments(t).filter(s => s.kind !== 'text')
  assert.deepEqual(links, [{ kind: 'url', value: 'github.com/settings/billing' }])
  assert.equal(m.linkHref(links[0]), 'https://github.com/settings/billing')
  assert.equal(m.linkHref({ kind: 'url', value: 'https://a.io' }), 'https://a.io')
  assert.equal(m.linkSegments(t).map(s => s.value).join(''), t)
})

test('openLink: .md paths open in the preview pane; other paths, URLs and a failed preview use the OS handler', async () => {
  const previewed = [], external = []
  let previewOk = true
  m.bindLinkApis({ os: { openExternal: async u => (external.push(u), true) }, openPreview: async p => (previewed.push(p), previewOk) })
  await m.openLink('/Users/you/design/plan.md', 'path')
  await m.openLink('~/notes/A.Markdown', 'path')
  await m.openLink('/tmp/x.log', 'path')
  await m.openLink('https://example.com/README.md', 'url')
  previewOk = false
  await m.openLink('/gone.md', 'path')
  assert.deepEqual(previewed, ['/Users/you/design/plan.md', '~/notes/A.Markdown', '/gone.md'])
  assert.deepEqual(external, ['/tmp/x.log', 'https://example.com/README.md', '/gone.md'])
  // Older core without ctx.openPreview: everything goes to the OS handler.
  external.length = 0
  m.bindLinkApis({ os: { openExternal: async u => (external.push(u), true) } })
  await m.openLink('/Users/you/design/plan.md', 'path')
  assert.deepEqual(external, ['/Users/you/design/plan.md'])
  m.bindLinkApis(null)
})

// ── worker failures vs genuine human blockers ───────────────────────────────
const REFUSAL_ERR = 'pid 4242 exited with code 1 Worker\'s last output: "Model declined to respond (safety refusal) \u2014 trying fallback... Anthropic\'s safety filter refused this request"'
const failureEvents = [
  ev(1, 'created', {}),
  ev(2, 'claimed', {}),
  ev(3, 'crashed', { exit_kind: 'nonzero_exit', exit_code: 1, worker_output: 'Model declined to respond (safety refusal)' }),
  ev(4, 'gave_up', { failures: 2, effective_limit: 2, limit_source: 'dispatcher', error: REFUSAL_ERR })
]
const T = (id, board = 'default') => ({ id, title: `Task ${id}`, status: 'blocked', board })

test('blockState: gave_up/refusal/crash with no blocked event is an agent failure, not a human block', () => {
  const st = m.blockState(failureEvents)
  assert.equal(st.origin, 'agent')
  assert.equal(st.block, null)
  assert.equal(st.failure.kind, 'gave_up')
  assert.equal(st.failure.refusal, true)
  assert.equal(st.failure.reason, 'The model declined to respond (safety refusal).')
  const crash = m.blockState([ev(1, 'crashed', { exit_kind: 'nonzero_exit', exit_code: 137, error: 'oom' })])
  assert.equal(crash.origin, 'agent')
  assert.match(crash.failure.reason, /crashed \(exit code 137\)/)
  assert.match(m.blockState([ev(1, 'gave_up', { failures: 3, error: 'spawn failed twice' })]).failure.reason, /gave up after 3 failed attempts\.$/)
  // raw worker transcript never leaks into the headline
  assert.doesNotMatch(st.failure.reason, /pid|last output/)
})

test('blockedItems excludes failure-parked cards; agentIssues lists them with no-action wording', () => {
  const details = new Map([['default:t_fail', { task: { body: 'Remaining after the sweep: three things.' }, events: failureEvents }]])
  assert.deepEqual(m.blockedItems([T('t_fail')], details), [])
  const issues = m.agentIssues([T('t_fail'), T('t_fail')], details)
  assert.equal(issues.length, 1)
  assert.equal(issues[0].id, 't_fail')
  assert.equal(issues[0].noAction, true)
  assert.equal(issues[0].refusal, true)
  assert.match(issues[0].reason, /safety refusal/)
  assert.doesNotMatch(issues[0].reason, /Remaining after/) // never the unrelated body line
})

test('explicit human block stays a human item with its reason, even after an earlier failure', () => {
  const events = [...failureEvents, ev(5, 'blocked', { reason: 'Approve the migration plan.', kind: 'needs_input', human_request: request('Approve the migration plan.') })]
  const details = new Map([['default:t_h', { task: { body: 'unrelated body' }, events }]])
  const items = m.blockedItems([T('t_h')], details)
  assert.equal(items.length, 1)
  assert.equal(items[0].text, 'Approve the migration plan.')
  assert.equal(items[0].reasonSource, 'recorded')
  assert.deepEqual(m.agentIssues([T('t_h')], details), [])
  // a failure AFTER the human block wins (the dispatcher parked it); unblock resets everything
  const later = [...events, ev(6, 'gave_up', { failures: 1, error: 'boom' })]
  assert.equal(m.blockedItems([T('t_h')], new Map([['default:t_h', { events: later }]])).length, 0)
  assert.equal(m.blockState([...later, ev(7, 'unblocked', {})]).origin, 'unknown')
})

test('legacy and initial-blocked cards without evidence stay operational', () => {
  const d = new Map([
    ['default:t_init', { task: { body: 'Rotate the key, then say done.' }, events: [ev(1, 'blocked', { reason: 'initial_status' })] }],
    ['default:t_noev', { task: { body: 'Remaining after the sweep: three things.' }, events: [] }],
    ['default:t_empty', { task: { body: 'Remaining after the sweep: three things.' }, events: [ev(1, 'blocked', { kind: 'needs_input' })] }]
  ])
  const items = m.blockedItems([T('t_init'), T('t_noev'), T('t_empty')], d)
  assert.deepEqual(items, [])
  const issues = m.agentIssues([T('t_init'), T('t_noev'), T('t_empty')], d)
  assert.deepEqual(issues.map(i => i.id).sort(), ['t_empty', 't_init', 't_noev'])
  for (const issue of issues) assert.equal(issue.owner, 'unassigned')
})

test('missing detail stays operational without claiming a human ask', () => {
  assert.deepEqual(m.blockedItems([T('t_load')], new Map()), [])
  assert.equal(m.agentIssues([T('t_load')], new Map())[0].failureKind, 'unclassified')
})

test('actionable multi-step human ask: askSummary is the first explicit step, not the body', () => {
  const reason = 'Do these: 1) Open Settings \u2192 Privacy. 2) Enable Automation for the app. 3) Click Done.'
  const d = new Map([['default:t_m', { task: { body: 'Unrelated first sentence.' }, events: [ev(1, 'blocked', { reason, kind: 'capability', human_request: request(reason, 'external') })] }]])
  const [it] = m.blockedItems([T('t_m')], d)
  assert.deepEqual(it.steps.slice(0, 2), ['Open Settings \u2192 Privacy.', 'Enable Automation for the app.'])
  assert.equal(m.askSummary(it), 'Open Settings \u2192 Privacy.')
  assert.equal(it.kindLabel, 'Needs access you have')
})

// ── explicit non-human pause (kanban_block kind=transient) ──────────────────
const PAUSE_REASON = 'No human action is needed. Automated recovery is paused after repeated model refusals. Hermes must \u001b[31mreview\u001b[0m the card.'
const pauseEvents = [...failureEvents, ev(5, 'blocked', { reason: PAUSE_REASON, kind: 'transient' })]
const mapOf = (id, events) => new Map([[`default:${id}`, { task: { body: 'unrelated body line.' }, events }]])

test('newer transient pause after gave_up stays visible as an agent issue (no ask, no buttons), reason ANSI-free', () => {
  const d = mapOf('t_p', pauseEvents)
  assert.deepEqual(m.blockedItems([T('t_p')], d), [])
  const issues = m.agentIssues([T('t_p')], d)
  assert.equal(issues.length, 1)
  assert.equal(issues[0].noAction, true)
  assert.equal(issues[0].refusal, true)
  assert.equal(issues[0].at, 1005)
  assert.equal(issues[0].reason, 'No human action is needed. Automated recovery is paused after repeated model refusals. Hermes must review the card.')
  assert.doesNotMatch(issues[0].reason, /\u001b|\[31m/)
  assert.doesNotMatch(issues[0].reason, /unrelated body/)
})

test('pause with no recorded reason says so instead of inventing one; benign dependency/time waits stay hidden', () => {
  const [i] = m.agentIssues([T('t_e')], mapOf('t_e', [ev(1, 'blocked', { kind: 'transient' })]))
  assert.equal(i.reason, 'Paused automatically; no reason was recorded.')
  assert.equal(i.refusal, false)
  for (const p of [{ kind: 'dependency', reason: 'Waiting for failed t_other.' }, { kind: 'time', reason: 'No human action needed. Waiting on time: nightly run failed.' }]) {
    const d = mapOf('t_w', [ev(1, 'blocked', p)])
    assert.deepEqual(m.blockedItems([T('t_w')], d), [])
    assert.deepEqual(m.agentIssues([T('t_w')], d), [])
  }
})

test('an actual human block after the pause overrides it; a later failure overrides the human block', () => {
  const human = [...pauseEvents, ev(6, 'blocked', { reason: 'Approve the retry.', kind: 'needs_input', human_request: request('Approve the retry.') })]
  const d = mapOf('t_o', human)
  const items = m.blockedItems([T('t_o')], d)
  assert.equal(items.length, 1)
  assert.equal(items[0].text, 'Approve the retry.')
  assert.deepEqual(m.agentIssues([T('t_o')], d), [])
  assert.equal(m.agentIssues([T('t_o')], mapOf('t_o', [...human, ev(7, 'crashed', { exit_code: 1 })])).length, 1)
})

test('mixed board: counts split asks vs issues, issues carry no unblock fields', () => {
  const d = new Map([...mapOf('t_a', failureEvents), ...mapOf('t_b', pauseEvents), ...mapOf('t_c', [ev(1, 'blocked', { reason: 'Plug in the phone.', kind: 'capability', human_request: request('Plug in the phone.', 'physical') })])])
  const tasks = [T('t_a'), T('t_b'), T('t_c')]
  assert.deepEqual(m.blockedItems(tasks, d).map(i => i.id), ['t_c'])
  const issues = m.agentIssues(tasks, d)
  assert.deepEqual(issues.map(i => i.id).sort(), ['t_a', 't_b'])
  for (const i of issues) assert.deepEqual([i.noAction, 'steps' in i, 'text' in i], [true, false, false])
})

test('failure summary never leaks the raw error or worker output, and strips ANSI', () => {
  const raw = '\u001b[31mTraceback\u001b[0m in /Users/you/x.py token=abc123 secret'
  for (const kind of ['gave_up', 'crashed', 'timed_out', 'spawn_failed']) {
    const r = m.failureSummary(kind, { error: raw, worker_output: raw, reason: raw, failures: 2, exit_code: 1 })
    assert.doesNotMatch(r, /Traceback|token|abc123|\/Users|\u001b|\[31m/, kind)
  }
  assert.match(m.failureSummary('gave_up', { error: raw, failures: 2 }), /^The dispatcher gave up after 2 failed attempts\.$/)
})

test('structured kind=time (external-event wait, classified in place) is not a human ask nor an agent issue', () => {
  const p = { kind: 'time', reason: 'External-event wait while the remote graph executes; parent verifies outcomes.', classified_in_place: true }
  assert.equal(m.needsHuman(p), false)
  const d = mapOf('t_wait', [ev(1, 'blocked', { reason: 'initial_status' }), ev(2, 'blocked', p)])
  assert.deepEqual(m.blockedItems([T('t_wait')], d), [])
  assert.deepEqual(m.agentIssues([T('t_wait')], d), [])
  // a real ask after it is shown again with its buttons' data
  d.get('default:t_wait').events.push(ev(3, 'blocked', { kind: 'needs_input', reason: 'Approve the plan in the doc.', human_request: request('Approve the plan in the doc.') }))
  assert.equal(m.blockedItems([T('t_wait')], d).length, 1)
  assert.equal(m.needsHuman({ kind: 'capability', reason: 'External-event wait: restore the API login.' }), false)
  assert.equal(m.needsHuman({ kind: 'capability', human_request: request('Restore the API login.', 'external') }), true)
})
