import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(process.env.HOME + '/.hermes/desktop-plugins/session-mentions/plugin.js', 'utf8')
Error.stackTraceLimit = 1
async function load(host) {
 globalThis.__smHost = host
 const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
 return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}
const route = {connectionId:'local',profile:'default',targetProfile:'default'}
const state = {connectionId:{get:()=> 'local'},profile:{get:()=> 'default'}}
test('same gateway uses owning primary runtime, not a newly pooled backend; queues busy sends', async()=>{
 const calls=[]
 const m=await load({state,profileRoutes:async()=>[route],requestProfile:async()=>{throw Error('SESSION_NOT_OWNED')},request:async(method,p)=>{
  calls.push([method,p]);
  if(method==='session.resume') return {session_id:'owner-live',running:true}
  return {status:'queued',user_row_id:41}
 }})
 const result=await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test')
 assert.equal(result.status,'queued'); assert.equal(result.delivered,false); assert.equal(result.queued,true)
 assert.equal(calls[1][1].queued,true)
 assert.equal(calls[0][1].profile,'default')
})
test('null connection atom resolves only the registry primary',async()=>{
 const m=await load({state:{...state,connectionId:{get:()=>null}},connections:async()=>[{id:'local',primary:true}],request:async(method)=>method==='session.resume'?{session_id:'live'}:{status:'streaming',user_row_id:12},requestProfile:async()=>{throw Error('wrong runtime')},profileRoutes:async()=>[route]})
 assert.equal((await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test')).ok,true)
})
test('does not fall back to another profile', async()=>{
 const m=await load({state,profileRoutes:async()=>[{...route,connectionId:'remote',profile:'other',targetProfile:'other'}],requestProfile:async()=>{throw Error('WRONG PROFILE CALLED')}})
 await assert.rejects(()=>m.deliver({connectionId:'remote',profile:'missing',sessionId:'stored'},'test'),/no exact route/)
})
test('unknown submit status is never reported successful',async()=>{
 const m=await load({state,request:async(method)=>method==='session.resume'?{session_id:'live'}:{status:'rejected'}})
 const r=await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test')
 assert.equal(r.ok,false)
})
test('missing user row cannot return an old reply',async()=>{
 const m=await load({state,request:async(method)=>method==='session.resume'?{session_id:'live'}:{status:'queued'}})
 const r=await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test',1)
 assert.equal(r.reply,null); assert.match(r.note,/correlat/)
})
test('lost submit response is unknown and is not retried',async()=>{
 let submitted=0
 const m=await load({state,request:async(method)=>{
  if(method==='session.resume')return {session_id:'live'}
  submitted++;throw Error('socket timeout')
 }})
 const r=await m.deliver({connectionId:'local',profile:'default',sessionId:'stored'},'test')
 assert.equal(submitted,1); assert.equal(r.status,'unknown'); assert.equal(r.retry_safe,false)
})
