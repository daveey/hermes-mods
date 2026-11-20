import React from 'react'
import {createRoot} from 'react-dom/client'
import plugin from '../../../desktop-plugins/account-usage/plugin.js'
import {host} from './harness-sdk.js'
const rows=new Map(), calls=[], deferred=[]
const row=id=>rows.get(id)||{session_id:id,provider:'anthropic',model:'same-model',revision:0,account_id:null,state:'empty'}
const desktopApi=async (req)=>{
 calls.push({kind:'api',...req})
 ;(window.__api||(window.__api=[])).push(req)
 const path=String(req.path||'')
 if(path.includes('/oauth/') && path.endsWith('/start')) {
  if(window.fixture.oauthStartError) throw new Error(window.fixture.oauthStartError)
  return {flow:'device_code',session_id:'oauth-sess-1',verification_url:'https://auth.example.com/device',user_code:'WXYZ-1234',expires_in:900}
 }
 if(path.includes('/oauth/') && path.includes('/poll/')){
  const status=window.fixture.oauthStatus||'approved'
  if(status==='approved') window.fixture.reviveDead=true
  return {session_id:'oauth-sess-1',status}
 }
 if(path.includes('/oauth/sessions/') && req.method==='DELETE'){ window.fixture.oauthCancelled=true; return {ok:true} }
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
  if(account==='inventory-dead') return {ok:false,renewed:0,status:'needs_sign_in',needs_sign_in:true,auth_flow:'device_code',provider:'openai-codex',account_id:account,label:'Dead account'}
  if(account==='inventory-expired') return {ok:true,renewed:1,status:'renewed',needs_sign_in:false}
  return {ok:true,renewed:1,status:'renewed',needs_sign_in:false}
 }
 let result=row(id)
 if(opts.method==='PUT') { if(window.fixture.refuse)throw new Error('Account became unavailable: cooldown'); if(opts.body.expected_revision!==result.revision)throw new Error('409 Selection changed in another tab'); result={...result,revision:result.revision+1,state:'pending',account_id:opts.body.account_id};rows.set(id,result) }
 if(opts.method==='DELETE'){if(Number(query.searchParams.get('expected_revision'))!==result.revision)throw new Error('409 Selection changed');result={...result,revision:result.revision+1,state:'cancelled',account_id:null};rows.set(id,result)}
 if(window.fixture.delay && (!window.fixture.delayMethod||window.fixture.delayMethod===(opts.method||'GET'))) {await new Promise(resolve=>deferred.push(resolve))}
 return structuredClone(result)
}}
window.hermesDesktop={api:desktopApi}
window.fixture={host,calls,rows,refuse:false,delay:false,oauthStatus:'approved',reviveDead:false,release(){this.delay=false;deferred.splice(0).forEach(r=>r())},focus(id){host.state.focusedSessionId.set(id)}}
plugin.register(ctx)
