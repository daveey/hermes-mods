import {test} from 'node:test'
import assert from 'node:assert/strict'
import {createRequire, register} from 'node:module'
import {resolve} from 'node:path'
import {homedir} from 'node:os'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime'), {JSDOM} = require('jsdom')
const dom = new JSDOM('<div id="root"></div>', {url:'https://example.test'})
globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
const host={requestProfile:async()=>{throw Error('not configured')}}
globalThis.processViewHarness={React,J,host}
register('data:text/javascript,'+encodeURIComponent(`
export async function resolve(s,c,n){
 const stubs={
 'react':'const R=globalThis.processViewHarness.React;export const useState=R.useState,useEffect=R.useEffect',
 'react/jsx-runtime':'const J=globalThis.processViewHarness.J;export const jsx=J.jsx,jsxs=J.jsxs',
 '@hermes/plugin-sdk':'const H=globalThis.processViewHarness,R=H.React;export const COMPOSER_AREAS={top:"composer.top"},useValue=a=>a?.get?.(),host=H.host,Dialog=p=>R.createElement("div",null,p.children),DialogContent=p=>R.createElement("div",{"data-process-view":p["data-process-view"]},p.children),DialogHeader=p=>R.createElement("header",null,p.children),DialogTitle=p=>R.createElement("h2",null,p.children),DialogDescription=p=>R.createElement("p",null,p.children)'
 };return stubs[s]?{url:'data:text/javascript,'+encodeURIComponent(stubs[s]),shortCircuit:true}:n(s,c)
}
export async function load(u,c,n){if(u.endsWith('/plugin.js')){const fs=await import('node:fs/promises');return{format:'module',source:await fs.readFile(new URL(u),'utf8'),shortCircuit:true}}return n(u,c)}
`))
const {processRoute,readProcessView,ProcessView}=await import('../plugin.js')
const target=Object.freeze({id:'proc_owned',session:'stored-owner',gateway:'owner',profile:'work',title:'sleep 600'})
const gateways=[{id:'owner-connection',kind:'remote'},{id:'ambient',kind:'local'}]
const bridge={connectionForHubGateway:g=>g==='owner'?'owner-connection':null}
const route=processRoute(target,bridge,gateways)
test('owner routing pins named connection/profile and has no active-backend fallback',()=>{
 assert.deepEqual(route,{connectionId:'owner-connection',mode:'remote',profile:'work',targetProfile:'work'})
 assert.ok(Object.isFrozen(route))
 assert.throws(()=>processRoute({...target,gateway:'removed'},bridge,gateways),/not connected/)
 assert.throws(()=>processRoute(target,bridge,[]),/not connected/)
 for(const key of ['id','session','gateway','profile']) assert.throws(()=>processRoute({...target,[key]:''},bridge,gateways),/owner is unavailable/)
 assert.equal(processRoute(target,bridge,[{id:'owner-connection',kind:'local'}]).mode,'local')
})
test('process open uses exact owner session attach and scoped list, not ambient RPC or a command',async()=>{
 const calls=[]
 const request=async(...args)=>{calls.push(args);return args[1]==='session.resume'?{stored_session_id:target.session,session_id:'runtime-owner'}:{processes:[{session_id:'proc_other',command:'other'},{session_id:target.id,command:'sleep 600',status:'running',output_tail:'hello <script>text</script>'}]}}
 assert.deepEqual(await readProcessView(target,route,request),{id:target.id,command:'sleep 600',status:'running',output:'hello <script>text</script>',exit_code:undefined})
 assert.deepEqual(calls,[
  [route,'session.resume',{session_id:target.session,lazy:true,omit_messages:true},30000,{spawnPriority:'foreground'}],
  [route,'process.list',{session_id:'runtime-owner'},10000]
 ])
})
test('a different resumed session never enters process.list',async()=>{
 const calls=[]
 await assert.rejects(readProcessView(target,route,async(...args)=>{calls.push(args);return{stored_session_id:'foreign',session_id:'rt'}}),/owning session changed/)
 assert.equal(calls.length,1)
 await assert.rejects(readProcessView(target,route,async()=>({stored_session_id:target.session})),/owning session changed/)
})
test('missing, malformed and foreign process inventory fail explicitly',async()=>{
 for(const response of [{processes:[]},{processes:[{session_id:'proc_other'}]},{processes:{}},null]){
  await assert.rejects(readProcessView(target,route,async(_r,method)=>method==='session.resume'?{stored_session_id:target.session,session_id:'rt'}:response),/inventory|unavailable/)
 }
})
const {createRoot}=require('react-dom/client')
const root=createRoot(document.getElementById('root'))
const paint=async props=>React.act(async()=>root.render(props?J.jsx(ProcessView,{onClose:()=>{},...props}):null))
test('mounted viewer draws real command/status/output as text and refresh is explicit',async()=>{
 let reads=0
 host.requestProfile=async(_r,method)=>method==='session.resume'?{stored_session_id:target.session,session_id:'rt'}:(reads++,{processes:[{session_id:target.id,command:'sleep 600',status:'running',output_tail:'<script>plain text</script>'}]})
 await paint({target,route})
 assert.equal(reads,1)
 assert.equal(document.querySelector('h2').textContent,'sleep 600')
 assert.equal(document.querySelector('[role=status]').textContent,'running')
 assert.equal(document.querySelector('pre').textContent,'<script>plain text</script>')
 assert.equal(document.querySelectorAll('script').length,0)
 await React.act(async()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='Refresh').click())
 assert.equal(reads,2)
 await paint(null)
})
test('foreign checkpoint missing from RPC keeps only a labelled at-open observation', async()=>{
 host.requestProfile=async(_r,method)=>method==='session.resume'?{stored_session_id:target.session,session_id:'rt'}:{processes:[]}
 await paint({target:{...target,detail:'running'},route})
 assert.equal(document.querySelector('[data-process-snapshot]').textContent,'Panel observation at open: running')
 assert.match(document.querySelector('[role=status]').textContent,/no longer in the owning session/)
 assert.equal(document.querySelector('[data-process-output]'),null)
 await paint(null)
})

test('late completion from a closed viewer cannot replace the next owner',async()=>{
 let release
 host.requestProfile=async(_r,method,params)=>{
  if(method==='session.resume') return{stored_session_id:params.session_id,session_id:params.session_id}
  if(params.session_id===target.session)return new Promise(r=>{release=r})
  return{processes:[{session_id:'proc_next',command:'next owner',status:'exited',exit_code:0}]}
 }
 await paint({target,route})
 const next={...target,id:'proc_next',session:'next-session'}
 await paint({target:next,route})
 assert.equal(document.querySelector('h2').textContent,'next owner')
 await React.act(async()=>release({processes:[{session_id:target.id,command:'wrong late owner',status:'running'}]}))
 assert.equal(document.querySelector('h2').textContent,'next owner')
 assert.equal(document.querySelector('[role=status]').textContent,'exited · exit 0')
 await paint(null)
})
