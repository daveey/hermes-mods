import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve } from 'node:path'
import { homedir } from 'node:os'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime'), {JSDOM}=require('jsdom')
const dom=new JSDOM('<div id="root"></div>',{url:'https://example.test'})
globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
globalThis.elapsedHarness={React,J}
register('data:text/javascript,'+encodeURIComponent(`export async function resolve(s,c,n){const m={'react':'const R=globalThis.elapsedHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.elapsedHarness.J;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':'export const host={},Codicon=()=>null,DisclosureCaret=()=>null,COMPOSER_AREAS={},useValue=()=>null'};return m[s]?{url:'data:text/javascript,'+encodeURIComponent(m[s]),shortCircuit:true}:n(s,c)}`))
const m=await import('../plugin.js'),{createRoot}=require('react-dom/client')
test('actual WaitLine advances from cached episode and preserves duration on remount', async()=>{
 const realNow=Date.now,now=Date.parse('2026-11-21T10:00:00Z'),s=now/1000
 Date.now=()=>now
 const root=createRoot(document.getElementById('root'))
 const chips=['ci','deploy'].map(kind=>({id:`elapsed-${kind}`,kind:'kanban',state:'scheduled',connectionId:'elapsed-dom',profile:'default',board:'default',waitingOn:`${kind} PR #123`,waitingSince:s-60}))
 const detail=kind=>({task:{id:`elapsed-${kind}`,status:'scheduled',title:kind,wait_episode:{kind,since:s-720},wake_check:{cmd:'probe',next_at:s+120}},links:{parents:[]},events:[{id:1,kind:'scheduled',created_at:s-60,payload:{awaiting:{kind,ref:'PR #123'},wake_at:s+3600}}]})
 try {
  for (const chip of chips) m.cardInfo(chip,chip.id,now,async()=>({detail:detail(chip.id.slice(8)),board:'default'}))
  await new Promise(r=>setImmediate(r))
  const render=async at=>React.act(async()=>{root.render(J.jsxs('div',{children:chips.map(chip=>J.jsx(m.WaitLine,{i:{key:chip.id,kind:'kanban',label:chip.id,chip},now:at},chip.id))}))})
  await render(now)
  for(const label of ['CI','Deploy']) assert.match(document.body.textContent,new RegExp(`${label} · waiting 12m`))
  assert.match(document.body.textContent,/next check in 2m/)
  assert.ok([...document.querySelectorAll('[data-wait-age]')].every(e=>e.textContent===''))
  await render(now+60_000)
  for(const label of ['CI','Deploy']) assert.match(document.body.textContent,new RegExp(`${label} · waiting 13m`))
  await React.act(async()=>root.render(null))
  await render(now+60_000)
  assert.match(document.body.textContent,/Deploy · waiting 13m/)
 } finally {await React.act(async()=>root.unmount());Date.now=realNow;dom.window.close()}
})
