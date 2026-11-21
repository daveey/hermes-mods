import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
 if (spec === '@hermes/plugin-sdk') return {url:'data:text/javascript,export const COMPOSER_AREAS={};export const host={};export const Codicon=()=>null;export const DisclosureCaret=()=>null;export const useValue=()=>null',shortCircuit:true};
 if (spec === 'react' || spec === 'react/jsx-runtime') return {url:'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const useRef=v=>({current:v});export const jsx=()=>null;export const jsxs=()=>null',shortCircuit:true};
 return next(spec,ctx)
}`))
const m = await import('../plugin.js')
const now = Date.parse('2026-11-21T10:00:00Z'), sec = now / 1000
const detail = kind => ({task:{status:'scheduled', wait_episode:{kind,since:sec-720},wake_check:{cmd:'probe',next_at:sec+120}},events:[{id:1,kind:'scheduled',created_at:sec-240,payload:{awaiting:{kind,ref:'https://example.com/pull/123'},wake_at:sec+3600}}]})
for (const kind of ['ci','git-ci','deploy']) test(`${kind} row labels elapsed beside reason, separate next check`, () => {
 const chip = {state:'scheduled', waitingOn:`${kind} PR #123`,waitingSince:sec-240}
 const item = {kind:'kanban',chip}
 assert.equal(m.needsPark(item), true)
 const d = detail(kind), park = m.parkFromDetail(d)
 const first = m.rowWait(item,now,{state:'ok',park})
 assert.match(first.reason.join(''),new RegExp(`${kind !== 'deploy' ? 'CI' : 'Deploy'} · waiting 12m`))
 assert.match(first.sub,/next check in 2m/)
 assert.doesNotMatch(first.sub,/left|ETA/)
 assert.equal(first.age,'')
 assert.match(m.rowWait(item,now+60_000,{state:'ok',park}).reason.join(''),/waiting 13m/)
 assert.deepEqual(m.rowWait(item,now,{state:'ok',park:m.parkFromDetail(JSON.parse(JSON.stringify(d)))}), first)
 assert.match(m.nextHop(d,now).leaf.text,/waiting 12m/)
})
test('unknown/invalid start never borrows generic wait age or render time', () => {
 for (const since of [undefined,null,0,NaN,Infinity,sec+10]) {
  const d = detail('deploy'); d.task.wait_episode.since=since
  const park=m.parkFromDetail(d)
  const r=m.rowWait({kind:'kanban',chip:{state:'scheduled',waitingOn:'deploy PR #123',waitingSince:sec-5000}},now,{state:'ok',park})
  assert.match(r.reason.join(''),/Deploy · waiting \(start unknown\)/)
  assert.equal(r.age,'')
 }
})
test('old backend is truthfully unknown and check deadline remains distinct',()=>{
 const d=detail('ci'); delete d.task.wait_episode
 const r=m.rowWait({kind:'kanban',chip:{state:'scheduled'}},now,{state:'ok',park:m.parkFromDetail(d)})
 assert.match(r.reason.join(''),/CI · waiting \(start unknown\)/)
 assert.match(r.sub,/next check in 2m/)
})
test('fresh contradictory producer supersedes older chip CI label and episode',()=>{
 for(const kind of ['card','clock','human','external']) {
  const d=detail('ci');d.events[0].payload.awaiting={kind,ref:kind==='card'?'t_12345678':'different producer'}
  const park=m.parkFromDetail(d)
  assert.equal(m.externalWait(park,now,'ci PR #123'),null)
  assert.doesNotMatch(m.rowWait({kind:'kanban',chip:{state:'scheduled',waitingOn:'ci PR #123'}},now,{state:'ok',park}).reason.join(''),/CI|PR #123/)
 }
})
test('next-check overdue and backstop never become deployment ETA',()=>{
 const d=detail('deploy'); d.task.wake_check.next_at=sec-60
 const r=m.nextHop(d,now).leaf
 assert.match(r.text,/Deploy · waiting 12m/)
 assert.match(r.sub,/next check overdue by 1m/)
 assert.doesNotMatch(r.sub,/left/)
})
