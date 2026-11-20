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
 assert.equal(plugin.needsReconnect({state:'unavailable',reason:'auth',disabled:true}),false)
 for (const a of [{state:'ready'},{state:'limited',limit_kind:'quota',reason:'usage_limit'},{state:'limited',limit_kind:'cooldown',reason:'rate_limit'},{state:'unknown'}]) assert.equal(plugin.needsReconnect(a),false)
 assert.equal(plugin.signInCount([{slug:'openai-codex',accounts:[{id:'d',state:'unavailable',reason:'auth'},{id:'q',state:'limited',limit_kind:'quota',reason:'usage_limit'}]}]),1)
})
test('reconnect POSTs the account, refreshes on success, offers sign-in when the grant is dead', async () => {
 const {plugin}=await setup(); const calls=[]; let refreshed=0; let reply={ok:true,status:'renewed',renewed:1}
 const ctx={onDispose(){},async rest(path,opts){calls.push({path,...opts});if(reply instanceof Error)throw reply;return reply}}
 const r=plugin.createReconnectStore(ctx,()=>refreshed++)
 await r.reconnect('openai-codex',{id:'acct-1'})
 assert.equal(calls[0].path,'/reconnect'); assert.equal(calls[0].method,'POST'); assert.deepEqual(calls[0].body,{provider:'openai-codex',account_id:'acct-1'})
 assert.equal(refreshed,1); assert.equal(r.$state.get()[r.key('openai-codex','acct-1')].done,true)
 reply={ok:false,status:'needs_sign_in',needs_sign_in:true,auth_flow:'device_code'}
 await r.reconnect('anthropic',{id:'acct-2'})
 const st=r.$state.get()[r.key('anthropic','acct-2')]
 assert.equal(st.needsSignIn,true); assert.equal(st.command,undefined); assert.equal(refreshed,1)
 reply={ok:false,status:'transient'}; await r.reconnect('anthropic',{id:'acct-3'})
 assert.match(r.$state.get()[r.key('anthropic','acct-3')].message,/retry/i)
 reply={ok:false,status:'quota'}; await r.reconnect('anthropic',{id:'acct-4'})
 assert.match(r.$state.get()[r.key('anthropic','acct-4')].message,/quota/i)
 assert.equal(r.$state.get()[r.key('anthropic','acct-4')].needsSignIn,undefined)
 reply=new Error('503 pool unavailable'); await r.reconnect('anthropic',{id:'acct-5'})
 assert.match(r.$state.get()[r.key('anthropic','acct-5')].message,/Reconnect failed: 503/)
})
test('dead grant sign-in launches device-code auth on the account gateway, not Settings', async () => {
 const {plugin,state}=await setup(); state.connectionId.set("gw-hub"); state.profile.set("work"); const apiCalls=[]; const opened=[]
 let pollStatus='approved'
 const ctx={onDispose(){}, os:{openExternal:async url=>{opened.push(url); return true}}, api:async req=>{apiCalls.push(req)
  if(String(req.path).endsWith('/start')) return {flow:'device_code',session_id:'sess-1',verification_url:'https://auth.example.com/device',user_code:'WXYZ'}
  if(String(req.path).includes('/poll/')) return {status:pollStatus}
  if(req.method==='DELETE') return {ok:true}
  throw new Error('unexpected '+req.path)
 }, async rest(){return {ok:false,status:'needs_sign_in',auth_flow:'device_code',sign_in:{mode:'singleton'}}}}
 const groups=[{slug:'openai-codex',accounts:[{id:'acct-9',state:'ready'}]}]
 const r=plugin.createReconnectStore(ctx,async()=>groups)
 await r.signIn('openai-codex',{id:'acct-9'},{connectionId:'gw-hub',profile:'work'})
 assert.equal(apiCalls[0].path,'/api/providers/oauth/openai-codex/start')
 assert.equal(apiCalls[0].connectionId,'gw-hub'); assert.equal(apiCalls[0].profile,'work')
 assert.ok(apiCalls.some(c=>String(c.path).includes('/poll/') && String(c.path).includes('wait=25')))
 assert.deepEqual(opened,['https://auth.example.com/device'])
 assert.equal(r.$state.get()[r.key('openai-codex','acct-9')].done,true)
 pollStatus='pending'
 const r2=plugin.createReconnectStore(ctx,async()=>groups)
 const p=r2.signIn('openai-codex',{id:'acct-8'},{connectionId:'gw-hub',profile:'work'})
 await new Promise(ok=>setTimeout(ok,20))
 const mid=r2.$state.get()[r2.key('openai-codex','acct-8')]
 assert.equal(mid.userCode,'WXYZ'); assert.equal(mid.verificationUrl,'https://auth.example.com/device')
 await r2.cancelAuth('openai-codex',{id:'acct-8'},{connectionId:'gw-hub',profile:'work'})
 await p
 assert.ok(apiCalls.some(c=>c.method==='DELETE' && String(c.path).includes('sess-1')))
 assert.match(r2.$state.get()[r2.key('openai-codex','acct-8')].message,/cancelled/i)
 // Anthropic never calls the dashboard OAuth start and never leaves for Settings. On a Desktop
 // without the recovery terminal it says so and starts nothing (test_terminal_signin.mjs has the flow).
 const before=apiCalls.length; const nav=[]; globalThis.__auTestHost.navigate=p=>nav.push(p)
 const ctx3={...ctx,async rest(){return {ok:false,status:'needs_sign_in',needs_sign_in:true,sign_in:{mode:'target',credential_id:'row7'}}}}
 const r3=plugin.createReconnectStore(ctx3,async()=>[{slug:'anthropic',accounts:[{id:'acct-7',state:'unavailable',reason:'auth'}]}])
 await r3.signIn('anthropic',{id:'acct-7'})
 assert.equal(apiCalls.length,before,'Anthropic never calls the in-app OAuth start')
 assert.deepEqual(nav,[])
 const ext=r3.$state.get()[r3.key('anthropic','acct-7')]
 assert.equal(ext.needsSignIn,true); assert.equal(ext.authFlow,'terminal'); assert.equal(ext.error,true)
 assert.match(ext.message,/Update Hermes Desktop/)
})

