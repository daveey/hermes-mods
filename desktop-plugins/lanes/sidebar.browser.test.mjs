// Manual real-layout/interaction proof. Install esbuild + playwright beside CI's desktop deps,
// then HERMES_DESKTOP_PACKAGE=/path/package.json node --test desktop-plugins/lanes/sidebar.browser.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
const require = createRequire(process.env.HERMES_DESKTOP_PACKAGE || new URL('./tests/package.json', import.meta.url))
const { build } = require('esbuild')
const { chromium } = require('playwright')
const here = dirname(fileURLToPath(import.meta.url))

test('sidebar: one-row geometry, independent pointer/keyboard folds and remount persistence', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'lanes-sidebar-'))
  const errors = [], receipts = []
  let browser
  try {
    const bundle = await build({ stdin: { contents: `
      import React from 'react'; import {createRoot} from 'react-dom/client';
      import {SidebarLanes, sourceFor} from './plugin.js';
      let root, push;
      window.render = async variant => {
        root?.unmount(); localStorage.clear();
        const snap = variant === 'loading' ? undefined : {
          configured:true, status:{run:variant === 'long' ? 123456789012345 : 7,you:3},totals:{not_ready:41},
          sidebar:{cost_today:12.34,idle_lanes:[]},
          governors:{idle_total:31,counts:{on:1,off:8,at_cap:2,unknown:variant === 'long'?987654321098765:4},
            rows:[{id:'llm_pool',name:'LLM pool',state:'on',cards_charged:31,reading:{used_pct:97,accounts:23},threshold:95,clears:'Release below cap',scope:[]},
              {id:'off',name:'Off governor',state:'off'}, {id:'unknown',name:'Unknown governor',state:'unknown'}]},
          lanes:{work:{share:20,actual:.2,target:.3,governor_held:31,status:{run:7,you:3},not_ready:41,steps:[],boards:[]}}};
        if(variant === 'unknown'){snap.flow_complete=false;snap.status.run=null;snap.governors=undefined}
        const ctx={rest:async()=>snap};
        sourceFor(ctx).subscribe=(_p,cb)=>{push=cb;if(snap)cb(snap);return Object.assign(()=>{},{refresh(){},update(){}})};
        window.remount=()=>{root?.unmount();root=createRoot(document.getElementById('root'));root.render(React.createElement(SidebarLanes,{ctx}))};
        window.remount();
        if(variant==='offline')setTimeout(()=>push(undefined,Error('offline')),50);
      };
    `, resolveDir: here }, bundle: true, write: false, format: 'iife', nodePaths: [resolve(dirname(require.resolve('react/package.json')), '..')],
      plugins: [{ name: 'sdk', setup(b) { b.onResolve({ filter: /^@hermes\/plugin-sdk$/ }, () => ({ path: 'sdk', namespace: 'stub' })); b.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({ contents: 'export const host = {profileRoutes:async()=>[],navigate(){}}; export const PALETTE_AREA="palette"' })) } }] })
    const css = readFileSync(join(here, 'plugin.js'), 'utf8').split('const CSS = `')[1].split('`')[0]
    writeFileSync(join(dir, 'index.html'), `<style>*{box-sizing:border-box}body{margin:0;font:13px Arial;background:#14141b;--ui-text-primary:#ddd;--ui-text-tertiary:#999}${css}</style><div id="root"></div><script>${bundle.outputFiles[0].text}</script>`)
    browser = await chromium.launch({ headless: true })
    const page = await browser.newPage()
    page.on('pageerror', e => errors.push(e.message))
    await page.goto('file://' + join(dir, 'index.html'))
    for (const width of [180, 240, 320, 480]) for (const variant of ['normal', 'loading', 'unknown', 'offline', 'long']) {
      await page.setViewportSize({ width, height: 900 })
      await page.evaluate(v => window.render(v), variant)
      const lanes = page.getByRole('button', { name: 'LANES', exact: true })
      const gov = page.getByRole('button', { name: 'GOVERNORS', exact: true })
      await lanes.waitFor()
      if (variant === 'offline') await page.waitForFunction(() => document.querySelector('[aria-label=LANES]').textContent.includes('offline'))
      const rows = await page.evaluate(() => [...document.querySelectorAll('[data-sidebar-section]')].map(section => {
        const button = section.querySelector('button'), summary = button.querySelector('.lanes-sb-summary')
        const s = section.getBoundingClientRect(), b = button.getBoundingClientRect(), t = summary.getBoundingClientRect()
        return { section: section.dataset.sidebarSection, height:s.height, buttonHeight:b.height, top:s.top, buttonTop:b.top,
          overflow:document.documentElement.scrollWidth > innerWidth, children:section.children.length,
          summaryHeight:t.height, clipped:summary.scrollWidth>summary.clientWidth,
          accessible:button.getAttribute('aria-description')===summary.textContent && summary.title===summary.textContent }
      }))
      for (const r of rows) {
        assert.equal(r.height, 24, JSON.stringify({width,variant,r}))
        assert.equal(r.buttonHeight, r.height); assert.equal(r.top, r.buttonTop)
        assert.equal(r.children, 1); assert.equal(r.summaryHeight, 24)
        assert.equal(r.overflow, false); assert.equal(r.accessible, true)
        if (variant === 'long' && width <= 320) assert.equal(r.clipped, true)
      }
      assert.equal(await page.locator('[data-sidebar-body], .lanes-share-bar, [data-governor], details').count(), 0)
      receipts.push({width,variant,rows})
      if (variant !== 'normal') continue
      // Real pointer opens GOVERNORS only; native Enter/Space toggle either section independently.
      await gov.click()
      assert.equal(await gov.getAttribute('aria-expanded'), 'true')
      assert.equal(await lanes.getAttribute('aria-expanded'), 'false')
      assert.equal(await page.locator('[data-sidebar-body=governors] [data-governor=llm_pool]').count(), 1)
      await lanes.focus(); await page.keyboard.press('Enter')
      assert.equal(await lanes.getAttribute('aria-expanded'), 'true')
      assert.equal(await page.locator('.lanes-share-bar').count(), 1)
      assert.match(await page.locator('[data-sidebar-body=lanes]').textContent(), /\$12.34 today/)
      await gov.focus(); await page.keyboard.press('Space')
      assert.equal(await gov.getAttribute('aria-expanded'), 'false')
      assert.equal(await lanes.getAttribute('aria-expanded'), 'true')
      await page.evaluate(() => window.remount())
      await page.waitForFunction(() => document.querySelector('[aria-label=LANES]')?.getAttribute('aria-expanded')==='true')
      assert.equal(await gov.getAttribute('aria-expanded'), 'false')
      await lanes.click()
      await gov.focus(); await page.keyboard.press('Enter')
      await page.evaluate(() => window.remount())
      await page.waitForFunction(() => document.querySelector('[aria-label=GOVERNORS]')?.getAttribute('aria-expanded')==='true')
      assert.equal(await lanes.getAttribute('aria-expanded'), 'false')
      await gov.focus(); await page.keyboard.press('Space')
      assert.equal(await page.locator('[data-sidebar-body]').count(), 0)
    }
    assert.deepEqual(errors, [])
    console.log('layout receipts:', JSON.stringify(receipts))
    if (process.env.LANES_LAYOUT_RECEIPT) writeFileSync(process.env.LANES_LAYOUT_RECEIPT, JSON.stringify(receipts, null, 2))
  } finally { await browser?.close(); rmSync(dir, { recursive: true, force: true }) }
})
