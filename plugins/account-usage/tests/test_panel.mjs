// Synthetic host/API only; loads the actual installed plugin source.
import { readFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
import test from 'node:test'
const source = await readFile(new URL('../../../desktop-plugins/account-usage/plugin.js', import.meta.url), 'utf8')
function atom(value) { const listeners = new Set(); return { get: () => value, set(v) { value = v; listeners.forEach(f => f(v)) }, subscribe(f) { listeners.add(f); f(value); return () => listeners.delete(f) } } }
async function setup() {
  const state = Object.fromEntries(Object.entries({focusedSessionId:'runtime-a', focusedSessionOwner:{connectionId:'local',profile:'default'}, focusedSessionProfile:'default', connectionId:'local',profile:'default',model:'same-model'}).map(([k,v]) => [k,atom(v)]))
  globalThis.__auTestHost = {state}
  globalThis.__auTestAtom = atom
  const plugin = await import('data:text/javascript;base64,' + Buffer.from('const host=globalThis.__auTestHost, atom=globalThis.__auTestAtom;\n' + source.replace(/^import .*$/gm, '') + '\n//' + Math.random()).toString('base64'))
  let row = {session_id:'runtime-a',provider:'anthropic',model:'same-model',revision:0,account_id:null,state:'none'}
  const calls=[]; const ctx={onDispose(){}, async rest(path,opts={}) { calls.push({path,...opts}); if(opts.method==='PUT') row={...row, revision:row.revision+1,account_id:opts.body.account_id,state:'pending'}; if(opts.method==='DELETE') row={...row,revision:row.revision+1,account_id:null,state:'cancelled'}; return {...row} }}
  return {plugin,state,ctx,calls,setRow:r=>row=r}
}
test('legacy gauge has no selectable account identity; stale readback cannot confirm a write', async () => {
 const {plugin,ctx}=await setup()
 const legacy=plugin.summarize({providers:[{slug:'anthropic',usage:{windows:[]}}]})[0].accounts[0]
 assert.equal(legacy.selectable,false)
 const s=plugin.createSelectionStore(ctx);await s.refresh();await s.select('anthropic',{id:'B',state:'ready'})
 const stale=s.$state.get().selection
 ctx.rest=async()=>stale
 await s.select('anthropic',{id:'B',state:'ready'})
 assert.match(s.$state.get().error,/confirmation|changed/i)
})
test('CAS and malformed/wrong-session readbacks never show successful pending state', async () => {
 const {plugin,ctx}=await setup();const s=plugin.createSelectionStore(ctx);await s.refresh()
 ctx.rest=async()=>{throw new Error('409 Selection changed in another tab')}
 await s.select('anthropic',{id:'B',state:'ready'})
 assert.match(s.$state.get().error,/409/);assert.equal(s.$state.get().selection,null)
 ctx.rest=async()=>({session_id:'other',revision:4,provider:'anthropic',model:'same-model',account_id:'B',state:'pending'})
 await s.refresh();assert.equal(s.$state.get().selection,null);assert.match(s.$state.get().error,/Invalid/)
})
test('profile/model changes invalidate intent; focus is read in click handler', async () => {
 const {plugin,ctx,state,calls}=await setup();const s=plugin.createSelectionStore(ctx);await s.refresh()
 state.focusedSessionId.set('runtime-b');await s.select('anthropic',{id:'B',state:'ready'})
 assert.equal(calls.filter(x=>x.method).length,0)
 state.focusedSessionId.set('runtime-a');await s.refresh();state.model.set('different');assert.equal(s.$state.get().selection,null)
 state.focusedSessionOwner.set({connectionId:'local',profile:'other'});await s.refresh();assert.match(s.$state.get().error,/profile/)
})
test('inventory responses and last-used data cannot cross ambient profiles', async () => {
 const {plugin,ctx,state}=await setup(); assert.equal(typeof plugin.createStore,'function')
 let resolve; globalThis.__auTestHost.request=()=>new Promise(r=>resolve=r)
 ctx.rest=async()=>({anthropic:{label:'Old profile account',at:1}})
 const s=plugin.createStore(ctx);const p=s.refresh()
 state.profile.set('other');resolve({providers:[{slug:'anthropic',usage:{accounts:[{id:'old',label:'Old profile account',state:'ready'}]}}]});await p
 assert.deepEqual(s.$state.get().groups,[])
})
test('unavailable and wrong-provider rows visibly refuse without PUT or cooldown reset', async () => {
 const {plugin,ctx,calls}=await setup(); const s=plugin.createSelectionStore(ctx); await s.refresh()
 await s.select('anthropic',{id:'B',state:'limited'})
 assert.match(s.$state.get().error,/unavailable|limited|cooldown/i)
 await s.select('openai-codex',{id:'B',state:'ready'})
 assert.match(s.$state.get().error,/provider/i)
 assert.equal(calls.filter(x=>x.method).length,0)
})
test('focus/owner change during GET discards response, including away-and-back', async () => {
 const {plugin,ctx,state}=await setup(); const s=plugin.createSelectionStore(ctx)
 let resolve; ctx.rest=()=>new Promise(r=>resolve=r); const p=s.refresh()
 state.focusedSessionId.set('runtime-b'); state.focusedSessionId.set('runtime-a')
 resolve({session_id:'runtime-a',provider:'anthropic',model:'same-model',revision:5,account_id:'B',state:'pending'}); await p
 assert.equal(s.$state.get().selection,null)
 state.focusedSessionOwner.set({connectionId:'remote',profile:'default'})
 await s.select('anthropic',{id:'B',state:'ready'})
 assert.match(s.$state.get().error,/connection|owner|gateway/i)
})
test('pending mutation locks replace/cancel and never refetches another tab', async () => {
 const {plugin,ctx,state,calls}=await setup(); const s=plugin.createSelectionStore(ctx); await s.refresh()
 const orig=ctx.rest; let resolve; ctx.rest=(path,opts)=>opts?.method==='PUT' ? (calls.push({path,...opts}), new Promise(r=>resolve=r)) : orig(path,opts)
 const p=s.select('anthropic',{id:'B',state:'ready'}); const cancel=s.cancel(); const replace=s.select('anthropic',{id:'C',state:'ready'})
 assert.equal(calls.filter(x=>x.method).length,1)
 state.focusedSessionId.set('runtime-b'); resolve({}); await p
 assert.equal(s.$state.get().selection,null)
 assert.ok(!calls.some(x=>x.path.includes('runtime-b')))
})
test('click queues inventory identity for runtime chat; readback then cancel uses CAS', async () => {
 const {plugin,ctx,calls}=await setup()
 assert.equal(typeof plugin.createSelectionStore,'function','selection controller is missing')
 const s=plugin.createSelectionStore(ctx); await s.refresh()
 await s.select('anthropic',{id:'inventory-account-B',label:'duplicate',state:'ready'})
 const put=calls.find(x=>x.method==='PUT')
 assert.deepEqual(put.body,{session_id:'runtime-a',provider:'anthropic',account_id:'inventory-account-B',expected_revision:0})
 assert.equal(s.$state.get().selection.state,'pending')
 assert.equal(calls.at(-1).method,undefined,'must GET read back exact target')
 await s.cancel()
 assert.match(calls.find(x=>x.method==='DELETE').path,/expected_revision=1/)
 assert.equal(s.$state.get().selection.state,'cancelled')
})
