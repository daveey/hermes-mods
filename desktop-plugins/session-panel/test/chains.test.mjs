// node --test: the dependency-chain walker (nextHop / chainFor / causeBreakdown), without a Desktop React install.
// [daveey 2026-11-20] every waiting card shows its full dependency chain, ending on a blocked card: you (red),
// an external service (pink) or a worker slot (grey). Design: claude.ai/artifact/H13j1JAueqaFVHyzF5hpLe.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const Codicon=()=>null;export const DisclosureCaret=()=>null;export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useRef=v=>({current:v});export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const NOW = Date.parse('2026-11-20T22:00:00Z'), S = NOW / 1000
const d = (task, extra = {}) => ({ task: { id: 't_x', title: 'X', ...task }, events: [], links: { parents: [] }, ...extra })

test('nextHop: every card state leads to the next card or a leaf', () => {
  const leaf = h => [h.leaf?.kind, h.leaf?.text]
  assert.deepEqual(leaf(m.nextHop(d({ status: 'blocked', block_kind: 'needs_input', human_request: { action: 'Pick A or B', why_human: 'only David decides', interaction: 'approval', scope: 'the release', evidence: 'two windows' } }), NOW)), ['you', 'Pick A or B'])
  // An ask-less (or invalid) needs_input/capability block is agent-owned in core: grey, never red.
  assert.deepEqual(leaf(m.nextHop(d({ status: 'blocked', block_kind: 'capability' }), NOW)), ['slot', 'an agent must clear a capability block'])
  assert.deepEqual(leaf(m.nextHop(d({ status: 'blocked', block_kind: 'needs_input', human_request: { action: 'x' } }), NOW)), ['slot', 'an agent must clear a needs_input block (its ask is not valid)'])
  assert.deepEqual(m.nextHop(d({ status: 'blocked', block_kind: 'dependency' }, { links: { parents: ['t_p1', 't_p2'] }, link_tasks: [{ id: 't_p1', status: 'done' }, { id: 't_p2', status: 'running' }] }), NOW), { ref: { id: 't_p2', status: 'running' } })
  assert.deepEqual(leaf(m.nextHop(d({ status: 'blocked', block_kind: 'agent_fix' }), NOW)), ['slot', 'an agent must clear a agent_fix block'])
  assert.deepEqual(m.nextHop(d({ status: 'todo' }, { links: { parents: ['t_p'] }, link_tasks: [{ id: 't_p', status: 'scheduled' }] }), NOW), { ref: { id: 't_p', status: 'scheduled' } })
  assert.deepEqual(leaf(m.nextHop(d({ status: 'todo' }), NOW)), ['slot', 'not started: waiting to be promoted'])
  assert.deepEqual(leaf(m.nextHop(d({ status: 'ready' }, { queue_note: 'current: 3 cards ahead on worker-a1; admissible capacity unknown' }), NOW)), ['slot', 'waiting for a worker: 3 cards ahead on worker-a1'])
  assert.deepEqual(leaf(m.nextHop(d({ status: 'ready' }, { capacity_note: 'current: restriction coverage incomplete; admissible capacity unknown' }), NOW)), ['slot', 'waiting for a worker'])
  assert.deepEqual(leaf(m.nextHop(d({ status: 'triage' }), NOW)), ['slot', 'needs triage: nobody is working it'])
  assert.deepEqual(leaf(m.nextHop(d({ status: 'review' }, { runs: [{ status: 'done' }] }), NOW)), ['slot', 'no reviewer running'])
  assert.deepEqual(leaf(m.nextHop(d({ status: 'review' }, { runs: [{ status: 'running' }] }), NOW)), ['running', 'review running'])
  assert.deepEqual(leaf(m.nextHop(d({ status: 'running', claim_lock: 'worker-a1:123' }), NOW)), ['running', 'running on worker-a1'])
  assert.deepEqual(leaf(m.nextHop(d({ status: 'done' }), NOW)), ['done', 'done · the waiting card should resume'])
  assert.deepEqual(leaf(m.nextHop(d({}), NOW)), ['unknown', 'status unknown'])
  // Scheduled: the owner's park.
  const park = (payload, extra = {}) => d({ status: 'scheduled', ...extra }, { events: [{ id: 2, kind: 'scheduled', created_at: S - 600, payload }] })
  assert.deepEqual(m.nextHop(park({ wake_at: S + 3600, awaiting: { kind: 'card', ref: 't_01a1eea7e9cf1de6' } }), NOW), { ref: { id: 't_01a1eea7e9cf1de6', status: '' } })
  assert.deepEqual(m.nextHop(park({ wake_at: S + 3600, awaiting: { kind: 'ci', ref: 'https://github.com/o/r/actions/runs/123' } }), NOW).leaf, { kind: 'external', text: 'CI run 123', sub: '1h left · Fri 15:00 PST' })
  assert.deepEqual(leaf(m.nextHop(park({ wake_at: S + 3600, awaiting: { kind: 'deploy', ref: 'https://github.com/o/r/pull/9' } }), NOW)), ['external', 'deploy: PR #9'])
  assert.deepEqual(leaf(m.nextHop(park({ wake_at: S + 3600, awaiting: { kind: 'human', why: 'pick a window' } }), NOW)), ['you', 'pick a window'])
  assert.deepEqual(leaf(m.nextHop(park({ wake_at: S + 3600, awaiting: { kind: 'clock', why: 'two-day soak' } }), NOW)), ['external', 'timer: two-day soak'])
  assert.deepEqual(leaf(m.nextHop(park({ reason: 'x' }, { block_kind: 'needs_input' }), NOW)), ['you', 'no timed wake: will be blocked for you'])
})

