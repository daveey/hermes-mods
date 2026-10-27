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

// ── limited-account causes: red quota vs amber cooldown (sanitized backend fields) ──
const SOON = new Date(Date.now() + 23 * 60_000).toISOString()
const LATER = new Date(Date.now() + 3 * 86400_000).toISOString()
function causePayload() {
 return {providers:[{slug:'anthropic',usage:{accounts:[
  {id:'ok',label:'ok',state:'ready',windows:[{label:'Session',used_percent:20,scope:'account'}],model_cooldowns:[]},
  {id:'cool',label:'cool',state:'limited',limit_kind:'cooldown',reason:'rate_limit',cooldown_until:SOON,resets_at:SOON,windows:[{label:'Session',used_percent:40,scope:'account'}]},
  {id:'spent',label:'spent',state:'limited',limit_kind:'quota',reason:'quota_window',resets_at:LATER,cooldown_until:null,windows:[{label:'Session',used_percent:100,scope:'account'}]},
  {id:'old',label:'old',state:'limited',resets_at:null,windows:[]},
  {id:'part',label:'part',state:'ready',windows:[{label:'Session',used_percent:10,scope:'account'}],model_cooldowns:[{model:'claude-sonnet-5-5',until:SOON}]},
 ]}},{slug:'openai-codex',usage:{accounts:[
  {id:'dc',label:'device_code',state:'limited',limit_kind:'quota',reason:'usage_limit',cooldown_until:LATER,resets_at:LATER,windows:[]},
  {id:'c2',label:'c2',state:'ready',windows:[{label:'Session',used_percent:5,scope:'account'}]},
 ]}}]}
}
test('cooldown is amber, quota is red, a legacy limited row without limit_kind stays red', async () => {
 const {plugin}=await setup()
 const [claude, codex]=plugin.summarize(causePayload())
 const tones=Object.fromEntries(claude.accounts.map(a=>[a.id,plugin.tone(a)]))
 assert.deepEqual(tones,{ok:'ready',cool:'cooldown',spent:'limited',old:'limited',part:'ready'})
 assert.equal(plugin.tone(codex.accounts[0]),'limited','Codex usage_limit_reached is a real exhaustion')
 assert.equal(claude.ready,2); assert.equal(claude.cooling,1); assert.equal(claude.limited,2); assert.equal(claude.total,5)
 assert.equal(claude.nextBack,SOON,'earliest limited account (cooldown) is next back')
})
test('describe shows the sanitized cause and expiry, and model-only cooldowns', async () => {
 const {plugin}=await setup()
 const [claude, codex]=plugin.summarize(causePayload())
 const byId=Object.fromEntries(claude.accounts.map(a=>[a.id,a]))
 assert.match(plugin.describe(byId.cool),/^cooling down · rate limited · back 2[23]m$/)
 assert.match(plugin.describe(byId.spent),/^out of quota · quota used up · back /)
 assert.equal(plugin.describe(byId.old),'out of quota')
 assert.match(plugin.describe(byId.part),/^claude-sonnet-5-5 cooling 2[23]m$/)
 assert.equal(plugin.describe(byId.ok),'')
 assert.match(plugin.describe(codex.accounts[0]),/^out of quota · usage limit reached · back /)
})
test('cooling-down and out-of-quota rows stay unselectable: visible refusal, no PUT', async () => {
 const {plugin,ctx,calls}=await setup()
 const [claude]=plugin.summarize(causePayload())
 const s=plugin.createSelectionStore(ctx); await s.refresh()
 for (const id of ['cool','spent','old']) {
  await s.select('anthropic',claude.accounts.find(a=>a.id===id))
  assert.match(s.$state.get().error,/Cooldowns are not reset/)
 }
 assert.match(s.$state.get().error,/limited: out of quota/)
 await s.select('anthropic',claude.accounts.find(a=>a.id==='cool'))
 assert.match(s.$state.get().error,/cooling down · rate limited/)
 assert.equal(calls.filter(c=>c.method==='PUT').length,0)
 // A model-only cooldown leaves the account ready, hence selectable.
 await s.select('anthropic',claude.accounts.find(a=>a.id==='part'))
 assert.equal(calls.filter(c=>c.method==='PUT').length,1)
})

