// Exercise real RPC projections and rendered consumers with the core SDK and source adapters.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve } from 'node:path'
import { homedir } from 'node:os'
import { execFileSync } from 'node:child_process'
import { loadProjector, loadFixture, caseInput, sdkStubSource } from './execution-support.mjs'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime')
const { renderToStaticMarkup } = require('react-dom/server')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('', { url: 'https://example.test' })
globalThis.window = dom.window
const atom = value => ({ get: () => value })
globalThis.__obsHost = { state: { focusedSessionId: atom('runtime'), focusedStoredSessionId: atom('owner'), busy: atom(false), connectionId: atom('connection'), profile: atom('work'), focusedSessionOwner: atom({ connectionId: 'connection', profile: 'work' }) } }
globalThis.__obsSdk = await loadProjector()
globalThis.__sourceReact = React; globalThis.__sourceJsx = J
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.__sourceReact;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.__sourceJsx;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':${JSON.stringify(sdkStubSource())}};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const tasks = await import('../plugin.js'), panel = await import('../../session-panel/plugin.js')
const skip = !globalThis.__obsSdk && 'set HERMES_CORE_SOURCE for real SDK/source tests'
const render = (chips, now) => {
  const old = Date.now
  Date.now = () => now * 1000
  window.__sessionTasks = { snapshot: { runtime: 'runtime', stored: 'owner', chips } }
  try { return renderToStaticMarkup(React.createElement(panel.Panel)) } finally { Date.now = old }
}
const sourceRecords = () => JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c', `
import json
from hermes_cli.turn_process_execution_observations import process, registry_subagent
class Child:
    session_id = \"child-session-a\"
    _parent_session_id = \"owner\"
    def get_activity_summary(self): return {"last_activity_ts": 1000}
