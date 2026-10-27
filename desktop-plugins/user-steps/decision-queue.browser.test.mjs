// Isolated real Chromium layout/keyboard test; no live backend or runtime dependency.
// HERMES_DESKTOP_DIR=<test deps with react/react-dom/esbuild/playwright> node --test this-file
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'
const desktopDir = process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`
const require = createRequire(resolve(desktopDir, 'package.json'))
const { build } = require('esbuild')
const { chromium } = require('playwright')

test('real DOM: 640px and 200% zoom, long inline plan, keyboard and many-request filter', async () => {
 const here=dirname(fileURLToPath(import.meta.url))
 const bundle=await build({stdin:{contents:`
 import React from 'react';import {createRoot} from 'react-dom/client';import {ActionQueue,refreshRequests} from './plugin.js';
 const owner={gatewayId:'fixture',profile:'default',storedSessionId:'S1'};
 const long='Exact files, tests, acceptance criteria and deliverables. '+('long-unbroken-identifier'.repeat(60));
 const requests=Array.from({length:8},(_,i)=>({schemaVersion:2,owner,requestId:'00000000-0000-4000-8000-'+String(i+1).padStart(12,'0'),revision:1,kind:'approval',title:'Request '+i,scope:{complete:true,markdown:long+'\\n'+('Complete plan paragraph.\\n'.repeat(60)),sourceRefs:[]},limits:[{key:'CPU',value:'≤4 CPU core-hours'},{key:'RAM',value:'≤4 GiB RAM'},{key:'GPU',value:'≤2 existing GPU-hours',permissionRequired:'owner permission'}],exclusions:['No rentals or new spend','Full training requires separate budget approval'],outcomes:{approve:'Stage A only',revise:'Work remains blocked',decline:'No execution'},choices:[{id:'yes',label:'Approve Stage A',effect:'approve'},{id:'revise',label:'Revise',effect:'revise'},{id:'no',label:'Decline',effect:'decline'}],aliases:[],state:'open'}));
 window.posts=[];window.hermesDesktop={api:async({path,...opts})=>{if(opts.method==='POST'){window.posts.push(opts.body);return {operation:{operationId:'pending',dispatchState:'pending'},request:requests[0]}}if(path.includes('/operations/'))return {operation:{operationId:'pending',dispatchState:'pending'},request:requests[0]};return {requests}}};
 await refreshRequests();createRoot(document.getElementById('root')).render(React.createElement(ActionQueue));
 `,resolveDir:here,sourcefile:'fixture.mjs'},bundle:true,format:'esm',write:false,nodePaths:[resolve(desktopDir,'node_modules')],plugins:[{name:'sdk-test-only',setup(b){b.onResolve({filter:/^@hermes\/plugin-sdk$/},()=>({path:'sdk',namespace:'test'}));b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:`const atom=v=>({get:()=>v});export const COMPOSER_AREAS={top:'composer.top'},TRANSCRIPT_DIRECTIVE_AREA='directives',host={state:{focusedStoredSessionId:atom('S1'),connectionId:atom('C1'),profile:atom('default'),focusedSessionOwner:atom({connectionId:'C1',profile:'default'})}},useValue=a=>a?.get();`,loader:'js'}))}}]})
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']})
 try {
  const page=await browser.newPage({viewport:{width:640,height:960}})
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.route('http://localhost/**',route=>route.fulfill({body:'<!doctype html><html></html>',contentType:'text/html'}))
  await page.goto('http://localhost/isolated-test')
  await page.setContent('<style>:root{--foreground:#ddd;--card:#202024;--border:#666;--primary:#82bfff;--ring:#82bfff}body{margin:8px;background:#161618;color:#ddd;font:14px system-ui}button,input,textarea{font:inherit}button{color:inherit}#root{width:100%;min-width:0}</style><main id="root"></main>')
  await page.addScriptTag({type:'module',content:bundle.outputFiles[0].text})
  await page.waitForSelector('[data-action-request]')
  assert.equal(await page.locator('[data-action-request]').count(),8)
  for(const zoom of [1,2]){
   await page.evaluate(z=>document.body.style.zoom=z,zoom)
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,`overflow at zoom ${zoom}`)
   const metrics=await page.locator('[data-action-request]').first().evaluate(el=>({scope:el.querySelector('[data-action-scope]').textContent,scroll:el.querySelector('[data-action-scope]').scrollHeight>el.querySelector('[data-action-scope]').clientHeight,buttons:[...el.querySelectorAll('button')].map(b=>b.getBoundingClientRect().height)}))
   assert.match(metrics.scope,/deliverables/);assert.ok(metrics.scope.endsWith('Complete plan paragraph.\n'));assert.equal(metrics.scroll,true);assert.ok(metrics.buttons.every(h=>h>=44))
  }
  await page.evaluate(()=>document.body.style.zoom=1)
  const first=page.locator('[data-action-request]').first()
  await first.getByRole('button',{name:'Revise',exact:true}).click()
  await page.getByRole('textbox',{name:'Revision or answer'}).fill('Keep the plan blocked; change the acceptance criteria')
  await page.keyboard.press('Escape')
  assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Revise')
  await page.keyboard.press('Enter')
  assert.equal(await page.getByRole('textbox',{name:'Revision or answer'}).inputValue(),'Keep the plan blocked; change the acceptance criteria')
  await page.keyboard.press('Control+Enter')
  await page.waitForFunction(()=>window.posts.length===1)
  assert.equal(await page.evaluate(()=>window.posts[0].choiceId),'revise')
  await page.getByRole('textbox',{name:'Filter decisions'}).fill('Request 7')
  assert.equal(await page.locator('[data-action-request]').count(),2,'in-flight row stays visible despite filter')
  assert.match(await page.getByRole('article',{name:'Request 0',exact:true}).innerText(),/delivery pending/)
  await page.getByRole('textbox',{name:'Filter decisions'}).press('ArrowDown')
  assert.equal(await page.evaluate(()=>document.activeElement.tagName),'ARTICLE')
  await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter')
  assert.equal(await page.evaluate(()=>document.activeElement.tagName),'BUTTON')
  assert.equal(await page.evaluate(()=>window.posts.length),1,'keyboard navigation must not approve')
  assert.deepEqual(errors,[])
 } finally {await browser.close()}
})