test('needsReconnect only flags dead or auth-benched accounts', async () => {
 const {plugin}=await setup()
 assert.equal(plugin.needsReconnect({state:'unavailable',reason:'auth'}),true)
 assert.equal(plugin.needsReconnect({state:'limited',limit_kind:'cooldown',reason:'auth'}),true)
 for (const a of [{state:'ready'},{state:'limited',limit_kind:'quota',reason:'usage_limit'},{state:'limited',limit_kind:'cooldown',reason:'rate_limit'},{state:'unknown'}]) assert.equal(plugin.needsReconnect(a),false)
})
test('reconnect POSTs the account, refreshes on success, offers sign-in when the grant is dead', async () => {
 const {plugin}=await setup(); const calls=[]; let refreshed=0; let reply={ok:true,renewed:1}
 const ctx={onDispose(){},async rest(path,opts){calls.push({path,...opts});if(reply instanceof Error)throw reply;return reply}}
 const r=plugin.createReconnectStore(ctx,()=>refreshed++)
 await r.reconnect('openai-codex',{id:'acct-1'})
 assert.deepEqual(calls[0],{path:'/reconnect',method:'POST',body:{provider:'openai-codex',account_id:'acct-1'}})
 assert.equal(refreshed,1); assert.equal(r.$state.get()[r.key('openai-codex','acct-1')].done,true)
 reply={ok:false,needs_sign_in:true,sign_in_command:'hermes auth add anthropic --type oauth --label x'}
 await r.reconnect('anthropic',{id:'acct-2'})
 const st=r.$state.get()[r.key('anthropic','acct-2')]
 assert.equal(st.needsSignIn,true); assert.equal(st.command,'hermes auth add anthropic --type oauth --label x'); assert.equal(refreshed,1)
 reply=new Error('503 pool unavailable'); await r.reconnect('anthropic',{id:'acct-3'})
 assert.match(r.$state.get()[r.key('anthropic','acct-3')].message,/Reconnect failed: 503/)
})
test('fmtReset rounds to the minute so a reset at 6:59:59.6 reads 7 AM, not 6 AM', async () => {
 const {plugin}=await setup()
 const now=Date.parse('2026-10-19T21:00:00Z')
 const fmt=t=>new Date(t).toLocaleString(undefined,{weekday:'short',hour:'numeric'})
 assert.equal(plugin.fmtReset('2026-10-25T13:59:59.592286+00:00',now), fmt(Date.parse('2026-10-25T14:00:00Z')))
 assert.equal(plugin.fmtReset('2026-10-25T14:00:00.022215+00:00',now), fmt(Date.parse('2026-10-25T14:00:00Z')))
 assert.equal(plugin.fmtReset('2026-10-24T15:29:00Z',now), fmt(Date.parse('2026-10-24T15:29:00Z')))
})

test('xai-oauth status-only row: blocked reads billing (red), ok reads ready, never quota_window', async () => {
 const {plugin}=await setup()
 const win=p=>({providers:[{slug:'xai-oauth',name:'Grok',usage:{windows:[{label:'API access',used_percent:p,resets_at:null,scope:'account'}]}}]})
 const [blocked]=plugin.summarize(win(100))
 assert.equal(blocked.slug,'xai-oauth'); assert.equal(blocked.name,'Grok'); assert.equal(blocked.statusOnly,true)
 assert.equal(blocked.accounts[0].state,'limited'); assert.equal(blocked.accounts[0].reason,'billing')
 assert.equal(plugin.tone(blocked.accounts[0]),'limited')
 assert.match(plugin.describe(blocked.accounts[0]),/billing/)
 const [ok]=plugin.summarize(win(0))
 assert.equal(ok.accounts[0].state,'ready'); assert.equal(ok.ready,1)
 // Claude legacy gauges keep the quota wording.
 const [claude]=plugin.summarize({providers:[{slug:'anthropic',usage:{windows:[{label:'Session (5h)',used_percent:100,scope:'account'}]}}]})
 assert.equal(claude.accounts[0].reason,'quota_window')
})