p = process({"id":"proc-a", "pid":71, "host_start_time":81, "observed_at":1000, "source":"process.registry"}, gateway="edge", profile="work", session="owner", now=1001)
s = registry_subagent({"subagent_id":"child-a", "delegation_id":"batch-a", "owner_agent_session_id":"owner", "started_at":900, "status":"running", "agent":Child()}, gateway="edge", profile="work", now=1001)
print(json.dumps([p,s]))
`], { cwd: process.env.HERMES_CORE_SOURCE, encoding: 'utf8' }))

test('real process/subagent adapter records survive RPC chips, both consumers and rendered expiry without a fetch', { skip }, () => {
  const [p, s] = sourceRecords()
  const procs = tasks.processChips([{ session_id: 'proc-a', status: 'running', command: 'test command', execution_observations: [p] }])
  const subs = tasks.subagentChips([{ subagent_id: 'child-a', child_session_id: 'child-session-a', delegation_id: 'batch-a', started_at: 900, status: 'running', goal: 'test child', tool_count: 90, execution_observations: [s] }])
  assert.strictEqual(procs[0].observations[0], p)
  assert.strictEqual(subs[0].observations[0], s)
  // The registry id is never the canonical identity: without the child session id the record cannot be adopted.
  const legacy = tasks.subagentChips([{ subagent_id: 'child-a', started_at: 900, status: 'running', goal: 'old core', execution_observations: [s] }])
  for (const m of [tasks, panel]) assert.equal(m.chipObservation(legacy[0], 1001).state, 'unknown')
  const chips = [...procs, ...subs]
  for (const m of [tasks, panel]) for (const chip of chips) {
    assert.equal(m.chipObservation(chip, 1001).state, 'running')
    assert.equal(m.chipObservation(chip, 1180).state, 'unknown')
    assert.equal(m.chipObservation(chip, 1001).progress, null)
  }
  const html = render(chips, 1001)
  assert.match(html, /In progress · 2/)
  assert.match(html, /process\.registry/)
  const expired = render(chips, 1180)
  assert.doesNotMatch(expired, /In progress ·/)
  assert.match(expired, /2 evidence unknown/)
  assert.match(expired, /source expired/)
  const strip = renderToStaticMarkup(React.createElement(tasks.Chip, { chip: procs[0], now: 1180000 }))
  assert.match(strip, /unknown/)
  assert.doesNotMatch(strip, /session-tasks-pulse/)
})

test('initial mixed-version and partial source stay visible beside running; no tools-as-progress/stuck inference', { skip }, () => {
  const [p] = sourceRecords()
  const good = tasks.processChips([{ session_id: 'proc-a', status: 'running', execution_observations: [p] }])
  const old = tasks.subagentChips([{ subagent_id: 'legacy-child', status: 'running', goal: 'legacy', tool_count: 100 }])
  const chips = [...good, ...old]
  const sorted = panel.classify(chips, { nowMs: 1001000, silence: new Map([[old[0].key, 999999999]]) })
  assert.equal(sorted.active.length, 1)
  assert.equal(sorted.waiting.length, 1)
  assert.equal(sorted.stuck.length, 0)
  assert.equal(panel.annotateEvidence(sorted, 1001000), 1)
  const html = render(chips, 1001)
  assert.match(html, /In progress · 1/)
  assert.match(html, /producer sent no observations/)
  assert.match(html, /1 evidence unknown/)
  assert.doesNotMatch(html, /Nudge agent/)
})

test('source identity fences same work id on other owner/kind/generation; ambiguous executions never choose a winner', { skip }, () => {
  const [p, s] = sourceRecords()
  const chip = tasks.processChips([{ session_id: 'proc-a', status: 'running', execution_observations: [p] }])[0]
  for (const m of [tasks, panel]) {
    for (const patch of [{ gateway: 'other' }, { profile: 'other' }, { generation: 'pid=99:start=99' }]) {
      const foreign = { ...p, identity: { ...p.identity, ...patch } }
      if (!patch.generation) assert.equal(m.chipObservation({ ...chip, gateway: 'edge', profile: 'work', observations: [foreign] }, 1001).state, 'unknown')
      assert.equal(m.chipObservation({ ...chip, observations: [foreign, p] }, 1001).state, 'unknown')
    }
    assert.equal(m.chipObservation({ ...chip, observations: [s] }, 1001).state, 'unknown')
    assert.equal(m.selectView([p, { ...p, identity: { ...p.identity, generation: 'old' }, observed_at: 1001 }], p.identity, 1002).state, 'running')
  }
})

test('shared golden records traverse actual task chips, panel classification and evidence annotation', { skip }, () => {
  const { fixture } = loadFixture()
  for (const t of fixture.cases) {
    if (t.expected_identity || (t.source_class && t.source_class !== 'sidebar')) continue
    const record = caseInput(fixture, t), id = record?.identity
    if (id?.kind !== 'card' || !id.work_id) continue
    const chips = tasks.sessionTaskChips([{ id: id.work_id, status: 'running', board: id.board, profile: id.profile, gateway: id.gateway, execution_observations: [record] }])
    const now = t.now ?? 1001
    // Real render clocks are milliseconds converted to finite epoch seconds. Invalid API-clock cases
    // are covered by the direct SDK parity suite, not coerced into a different input here.
    if (typeof now !== 'number' || !Number.isFinite(now)) continue
    const sorted = panel.classify(chips, { nowMs: now * 1000, evidence: c => panel.chipObservation(c, now) })
    panel.annotateEvidence(sorted, now * 1000)
    const rows = [...sorted.active, ...sorted.stuck, ...sorted.waiting, ...sorted.queued, ...sorted.unasked]
    assert.equal(rows.length, 1, t.id)
    if ('state' in t.expected) assert.equal(rows[0].evidenceState, t.expected.state, t.id)
  }
})

test('an older producer\'s legacy running card chip renders unknown, without the running pulse; a parked wait keeps its look', () => {
  const legacy = { key: 'kb:a', kind: 'kanban', id: 't_a', label: 'legacy card', state: 'running', title: 't_a · running', observations: null, since: 0 }
  const strip = renderToStaticMarkup(React.createElement(tasks.Chip, { chip: legacy, now: 1001000 }))
  assert.doesNotMatch(strip, /session-tasks-pulse/)
  assert.match(strip, /producer sent no observations/)
  assert.match(strip, /last known: running/)
  const current = renderToStaticMarkup(React.createElement(tasks.Chip, { chip: { ...legacy, observations: [] }, now: 1001000 }))
  assert.match(current, /session-tasks-pulse/, 'a current producer keeps its own (card) rendering')
  const parked = renderToStaticMarkup(React.createElement(tasks.Chip, { chip: { ...legacy, state: 'blocked', look: 'needs-you' }, now: 1001000 }))
  assert.doesNotMatch(parked, /producer sent no observations/)
})