test('chainFor: walks card to card to the leaf; this chat\'s cards come from their rows; cycles and long chains end unknown', () => {
  const details = {
    t_c1c1c1: d({ id: 't_c1c1c1', title: 'C1', status: 'todo', session_id: 's1' }, { links: { parents: ['t_c2c2c2'] }, link_tasks: [{ id: 't_c2c2c2', status: 'blocked' }] }),
    t_c2c2c2: d({ id: 't_c2c2c2', title: 'C2', status: 'blocked', block_kind: 'needs_input', human_request: { action: 'Approve', why_human: 'w', interaction: 'approval', scope: 's', evidence: 'e' } })
  }
  const info = (chip, id) => (details[id] ? { state: 'ok', info: { id, title: details[id].task.title, status: details[id].task.status, session: str(details[id].task.session_id), owner: '', board: 'default', hop: m.nextHop(details[id], NOW) } } : { state: 'failed', info: null })
  const str = v => (v == null ? '' : String(v))
  const root = (id, waitingOn, extra = {}) => ({ key: `kb:${id}`, kind: 'kanban', label: id, chip: { id, state: 'scheduled', look: 'waiting', waitingOn, connectionId: 'C1', profile: 'default', ...extra } })
  const ch = m.chainFor(root('t_r', 'card t_c1c1c1'), NOW, { info, local: new Map() })
  assert.deepEqual([ch.nodes.map(n => n.id), ch.leaf.kind, ch.leaf.text, m.hops(ch)], [['t_c1c1c1', 't_c2c2c2'], 'you', 'Approve', 3])
  // An unreadable card ends the chain as unknown, never a guess.
  assert.deepEqual(m.chainFor(root('t_r', 'card t_dddddd'), NOW, { info, local: new Map() }).leaf, { kind: 'unknown', text: 'could not read this card' })
  // A loading card says so.
  const loading = m.chainFor(root('t_r', 'card t_c1c1c1'), NOW, { info: () => ({ state: 'loading', info: null }), local: new Map() })
  assert.deepEqual([loading.leaf.text, loading.loading], ['reading…', true])
  // A cycle back to the root.
  const loop = { t_1e1e1e: d({ id: 't_1e1e1e', status: 'todo' }, { links: { parents: ['t_r'] }, link_tasks: [{ id: 't_r', status: 'scheduled' }] }) }
  const linfo = (chip, id) => (loop[id] ? { state: 'ok', info: { id, title: id, status: 'todo', session: '', owner: '', board: 'default', hop: m.nextHop(loop[id], NOW) } } : { state: 'failed', info: null })
  assert.deepEqual(m.chainFor(root('t_r', 'card t_1e1e1e'), NOW, { info: linfo, local: new Map() }).leaf, { kind: 'unknown', text: 'circular wait' })
  // Longer than MAX_HOPS.
  const longInfo = (chip, id) => { const n = parseInt(id.slice(2), 16); return { state: 'ok', info: { id, title: id, status: 'todo', session: '', owner: '', board: 'default', hop: { ref: { id: `t_${(n + 1).toString(16).padStart(6, '0')}`, status: 'todo' } } } } }
  const long = m.chainFor(root('t_r', 'card t_000001'), NOW, { info: longInfo, local: new Map() })
  assert.deepEqual([long.nodes.length, long.leaf.text], [m.MAX_HOPS, `chain longer than ${m.MAX_HOPS} cards`])
  // A card of this chat: from its own row (running here), no remote read.
  const local = new Map([['t_a0a0a0', { item: { chip: { id: 't_a0a0a0', state: 'running' } }, waiting: false, label: 'Mine', chip: { id: 't_a0a0a0', state: 'running' } }]])
  const lc = m.chainFor(root('t_r', 'card t_a0a0a0'), NOW, { info: () => { throw new Error('no remote read') }, local })
  assert.deepEqual([lc.nodes[0].title, lc.leaf.kind], ['Mine', 'running'])
})

