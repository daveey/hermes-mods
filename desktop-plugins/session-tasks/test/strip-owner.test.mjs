import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host=globalThis.stripHost;export const useValue=a=>(globalThis.stripReads.push(a),a?.get())', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=(fn,deps)=>globalThis.stripEffects.push(deps);export const useState=v=>[v,()=>{}];export const jsx=(type,props)=>({type,props});export const jsxs=jsx', shortCircuit: true }
  return next(spec, ctx)
}`))

const atom = value => ({ value, get() { return this.value } })
globalThis.stripReads = []
globalThis.stripEffects = []
globalThis.stripHost = {
  state: {
    focusedSessionId: atom('runtime'), focusedStoredSessionId: atom(null),
    focusedSessionOwner: atom({ connectionId: 'owner-a', profile: 'default' }),
    connectionId: atom('owner-a'), profile: atom('default')
  },
  request: async method => method === 'process.list' ? { processes: [{ session_id: 'p', command: 'example', status: 'running' }] } : { subagents: [] }
}
globalThis.document = { hidden: false, getElementById: () => true, addEventListener() {}, removeEventListener() {} }
globalThis.window = {}
const { default: plugin, refresh, sessionTaskChips, subscribeDecisionChanges } = await import('../plugin.js')

test('decision ownership changes survive producer load order and hot reload', () => {
  const target = new EventTarget()
  let paints = 0
  // Consumer mounts before producer, so there is no __userSteps to subscribe to.
  const stop = subscribeDecisionChanges(() => { paints++ }, target)
  target.dispatchEvent(new Event('hermes:user-steps-changed'))
  target.dispatchEvent(new Event('hermes:user-steps-changed'))
  assert.equal(paints, 2)
  stop()
  target.dispatchEvent(new Event('hermes:user-steps-changed'))
  assert.equal(paints, 2, 'unmount disposes the subscription')
})
let contribution
plugin.register({ register: c => { contribution = c } })
const render = () => contribution.render().type()

test('real Strip hides prior owner snapshot immediately on same-runtime owner switch', async () => {
  await refresh()
  assert.ok(render(), 'same owner displays its process chip')
  stripHost.state.focusedSessionOwner.value = { connectionId: 'owner-b', profile: 'default' }
  assert.equal(render(), null, 'must not display owner-a chips in owner-b chat')
  assert.ok(stripReads.includes(stripHost.state.focusedSessionOwner), 'owner changes must trigger rendering')
  await refresh()
  assert.ok(render(), 'new owner snapshot displays after refresh')
  const deps = stripEffects.at(-1)
  stripHost.state.focusedSessionOwner.value = { connectionId: 'owner-b', profile: 'work' }
  assert.equal(render(), null, 'same connection, different profile hides old chips')
  assert.notDeepEqual(stripEffects.at(-1), deps, 'profile change schedules refresh')
  await refresh()
  assert.ok(render())
  stripHost.state.focusedStoredSessionId.value = 'other-stored'
  assert.equal(render(), null, 'same runtime, different stored chat hides old chips')
  assert.ok(stripReads.includes(stripHost.state.focusedStoredSessionId))
})

test('rendered chips disable unresolved owners and click only their captured route', async () => {
  stripHost.state.focusedStoredSessionId.value = null
  await refresh()
  assert.equal(render().type, 'details', 'agent progress starts collapsed')
  assert.equal(render().props.open, undefined)
  const Chip = render().props.children[1].props.children[0].type
  const row = { id: 't_same', status: 'running', board: 'bb' }
  const [known] = sessionTaskChips([row], { connectionId: 'A', profile: 'work' })
  const [unknown] = sessionTaskChips([{ ...row, profile: null }])
  const disabled = Chip({ chip: unknown, now: Date.now() })
  assert.equal(disabled.type, 'span')
  assert.equal(disabled.props.onClick, undefined)
  const calls = []
  const open = (...args) => calls.push(args)
  open.supportsQualifiedOwners = true
  window.__hermesKanbanOpenTask = open
  window.__taskMentionsOpen = () => assert.fail('bare task mention')
  const button = Chip({ chip: known, now: Date.now() })
  assert.equal(button.type, 'button')
  stripHost.state.focusedSessionOwner.value = { connectionId: 'B', profile: 'default' }
  button.props.onClick()
  assert.deepEqual(calls, [['t_same', 'bb', 'A', 'work']])
})