// ── review round 1 regressions ───────────────────────────────────────────────
const deviceCtx=(over={})=>{ const apiCalls=[], opened=[]; let n=0
 const ctx={onDispose(){}, os:{openExternal:async url=>{opened.push(url); return true}}, api:async req=>{apiCalls.push(req)
  if(String(req.path).endsWith('/start')) return {flow:'device_code',session_id:`sess-${++n}`,verification_url:'https://auth.example.com/device',user_code:'WXYZ'}
  if(String(req.path).includes('/poll/')) return over.poll ? over.poll(req) : {status:'approved'}
  if(req.method==='DELETE') return {ok:true}
  throw new Error('unexpected '+req.path)
 }, async rest(path,opts){return typeof over.reply==='function'?over.reply(opts):over.reply||{ok:false,status:'needs_sign_in',auth_flow:'device_code',sign_in:{mode:'singleton'}}}}
 return {ctx,apiCalls,opened}
}
test('completion readback: only a healthy read-back account says Reconnected', async () => {
 const {plugin,state}=await setup(); state.connectionId.set('gw'); state.profile.set('p'); const acct={id:'acct-1',label:'one'}
 const run=async onDone=>{ const {ctx}=deviceCtx(); const r=plugin.createReconnectStore(ctx,onDone); await r.signIn('openai-codex',acct,{connectionId:'gw',profile:'p'}); return {r,st:r.$state.get()[r.key('openai-codex','acct-1')]} }
 const groups=a=>[{slug:'openai-codex',accounts:a}]
 let {st}=await run(async()=>{throw new Error('model.options 503')})
 assert.notEqual(st.done,true); assert.equal(st.unverified,true); assert.equal(st.check,true); assert.match(st.message,/Could not confirm/); assert.doesNotMatch(st.message,/Reconnected/)
 ;({st}=await run(async()=>null))
 assert.notEqual(st.done,true); assert.match(st.message,/Could not confirm/)
 ;({st}=await run(async()=>groups([{id:'other',label:'two',state:'ready'}])))
 assert.notEqual(st.done,true); assert.equal(st.unverified,true); assert.equal(st.needsSignIn,true); assert.match(st.message,/not in the pool/)
 ;({st}=await run(async()=>groups([{id:'acct-1',label:'one',state:'unavailable',reason:'auth'}])))
 assert.notEqual(st.done,true); assert.equal(st.needsSignIn,true); assert.equal(st.message,'Still needs sign-in')
 ;({st}=await run(async()=>groups([{id:'acct-1',label:'one',state:'limited',limit_kind:'cooldown',reason:'auth'}])))
 assert.notEqual(st.done,true); assert.equal(st.retry,true); assert.match(st.message,/still disconnected/)
 ;({st}=await run(async()=>groups([{id:'acct-1',label:'one',state:'limited',limit_kind:'quota',reason:'usage_limit'}])))
 assert.equal(st.done,true); assert.match(st.message,/out of quota/); assert.doesNotMatch(st.message,/Reconnected/)
 ;({st}=await run(async()=>groups([{id:'acct-1',label:'one',state:'unknown'}])))
 assert.match(st.message,/not checked yet/); assert.doesNotMatch(st.message,/Reconnected/)
 // A failed readback is recoverable: Check reads again and only then confirms.
 let fail=true; const out=await run(async()=>{ if(fail) throw new Error('x'); return groups([{id:'acct-1',label:'one',state:'ready'}]) })
 assert.match(out.st.message,/Could not confirm/); fail=false
 await out.r.check('openai-codex',acct)
 assert.deepEqual([out.r.$state.get()[out.r.key('openai-codex','acct-1')].done,out.r.$state.get()[out.r.key('openai-codex','acct-1')].message],[true,'Reconnected'])
 assert.deepEqual(['ready','limited','unknown'].map(state=>plugin.accountHealth(groups([{id:'a',state,limit_kind:'cooldown'}]),'openai-codex','a')),['ok','ok','unchecked'])
})
test('cancel then immediate re-sign-in: the cancelled poll loop never writes over the new flow', async () => {
 const {plugin,state}=await setup(); state.connectionId.set('gw'); state.profile.set('p'); const waiting=[]; let verified=0
 const {ctx,apiCalls}=deviceCtx({poll:()=>new Promise(ok=>waiting.push(ok))})
 const r=plugin.createReconnectStore(ctx,async()=>{verified++; return [{slug:'openai-codex',accounts:[{id:'a',state:'ready'}]}]})
 const acct={id:'a',label:'A'}, k=r.key('openai-codex','a'), tick=()=>new Promise(ok=>setTimeout(ok,5))
 const first=r.signIn('openai-codex',acct,{connectionId:'gw',profile:'p'}); await tick()
 assert.equal(r.$state.get()[k].sessionId,'sess-1'); assert.equal(waiting.length,1)
 await r.cancelAuth('openai-codex',acct,{connectionId:'gw',profile:'p'})
 assert.equal(r.$state.get()[k].needsSignIn,true,'cancel keeps the sign-in action')
 assert.ok(apiCalls.some(c=>c.method==='DELETE'&&String(c.path).includes('sess-1')))
 const second=r.signIn('openai-codex',acct,{connectionId:'gw',profile:'p'}); await tick()
 assert.equal(r.$state.get()[k].sessionId,'sess-2'); assert.equal(waiting.length,2)
 waiting[0]({status:'approved'}); await first
 const st=r.$state.get()[k]
 assert.equal(st.sessionId,'sess-2'); assert.equal(st.launching,true); assert.notEqual(st.done,true); assert.equal(verified,0,'old flow must not run the readback')
 waiting[1]({status:'approved'}); await second
 assert.equal(r.$state.get()[k].done,true); assert.equal(verified,1)
})
test('refreshable expiry reads "disconnected"; "sign-in needed" only once a dead grant is confirmed', async () => {
 const {plugin}=await setup()
 const dead={id:'d',state:'unavailable',reason:'auth'}, benched={id:'b',state:'limited',limit_kind:'cooldown',reason:'auth'}, quota={id:'q',state:'limited',limit_kind:'quota',reason:'usage_limit'}
 assert.equal(plugin.signInConfirmed(dead,undefined,'openai-codex'),true)
 assert.equal(plugin.signInConfirmed(benched,undefined,'openai-codex'),false)
 assert.equal(plugin.signInConfirmed(benched,{needsSignIn:true},'openai-codex'),true,'a reconnect result confirms it')
 assert.equal(plugin.signInConfirmed(quota,{needsSignIn:true},'openai-codex'),false)
 assert.equal(plugin.signInConfirmed({...dead,disabled:true},undefined,'openai-codex'),false)
 const g={slug:'openai-codex',accounts:[dead,benched,quota,{id:'r',state:'ready'},{...dead,id:'off',disabled:true}]}
 assert.deepEqual(plugin.authCounts(g,{}),{signIn:1,disconnected:1})
 assert.equal(plugin.authLabel(plugin.authCounts(g,{})),'sign-in · disconnected')
 assert.deepEqual(plugin.authCounts(g,{['openai-codex\u0000b']:{needsSignIn:true}}),{signIn:2,disconnected:0})
 assert.equal(plugin.authLabel({signIn:2,disconnected:0}),'2 sign-in'); assert.equal(plugin.authLabel({signIn:0,disconnected:3}),'3 disconnected'); assert.equal(plugin.authLabel({signIn:0,disconnected:0}),'')
 assert.equal(plugin.signInCount([g]),1)
 // A rejected Gemini key / xAI block carries reason 'auth' but is not a sign-in the panel can renew.
 const [gem]=plugin.summarize({providers:[{slug:'gemini',usage:{accounts:[{id:'k',label:'k',state:'limited',limit_kind:'quota',windows:[]}]}}]})
 assert.equal(gem.accounts[0].reason,'auth'); assert.equal(plugin.needsReconnect(gem.accounts[0],gem.slug),false)
 assert.deepEqual(plugin.authCounts(gem,{}),{signIn:0,disconnected:0})
 assert.deepEqual(plugin.authCounts({slug:'xai-oauth',accounts:[dead]},{}),{signIn:0,disconnected:0})
})
test('a row core removed for a dead grant stays listed as signed out until sign-in is verified', async () => {
 const {plugin}=await setup()
 const {ctx}=deviceCtx({reply:{ok:false,status:'needs_sign_in',needs_sign_in:true,removed:1,auth_flow:'device_code',sign_in:{mode:'singleton'}}})
 let groups=[{slug:'openai-codex',title:'ChatGPT (Codex)',total:1,ready:1,accounts:[{id:'keep',label:'keep',state:'ready'}]}]
 const r=plugin.createReconnectStore(ctx,async()=>groups)
 const acct={id:'gone',label:'Gone acct'}, k=r.key('openai-codex','gone')
 await r.reconnect('openai-codex',acct)
 assert.equal(r.$state.get()[k].needsSignIn,true); assert.equal(r.$state.get()[k].removed,true)
 let [g]=plugin.withSignedOut(groups,r.$state.get())
 assert.deepEqual(g.accounts.map(a=>[a.id,a.state,!!a.signedOut]),[['keep','ready',false],['gone','unavailable',true]]); assert.equal(g.total,2)
 assert.equal(plugin.signInConfirmed(g.accounts[1],r.$state.get()[k],g.slug),true)
 assert.deepEqual(plugin.authCounts(g,r.$state.get()),{signIn:1,disconnected:0})
 assert.match(plugin.describe(g.accounts[1]),/signed out/)
 // The provider's last account: the chip itself must not vanish.
 const alone=plugin.withSignedOut([{slug:'anthropic',accounts:[{id:'c',label:'c',state:'ready'}],total:1}],r.$state.get())
 assert.deepEqual(alone.map(x=>x.slug),['anthropic','openai-codex']); assert.equal(alone[1].accounts[0].signedOut,true); assert.equal(alone[1].tag,'GPT')
 // Only the same backend identity suppresses a ghost; a matching label proves nothing.
 assert.equal(plugin.withSignedOut([{slug:'openai-codex',total:1,accounts:[{id:'gone',label:'x',state:'unavailable'}]}],r.$state.get())[0].accounts.length,1)
 assert.equal(plugin.withSignedOut([{slug:'openai-codex',total:1,accounts:[{id:'new',label:'Gone acct',state:'ready'}]}],r.$state.get())[0].accounts.length,2)
 // Sign-in on the ghost row survives cancel, and a verified sign-in removes the ghost.
 await r.cancelAuth('openai-codex',acct); assert.equal(r.$state.get()[k].removed,true)
 groups=[{slug:'openai-codex',total:2,accounts:[{id:'keep',label:'keep',state:'ready'},{id:'gone',label:'Gone acct',state:'ready'}]}]
 await r.signIn('openai-codex',acct)
 assert.equal(r.$state.get()[k].done,true); assert.equal(r.$state.get()[k].message,'Reconnected')
 assert.equal(plugin.withSignedOut(groups,r.$state.get())[0].accounts.length,2)
 // Without `removed`, a needs-sign-in state never invents a row.
 assert.equal(plugin.withSignedOut([],{x:{provider:'openai-codex',id:'z',needsSignIn:true}}).length,0)
})
async function setupUi() {
 const prelude = 'const host=globalThis.__auTestHost, atom=globalThis.__auTestAtom;\nconst jsx=(type,props)=>({type,props}), jsxs=jsx;\n'
 const state = Object.fromEntries(Object.entries({connectionId:'gw-hub',profile:'work'}).map(([k,v]) => [k,atom(v)]))
 const nav=[]; globalThis.__auTestHost = {state, navigate:p=>nav.push(p)}; globalThis.__auTestAtom = atom
 const mod = await import('data:text/javascript;base64,' + Buffer.from(prelude + source.replace(/^import .*$/gm, '') + '\n//' + Math.random()).toString('base64'))
 return {mod,nav}
}
const controls=el=>el ? el.props.children.map(c=>c.type==='button'?['button',c.props['aria-label'],c.props.onClick]:['text',c.props.children]) : null
test('every auth row has a supported next step: Reconnect or in-app sign-in, never Settings', async () => {
 const {mod,nav}=await setupUi(); const calls=[]
 const store={key:(p,id)=>`${p}\u0000${id}`,reconnect:(...x)=>calls.push(['reconnect',...x]),signIn:(...x)=>calls.push(['signIn',...x]),check:(...x)=>calls.push(['check',...x]),cancelAuth(){},openPage(){}}
 const line=(slug,a,state={})=>controls(mod.ReconnectLine({g:{slug},a,reconnectStore:store,reconnectState:state}))
 const dead={id:'d',label:'D',state:'unavailable',reason:'auth'}, benched={id:'b',label:'B',state:'limited',limit_kind:'cooldown',reason:'auth'}
 // Codex: auth-benched -> Reconnect only; dead -> sign-in, no Reconnect.
 let c=line('openai-codex',benched); assert.deepEqual(c.map(x=>x.slice(0,2)),[['button','Reconnect B']])
 c[0][2](); assert.deepEqual(calls.pop().slice(0,3),['reconnect','openai-codex',benched])
 assert.deepEqual(calls.length,0)
 c=line('openai-codex',dead); assert.deepEqual(c.map(x=>x.slice(0,2)),[['button','Sign in to D again'],['text','Saved sign-in expired — sign in again']])
 c[0][2](); const signed=calls.pop(); assert.deepEqual(signed.slice(0,3),['signIn','openai-codex',dead]); assert.deepEqual(signed[3],{connectionId:'gw-hub',profile:'work'},'sign-in targets the chip gateway and profile')
 // Claude dead grant: the same sign-in control as Codex, on the chip gateway; never Reconnect.
 c=line('anthropic',dead); assert.deepEqual(c.map(x=>x.slice(0,2)),[['button','Sign in to D again'],['text','Saved sign-in expired — sign in again']])
 c[0][2](); const claude=calls.pop(); assert.deepEqual(claude.slice(0,3),['signIn','anthropic',dead]); assert.deepEqual(claude[3],{connectionId:'gw-hub',profile:'work'})
 const k='anthropic\u0000b'
 c=line('anthropic',benched,{[k]:{needsSignIn:true,authFlow:'terminal',message:'Saved sign-in expired — sign in again'}})
 assert.deepEqual(c.map(x=>x[1]),['Sign in to B again','Saved sign-in expired — sign in again'])
 // An unconfirmed result offers a re-check next to the sign-in.
 c=line('anthropic',benched,{[k]:{needsSignIn:true,authFlow:'terminal',check:true,message:'m'}})
 assert.deepEqual(c.map(x=>x[1]),['Sign in to B again','Check sign-in for B','m'])
 c[1][2](); assert.deepEqual(calls.pop().slice(0,3),['check','anthropic',benched])
 // Finished, quota, disabled-provider rows offer no auth control.
 assert.deepEqual(line('anthropic',{id:'b',label:'B',state:'ready'},{[k]:{done:true,message:'Reconnected'}}).map(x=>x.slice(0,2)),[['text','Reconnected']])
 assert.equal(line('openai-codex',{id:'q',state:'limited',limit_kind:'quota',reason:'usage_limit'}),null)
 assert.equal(line('gemini',{id:'k',state:'limited',reason:'auth'}),null)
 // A grant that dies again after "Reconnected" gets its controls back.
 assert.deepEqual(line('openai-codex',dead,{['openai-codex\u0000d']:{done:true,message:'Reconnected'}}).map(x=>x[1]),['Sign in to D again','Saved sign-in expired — sign in again'])
 assert.deepEqual(nav,[])
 // Collapsed row pill wording.
 const pill=(slug,a,state={})=>{ const el=mod.AccountRow({g:{slug,accounts:[],markId:null},a,open:false,onToggle(){},selectionStore:{select(){}},selectionState:{busy:false,loading:false,selection:null},reconnectStore:store,reconnectState:state})
  const p=el.props.children[0].props.children[0].props.children[1].props.children.find(x=>x&&x.props&&x.props['data-auth']); return p?[p.props.className,p.props.children,el.props.children.length>1&&!!el.props.children[1]]:null }
 assert.deepEqual(pill('openai-codex',dead),['hermes-au-signin','sign-in needed',true])
 assert.deepEqual(pill('openai-codex',benched),['hermes-au-disc','disconnected',true])
 assert.deepEqual(pill('openai-codex',benched,{['openai-codex\u0000b']:{needsSignIn:true,message:'x'}}),['hermes-au-signin','sign-in needed',true])
 assert.equal(pill('openai-codex',{id:'q',label:'Q',state:'limited',limit_kind:'quota',reason:'usage_limit'}),null)
 assert.equal(pill('openai-codex',{...dead,disabled:true}),null)
})
test('Claude sign-in: a changed fingerprint cannot be verified by a matching display label', async () => {
 const {mod,nav}=await setupUi(); const apiCalls=[]
 let groups=[{slug:'anthropic',accounts:[{id:'fp:old',label:'me',state:'unavailable',reason:'auth'}]}]
 // An older backend: a dead grant, but no answer about which stored row a sign-in must repair.
 const ctx={onDispose(){},api:async req=>{apiCalls.push(req); throw new Error('no in-app Anthropic OAuth')},async rest(){return {ok:false,status:'needs_sign_in',needs_sign_in:true,auth_flow:'external'}}}
 const r=mod.createReconnectStore(ctx,async()=>groups); const acct={id:'fp:old',label:'me'}, k=r.key('anthropic','fp:old')
 await r.reconnect('anthropic',acct)
 assert.equal(r.$state.get()[k].authFlow,'terminal'); assert.equal(r.$state.get()[k].message,'Saved sign-in expired — sign in again')
 await r.signIn('anthropic',acct)
 assert.deepEqual(nav,[]); assert.equal(apiCalls.length,0)
 assert.match(r.$state.get()[k].message,/Restart this gateway/); assert.equal(r.$state.get()[k].error,true)
 await r.check('anthropic',acct)
 let st=r.$state.get()[k]; assert.equal(st.message,'Still needs sign-in'); assert.equal(st.authFlow,'terminal'); assert.notEqual(st.done,true)
 groups=[{slug:'anthropic',accounts:[{id:'fp:new',label:'me',state:'ready'}]}]
 await r.check('anthropic',acct)
 st=r.$state.get()[k]; assert.notEqual(st.done,true); assert.equal(st.unverified,true)
 assert.deepEqual(mod.rowFix(r.$state.get(),'anthropic',groups[0].accounts[0]),{},'a label is not verified account identity')
 assert.deepEqual(mod.rowFix(r.$state.get(),'anthropic',{id:'fp:other',label:'someone else'}),{})
 // A sign-in made elsewhere that added a row keeps the old DEAD row: never claim it was repaired.
 groups[0].accounts.push({id:'fp:old',label:'me',state:'unavailable',reason:'auth'})
 await r.check('anthropic',acct)
 assert.equal(r.$state.get()[k].message,'Still needs sign-in')
 // Two accounts sharing a label: no guess.
 assert.equal(mod.accountHealth([{slug:'anthropic',accounts:[{id:'1',label:'me',state:'ready'},{id:'2',label:'me',state:'ready'}]}],'anthropic','fp:old','me'),'missing')
 // Another account that merely shares the label keeps its own dead-grant controls.
 const twin={id:'fp:twin',label:'me',state:'unavailable',reason:'auth'}
 assert.deepEqual(mod.rowFix(r.$state.get(),'anthropic',twin),{})
 const store={key:r.key,check(){},reconnect(){},signIn(){},cancelAuth(){},openPage(){}}
 assert.deepEqual(controls(mod.ReconnectLine({g:{slug:'anthropic'},a:twin,reconnectStore:store,reconnectState:r.$state.get()})).map(x=>x[1]),
  ['Sign in to me again','Saved sign-in expired — sign in again'])
})
test('same-label siblings neither verify nor hide a removed selected account', async () => {
 const {plugin}=await setup()
 const sibling={id:'sibling',label:'Shared name',state:'ready'}
 const groups=[{slug:'openai-codex',accounts:[sibling],total:1}]
 assert.equal(plugin.accountHealth(groups,'openai-codex','removed','Shared name'),'missing')
 const state={x:{removed:true,provider:'openai-codex',id:'removed',label:'Shared name'}}
 assert.deepEqual(plugin.withSignedOut(groups,state)[0].accounts.map(a=>a.id),['sibling','removed'])
 assert.equal(plugin.withSignedOut([{...groups[0],accounts:[{...sibling,id:'removed'}]}],state)[0].accounts.length,1)
})
test('reconnect state is scoped to its gateway and profile: no ghost, result or action crosses over', async () => {
 const {plugin,state}=await setup(); state.connectionId.set('gw-A'); state.profile.set('pA')
 // Each gateway answers for its own pool: only gateway A's row was removed.
 const {ctx,apiCalls}=deviceCtx({reply:opts=>({ok:false,status:'needs_sign_in',needs_sign_in:true,auth_flow:'device_code',sign_in:{mode:'singleton'},...(opts?.connectionId==='gw-B'?{}:{removed:1})}),poll:()=>new Promise(()=>{})})
 let groups=[]; const r=plugin.createReconnectStore(ctx,async()=>groups)
 const acct={id:'x',label:'X'}, k=r.key('openai-codex','x'), tick=()=>new Promise(ok=>setTimeout(ok,5))
 await r.reconnect('openai-codex',acct)
 assert.equal(plugin.withSignedOut([],plugin.scopedFix(r.$state.get())).length,1,'ghost on its own gateway')
 state.connectionId.set('gw-B'); state.profile.set('pB')
 const onB=plugin.scopedFix(r.$state.get())
 assert.deepEqual(onB,{}); assert.equal(plugin.withSignedOut([],onB).length,0,'no ghost on another gateway')
 assert.deepEqual(plugin.authCounts({slug:'openai-codex',accounts:[{id:'x',state:'limited',limit_kind:'cooldown',reason:'auth'}]},onB),{signIn:0,disconnected:1})
 // Same account id on gateway B: A's state neither blocks nor colours it, and A's late readback cannot write.
 state.connectionId.set('gw-A'); state.profile.set('pA')
 r.signIn('openai-codex',acct); await tick()
 assert.equal(r.$state.get()[k].sessionId,'sess-1')
 state.connectionId.set('gw-B'); state.profile.set('pB')
 await r.cancelAuth('openai-codex',acct)
 assert.equal(r.$state.get()[k].sessionId,'sess-1','cancel from gateway B leaves gateway A flow alone')
 assert.ok(!apiCalls.some(c=>c.method==='DELETE'))
 r.signIn('openai-codex',acct); await tick()
 const st=r.$state.get()[k]
 assert.equal(st.scope,plugin.scopeOf({connectionId:'gw-B',profile:'pB'})); assert.equal(st.sessionId,'sess-2'); assert.notEqual(st.removed,true)
 assert.deepEqual(apiCalls.filter(c=>String(c.path).endsWith('/start')).map(c=>[c.connectionId,c.profile]),[['gw-A','pA'],['gw-B','pB']])
 // Taking the key over deletes gateway A's server session, on gateway A.
 assert.deepEqual(apiCalls.filter(c=>c.method==='DELETE').map(c=>[String(c.path).split('/').pop(),c.connectionId,c.profile]),[['sess-1','gw-A','pA']])
 // A reconnect still in flight on gateway A cannot write its late result into gateway B's state.
 const s3=await setup(); s3.state.connectionId.set('gw-A'); s3.state.profile.set('pA')
 const replies=[]; const ctx3={onDispose(){},async rest(){return new Promise(ok=>replies.push(ok))}}
 const r3=s3.plugin.createReconnectStore(ctx3,async()=>[])
 const slow=r3.reconnect('openai-codex',acct); await tick()
 s3.state.connectionId.set('gw-B'); s3.state.profile.set('pB')
 const fast=r3.reconnect('openai-codex',acct); await tick()
 replies[1]({ok:false,status:'transient'}); await fast
 replies[0]({ok:true,status:'renewed'}); await slow
 const b=r3.$state.get()[k]
 assert.equal(b.scope,plugin.scopeOf({connectionId:'gw-B',profile:'pB'})); assert.equal(b.message,'Reconnect failed — retry'); assert.notEqual(b.done,true)
 // A readback that lands after the panel moved to another gateway proves nothing.
 const s2=await setup(); s2.state.connectionId.set('gw-A'); s2.state.profile.set('pA')
 const d=deviceCtx(); const r2=s2.plugin.createReconnectStore(d.ctx,async()=>{ s2.state.connectionId.set('gw-B'); return [{slug:'openai-codex',accounts:[{id:'x',label:'X',state:'ready'}]}] })
 await r2.signIn('openai-codex',acct)
 const late=r2.$state.get()[k]; assert.notEqual(late.done,true); assert.match(late.message,/Could not confirm/)
 assert.deepEqual(s2.plugin.scopedFix(r2.$state.get()),{})
})
test('a started sign-in session is never abandoned: browser-launch failure keeps it cancellable, errors delete it', async () => {
 const {plugin}=await setup(); const acct={id:'a',label:'A'}; const tick=()=>new Promise(ok=>setTimeout(ok,5))
 // openExternal rejects: the session stays live, visible and cancellable.
 let d=deviceCtx({poll:()=>new Promise(()=>{})}); d.ctx.os.openExternal=async()=>{throw new Error('no browser')}
 let r=plugin.createReconnectStore(d.ctx,async()=>[]); const k=r.key('openai-codex','a')
 r.signIn('openai-codex',acct); await tick()
 let st=r.$state.get()[k]
 assert.equal(st.sessionId,'sess-1'); assert.equal(st.launching,true); assert.equal(st.message,'Open the sign-in page to continue'); assert.equal(st.verificationUrl,'https://auth.example.com/device')
 await r.cancelAuth('openai-codex',acct)
 assert.ok(d.apiCalls.some(c=>c.method==='DELETE'&&String(c.path).includes('sess-1')))
 // Poll keeps failing: the flow ends, the server session is deleted, sign-in stays offered.
 d=deviceCtx({poll:()=>{throw new Error('boom')}}); r=plugin.createReconnectStore(d.ctx,async()=>[])
 await r.signIn('openai-codex',acct)
 st=r.$state.get()[k]
 assert.equal(st.needsSignIn,true); assert.equal(st.error,true); assert.notEqual(st.busy,true); assert.match(st.message,/Sign-in failed/)
 assert.ok(d.apiCalls.some(c=>c.method==='DELETE'&&String(c.path).includes('sess-1')))
 // A start that is not a usable device-code flow but carries a session: deleted too.
 d=deviceCtx(); const api=d.ctx.api; d.ctx.api=async req=>String(req.path).endsWith('/start')?{flow:'pkce',session_id:'sess-x'}:api(req)
 r=plugin.createReconnectStore(d.ctx,async()=>[])
 await r.signIn('openai-codex',acct)
 assert.ok(d.apiCalls.some(c=>c.method==='DELETE'&&String(c.path).includes('sess-x')))
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
test('status-only chips hide from the bar unless red or yellow; usage chips always show', async () => {
 const {plugin}=await setup()
 const g=(slug,accts)=>plugin.summarize({providers:[{slug,usage:{accounts:accts}}]})[0]
 const a=(state,extra={})=>({id:state,label:state,state,windows:[],...extra})
 assert.equal(plugin.chipVisible(g('gemini',[a('ready')])),false)
 assert.equal(plugin.chipVisible(g('xai-oauth',[a('ready')])),false)
 assert.equal(plugin.chipVisible(g('xai-oauth',[a('unknown')])),false)
 assert.equal(plugin.chipVisible(g('gemini',[a('ready'),a('limited',{limit_kind:'quota'})])),true)
 assert.equal(plugin.chipVisible(g('xai-oauth',[a('limited',{limit_kind:'cooldown'})])),true)
 assert.equal(plugin.chipVisible(g('xai-oauth',[a('ready',{windows:[{label:'API',used_percent:95,scope:'account'}]})])),true)
 assert.equal(plugin.chipVisible(g('anthropic',[a('ready')])),true)
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
test('rows are non-clickable with the backend reason only when selection.unavailable is set', async () => {
  const prelude = 'const host=globalThis.__auTestHost, atom=globalThis.__auTestAtom;\nconst jsx=(type,props)=>({type,props}), jsxs=jsx;\n'
  globalThis.__auTestHost = {state: {}}; globalThis.__auTestAtom = atom
  const mod = await import('data:text/javascript;base64,' + Buffer.from(prelude + source.replace(/^import .*$/gm, '') + '\n//' + Math.random()).toString('base64'))
  const reason = 'Account selection is unavailable while managed model routing is on: core pins each chat to its routed subscription account'
  const g = {slug: 'anthropic', accounts: [], markId: null}, a = {id: 'B', label: 'Account B', state: 'ready', windows: []}
  const row = selection => {
    const calls = []
    const el = mod.AccountRow({g, a, open: false, onToggle() {}, selectionStore: {select: (...x) => calls.push(x)},
      selectionState: {busy: false, loading: false, selection}, reconnectStore: {key: () => 'k'}, reconnectState: {}})
    const button = el.props.children[0].props.children[0]
    return {button: button.props, calls}
  }
  const base = {session_id: 'runtime-a', provider: 'anthropic', model: 'm', revision: 0, account_id: null, state: 'none'}
  const off = row({...base, unavailable: reason})
  assert.equal(off.button.disabled, true); assert.equal(off.button.onClick, undefined)
  assert.ok(off.button.title.includes(reason)); assert.match(off.button['aria-label'], /managed model routing/)
  const on = row(base)
  assert.equal(on.button.disabled, false); assert.equal(typeof on.button.onClick, 'function')
  on.button.onClick(); assert.deepEqual(on.calls, [['anthropic', a]])
  assert.ok(!on.button.title.includes('managed'))
})

// Core's stale-code refusal of model.options after a core update (hermes-hub 2026-11-12, SSH backend on f4caf04 vs
// ef7a9e6): the bar must say the backend needs a restart, not a bare "usage ?" (t_01a1cea849893f99).
const STALE_503 = 'Error invoking remote method \'hermes:api\': Error: 503: {"detail":"Restart required: This process is running code from f4caf04160 but the checkout on disk is now ef7a9e6b2d. The model picker would risk a stale-module crash — restart the Desktop-owned backend to load the new code (use Restart backend in Hermes Desktop, or quit and reopen the app)"}'
// ── selected-account sign-in: the backend names the row, the gateway binds the session ──────────
const boundCtx=({reply,start,poll}={})=>{ const apiCalls=[], opened=[], restCalls=[]
 const ctx={onDispose(){}, os:{openExternal:async url=>{opened.push(url); return true}}, api:async req=>{apiCalls.push(req)
  if(String(req.path).endsWith('/start')) return start ? start(req) : {flow:'device_code',session_id:'sess-b',verification_url:'https://auth.example.com/device',user_code:'WXYZ',target_bound:true,credential_id:req.body.credential_id}
  if(String(req.path).includes('/poll/')) return poll ? poll(req) : {status:'approved',credential_id:'row-7',identity_verified:true}
  if(req.method==='DELETE') return {ok:true}
  throw new Error('unexpected '+req.path)
 }, async rest(path,opts){restCalls.push({path,...opts}); return reply||{ok:false,status:'needs_sign_in',needs_sign_in:true,auth_flow:'device_code',sign_in:{mode:'target',credential_id:'row-7'}}}}
 return {ctx,apiCalls,opened,restCalls}
}
const starts=calls=>calls.filter(c=>String(c.path).endsWith('/start')), drops=calls=>calls.filter(c=>c.method==='DELETE').map(c=>String(c.path).split('/').pop())
test('sign-in for a dead manual row is bound to that pool row on the account gateway and read back', async () => {
 const {plugin,state}=await setup(); state.connectionId.set('gw-hub'); state.profile.set('work')
 const acct={id:'codex:ws:sub',label:'Work'}, route={connectionId:'gw-hub',profile:'work'}
 const {ctx,apiCalls,opened,restCalls}=boundCtx()
 const r=plugin.createReconnectStore(ctx,async()=>[{slug:'openai-codex',accounts:[{id:'codex:ws:sub',label:'Work',state:'ready'}]}])
 await r.signIn('openai-codex',acct,route)
 // The wire account id is resolved to a pool row by the backend on the same gateway, never by the panel.
 assert.deepEqual(restCalls.map(c=>[c.path,c.body,c.connectionId,c.profile]),[['/reconnect',{provider:'openai-codex',account_id:'codex:ws:sub'},'gw-hub','work']])
 const [start]=starts(apiCalls)
 assert.deepEqual([start.body,start.connectionId,start.profile],[{credential_id:'row-7'},'gw-hub','work'])
 assert.deepEqual(opened,['https://auth.example.com/device'])
 const st=r.$state.get()[r.key('openai-codex','codex:ws:sub')]
 assert.deepEqual([st.done,st.message],[true,'Reconnected']); assert.deepEqual(drops(apiCalls),[])
 assert.deepEqual(plugin.signInRoute({sign_in:{mode:'target',credential_id:'row-7'}}),{mode:'target',credentialId:'row-7'})
 for (const bad of [null,{},{sign_in:{mode:'target'}},{sign_in:{mode:'target',credential_id:7}},{sign_in:{mode:'label'}}]) assert.deepEqual(plugin.signInRoute(bad),{mode:'unknown'})
})
test('a gateway that ignores the target never completes as an account-add: its session is deleted', async () => {
 const {plugin}=await setup(); const acct={id:'a',label:'A'}
 for (const reply of [{flow:'device_code',session_id:'sess-old',verification_url:'https://auth.example.com/device',user_code:'WXYZ'},
                      {flow:'device_code',session_id:'sess-old',verification_url:'https://auth.example.com/device',user_code:'WXYZ',target_bound:true,credential_id:'other-row'}]) {
  const {ctx,apiCalls,opened}=boundCtx({start:()=>reply}); let read=0
  const r=plugin.createReconnectStore(ctx,async()=>{read++; return []})
  await r.signIn('openai-codex',acct)
  const st=r.$state.get()[r.key('openai-codex','a')]
  assert.deepEqual(opened,[],'the unbound sign-in page is never opened'); assert.deepEqual(drops(apiCalls),['sess-old'])
  assert.ok(!apiCalls.some(c=>String(c.path).includes('/poll/'))); assert.equal(read,0)
  assert.notEqual(st.done,true); assert.equal(st.needsSignIn,true); assert.match(st.message,/Update this gateway/)
 }
})
test('a bound sign-in that saved nothing, or saved elsewhere, is never reported as reconnected', async () => {
 const {plugin}=await setup(); const acct={id:'a',label:'A'}; const healthy=[{slug:'openai-codex',accounts:[{id:'a',label:'A',state:'ready'}]}]
 const run=async poll=>{ const {ctx,apiCalls}=boundCtx({poll:()=>poll}); let read=0; const r=plugin.createReconnectStore(ctx,async()=>{read++; return healthy}); await r.signIn('openai-codex',acct); return {st:r.$state.get()[r.key('openai-codex','a')],read,apiCalls,r} }
 let out=await run({status:'error',reason:'reauth_conflict',error_message:'server text is not shown'})
 assert.notEqual(out.st.done,true); assert.equal(out.read,0); assert.match(out.st.message,/different account.*nothing was saved/); assert.doesNotMatch(out.st.message,/server text/)
 assert.deepEqual(out.st.signInRoute,{mode:'target',credentialId:'row-7'},'the row stays retryable against the same target')
 for (const poll of [{status:'approved'},{status:'approved',credential_id:'other-row'}]) {
  out=await run(poll)
  assert.notEqual(out.st.done,true); assert.equal(out.st.unverified,true); assert.equal(out.read,0,'a healthy panel row cannot certify a sign-in saved elsewhere'); assert.match(out.st.message,/not for this account/)
 }
 // Retry after a conflict reuses the bound target without asking the backend to refresh a dead grant again.
 out=await run({status:'error',reason:'reauth_conflict'}); const before=starts(out.apiCalls).length
 await out.r.signIn('openai-codex',acct)
 assert.deepEqual(starts(out.apiCalls).slice(before).map(c=>c.body),[{credential_id:'row-7'}])
})
test('sign-in is refused, with no auth call, when the backend cannot name where it belongs', async () => {
 const {plugin}=await setup(); const acct={id:'a',label:'A'}; const base={ok:false,status:'needs_sign_in',needs_sign_in:true,auth_flow:'device_code'}
 for (const [reply,want] of [[{...base,sign_in:{mode:'unsupported'}},/managed outside this profile/],[base,/Restart this gateway/],[{...base,sign_in:{mode:'target',credential_id:''}},/Restart this gateway/]]) {
  const {ctx,apiCalls,opened}=boundCtx({reply}); const r=plugin.createReconnectStore(ctx,async()=>[])
  await r.signIn('openai-codex',acct)
  const st=r.$state.get()[r.key('openai-codex','a')]
  assert.deepEqual([apiCalls.length,opened.length],[0,0]); assert.equal(st.needsSignIn,true); assert.notEqual(st.done,true); assert.match(st.message,want)
 }
})
test('"Sign in again" on a grant that still refreshes renews it without launching auth; quota and disabled are not sign-in', async () => {
 const {plugin}=await setup(); const acct={id:'a',label:'A'}
 for (const [reply,check] of [[{ok:true,status:'renewed',renewed:1},st=>assert.deepEqual([st.done,st.message],[true,'Reconnected'])],
                              [{ok:false,status:'quota'},st=>{assert.match(st.message,/quota/i); assert.equal(st.needsSignIn,undefined)}],
                              [{ok:false,status:'disabled'},st=>{assert.match(st.message,/disabled/i); assert.equal(st.needsSignIn,undefined)}],
                              [{ok:false,status:'transient'},st=>{assert.equal(st.retry,true); assert.equal(st.needsSignIn,undefined)}]]) {
  const {ctx,apiCalls,opened}=boundCtx({reply}); const r=plugin.createReconnectStore(ctx,async()=>[])
  await r.signIn('openai-codex',acct)
  assert.deepEqual([apiCalls.length,opened.length],[0,0]); check(r.$state.get()[r.key('openai-codex','a')])
 }
})
test('cancelling a bound sign-in deletes its session on the account gateway and keeps the target for a retry', async () => {
 const {plugin,state}=await setup(); state.connectionId.set('gw-hub'); state.profile.set('work'); const acct={id:'a',label:'A'}, route={connectionId:'gw-hub',profile:'work'}
 const {ctx,apiCalls}=boundCtx({poll:()=>new Promise(()=>{})}); let read=0
 const r=plugin.createReconnectStore(ctx,async()=>{read++; return []})
 r.signIn('openai-codex',acct,route); await new Promise(ok=>setTimeout(ok,20))
 assert.equal(r.$state.get()[r.key('openai-codex','a')].sessionId,'sess-b')
 await r.cancelAuth('openai-codex',acct,route)
 const st=r.$state.get()[r.key('openai-codex','a')]
 assert.deepEqual(apiCalls.filter(c=>c.method==='DELETE').map(c=>[String(c.path).split('/').pop(),c.connectionId,c.profile]),[['sess-b','gw-hub','work']])
 assert.match(st.message,/cancelled/i); assert.notEqual(st.done,true); assert.equal(read,0)
 assert.deepEqual(st.signInRoute,{mode:'target',credentialId:'row-7'})
})
test('a removed row signs in again after a cancel without asking the pool about an id it no longer has', async () => {
 const {plugin}=await setup(); const acct={id:'gone',label:'Gone'}; let asked=0, pollStatus='pending'
 const {ctx,apiCalls}=boundCtx({poll:()=>pollStatus==='pending'?new Promise(()=>{}):{status:'approved'}})
 ctx.rest=async()=>{ if(asked++) throw new Error('404 Account not found; refresh the panel'); return {ok:false,status:'needs_sign_in',needs_sign_in:true,removed:1,auth_flow:'device_code',sign_in:{mode:'singleton'}} }
 const r=plugin.createReconnectStore(ctx,async()=>[{slug:'openai-codex',accounts:[{id:'gone',label:'Gone',state:'ready'}]}]), k=r.key('openai-codex','gone')
 await r.reconnect('openai-codex',acct)
 r.signIn('openai-codex',acct); await new Promise(ok=>setTimeout(ok,20))
 await r.cancelAuth('openai-codex',acct)
 pollStatus='approved'; await r.signIn('openai-codex',acct)
 assert.equal(asked,1); assert.deepEqual(starts(apiCalls).map(c=>c.body),[{},{}])
 assert.deepEqual([r.$state.get()[k].done,r.$state.get()[k].message],[true,'Reconnected'])
})
test('stale-code model.options refusal reads "backend on old code: restart"; other errors stay "usage ?"', async () => {
  const { plugin, ctx } = await setup()
  globalThis.__auTestHost.request = async () => { throw new Error(STALE_503) }
  const store = plugin.createStore(ctx)
  await store.refresh()
  const { error, groups } = store.$state.get()
  assert.deepEqual(groups, [])
  const stale = plugin.unavailableLabel(error)
  assert.equal(stale.label, 'backend on old code: restart')
  assert.match(stale.title, /^Account usage unavailable: backend on old code: restart/)
  assert.match(stale.title, /f4caf04160/)
  const other = plugin.unavailableLabel('Error: 502: gateway timeout')
  assert.deepEqual(other, { label: 'usage ?', title: 'Account usage unavailable: Error: 502: gateway timeout' })
  delete globalThis.__auTestHost.request
})
test('the in-use mark follows the account id the backend names, never a shared label', async () => {
 const {plugin}=await setup(); const now=Date.now(), at=Math.floor(now/1000)-5
 const payload={providers:[{slug:'anthropic',usage:{accounts:[{id:'fp:one',label:'Work',state:'ready',windows:[]},{id:'fp:two',label:'Work',state:'ready',windows:[]},{id:'fp:three',label:'Home',state:'ready',windows:[]}]}}]}
 const g=last=>plugin.summarize(payload,{anthropic:last},now)[0]
 // Two rows share the label: the named id decides, whichever row is listed first.
 assert.equal(g({account_id:'fp:two',label:'Work',at}).markId,'fp:two')
 assert.equal(g({account_id:'fp:two',label:'Work',at}).mark,'active')
 // A named id that is not listed marks nothing; its label is not used instead.
 assert.equal(g({account_id:'fp:gone',label:'Home',at}).markId,null)
 // Label-only record (older backend): an ambiguous label marks no row, a unique one still does.
 assert.equal(g({label:'Work',at}).markId,null)
 assert.equal(g({label:'Work',at}).mark,null)
 assert.equal(g({label:'Home',at}).markId,'fp:three')
})
