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

// Sanitized production shape: creation emitted no kind or human action, only initial_status.
// Coordination prose and failed unattended authentication are not a request for authorization.
test('initial blocked installation is operational, not a For you approval', () => {
  const task = { id: 't_install', board: 'ops', status: 'blocked', assignee: 'operator', title: 'Authorize bounded probe installation' }
  const details = new Map([['ops:t_install', {
    task: { ...task, body: 'Origin-owned privileged-install decision prerequisite for ops/t_parent.\nChecked: sudo -n requires password; existing helper cannot install this root target.\nNeeds supported privileged authorization/interactive administration route, not alternate executor bypass. Approval alone is not installation.' },
    events: [{ id: 1, kind: 'blocked', created_at: 1000, payload: { reason: 'initial_status', status: 'blocked', actor: 'default' } }]
  }]])
  assert.deepEqual(m.blockedItems([task], details), [])
  const issues = m.agentIssues([task], details)
  assert.equal(issues.length, 1)
  assert.equal(issues[0].noAction, true)
  assert.equal(issues[0].owner, 'operator')
  assert.equal(task.status, 'blocked') // Classification must not authorize or unblock execution.
})

test('triage escalations retain explicit asks without prose inference', () => {
  const task = { id: 't_triage', board: 'default', status: 'triage', assignee: 'operator' }
  const payload = { kind: 'needs_input', reason: 'No human action needed was the previous diagnosis.', human_request: request }
  const events = [{ id: 1, kind: 'block_loop_detected', payload }]
  const details = new Map([['default:t_triage', { task, events }]])
  assert.equal(m.blockedItems([task], details).length, 1)
  assert.deepEqual(m.agentIssues([task], details), [])
  assert.equal(m.needsHuman(m.latestBlock(events)), true)
  const unknown = new Map([['default:t_triage', { task, events: [] }]])
  assert.equal(m.agentIssues([task], unknown)[0].owner, 'operator')
})

const request = { action: 'Approve the bounded installation.', why_human: 'The organization requires owner authorization for this installation.', interaction: 'approval', scope: 'Install only the reviewed probe file.', evidence: 'Policy inspection returned owner approval required.' }

test('latestBlock propagates explicit evidence and clears it on unblock or unrelated reblock', () => {
  const first = { id: 1, kind: 'blocked', payload: JSON.stringify({ reason: 'initial_status', human_request: request }) }
  assert.deepEqual(m.latestBlock([first]).human_request, request)
  assert.equal(m.needsHuman(m.latestBlock([first])), true)
  assert.equal(m.latestBlock([first, { id: 2, kind: 'unblocked' }]), null)
  const events = [first, { id: 2, kind: 'unblocked' }, { id: 3, kind: 'blocked', payload: { kind: 'capability', reason: 'sudo requires authentication' } }]
  assert.equal(m.latestBlock(events).human_request, undefined)
  assert.equal(m.blockState(events).origin, 'agent')
  for (const key of ['action', 'why_human', 'scope', 'evidence']) {
    for (const value of ['  ', null, 42, [], {}]) {
      assert.equal(m.needsHuman({ human_request: { ...request, [key]: value } }), false)
    }
  }
  for (const interaction of ['', 'execute', 'Approval', null]) assert.equal(m.needsHuman({ human_request: { ...request, interaction } }), false)
})

test('capability failures and absent events remain operational; typed actionable requests survive', () => {
  const task = { id: 't_action', board: 'default', status: 'blocked', assignee: 'operator' }
  for (const payload of [null, { kind: 'capability', reason: 'sudo: a password is required' }, { kind: 'needs_input', reason: 'Approve installation.' }]) {
    const events = payload ? [{ id: 1, kind: 'blocked', payload }] : []
    const details = new Map([['default:t_action', { task, events }]])
    assert.deepEqual(m.blockedItems([task], details), [])
    assert.equal(m.agentIssues([task], details).length, 1)
  }
  const details = new Map([['default:t_action', { task, events: [{ id: 1, kind: 'blocked', payload: { kind: 'needs_input', reason: request.action, human_request: request } }] }]])
  assert.equal(m.blockedItems([task], details).length, 1)
  assert.deepEqual(m.agentIssues([task], details), [])
  for (const key of Object.keys(request)) {
    assert.equal(m.needsHuman({ kind: 'needs_input', human_request: { ...request, [key]: '' } }), false)
  }
  for (const interaction of ['approval', 'reply', 'user_terminal', 'physical', 'external']) {
    assert.equal(m.needsHuman({ kind: 'capability', human_request: { ...request, interaction } }), true)
  }
  for (const kind of ['dependency', 'transient', 'time']) {
    assert.equal(m.needsHuman({ kind, human_request: request }), false)
  }
})