test('firstHop: a waiting card\'s own row starts the walk; causeBreakdown counts by root cause', () => {
  const r = (id, chip) => ({ key: `kb:${id}`, kind: 'kanban', label: id, chip: { id, connectionId: null, profile: 'default', ...chip } })
  assert.deepEqual(m.firstHop(r('a', { state: 'scheduled', waitingOn: 'card t_01a1eea7e9cf1de6' }), NOW), { ref: { id: 't_01a1eea7e9cf1de6', status: '' } })
  assert.equal(m.firstHop(r('b', { state: 'triage', waitingOn: 'triage' }), NOW).leaf.kind, 'slot')
  assert.equal(m.firstHop(r('c', { state: 'scheduled', waitingOn: 'ci https://github.com/o/r/actions/runs/5' }), NOW).leaf.kind, 'external')
  assert.equal(m.firstHop(r('d', { state: 'running', stale: true }), NOW).leaf.kind, 'unknown')
  assert.equal(m.firstHop(r('e', { state: 'review', activity: 'review_route_deferred', activityAt: NOW / 1000 - 60 }), NOW).leaf.kind, 'slot')
  assert.deepEqual(m.causeBreakdown([r('b', { state: 'triage', waitingOn: 'triage' }), r('c', { state: 'scheduled', waitingOn: 'ci x' }), r('c2', { state: 'scheduled', waitingOn: 'the clock until 2026-11-21 08:00 UTC' })], NOW).map(x => `${x.count} ${x.label}`), ['2 external', '1 worker slot'])
})