test('gemini status-only row: rejected key reads "key rejected", never out of quota; valid reads ready', async () => {
 const {plugin}=await setup()
 const acct=(id,p)=>({id,label:id,state:p>=100?'limited':'ready',limit_kind:p>=100?'quota':null,selectable:false,windows:[{label:'API key',used_percent:p,scope:'account'}]})
 const payload={providers:[{slug:'gemini',name:'Gemini',usage:{accounts:[acct('GOOGLE_API_KEY',0),acct('GEMINI_API_KEY',100)]}}]}
 const [g]=plugin.summarize(payload)
 assert.equal(g.slug,'gemini'); assert.equal(g.title,'Gemini (AI Studio key)'); assert.equal(g.statusOnly,true)
 assert.equal(g.ready,1); assert.equal(g.total,2); assert.equal(g.limited,1)
 assert.equal(plugin.describe(g.accounts[1]),'key rejected')
 assert.doesNotMatch(plugin.describe(g.accounts[1]),/quota/)
 assert.equal(plugin.describe(g.accounts[0]),'')
 // legacy single-gauge shape
 const [one]=plugin.summarize({providers:[{slug:'gemini',usage:{windows:[{label:'API key',used_percent:100,scope:'account'}]}}]})
 assert.equal(one.accounts[0].reason,'auth'); assert.equal(plugin.describe(one.accounts[0]),'key rejected')
 // no gemini row -> no chip
 assert.equal(plugin.summarize({providers:[]}).length,0)
})
test('status-only unchecked key (state unknown, no windows) reads "not checked yet", never "key rejected"', async () => {
 const {plugin}=await setup()
 // Live backend shape on a cold/stale cache: the recheck has not landed yet.
 const payload={providers:[{slug:'gemini',name:'Gemini',usage:{accounts:[{id:'fp:x',label:'GOOGLE_API_KEY',windows:[],state:'unknown',resets_at:null,limit_kind:null,reason:null,cooldown_until:null,model_cooldowns:[]}]}}]}
 const [g]=plugin.summarize(payload)
 assert.equal(g.ready,0); assert.equal(g.limited,0)
 const hint=plugin.statusHint(g,g.accounts[0])
 assert.doesNotMatch(hint,/rejected|blocked/i); assert.match(hint,/not checked yet/)
 assert.notEqual(plugin.countColor(g),'#f85149')
 // a real rejection still reads rejected, in red
 const [bad]=plugin.summarize({providers:[{slug:'gemini',usage:{accounts:[{id:'k',label:'k',state:'limited',limit_kind:'quota',windows:[{label:'API key',used_percent:100,scope:'account'}]}]}}]})
 assert.match(plugin.statusHint(bad,bad.accounts[0]),/key rejected/); assert.equal(plugin.countColor(bad),'#f85149')
 // valid key
 const [ok]=plugin.summarize({providers:[{slug:'gemini',usage:{accounts:[{id:'k',label:'k',state:'ready',windows:[{label:'API key',used_percent:0,scope:'account'}]}]}}]})
 assert.match(plugin.statusHint(ok,ok.accounts[0]),/API key valid/); assert.equal(plugin.countColor(ok),undefined)
 // xAI unchecked is not "API blocked" either
 const [x]=plugin.summarize({providers:[{slug:'xai-oauth',usage:{accounts:[{id:'a',label:'a',state:'unknown',windows:[]}]}}]})
 assert.doesNotMatch(plugin.statusHint(x,x.accounts[0]),/blocked/)
})
test('clean table: only rows with something to say get a chevron; status-only cells use a short label', async () => {
 const {plugin}=await setup()
 const g={statusOnly:false}
 assert.equal(plugin.hasDetails(g,{state:'ready',windows:[]}),false)
 assert.equal(plugin.hasDetails(g,{state:'limited',limit_kind:'quota',reason:'usage_limit'}),true)
 assert.equal(plugin.hasDetails(g,{state:'unavailable',reason:'auth'}),true)
 assert.equal(plugin.hasDetails(g,{state:'ready',model_cooldowns:[{model:'m',until:'2099-01-01T00:00:00Z'}]}),true)
 assert.equal(plugin.hasDetails({statusOnly:true},{state:'ready'}),true)
 const [gem]=plugin.summarize({providers:[{slug:'gemini',usage:{accounts:[{id:'k',label:'k',state:'limited',limit_kind:'quota',windows:[]}]}}]})
 assert.equal(plugin.statusLabel(gem,gem.accounts[0]),'key rejected')
 const x={statusOnly:true}
 assert.deepEqual(['ready','limited','unavailable','unknown'].map(state=>plugin.statusLabel(x,{state})),['ok','blocked','sign-in needed','not checked'])
})
