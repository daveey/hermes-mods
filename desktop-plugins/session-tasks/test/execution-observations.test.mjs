// Shared golden parity for BOTH public consumers (session-tasks and session-panel) of the SDK's execution projector.
// Expected verdicts come only from the frozen core fixture (HERMES_CORE_SOURCE, else the hash-pinned public copy);
// the projector is the core's own module. See execution-support.mjs. Projector-backed tests skip without a core.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'
import { resolve } from 'node:path'
import { createRequire } from 'node:module'
import { homedir } from 'node:os'
import { PUBLIC_FIXTURE_SHA256, caseInput, clone, itemInput, loadFixture, loadProjector, sdkStubSource, sha256 } from './execution-support.mjs'
import { readFileSync } from 'node:fs'
import { PUBLIC_FIXTURE } from './execution-support.mjs'

const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react')
const projector = await loadProjector()
globalThis.__obsSdk = projector || undefined
globalThis.__obsReact = React
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.__obsReact;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'export const jsx=()=>null,jsxs=()=>null','@hermes/plugin-sdk':${JSON.stringify(sdkStubSource())}};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const tasks = await import('../plugin.js')
const panel = await import('../../session-panel/plugin.js')
const consumers = { 'session-tasks': tasks, 'session-panel': panel }
const { fixture, fromCore, sha256: fixtureSha } = loadFixture()
const skip = projector ? false : 'no core checkout: set HERMES_CORE_SOURCE to run projector-backed parity'
const cls = t => t.source_class ?? 'sidebar'

test('public fixture copy is byte-identical to the pinned hash, and to the core fixture when one is given', () => {
  assert.equal(sha256(readFileSync(PUBLIC_FIXTURE)), PUBLIC_FIXTURE_SHA256)
  assert.equal(fixtureSha, PUBLIC_FIXTURE_SHA256, fromCore ? 'core fixture drifted from the public copy: update the copy and pin' : 'public copy changed')
})

// Map the SDK-expected verdict onto the consumer view; nothing here decides a verdict.
function assertView(view, expected, now, label) {
  if ('state' in expected) assert.equal(view.state, expected.state, `${label} state`)
  if ('freshness' in expected) assert.equal(view.freshness, expected.freshness, `${label} freshness`)
  if ('unknown_reason' in expected) assert.equal(view.reason, expected.unknown_reason, `${label} reason`)
  if ('last_known_state' in expected) assert.equal(view.lastKnown, expected.last_known_state, `${label} lastKnown`)
  if ('last_progress' in expected) {
    if (expected.last_progress === null) assert.equal(view.progress, null, `${label} progress`)
    else assert.ok(Math.abs(view.progress.ageSec - (now - expected.last_progress)) < 1e-6, `${label} progress age`)
  }
}

for (const [name, m] of Object.entries(consumers)) {
  test(`${name}: every project case matches the shared fixture`, { skip }, () => {
    assert.ok(fixture.cases.length > 150)
    for (const t of fixture.cases) {
      const now = t.now ?? 1001
      const view = m.projectView(caseInput(fixture, t), now, cls(t), undefined, t.expected_identity)
      assertView(view, t.expected, now, `${name}/${t.id}`)
    }
  })

  test(`${name}: every select case matches the shared fixture (identity fencing, retained asks and receipts)`, { skip }, () => {
    for (const t of fixture.select_cases) {
      const items = 'values_raw' in t ? clone(t.values_raw) : t.items.map(i => itemInput(fixture, i))
      const view = m.selectView(items, t.identity ?? fixture.base.identity, t.now, cls(t))
      assertView(view, t.expected, t.now, `${name}/select:${t.id}`)
      if (t.outstanding !== undefined) assert.equal(view.asks, t.outstanding, `${name}/select:${t.id} outstanding`)
      if (t.expected.historical_delivery_receipts) assert.equal(view.receipts, t.expected.historical_delivery_receipts.length)
    }
  })

  test(`${name}: every summarize case matches the shared fixture`, { skip }, () => {
    for (const t of fixture.summarize_cases) {
      const items = 'values_raw' in t ? clone(t.values_raw) : t.items.map(i => itemInput(fixture, i))
      const s = m.summaryView(items, t.now, cls(t))
      // The fixture is a subset spec: assert only the keys a case states.
      if ('counts' in t.expected) assert.deepEqual(s.counts, t.expected.counts, `${name}/${t.id} counts`)
      if ('uncertain' in t.expected) assert.equal(s.uncertain, t.expected.uncertain, `${name}/${t.id} uncertain`)
      if ('outstanding_human_requests' in t.expected) assert.equal(s.outstanding, t.expected.outstanding_human_requests, `${name}/${t.id} outstanding`)
      if (t.expected.states) assert.deepEqual(s.states, t.expected.states, `${name}/${t.id} states`)
    }
  })

  test(`${name}: an old host without the projector shows explicit unknown, never a zero`, () => {
    for (const sdk of [{}, { projectExecutionObservation() {} }]) {
      assert.equal(m.observationSdk(sdk), null)
      const v = m.projectView(clone(fixture.base), 1001, 'sidebar', m.observationSdk(sdk))
      assert.deepEqual([v.state, v.freshness, v.reason, v.sourced], ['unknown', 'unknown', 'sdk_projector_unavailable', false])
      const s = m.summaryView([clone(fixture.base)], 1001, 'sidebar', m.observationSdk(sdk))
      assert.deepEqual([s.counts, s.uncertain, s.outstanding], [null, true, null])
      assert.match(m.observationLine(v, 1001), /^unknown \(sdk projector unavailable\) · progress unknown$/)
    }
  })
}

