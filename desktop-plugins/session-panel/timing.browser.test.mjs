// Chromium visibility proof; this is an owned fixture, not native Desktop proof.
// HERMES_DESKTOP_DIR contains CI dependencies plus esbuild/playwright.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'
const here = dirname(fileURLToPath(import.meta.url)), deps = process.env.HERMES_DESKTOP_DIR
const require = createRequire(resolve(deps, 'package.json'))
const {build} = require('esbuild'), {chromium} = require('playwright')
const fixtures = JSON.parse(execFileSync(process.env.HERMES_MODS_PYTHON || 'python3', [resolve(here, 'test/hub-model-fixtures.py')], {encoding:'utf8'}))

test('timing text stays inside the painted panel across ticks, replacement and remount', async () => {
 const bundle = await build({stdin:{contents:`
 import React from 'react'; import {createRoot} from 'react-dom/client'; import {renderModel} from './plugin.js';
 let root = createRoot(document.getElementById('root'));
 window.paint = (model, now, remount) => { if (remount) {root.unmount(); root=createRoot(document.getElementById('root'))} root.render(renderModel(model,{now})); };
 `,resolveDir:here,sourcefile:'timing-fixture.mjs'},bundle:true,format:'esm',write:false,nodePaths:[resolve(deps,'node_modules')],define:{'process.env.NODE_ENV':'"production"'},
 plugins:[{name:'unused-sdk',setup(b){b.onResolve({filter:/^@hermes\/plugin-sdk$/},()=>({path:'sdk',namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:'export const host={},COMPOSER_AREAS={top:"top"},useValue=()=>null'}))}}]})
 const browser = await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})})
 try {
  const page = await browser.newPage({viewport:{width:1100,height:1000}}), errors=[]
  page.on('pageerror',e=>errors.push(e.message))
  await page.route('http://localhost/**',r=>r.fulfill({body:'<style>body{margin:0;font:14px system-ui}#root{width:100%;overflow:hidden}</style><main id="root"></main>',contentType:'text/html'}))
  await page.goto('http://localhost/timing-fixture'); await page.addScriptTag({type:'module',content:bundle.outputFiles[0].text})
  await page.waitForFunction(()=>typeof window.paint==='function')
  const visible = async (selector, suffix) => {
   const metrics = await page.locator(selector).first().evaluate((el,suffix)=>{
    const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT), rects=[]; let text
    while ((text=walker.nextNode())) { const start=suffix?text.textContent.lastIndexOf(suffix):0;if(start<0)continue;const range=document.createRange();range.setStart(text,start);range.setEnd(text,text.textContent.length);rects.push(...Array.from(range.getClientRects()).map(r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom}))) }
    const panel=el.closest('[data-session-panel]').getBoundingClientRect(), bounds=el.getBoundingClientRect()
    return {text:el.textContent,rects,panel:{left:panel.left,right:panel.right},bounds:{left:bounds.left,right:bounds.right,top:bounds.top,bottom:bounds.bottom},width:innerWidth}
   },suffix)
   assert.ok(metrics.rects.length,JSON.stringify(metrics))
   for(const r of metrics.rects) assert.ok(r.left>=metrics.panel.left && r.right<=Math.min(metrics.width,metrics.panel.right)+1 && r.top>=metrics.bounds.top-1 && r.bottom<=metrics.bounds.bottom+1,`clipped timing: ${JSON.stringify(metrics)}`)
  }
  for(const width of [1100,700,420]) {
   await page.setViewportSize({width,height:1000})
   for(const nested of [false,true]) for(const step of [0,60,120]) {
    const {now,model:base}=fixtures.elapsed_layout, model=structuredClone(base)
    // Running children are a renderer compatibility control: current producer
    // normally nests waiting children beneath running parents, not vice versa.
    if(nested) { const row=model.sections.running[0], parent={...row,id:'parent',key:'parent',title:'Parent',detail:'',clock_text:{},parent:null};row.parent='parent';row.depth=1;model.sections.running.unshift(parent) }
    model.revision+=`-${nested}-${step}`
    await page.evaluate(({model,now,remount})=>window.paint(model,now,remount),{model,now:now+step,remount:step===120})
    await page.waitForFunction(rev=>document.querySelector('[data-hub-model]')?.dataset.hubModel===rev,model.revision)
    await visible('[data-panel-row$="t_elapsed"] [data-row-detail]',`${7+step/60}m`)
   }
   for(const kind of ['ci','deploy','clock']) for(const mode of ['normal','over','unknown']) for(const step of [0,60,120]) {
    const fixture=fixtures[`eta_${kind}_${mode}`],model=structuredClone(fixture.model); model.revision+=`-${step}`
    await page.evaluate(({model,now,remount})=>window.paint(model,now,remount),{model,now:fixture.now+step,remount:step===120})
    await page.waitForFunction(rev=>document.querySelector('[data-hub-model]')?.dataset.hubModel===rev,model.revision)
    await visible('[data-wait-eta]')
    const text=await page.locator('[data-wait-eta]').textContent()
    assert.match(text,mode==='unknown'?/\/ \?—/:mode==='over'?/\+/:/\/ 25m/)
   }
  }
  assert.deepEqual(errors,[])
 } finally {await browser.close()}
})