test('rowLeafKind: a row\'s own wait by core\'s labels when the card cannot be read; firstHop reads the card itself', () => {
  const r = (waitingOn, extra = {}) => ({ key: 'kb:x', kind: 'kanban', label: 'x', chip: { id: 't_x', state: 'blocked', waitingOn, connectionId: null, ...extra } })
  const k = (w, text = '') => m.rowLeafKind(r(w), text)
  assert.equal(k('an agent to clear a transient block', 'on an agent to clear a transient block'), 'slot')
  assert.equal(k('an agent to triage it'), 'slot')
  assert.equal(k('nothing named'), 'slot')
  assert.equal(k('triage', 'needs triage'), 'slot')
  assert.equal(k('you'), 'you')
  assert.equal(k('a hidden ask (not visible to you)'), 'you')
  assert.equal(k('an unvalidated ask (needs_input)'), 'you')
  assert.equal(k('a parent card', 'on a parent card'), 'unknown', 'a parent that was not read is not guessed')
  assert.equal(k('a human or shared blocker (block loop)'), 'unknown')
  assert.equal(k('ci https://x/actions/runs/1', 'on CI run 1'), 'external')
  assert.equal(k('the clock until 2026-11-23 08:00 UTC', '10h left'), 'external')
  assert.equal(k('', 'no wake published'), 'unknown')
  assert.equal(m.rowLeafKind(r('ci x', { stale: true }), 'on ci x'), 'unknown')
  // With a route, the card itself is read and its own state decides (here: a dependency to its open parent).
  const detail = { task: { id: 't_x', status: 'blocked', block_kind: 'dependency' }, events: [], links: { parents: ['t_pppppp'] }, link_tasks: [{ id: 't_pppppp', status: 'running' }] }
  const info = (chip, id) => ({ state: 'ok', info: { id, title: 'X', status: 'blocked', session: '', owner: '', board: 'default', hop: m.nextHop(detail, NOW) } })
  assert.deepEqual(m.firstHop(r('a parent card', { connectionId: 'C1', profile: 'default' }), NOW, { info }), { ref: { id: 't_pppppp', status: 'running' } })
  // While it is being read the chain says so and is not counted.
  const loading = m.chainFor(r('a parent card', { connectionId: 'C1', profile: 'default' }), NOW, { info: () => ({ state: 'loading', info: null }) })
  assert.deepEqual([loading.loading, loading.leaf.text], [true, 'reading…'])
  assert.deepEqual(m.causeBreakdown([r('a parent card', { connectionId: 'C1', profile: 'default' })], NOW), [])
})

test('cardInfo: a failed re-read keeps the last answer, marked stale', async () => {
  const chip = { id: 't_root', connectionId: 'C1', profile: 'default', board: 'default' }
  let fail = false
  const read = () => Promise.resolve(fail ? null : { detail: { task: { id: 't_s1s1s1', title: 'S', status: 'running' }, events: [], links: { parents: [] } }, board: 'default' })
  m.cardInfo(chip, 't_s1s1s1', Date.now(), read); await new Promise(r => setTimeout(r, 0))
  assert.equal(m.cardInfo(chip, 't_s1s1s1', Date.now(), read).state, 'ok')
  fail = true
  m.cardInfo(chip, 't_s1s1s1', Date.now() + 60_001, read); await new Promise(r => setTimeout(r, 0))
  const st = m.cardInfo(chip, 't_s1s1s1', Date.now() + 60_002, read)
  assert.deepEqual([st.state, st.info.title, st.info.stale], ['stale', 'S', true])
})

