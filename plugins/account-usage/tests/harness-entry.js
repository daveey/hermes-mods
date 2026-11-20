import React from 'react'
import {createRoot} from 'react-dom/client'
import plugin from '../../../desktop-plugins/account-usage/plugin.js'
import {host} from './harness-sdk.js'
const rows=new Map(), calls=[], deferred=[]
const row=id=>rows.get(id)||{session_id:id,provider:'anthropic',model:'same-model',revision:0,account_id:null,state:'empty'}
const oauthSessions=new Map(); let oauthSeq=0 // one fake server session per /start, as on a real gateway
const desktopApi=async (req)=>{
 calls.push({kind:'api',...req})
 ;(window.__api||(window.__api=[])).push(req)
 const path=String(req.path||'')
 if(path.includes('/oauth/') && path.endsWith('/start')) {
  if(window.fixture.oauthStartError) throw new Error(window.fixture.oauthStartError)
  // A fake gateway with core's contract: a named pool row binds the session; an old core ignores it.
  const target=req.body?.credential_id
  const sid=`oauth-sess-${++oauthSeq}`
  oauthSessions.set(sid,{target:target && !window.fixture.oldCore ? target : null,cancelled:false})
  window.fixture.oauthCancelled=false
  return {flow:'device_code',session_id:sid,verification_url:'https://auth.example.com/device',user_code:'WXYZ-1234',expires_in:900,
   ...(oauthSessions.get(sid).target?{target_bound:true,credential_id:target}:{})}
 }
 if(path.includes('/oauth/') && path.includes('/poll/')){
  const sid=path.split('/poll/')[1].split('?')[0], sess=oauthSessions.get(sid)
  const status=window.fixture.oauthStatus||'approved'
  if(!sess || sess.cancelled) return {session_id:sid,status:'cancelled'}
  if(status!=='approved') return {session_id:sid,status}
  const bound=sess.target
  if(bound){
   // Another principal signed in: core refuses and writes nothing.
   if(window.fixture.wrongPrincipal) return {session_id:sid,status:'error',reason:'reauth_conflict',error_message:'fixture conflict text'}
   if(bound==='pool-dead') window.fixture.reviveDead=true
   return {session_id:sid,status,credential_id:bound,identity_verified:true}
  }
  // An ordinary login rewrites the provider's own sign-in state: it reseeds a row core dropped,
  // and beside a manual row that is still in the pool it adds a second one.
  if(window.fixture.vanished) window.fixture.reviveVanished=true; else window.fixture.duplicate=true
  return {session_id:sid,status}
 }
 if(path.includes('/oauth/sessions/') && req.method==='DELETE'){ const sess=oauthSessions.get(path.split('/oauth/sessions/')[1].split('?')[0]); if(sess) sess.cancelled=true; window.fixture.oauthCancelled=true; return {ok:true} }
 throw new Error('unexpected api '+path)
}
const ctx={onDispose(){},setTimeout(){},setInterval(){},os:{openExternal:async url=>{(window.__opened||(window.__opened=[])).push(url); return true}},api:desktopApi,register(c){createRoot(document.getElementById('root')).render(c.render())},async rest(path,opts={}) {
 const query=new URL(path,'http://fixture'); const id=opts.body?.session_id||query.searchParams.get('session_id')
 calls.push({path,...opts})
 if(path==='/forecast'){ // GPT gets a forecast; Claude stays on the old-backend path (dots, "no forecast").
  const h=Array.from({length:169},(_,i)=>i<17?Math.round(85-5*i):i<22?0:Math.max(0,100-0.6*(i-22)))
  return {'openai-codex':{verdict:'short',left:85,burn_per_h:5,history_h:20,curve:h,gaps:[{start_h:17,end_h:22}],runout_h:17,pace:1.4,resets_h:[22]}}
 }
 if(path==='/last')return {anthropic:{label:'Account A',at:Math.floor(Date.now()/1000)-3600}}
 if(path==='/reconnect'){
  const account=opts.body.account_id
  // Claude dead grant on one owned manual row: the backend names that row.
  if(account==='inventory-claude-dead') return {ok:false,renewed:0,status:'needs_sign_in',needs_sign_in:true,auth_flow:'external',sign_in:{mode:'target',credential_id:'pool-claude'},provider:'anthropic',account_id:account,label:'Claude dead'}
  if(account==='inventory-dead') return {ok:false,renewed:0,status:'needs_sign_in',needs_sign_in:true,auth_flow:'device_code',sign_in:{mode:'target',credential_id:'pool-dead'},provider:'openai-codex',account_id:account,label:'Dead account'}
  // Core quarantine: the dead-grant row leaves the pool; the next model.options no longer lists it.
  if(account==='inventory-vanish'){ window.fixture.vanished=true; return {ok:false,renewed:0,removed:1,status:'needs_sign_in',needs_sign_in:true,auth_flow:'device_code',sign_in:{mode:'singleton'},provider:'openai-codex',account_id:account,label:'Vanishing account'} }
  if(account==='inventory-expired'){ window.fixture.reviveExpired=true; return {ok:true,renewed:1,status:'renewed',needs_sign_in:false} }
  return {ok:true,renewed:1,status:'renewed',needs_sign_in:false}
 }
 // The stored row by its row id: after the sign-in it carries a new account id.
 if(path==='/credential-health') return window.fixture.claudeRevived
  ? {found:true,provider:'anthropic',credential_id:opts.body.credential_id,account_id:'inventory-claude-new',status:'ok'}
  : {found:true,provider:'anthropic',credential_id:opts.body.credential_id,account_id:'inventory-claude-dead',status:'needs_sign_in'}
 let result=row(id)
 if(opts.method==='PUT') { if(window.fixture.refuse)throw new Error('Account became unavailable: cooldown'); if(opts.body.expected_revision!==result.revision)throw new Error('409 Selection changed in another tab'); result={...result,revision:result.revision+1,state:'pending',account_id:opts.body.account_id};rows.set(id,result) }
 if(opts.method==='DELETE'){if(Number(query.searchParams.get('expected_revision'))!==result.revision)throw new Error('409 Selection changed');result={...result,revision:result.revision+1,state:'cancelled',account_id:null};rows.set(id,result)}
 if(window.fixture.delay && (!window.fixture.delayMethod||window.fixture.delayMethod===(opts.method||'GET'))) {await new Promise(resolve=>deferred.push(resolve))}
 return structuredClone(result)
}}
// Desktop's recovery terminal, scripted like `hermes auth reauth anthropic <row>`: confirm, then a
// sign-in link and a hidden code prompt; the code saves the row. Records every call it receives.
const term={started:[],written:[],disposed:[],data:new Map(),exit:new Map(),step:new Map()}
const emit=(id,text)=>term.data.get(id)?.(text)
const terminal={
 async startRecovery(opts){ term.started.push(opts); if(window.fixture.recoveryUnavailable) return {id:'',recovery:true,unavailable:window.fixture.recoveryUnavailable}
  const id='pty-'+term.started.length; term.step.set(id,0); return {id,recovery:true,connectionId:opts.connectionId} },
 async start(){ throw new Error('an ordinary shell must never be started') },
 onData(id,fn){ term.data.set(id,fn); return ()=>term.data.delete(id) },
 onExit(id,fn){ term.exit.set(id,fn); return ()=>term.exit.delete(id) },
 async attach(id){ emit(id,'The stored grant has no verifiable account identity.\r\n\u001b[1mReplace this selected credential?\u001b[0m Type replace to continue: '); return true },
 async write(id,text){ term.written.push({id,length:text.length,enter:text.endsWith('\r')}); const step=term.step.get(id)
  if(step===0){ if(text!=='replace\r'){ emit(id,'\r\nSign-in cancelled; no credential was changed.\r\n'); term.exit.get(id)?.({code:0,signal:null}); return true }
   term.step.set(id,1); emit(id,'\r\nOpen https://claude.ai/oauth/authorize?code=true&state=fixture\r\nAuthorization code: '); return true }
  window.fixture.claudeRevived=true
  emit(id,'\r\nSelected credential saved. Account identity remains unverified.\r\n'); term.exit.get(id)?.({code:0,signal:null}); return true },
 async dispose(id){ term.disposed.push(id); return true }
}
window.hermesDesktop={api:desktopApi,terminal}
window.__term=term
window.fixture={host,calls,rows,refuse:false,delay:false,oauthStatus:'approved',reviveDead:false,release(){this.delay=false;deferred.splice(0).forEach(r=>r())},focus(id){host.state.focusedSessionId.set(id)}}
plugin.register(ctx)