test('both consumers produce identical views for every fixture record', { skip }, () => {
  for (const t of fixture.cases) {
    const a = tasks.projectView(caseInput(fixture, t), t.now ?? 1001, cls(t), undefined, t.expected_identity)
    const b = panel.projectView(caseInput(fixture, t), t.now ?? 1001, cls(t), undefined, t.expected_identity)
    assert.deepEqual(a, b, t.id)
    assert.equal(tasks.observationLine(a, t.now ?? 1001), panel.observationLine(b, t.now ?? 1001), t.id)
  }
})

// ── chip level: what each consumer shows for the card a producer sent ──────────────────────────────────────
const chipFor = (records, extra = {}) => ({ id: 'task-a', board: 'tasks', profile: 'work', gateway: 'edge', kind: 'kanban', observations: records, ...extra })
const rec = patch => caseInput(fixture, patch)

for (const [name, m] of Object.entries(consumers)) {
  test(`${name}: card evidence expires on the clock alone; cached zero/terminal expires too and the receipt stays historical`, { skip }, () => {
    const running = chipFor([rec({})])
    assert.equal(m.chipObservation(running, 1100).state, 'running')
    const expired = m.chipObservation(running, 1180) // same cached chip, no new fetch: only the clock moved
    assert.deepEqual([expired.state, expired.freshness, expired.reason, expired.lastKnown], ['unknown', 'stale', 'source_expired', 'running'])
    const receipt = { verified: true, disposition: 'delivered', evidence_ref: 'receipt:1', identity: fixture.base.identity }
    const terminal = chipFor([rec({ set: { state: 'done', delivery_receipt: receipt } })])
    assert.equal(m.chipObservation(terminal, 1100).state, 'done')
    const after = m.chipObservation(terminal, 1200)
    assert.deepEqual([after.state, after.lastKnown, after.receipts], ['unknown', 'done', 1])
    assert.match(m.chipEvidenceLine(terminal, 1200), /^unknown \(source expired\) · last known done · observed 3 min ago.* · 1 historical completion receipt · /)
  })

  test(`${name}: a cached (stale) chip is never current, whatever its record says`, { skip }, () => {
    const v = m.chipObservation(chipFor([rec({})], { stale: true }), 1001)
    assert.deepEqual([v.state, v.freshness, v.reason, v.lastKnown], ['unknown', 'stale', 'cached_after_failed_read', 'running'])
  })

  test(`${name}: absent records from an older producer are explicit unknown; absent matching row is unknown too`, { skip }, () => {
    for (const records of [null, undefined, 'nope']) {
      const v = m.chipObservation(chipFor(records), 1001)
      assert.deepEqual([v.state, v.reason, v.sourced], ['unknown', 'producer_sent_no_observations', false])
    }
    const other = m.chipObservation(chipFor([rec({ set: { 'identity.work_id': 'task-b' } })]), 1001)
    assert.deepEqual([other.state, other.reason], ['unknown', 'no_observation_for_card'])
    const empty = m.chipObservation(chipFor([]), 1001)
    assert.deepEqual([empty.state, empty.reason], ['unknown', 'no_observation_for_card'])
  })

  test(`${name}: same card id on another gateway/profile/board never aliases; two runs of one card stay unknown`, { skip }, () => {
    for (const set of [{ 'identity.gateway': 'other' }, { 'identity.profile': 'other' }, { 'identity.board': 'other' }]) {
      assert.equal(m.chipObservation(chipFor([rec({ set })]), 1001).reason, 'no_observation_for_card')
    }
    const two = m.chipObservation(chipFor([rec({}), rec({ set: { 'identity.run_id': 8, observed_at: 1001 } })]), 1002)
    assert.deepEqual([two.state, two.reason], ['unknown', 'ambiguous_execution_identity'])
    // The run-8 record can never overwrite a run-7 read selected by exact identity.
    const sel = m.selectView([rec({}), rec({ set: { 'identity.run_id': 8, observed_at: 1001, state: 'queued' } })], fixture.base.identity, 1002)
    assert.equal(sel.state, 'running')
  })

  test(`${name}: heartbeat activity is not progress; explicit progress keeps its evidence reference`, { skip }, () => {
    const hb = m.chipObservation(chipFor([rec({ set: { last_activity: 1000, last_activity_kind: 'heartbeat' } })]), 1001)
    assert.equal(hb.progress, null)
    assert.deepEqual([hb.activity.kind], ['heartbeat'])
    assert.match(m.observationLine(hb, 1001), /progress unknown/)
    const p = rec({ set: { last_progress: 999, last_progress_kind: 'test_result', last_progress_ref: 'test:1', progress_evidence: { successful: true, observed_at: 999, evidence_ref: 'test:1' } } })
    const v = m.chipObservation(chipFor([p]), 1001)
    assert.deepEqual([v.progress.kind, v.progress.ref], ['test_result', 'test:1'])
    assert.match(m.observationLine(v, 1001), /progress test_result 2 s ago \(test:1\)/)
  })

  test(`${name}: waits show source, owner and condition; an incomplete wait is unknown not guessed`, { skip }, () => {
    const wait = { kind: 'dependency', target: { kind: 'card', gateway: 'edge', profile: 'work', board: 'tasks', work_id: 'task-p' }, predicate: 'parent_done', since: 900, due_at: null, owner: { gateway: 'edge', profile: 'work' } }
    const ok = m.chipObservation(chipFor([rec({ set: { state: 'waiting', wait } })]), 1001)
    assert.equal(ok.state, 'waiting')
    assert.match(m.observationLine(ok, 1001), /waiting: dependency parent_done on edge\/work\/tasks\/task-p · owner edge\/work · 1 min/)
    const bad = m.chipObservation(chipFor([rec({ set: { state: 'waiting', wait: { kind: 'dependency' } } })]), 1001)
    assert.deepEqual([bad.state, bad.reason], ['unknown', 'incomplete_wait_condition'])
  })

  test(`${name}: capacity diagnostics stay separate and never become a work state`, { skip }, () => {
    const capacity = { version: 1, source: 'kanban.dispatch', state: 'running', reasons: [{ code: 'capacity.provider_limit', native_code: 'provider_busy', phase: 'post_start', evidence_ref: 'event:41' }] }
    const only = chipFor([capacity])
    assert.deepEqual(m.chipCapacity(only), ['capacity.provider_limit'])
    assert.equal(m.chipObservation(only, 1001).sourced, false, 'a capacity record is not work evidence')
    const both = chipFor([capacity, rec({})])
    assert.equal(m.chipObservation(both, 1001).state, 'running')
    assert.match(m.chipEvidenceLine(both, 1001), /capacity diagnostic \(not work state\): capacity\.provider_limit$/)
  })

  test(`${name}: needs_user observations never grant an ask; only the legacy human_request does`, { skip }, () => {
    const request = { action: 'Choose', why_human: 'Preference', interaction: 'reply', scope: 'task', evidence: 'request:1' }
    const wait = { kind: 'human', target: { kind: 'request', gateway: 'edge', profile: 'work', work_id: 'r1' }, predicate: 'request_answered', since: 990, due_at: null, owner: { gateway: 'edge', profile: 'work' } }
    const v = m.chipObservation(chipFor([rec({ set: { state: 'needs_user', human_request: request, wait } })]), 1001)
    assert.equal(v.state, 'needs_user')
    assert.equal(v.asks, 1)
  })
}