test('review nits: strict row fallback, the full ask check, a stale root, one read for park and chain', async () => {
  const r = (waitingOn, extra = {}) => ({ key: 'kb:x', kind: 'kanban', label: 'x', chip: { id: 't_x', state: 'scheduled', waitingOn, connectionId: null, ...extra } })
  assert.equal(m.rowLeafKind(r('card with no named producer'), 'on card with no named producer'), 'slot', 'core\'s agent-owned "<kind> with no named producer"')
  assert.equal(m.rowLeafKind(r('something odd'), 'on something odd'), 'unknown', 'unrecognised is unknown, never pink')
  assert.equal(m.rowLeafKind(r('external natural GitHub REST reset'), 'on external natural GitHub REST reset'), 'external')
  // validate_human_request: choices and resolved_check too.
  const hr = { action: 'a', why_human: 'w', interaction: 'approval', scope: 's', evidence: 'e' }
  assert.equal(m.validHumanRequest(hr, 'needs_input'), true)
  assert.equal(m.validHumanRequest({ ...hr, choices: [{ label: 'A', value: 'a' }] }), false, 'one choice is not a choice')
  assert.equal(m.validHumanRequest({ ...hr, choices: [{ label: 'A', value: 'a' }, { label: 'B', value: 'a' }] }), false, 'values must be distinct')
  assert.equal(m.validHumanRequest({ ...hr, choices: [{ label: 'A', value: 'a' }, { label: 'B', value: 'b' }] }), true)
  assert.equal(m.validHumanRequest({ ...hr, resolved_check_every: 300 }), false, 'an interval needs a check')
  assert.equal(m.validHumanRequest({ ...hr, resolved_check: 'test -f x', resolved_check_every: 60 }), false, 'interval below 120 s')
  assert.equal(m.validHumanRequest({ ...hr, resolved_check: 'test -f x' }), true)
  assert.equal(m.validHumanRequest({ ...hr, interaction: 'telepathy' }), false)
  assert.equal(m.validHumanRequest(hr, 'dependency'), false)
  // A stale read of the waiting card itself is flagged on its chain.
  const detail = { task: { id: 't_x', status: 'ready' }, events: [], links: { parents: [] } }
  const stale = (chip, id) => ({ state: 'stale', info: { id, title: 'X', status: 'ready', session: '', owner: '', board: 'default', hop: m.nextHop(detail, NOW), stale: true } })
  const ch = m.chainFor(r('', { connectionId: 'C1', profile: 'default', state: 'ready' }), NOW, { info: stale })
  assert.deepEqual([ch.leaf.kind, ch.stale], ['slot', true])
  // One read: the park reader's answer seeds the chain cache (no second request for the same card).
  const calls = []
  globalThis.window = { hermesDesktop: { api: async a => { calls.push(a.path); return { task: { id: 't_ab12ab', title: 'P', status: 'scheduled' }, events: [{ id: 1, kind: 'scheduled', created_at: S, payload: { wake_at: S + 600, awaiting: { kind: 'ci', ref: 'x' } } }], links: { parents: [] } } } } }
  try {
    const chip = { id: 't_ab12ab', connectionId: 'C7', profile: 'default', board: 'default' }
    m.parkFor(chip, Date.now()); await new Promise(res => setTimeout(res, 0)); await new Promise(res => setTimeout(res, 0))
    const ci = m.cardInfo(chip, 't_ab12ab', Date.now())
    assert.deepEqual([ci.state, ci.info.title, calls.length], ['ok', 'P', 1])
  } finally { delete globalThis.window }
})

// [daveey 2026-11-21] "worker slot is not it, show the real reason": t_01a1dff72ec562a6 sat in triage for days as
// "needs triage · worker slot" while core held it on spend_budget_triaged until its chat promotes or archives it.
const stop = { id: 7, kind: 'spend_budget_triaged', created_at: S - 3600, payload: { limit: 'runs', runs: 2, cost_usd: 2.69, reason: 'Spend budget: 2 runs since 2026-11-19T01:54Z without a completion or review hand-off' } }
const chats = { id: 8, kind: 'spend_budget_decision_ready', created_at: S - 3600, payload: { episode: '7', producer: { kind: 'chats' } } }
const held = (events, task = {}) => d({ status: 'triage', block_kind: 'dependency', ...task }, { events, links: { parents: ['t_p'] }, link_tasks: [{ id: 't_p', status: 'done' }] })
const HOLD = { kind: 'hold', text: 'spend budget hit: its chat must re-scope and promote it, or archive it', sub: '2 runs without a hand-off · ~$2.69' }

