import React from 'react'
import {createRoot} from 'react-dom/client'
import plugin from '../../../desktop-plugins/account-usage/plugin.js'
import {host} from './harness-sdk.js'
const rows=new Map(), calls=[], deferred=[]
const row=id=>rows.get(id)||{session_id:id,provider:'anthropic',model:'same-model',revision:0,account_id:null,state:'empty'}
const ctx={onDispose(){},setTimeout(){},setInterval(){},register(c){createRoot(document.getElementById('root')).render(c.render())},async rest(path,opts={}) {
 const query=new URL(path,'http://fixture'); const id=opts.body?.session_id||query.searchParams.get('session_id')
 calls.push({path,...opts})
 if(path==='/forecast'){ // GPT gets a forecast; Claude stays on the old-backend path (dots, "no forecast").
  const h=Array.from({length:169},(_,i)=>i<17?Math.round(85-5*i):i<22?0:Math.max(0,100-0.6*(i-22)))
  return {'openai-codex':{verdict:'short',left:85,burn_per_h:5,history_h:20,curve:h,gaps:[{start_h:17,end_h:22}],runout_h:17,pace:1.4,resets_h:[22]}}}
 if(path==='/last')return {anthropic:{label:'Account A',at:Math.floor(Date.now()/1000)-3600}}
 if(path==='/reconnect')return opts.body.account_id==='inventory-dead'?{ok:false,renewed:0,needs_sign_in:true,sign_in_command:'hermes auth add openai-codex --type oauth --label dead'}:{ok:true,renewed:1,needs_sign_in:false}
 let result=row(id)
 if(opts.method==='PUT') { if(window.fixture.refuse)throw new Error('Account became unavailable: cooldown'); if(opts.body.expected_revision!==result.revision)throw new Error('409 Selection changed in another tab'); result={...result,revision:result.revision+1,state:'pending',account_id:opts.body.account_id};rows.set(id,result) }
 if(opts.method==='DELETE'){if(Number(query.searchParams.get('expected_revision'))!==result.revision)throw new Error('409 Selection changed');result={...result,revision:result.revision+1,state:'cancelled',account_id:null};rows.set(id,result)}
 if(window.fixture.delay && (!window.fixture.delayMethod||window.fixture.delayMethod===(opts.method||'GET'))) {await new Promise(resolve=>deferred.push(resolve))}
 return structuredClone(result)
}}
window.fixture={host,calls,rows,refuse:false,delay:false,release(){this.delay=false;deferred.splice(0).forEach(r=>r())},focus(id){host.state.focusedSessionId.set(id)}}
plugin.register(ctx)
