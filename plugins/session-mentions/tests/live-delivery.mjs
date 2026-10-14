// Opt-in live test: creates ONLY a new throwaway session, two independent sockets.
// SM_LIVE_URL=http://127.0.0.1:PORT node live-delivery.mjs
import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import { hideThrowaway, deleteThrowaway } from './live-throwaway.mjs'
const base=process.env.SM_LIVE_URL
if(!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base))throw Error('Set SM_LIVE_URL to an explicit local backend')
const home=process.env.HOME+'/.hermes'
const html=await (await fetch(base+'/')).text()
const token=html.match(/__HERMES_SESSION_TOKEN__\s*=\s*["']([^"']+)/)?.[1]
if(!token)throw Error('Local bootstrap token unavailable')
const sockets=[]
async function client(){
 const ws=new WebSocket(base.replace('http:','ws:')+'/api/ws?token='+encodeURIComponent(token));sockets.push(ws)
 await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=()=>rej(Error('Websocket failed'))})
 const pending=new Map();let count=0
 ws.onmessage=e=>{const m=JSON.parse(e.data); const p=pending.get(m.id);if(p){pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}}
 return (method,params={})=>new Promise((resolve,reject)=>{const id='smtest-'+ ++count;const timer=setTimeout(()=>{pending.delete(id);reject(Error('RPC timeout: '+method))},60000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({jsonrpc:'2.0',id,method,params}))})
}
const evidence={}
let sid,key,owner
try{
 owner=await client();const sender=await client()
 const created=await owner('session.create',{profile:'default',source:'desktop',title:'Session Mentions isolated delivery test',cwd:process.env.HOME+'/.hermes/cache/scratch'})
 sid=created.session_id;key=created.stored_session_id||created.session_key
 assert.ok(sid&&key)
 evidence.session_id=key;evidence.live_session_id=sid
 await owner('prompt.submit',{session_id:sid,text:'This is an isolated transport test. Do not use tools. Reply exactly: SM-READY',queued:true})
 await hideThrowaway(base,token,key)
 const lease=async()=>JSON.parse(await fs.readFile(home+'/runtime/active_sessions.json','utf8')).entries.find(e=>e.session_id===key)
 const before=await lease();assert.ok(before);evidence.owner_before=before
 globalThis.__smHost={state:{connectionId:{get:()=> 'local'},profile:{get:()=> 'default'}},request:sender,requestProfile:async()=>{throw Error('Wrong pooled backend chosen')}}
 globalThis.window={hermesDesktop:{api:async({path})=>{
  const r=await fetch(base+path,{headers:{'X-Hermes-Session-Token':token}})
  if(!r.ok)throw Error('REST failed '+r.status)
  return r.json()
 }}}
 const source=await fs.readFile(home+'/desktop-plugins/session-mentions/plugin.js','utf8')
 const code=source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'",'const host=globalThis.__smHost;const COMPOSER_AREAS={}')
 const m=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
 const marker='SM-OWNER-'+crypto.randomUUID()
 evidence.marker=marker
 const result=await m.performRequest({action:'send',args:{target:'@session:default/'+key,message:'Do not use tools. Reply exactly: '+marker},wait_for_reply:0,sender:{title:'isolated sender',profile:'default'}},'local',[{id:'local',key:'this-device',label:'This device'}])
 evidence.send_result=result;assert.equal(result.ok,true);assert.equal(result.live_session_id,sid)
 const deadline=Date.now()+180000
 let page
 while(Date.now()<deadline){
  await new Promise(r=>setTimeout(r,2000))
  page=await window.hermesDesktop.api({path:'/api/sessions/'+key+'/messages?profile=default&limit=200&order=latest'})
  if(page.messages?.some(m=>m.role==='assistant'&&m.content===marker))break
 }
 assert.ok(page.messages.some(m=>m.role==='assistant'&&m.content===marker),'Expected reply absent')
 assert.equal(page.messages.filter(m=>m.role==='user'&&m.content?.includes(marker)).length,1)
 evidence.transcript=page.messages.map(m=>({id:m.id,role:m.role,content:m.content}))
 const after=await lease();assert.equal(after.lease_id,before.lease_id);assert.equal(after.pid,before.pid);assert.equal(after.metadata.live_session_id,sid)
 evidence.owner_after=after
 const idleMarker='SM-IDLE-'+crypto.randomUUID()
 evidence.idle_result=await m.deliver({connectionId:'local',profile:'default',sessionId:key},'Do not use tools. Reply exactly: '+idleMarker,90)
 assert.equal(evidence.idle_result.reply,idleMarker)
 const finalLease=await lease();assert.equal(finalLease.lease_id,before.lease_id)
 evidence.verified=true
 await fs.writeFile(new URL('live-evidence.json',import.meta.url),JSON.stringify(evidence,null,2))
 console.log(JSON.stringify(evidence,null,2))
}finally{
 // Close the throwaway runtime, then delete its row — close alone leaves it in the sidebar.
 if(sid&&owner)await owner('session.close',{session_id:sid}).catch(()=>undefined)
 if(key)await deleteThrowaway(base,token,key).catch(e=>console.error(String(e)))
 for(const ws of sockets)ws.close()
}