test('nextHop: a spend-budget stop is a spend hold for its chat, never a worker slot', () => {
  assert.deepEqual(m.nextHop(held([stop, chats]), NOW).leaf, HOLD)
  // Before its decision notice lands, and after a manual status change (core: only promote or archive releases it).
  assert.deepEqual(m.nextHop(held([stop]), NOW).leaf, HOLD)
  for (const status of ['todo', 'ready', 'blocked', 'scheduled']) assert.equal(m.nextHop(held([stop, chats], { status }), NOW).leaf.kind, 'hold', status)
  assert.equal(m.nextHop(held([stop, chats], { status: 'running' }), NOW).leaf.kind, 'running')
  assert.equal(m.nextHop(held([stop, chats], { status: 'archived' }), NOW).leaf.kind, 'done')
  // A string payload (older reads), a cache-read token stop, no cost.
  const tok = { ...stop, payload: JSON.stringify({ limit: 'cache_read_tokens', runs: 1, tokens: { cache_read_tokens: 31_400_000 }, cost_usd: 0 }) }
  assert.equal(m.nextHop(held([tok]), NOW).leaf.sub, '31.4M cache-read tokens without a hand-off')
  // No chat owns the decision: core opened a supervisor card, and the walk goes on to it.
  const sup = { ...chats, payload: JSON.stringify({ episode: '7', producer: { kind: 'supervisor', task_id: 't_01a1f0000000abcd' } }) }
  assert.deepEqual(m.nextHop(held([stop, sup]), NOW), { ref: { id: 't_01a1f0000000abcd', status: '' } })
  // A later promotion or archive ends the hold (same second: the higher id is newer); an earlier one does not.
  const promoted = (at, id = 9) => ({ id, kind: 'promoted_manual', created_at: at, payload: { reason: 're-scoped' } })
  assert.deepEqual(m.nextHop(held([stop, chats, promoted(S - 60)]), NOW).leaf, { kind: 'slot', text: 'needs triage: nobody is working it' })
  assert.equal(m.nextHop(held([stop, chats, promoted(S - 3600)]), NOW).leaf.kind, 'slot')
  assert.equal(m.nextHop(held([stop, chats, promoted(S - 3600, 5)]), NOW).leaf.kind, 'hold')
  assert.equal(m.nextHop(held([promoted(S - 7200), stop, chats]), NOW).leaf.kind, 'hold')
  assert.equal(m.nextHop(held([stop, { id: 10, kind: 'archived', created_at: S - 30, payload: {} }]), NOW).leaf.kind, 'slot')
  // No spend events: the plain triage leaf is unchanged.
  assert.equal(m.spendHold(held([])), null)
})

test('spendHold: same-second marks order like core _event_order (consistent v7 uid ms, then local id)', () => {
  // Real shape (t_01a1dff72ec562a6, 2026-11-19): the stop and its notice share a second; episode = the stop's uid.
  const sec = 1795068991, v7 = ms => `${ms.toString(16).padStart(12, '0').replace(/^(.{8})(.{4})$/, '$1-$2')}-7c4f-9166-db7eda4bdad0`
  const realStop = { id: 1757619, uid: v7(sec * 1000 + 100), kind: 'spend_budget_triaged', created_at: sec, payload: { limit: 'runs', runs: 2, cost_usd: 2.69 } }
  const realNotice = { id: 1757621, uid: v7(sec * 1000 + 101), kind: 'spend_budget_decision_ready', created_at: sec, payload: { episode: realStop.uid, producer: { kind: 'chats' } } }
  assert.equal(m.spendHold(held([realStop, realNotice])).sub, '2 runs without a hand-off · ~$2.69')
  // Verdicts below are core's own (_event_order run on these exact rows, 2026-11-21).
  // A promotion delivered out of order in the same second: its uid ms (minted later) wins over its lower local id.
  const promo = (ms, id) => ({ id, uid: v7(ms), kind: 'promoted_manual', created_at: sec, payload: {} })
  assert.equal(m.spendHold(held([realStop, promo(sec * 1000 + 500, 1757000)])), null)
  assert.notEqual(m.spendHold(held([realStop, promo(sec * 1000 + 50, 1757999)])), null)
  // A backfilled uid far outside its second, or a non-v7 uid, has no millisecond: it sorts at the second's start,
  // before the stop's consistent uid, whatever its local id.
  const v4 = id => ({ id, uid: '9b2f0c1e-4a7d-4c3b-8e5f-0a1b2c3d4e5f', kind: 'promoted_manual', created_at: sec, payload: {} })
  for (const p of [promo((sec + 90) * 1000, 1757000), promo((sec + 90) * 1000, 1757999), v4(1757000), v4(1757999)]) assert.notEqual(m.spendHold(held([realStop, p])), null, p.uid)
  // Neither has a usable millisecond: arrival order (the local id) decides.
  const bare = { ...realStop, uid: null }
  assert.notEqual(m.spendHold(held([bare, promo((sec + 90) * 1000, 1757000)])), null)
  assert.equal(m.spendHold(held([bare, promo((sec + 90) * 1000, 1757999)])), null)
  assert.notEqual(m.spendHold(held([bare, v4(1757000)])), null)
  assert.equal(m.spendHold(held([bare, v4(1757999)])), null)
})

