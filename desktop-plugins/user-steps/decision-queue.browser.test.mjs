// Isolated real Chromium layout/keyboard test; fake REST, real Desktop Markdown renderer.
// HERMES_DESKTOP_DIR=<core apps/desktop checkout with dependencies> node --test this-file
// No playwright headless shell installed ("chromium_headless_shell-NNNN not installed")? Use the system Chrome:
// CHROME_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' node --test this-file
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'
import { mkdir } from 'node:fs/promises'
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
 let requests=Array.from({length:8},(_,i)=>({schemaVersion:2,owner,requestId:'00000000-0000-4000-8000-'+String(i+1).padStart(12,'0'),revision:1,kind:'approval',title:'Request '+i,createdAt:new Date(Date.now()-305000).toISOString(),scope:{complete:true,markdown:long+'\\n'+('Complete plan paragraph.\\n'.repeat(60)),sourceRefs:[]},limits:[{key:'CPU',value:'≤4 CPU core-hours'},{key:'RAM',value:'≤4 GiB RAM'},{key:'GPU',value:'≤2 existing GPU-hours',permissionRequired:'owner permission'}],exclusions:['No rentals or new spend','Full training requires separate budget approval'],outcomes:{approve:'Stage A only',revise:'Work remains blocked',decline:'No execution'},choices:[{id:'yes',label:'Approve Stage A',effect:'approve'},{id:'revise',label:'Request changes',effect:'revise'},{id:'no',label:'Decline',effect:'decline'}],aliases:[],state:'open'}));
 window.setRequests=async value=>{requests=value;await refreshRequests()};window.fixtureOwner=owner;window.posts=[];window.withdraws=[];window.hermesDesktop={api:async({path,...opts})=>{if(path.endsWith('/ping'))return {ok:true,api:2,features:['withdraw']};if(opts.method==='POST'&&path.endsWith('/withdraw')){window.withdraws.push({path,connectionId:opts.connectionId,profile:opts.profile,body:opts.body});const id=decodeURIComponent(path.split('/').at(-2));requests=requests.map(r=>r.requestId===id?{...r,state:'withdrawn',withdrawal:{by:'user',reason:opts.body.reason,at:new Date().toISOString()}}:r);return {request:requests.find(r=>r.requestId===id)}}if(opts.method==='POST'){window.posts.push(opts.body);return {operation:{operationId:'pending',dispatchState:'pending'},request:requests[0]}}if(path.includes('/operations/'))return {operation:{operationId:'pending',dispatchState:'pending'},request:requests[0]};return {requests}}};
 await refreshRequests();createRoot(document.getElementById('root')).render(React.createElement(ActionQueue));
 `,resolveDir:here,sourcefile:'fixture.mjs'},bundle:true,format:'esm',write:false,outdir:'fixture-output',nodePaths:[resolve(desktopDir,'node_modules'),resolve(desktopDir,'../../node_modules')],define:{'process.env.NODE_ENV':'"production"','import.meta.env':'{"DEV":false,"PROD":true}'},plugins:[{name:'sdk-test-only',setup(b){b.onResolve({filter:/^@hermes\/plugin-sdk$/},()=>({path:'sdk',namespace:'test'}));b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:`const atom=v=>({get:()=>v});export const COMPOSER_AREAS={top:'composer.top'},TRANSCRIPT_DIRECTIVE_AREA='directives',host={state:{focusedStoredSessionId:atom('S1'),connectionId:atom('C1'),profile:atom('default'),focusedSessionOwner:atom({connectionId:'C1',profile:'default'})}},useValue=a=>a?.get();export {MessageTextContent} from ${JSON.stringify(resolve(desktopDir,'src/components/assistant-ui/markdown-text.tsx'))};`,loader:'js',resolveDir:desktopDir}))}}]})
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {}),args:['--no-sandbox']})
 try {
  const page=await browser.newPage({viewport:{width:640,height:960}})
  const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.error(e.stack)})
  await page.route('http://localhost/**',route=>route.fulfill({body:'<!doctype html><html></html>',contentType:'text/html'}))
  await page.goto('http://localhost/isolated-test')
  await page.setContent('<style>:root{--foreground:#ddd;--card:#202024;--border:#666;--primary:#82bfff;--ring:#82bfff}body{margin:8px;background:#161618;color:#ddd;font:14px system-ui}button,input,textarea{font:inherit}button{color:inherit}#root{width:100%;min-width:0}</style><main id="root"></main>')
  await page.addScriptTag({type:'module',content:bundle.outputFiles[0].text})
  await page.waitForSelector('[data-action-request]')
  assert.equal(await page.locator('[data-action-request]').count(),8)
  // Not needed and the age label are part of every layout check below (owner ping advertises withdraw).
  await page.waitForSelector('[data-withdraw-request]')
  assert.equal(await page.locator('[data-withdraw-request]').count(),8,'every undecided tile offers Not needed')
  assert.match(await page.locator('[data-action-request]').first().locator('[data-asked-age]').innerText(),/^asked \d+ min ago$/)
  for(const zoom of [1,2]){
   await page.evaluate(z=>document.body.style.zoom=z,zoom)
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,`overflow at zoom ${zoom}`)
   const metrics=await page.locator('[data-action-request]').first().evaluate(el=>({scope:el.querySelector('[data-action-scope]').textContent,scroll:el.querySelector('[data-action-scope]').scrollHeight>el.querySelector('[data-action-scope]').clientHeight,buttons:[...el.querySelectorAll('button')].map(b=>b.getBoundingClientRect().height)}))
   assert.match(metrics.scope,/deliverables/);assert.ok(metrics.scope.trimEnd().endsWith('Complete plan paragraph.'));assert.equal(metrics.scope.match(/Complete plan paragraph\./g).length,60);assert.equal(metrics.scroll,true);assert.ok(metrics.buttons.every(h=>h>=44))
  }
  await page.evaluate(()=>document.body.style.zoom=1)
  const first=page.locator('[data-action-request]').first()
  await first.getByRole('button',{name:'Request changes',exact:true}).click()
  await page.getByRole('textbox',{name:'Revision or answer'}).fill('Keep the plan blocked; change the acceptance criteria')
  await page.keyboard.press('Escape')
  assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Request changes')
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
  // Real SDK Markdown renderer, sanitized representative single-request fixtures.
  await page.getByRole('textbox',{name:'Filter decisions'}).fill('')
  const fixture={schemaVersion:2,owner:{gatewayId:'fixture',profile:'default',storedSessionId:'S1'},requestId:'00000000-0000-4000-8000-000000000090',revision:1,kind:'approval',title:'Open a pull request for the settings update',scope:{complete:true,markdown:'**Review the settings update** before opening a pull request.\n\n- Update `settings.js` and its unit tests.\n- Run the settings test suite.\n- Deliver a draft pull request with test results.',sourceRefs:[]},limits:[],exclusions:['Do not merge or deploy.'],outcomes:{approve:'Open the draft pull request only.',revise:'Keep work blocked and revise the plan.',decline:'Do not open the pull request.'},choices:[{id:'yes',label:'Open draft pull request',effect:'approve'},{id:'edit',label:'Revise plan',effect:'revise'},{id:'no',label:'Decline',effect:'decline'}],aliases:[],state:'open'}
  await page.evaluate(value=>window.setRequests([value]),fixture)
  const card=page.getByRole('article',{name:fixture.title})
  await card.waitFor()
  await card.locator('[data-action-scope] [data-streamdown="strong"]').waitFor()
  assert.equal(await card.locator('[data-action-scope] [data-streamdown="strong"]').innerText(),'Review the settings update')
  assert.equal(await card.locator('[data-action-scope] li').count(),3)
  assert.equal(await card.locator('[data-action-scope] code').innerText(),'settings.js')
  assert.equal(await card.getByRole('heading',{name:'Read this'}).count(),1)
  assert.equal(await card.getByRole('heading',{name:'Your decision'}).count(),1)
  const screenshots=process.env.APPROVAL_SCREENSHOTS
  if(screenshots)await mkdir(screenshots,{recursive:true})
  for(const width of [640,360]){
   await page.setViewportSize({width,height:1000})
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   if(screenshots)await page.locator('[data-action-queue]').screenshot({path:resolve(screenshots,`complete-${width}.png`)})
  }
  await card.getByRole('button',{name:'Approve',exact:true}).focus()
  await page.keyboard.press('Enter')
  await page.waitForFunction(()=>window.posts.length===2)
  assert.equal(await page.evaluate(()=>window.posts[1].choiceId),'yes')
  assert.equal(await page.evaluate(()=>window.posts[1].expectedRevision),1)
  const incomplete={...fixture,requestId:'00000000-0000-4000-8000-000000000091',title:'Confirm the proposed settings update',state:'needs_context',scope:{complete:false,markdown:'Do you mean **open a pull request**, or something else?',sourceRefs:[]},outcomes:{revise:'Request complete action; keep work blocked.'},exclusions:[],choices:[{id:'request-context',label:'Request complete action',effect:'revise'}]}
  await page.evaluate(value=>window.setRequests([value]),incomplete)
  const blocked=page.getByRole('article',{name:incomplete.title})
  await blocked.locator('[data-action-scope] [data-streamdown="strong"]').waitFor()
  // desktop-1321064734: a plan-less ask offers exactly two things to do, never a greyed-out Approve. This owner
  // advertises withdraw, so the second is the owner-side Not needed (older owners get the local Dismiss).
  assert.equal(await blocked.getByRole('button',{name:'Approve',exact:true}).count(),0)
  assert.deepEqual(await blocked.getByRole('button').allInnerTexts(),['Ask agent for the plan','Not needed'])
  assert.match(await blocked.locator('[data-approval-blocked]').innerText(),/without a plan/)
  for(const width of [640,360]){
   await page.setViewportSize({width,height:1000})
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   if(screenshots)await page.locator('[data-action-queue]').screenshot({path:resolve(screenshots,`incomplete-${width}.png`)})
  }
  assert.match(await blocked.locator('[data-agent-completion-pending]').innerText(),/asked you to approve this without a plan/)
  assert.equal(await blocked.getByRole('button',{name:/Request/}).count(),0)
  assert.equal(await page.evaluate(()=>window.posts.length),2,'agent completion does not require a user request')
  const question={...fixture,requestId:'00000000-0000-4000-8000-000000000092',kind:'choice',title:'Which output format?',scope:{complete:true,markdown:'Choose the output format.',sourceRefs:[]},outcomes:{answer:'Record the answer only; execution is not authorized.'},choices:[{id:'json',label:'JSON document',effect:'answer'},{id:'answer-text',label:'Something else',effect:'answer'}]}
  await page.evaluate(value=>window.setRequests([value]),question)
  const clarification=page.getByRole('article',{name:question.title})
  await clarification.waitFor()
  assert.equal(await clarification.getByRole('button',{name:/Approve|Request changes|Request full plan/}).count(),0)
  await clarification.getByRole('button',{name:'Something else',exact:true}).click()
  await page.getByRole('textbox',{name:'Revision or answer'}).fill('Plain text')
  await page.keyboard.press('Escape')
  await clarification.getByRole('button',{name:'JSON document',exact:true}).click()
  await page.waitForFunction(()=>window.posts.length===3)
  assert.equal(await page.evaluate(()=>window.posts[2].revisionText),'JSON document')
  assert.equal(await page.evaluate(()=>window.posts[2].choiceId),'json')
  // Not needed: keyboard-reachable right after the decision buttons with a visible focus ring; one withdraw POST
  // through the owner route; the tile leaves, focus stays in the queue, and no decision is recorded.
  const stale={...fixture,requestId:'00000000-0000-4000-8000-000000000094',title:'Stale ask to dismiss',createdAt:new Date(Date.now()-3*3600e3-60e3).toISOString()}
  const kept={...fixture,requestId:'00000000-0000-4000-8000-000000000095',title:'Ask that stays'}
  await page.evaluate(value=>window.setRequests(value),[stale,kept])
  const staleCard=page.getByRole('article',{name:stale.title})
  await staleCard.getByRole('button',{name:'Not needed'}).waitFor()
  assert.equal(await staleCard.locator('[data-asked-age]').innerText(),'asked 3 h ago')
  for(const width of [640,360]){
   await page.setViewportSize({width,height:1000})
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`overflow at ${width}px with Not needed`)
   if(screenshots)await page.locator('[data-action-queue]').screenshot({path:resolve(screenshots,`withdraw-${width}.png`)})
  }
  await staleCard.getByRole('button',{name:'Decline',exact:true}).focus()
  await page.keyboard.press('Tab')
  const focused=await page.evaluate(()=>{const a=document.activeElement,s=getComputedStyle(a);return {text:a.textContent,title:a.title,outline:s.outlineStyle,width:s.outlineWidth,height:a.getBoundingClientRect().height}})
  assert.equal(focused.text,'Not needed');assert.match(focused.title,/without deciding; nothing is sent to the agent/)
  assert.equal(focused.outline,'solid');assert.equal(focused.width,'2px');assert.ok(focused.height>=44)
  await page.keyboard.press('Enter')
  await page.waitForFunction(()=>window.withdraws.length===1)
  assert.deepEqual(await page.evaluate(()=>window.withdraws),[{path:`/api/plugins/user-steps/requests/${stale.requestId}/withdraw`,connectionId:'C1',profile:'default',body:{session:'S1',expectedRevision:1,reason:'Dismissed in Desktop as not needed'}}])
  await page.waitForFunction(()=>document.querySelectorAll('[data-action-request]').length===1)
  assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),kept.title,'focus moves to the remaining ask')
  assert.equal(await page.locator('[data-action-queue] h2').innerText(),'For you (1)')
  assert.equal(await page.evaluate(()=>window.posts.length),3,'Not needed records no decision')
  assert.deepEqual(errors,[])
 } finally {await browser.close()}
})
