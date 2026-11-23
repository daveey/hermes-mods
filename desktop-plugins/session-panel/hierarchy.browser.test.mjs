// Real Chromium CSS/layout control, not native Desktop acceptance.
// HERMES_DESKTOP_DIR=<isolated npm deps: react, react-dom, esbuild, playwright> node --test this-file
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
const here = dirname(fileURLToPath(import.meta.url))
const deps = process.env.HERMES_DESKTOP_DIR
assert.ok(deps, 'provide isolated HERMES_DESKTOP_DIR dependencies')
const require = createRequire(resolve(deps, 'package.json'))
const { build } = require('esbuild'), { chromium } = require('playwright')
const fixtures = JSON.parse(execFileSync(process.env.HERMES_MODS_PYTHON || 'python3', [resolve(here, 'test/hub-model-fixtures.py')], { encoding: 'utf8' }))
test('real renderer tree guides survive current, legacy and missing theme tokens', async () => {
 const bundle = await build({ stdin: { contents: `
 import React from 'react'; import {createRoot} from 'react-dom/client'; import {renderModel} from './plugin.js';
 const root = createRoot(document.getElementById('root'));
 window.paint = (model, now = 1800000000) => root.render(renderModel(model, {now}));
 `, resolveDir: here, sourcefile: 'fixture.mjs' }, bundle: true, format: 'esm', write: false,
 nodePaths: [resolve(deps, 'node_modules')], define: {'process.env.NODE_ENV': '"production"'},
 plugins: [{name: 'unused-sdk', setup(b) {
 b.onResolve({filter: /^@hermes\/plugin-sdk$/}, () => ({path: 'sdk', namespace: 'fixture'}));
 b.onLoad({filter: /.*/, namespace: 'fixture'}, () => ({contents: 'export const host={},Codicon=()=>null,DisclosureCaret=()=>null,COMPOSER_AREAS={top:"top"},useValue=()=>null'}));
 }}] })
 const browser = await chromium.launch({headless: true, args: ['--no-sandbox'], ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})})
 try {
  const page = await browser.newPage({viewport: {width: 900, height: 1000}})
  const errors = []; page.on('pageerror', e => errors.push(e.message))
  await page.route('http://localhost/**', r => r.fulfill({body: '<main id="root"></main>', contentType: 'text/html'}))
  await page.goto('http://localhost/hierarchy-fixture')
  await page.addScriptTag({type: 'module', content: bundle.outputFiles[0].text})
  await page.waitForFunction(() => typeof window.paint === 'function')
  for (const [theme, tokens, background, foreground] of [
   ['current-dark', '--dt-border:#586878', '#15181c', '#eeeeee'],
   ['current-light', '--dt-border:#667788', '#ffffff', '#202020'],
   ['legacy', '--border:#607080', '#15181c', '#eeeeee'],
   ['missing', '', '#15181c', '#eeeeee']
  ]) {
   await page.locator('html').evaluate((el, css) => el.setAttribute('style', css), tokens)
   await page.locator('body').evaluate((el, colors) => {el.style.background=colors[0];el.style.color=colors[1];el.style.font='14px system-ui'}, [background, foreground])
   for (const name of ['tree_direct', 'tree_multihop', 'tree_cross_section', 'tree_group', 'tree_depth']) {
    const model = fixtures[name].model
    await page.evaluate(model => window.paint(model), model)
    await page.waitForFunction(rev => document.querySelector('[data-hub-model]')?.dataset.hubModel === rev, model.revision)
    const metrics = await page.locator('[data-tree-guide]').evaluateAll(nodes => nodes.map(el => {
     const s=getComputedStyle(el), r=el.getBoundingClientRect();
     return {border:s.borderLeft,width:s.borderLeftWidth,style:s.borderLeftStyle,color:s.borderLeftColor,height:r.height}
    }))
    assert.ok(metrics.length > 0, `${theme}/${name} has guides`)
    for (const guide of metrics) {
     assert.equal(guide.width, '1px', `${theme}/${name}: ${guide.border}`)
     assert.equal(guide.style, 'solid')
     assert.notEqual(guide.color, 'rgba(0, 0, 0, 0)')
     assert.ok(guide.height > 0)
    }
    const nested = await page.locator('[data-tree-parent]').evaluateAll(nodes => nodes.map(el => {
     const own=el.querySelector(':scope > [data-panel-row]'), parent=el.parentElement.parentElement.querySelector(':scope > [data-panel-row]');
     return own.getBoundingClientRect().x-parent.getBoundingClientRect().x
    }))
    assert.ok(nested.every(x => x >= 24 && x <= 26), `${theme}/${name}: indent ${nested}`)
    if (process.env.HIERARCHY_SCREENSHOTS && name === 'tree_group') {
     await mkdir(process.env.HIERARCHY_SCREENSHOTS, {recursive:true})
     await page.screenshot({path:resolve(process.env.HIERARCHY_SCREENSHOTS, `${theme}.png`)})
    }
   }
  }
  // Real reducer copy, with an intentionally long administrative reason. A DOM
  // text assertion alone cannot catch an age hidden by overflow/ellipsis.
  for (const width of [1100, 700, 420]) {
   await page.setViewportSize({width, height: 1000})
   const fixture = fixtures.agent_held_local
   await page.evaluate(({model, now}) => window.paint(model, now), fixture)
   await page.waitForFunction(() => document.querySelector('[data-row-detail]')?.textContent.includes('in triage'))
   const metric = await page.locator('[data-row-detail]').first().evaluate(el => {
    const text = el.firstChild, content = el.textContent
    const start = content.lastIndexOf('in triage')
    const range = document.createRange(); range.setStart(text, start); range.setEnd(text, content.length)
    const age = range.getBoundingClientRect(), bounds = el.getBoundingClientRect()
    return {text:content, age:{left:age.left,right:age.right,top:age.top,bottom:age.bottom},
     bounds:{left:bounds.left,right:bounds.right,top:bounds.top,bottom:bounds.bottom},
     overflow:el.scrollWidth > el.clientWidth, viewport:innerWidth}
   })
   assert.ok(metric.text.includes('5h 20m'), JSON.stringify(metric))
   assert.ok(!metric.overflow && metric.age.right <= metric.bounds.right + 1 && metric.age.left >= metric.bounds.left - 1 && metric.age.right <= width, `${width}px: age clipped: ${JSON.stringify(metric)}`)
   assert.ok(metric.age.bottom <= metric.bounds.bottom + 1, `${width}px: age clipped vertically`)
   if (process.env.HIERARCHY_SCREENSHOTS) await page.screenshot({path:resolve(process.env.HIERARCHY_SCREENSHOTS, `age-${width}.png`)})
  }
  assert.deepEqual(errors, [])
 } finally { await browser.close() }
})