test('chainFor: a card waiting behind a spend-held card ends on the amber spend hold', () => {
  const info = (chip, id) => ({ state: 'ok', info: { id, status: 'triage', title: 'Held', hop: m.nextHop(held([stop, chats]), NOW) } })
  const root = { key: 'kb:t_r', kind: 'kanban', label: 't_r', chip: { id: 't_r', state: 'scheduled', look: 'waiting', waitingOn: 'card t_a0a0a0', connectionId: 'C1', profile: 'default' } }
  const ch = m.chainFor(root, NOW, { info, local: new Map() })
  assert.deepEqual(ch.leaf, HOLD)
  assert.deepEqual([m.LEAF.hold.label, m.LEAF.hold.color], ['spend hold', '#d29922'])
})

test('lane_gate triage reads "Waiting for admission to lane", never worker slot; real slot waits and dependency labels are unchanged', () => {
  const lane = m.nextHop(d({ status: 'triage', block_kind: 'lane_gate' }), NOW)
  assert.equal(lane.leaf.kind, 'lane')
  assert.equal(m.LEAF.lane.label, 'Waiting for admission to lane')
  assert.notEqual(m.LEAF.lane.label, m.LEAF.slot.label)
  // Plain triage, ready (a real capacity wait) and a dependency-blocked triage keep their labels.
  assert.equal(m.nextHop(d({ status: 'triage' }), NOW).leaf.kind, 'slot')
  assert.equal(m.nextHop(d({ status: 'triage', block_kind: 'capability' }), NOW).leaf.kind, 'slot')
  assert.equal(m.nextHop(d({ status: 'ready' }, { queue_note: 'current: 3 cards ahead on worker-a1' }), NOW).leaf.kind, 'slot')
  assert.equal(m.LEAF.slot.label, 'worker slot')
  assert.deepEqual(m.nextHop(d({ status: 'triage', block_kind: 'dependency' }, { links: { parents: ['t_p'] }, link_tasks: [{ id: 't_p', status: 'running' }] }), NOW), { ref: { id: 't_p', status: 'running' } })
  // Row-only fallback (card unreadable) and the Waiting breakdown name it too.
  assert.equal(m.rowLeafKind({ chip: {} }, 'Waiting for admission to lane'), 'lane')
  const r = (id, leafText) => ({ key: `kb:${id}`, kind: 'kanban', label: id, chip: { id, state: 'triage', look: 'waiting', waitingOn: 'triage', connectionId: 'C1', profile: 'default' } })
  const info = (chip, id) => ({ state: 'ok', info: { id, title: id, status: 'triage', session: '', owner: '', board: 'default', hop: m.nextHop(d({ id, status: 'triage', block_kind: 'lane_gate' }), NOW) } })
  const ch = m.chainFor(r('t_gate'), NOW, { info, local: new Map() })
  assert.deepEqual([ch.leaf.kind, m.LEAF[ch.leaf.kind].label], ['lane', 'Waiting for admission to lane'])
})

test('two waiting cards sharing one running prerequisite: waiters are counted once each, the running card is not counted as waiting', () => {
  const run = { chip: { id: 't_a0a0d0', state: 'running', where: 'worker-a1' }, label: 'Stage2 (d)' }
  const local = m.chainLocal([], [run])
  const w = id => ({ key: `kb:${id}`, kind: 'kanban', label: id, chip: { id, state: 'scheduled', look: 'waiting', waitingOn: 'card t_a0a0d0', connectionId: 'C1', profile: 'default' } })
  const rows = [w('t_a0a0e0'), w('t_a0a0f0')]
  const noRead = () => { throw new Error('no remote read') }
  const chains = rows.map(i => m.chainFor(i, NOW, { info: noRead, local }))
  for (const ch of chains) assert.deepEqual([ch.nodes.map(n => [n.id, n.status]), ch.leaf.kind], [[['t_a0a0d0', 'running']], 'running'])
  // Two waiting cards, one running one: the breakdown counts the waiters (2), never a third "running" card.
  assert.deepEqual(m.causeBreakdown(rows, NOW, local).map(x => `${x.count} ${x.label}`), ['2 running'])
  assert.equal(new Set([...rows.map(i => i.chip.id), run.chip.id]).size, 3)
})
