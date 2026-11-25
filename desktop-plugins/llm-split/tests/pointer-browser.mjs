// Run: HERMES_POINTER_PACKAGE=/path/to/deps/package.json node tests/pointer-browser.mjs
// deps: React 19.2.0, react-dom 19.2.0, @tanstack/react-query 5.90.5,
// radix-ui 1.4.3 (same as unit fixtures), esbuild and playwright.
// CHROME_BIN selects installed Chromium; otherwise Playwright's installed browser.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
const here = dirname(fileURLToPath(import.meta.url))
const require = createRequire(process.env.HERMES_POINTER_PACKAGE || new URL('./package.json', import.meta.url))
const { build } = require('esbuild')
const { chromium } = require('playwright')
const sdk = `import React from 'react'; import {Popover as P,ContextMenu as C} from 'radix-ui';
export {useQuery,useQueryClient} from '@tanstack/react-query';
export const Popover=P.Root,PopoverTrigger=P.Trigger;
export const PopoverContent=props=>React.createElement(P.Portal,null,React.createElement(P.Content,props));
export const ContextMenu=C.Root,ContextMenuTrigger=C.Trigger,ContextMenuContent=C.Content,ContextMenuItem=C.Item,ContextMenuCheckboxItem=C.CheckboxItem,ContextMenuSeparator=C.Separator;
export const STATUSBAR_AREAS={right:'statusBar.right'},PALETTE_AREA='palette';`
const out = await build({
  entryPoints: [resolve(here, 'pointer-fixture.mjs')], bundle: true, write: false, format: 'iife',
  nodePaths: [resolve(dirname(require.resolve('react/package.json')), '..')],
  plugins: [{ name: 'sdk', setup(b) {
    b.onResolve({ filter: /^@hermes\/plugin-sdk$/ }, () => ({ path: 'sdk', namespace: 'sdk' }))
    b.onLoad({ filter: /.*/, namespace: 'sdk' }, () => ({ contents: sdk, resolveDir: here }))
  } }]
})
const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_BIN ? { executablePath: process.env.CHROME_BIN } : {}), args: ['--no-sandbox'] })
const checks = [], errors = []
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } })
  page.on('pageerror', (e) => errors.push(e.message))
  // Serve the DOM in the navigation: replacing a navigated document with setContent
  // can silently disable native pointer capture in Chromium 159, even on a plain div.
  const html = `<style>*{box-sizing:border-box}body{font:13px sans-serif}
    .llms-pop,.hermes-tp-popover{background:#eee;border:1px solid #777}
    #outside{position:absolute;top:800px}
    /* Expose an outer-button padded strip independently of the span's own padding. */
    .llms-tool .llms-chip,.hermes-tp-tool .hermes-tp-tbchip{height:20px}
    </style><div id="root"></div><input id="outside"/>`
  await page.route('http://fixture.test/', (r) => r.fulfill({ contentType: 'text/html', body: html }))
  await page.goto('http://fixture.test/')
  await page.addScriptTag({ content: out.outputFiles[0].text })
  const state = async (panel, open, label) => {
    await page.waitForTimeout(80) // include Radix's deferred outside-pointer listener
    assert.equal(await page.locator(panel).count(), Number(open), label)
    checks.push(label)
  }
  for (const [name, trigger, panel, outer] of [
    ['split', '.llms-chip', '.llms-pop', '.llms-tool'],
    ['throughput', '.hermes-tp-tbchip', '.hermes-tp-popover', '.hermes-tp-tool']
  ]) {
    await page.locator(trigger).waitFor()
    await page.locator(trigger).hover()
    await state(panel, false, `${name}: hover never opens`)
    // Near-left edge tests the span padding rather than its text/chart.
    await page.locator(trigger).click({ position: { x: 3, y: 10 } })
    await state(panel, true, `${name}: span padded click opens`)
    await page.mouse.move(20, 400)
    await state(panel, true, `${name}: leaving trigger stays open`)
    await page.locator(panel).hover()
    await state(panel, true, `${name}: entering panel stays open`)
    await page.mouse.move(20, 400)
    await state(panel, true, `${name}: leaving panel stays open`)
    // Focus alone is explicitly not dismissal; opening/closing still use trusted input.
    await page.locator('#outside').focus()
    await state(panel, true, `${name}: focus outside stays open`)
    await page.locator(trigger).click()
    await state(panel, false, `${name}: second chip click closes`)
    await page.locator(outer).click({ position: { x: 3, y: 1 } })
    await state(panel, true, `${name}: outer padded click opens`)
    await page.locator(outer).click({ position: { x: 3, y: 1 } })
    await state(panel, false, `${name}: second outer padded click closes`)
    await page.locator(trigger).click()
    await page.locator('#outside').click()
    await state(panel, false, `${name}: outside click closes`)
    await page.locator(trigger).click()
    await page.keyboard.press('Escape')
    await state(panel, false, `${name}: Escape closes`)
    await page.locator(outer).focus()
    await page.keyboard.press('Enter')
    await state(panel, true, `${name}: Enter opens`)
    await page.locator(outer).focus()
    await page.keyboard.press('Space')
    await state(panel, false, `${name}: Space second activation closes`)
    await page.locator(trigger).click()
    if (name === 'split') {
      await page.getByRole('radio', { name: 'Auto', exact: true }).click()
      await state(panel, true, `${name}: panel Auto interaction stays open`)
      assert.ok(await page.evaluate(() => calls.some(c => c.path === '/v1/split' && c.body?.mode === 'auto')))
      const bar = await page.locator('[data-testid="traffic-bar"]').boundingBox()
      await page.mouse.move(bar.x + 20, bar.y + 8)
      await page.mouse.down()
      await page.mouse.move(bar.x + 70, bar.y + 8, { steps: 4 })
      await page.mouse.up()
      await state(panel, true, `${name}: panel drag stays open`)
      assert.ok(await page.evaluate(() => calls.some(c => c.body?.mode === 'manual' && c.body?.share)))
      const manualCount = await page.evaluate(() => calls.filter(c => c.body?.mode === 'manual' && c.body?.share).length)
      const knob = await page.getByRole('slider', { name: 'Claude share of traffic' }).boundingBox()
      await page.mouse.move(knob.x + knob.width / 2, knob.y + knob.height / 2)
      await page.mouse.down()
      await page.mouse.move(bar.x + 120, bar.y + 8, { steps: 4 })
      await page.mouse.up()
      await state(panel, true, `${name}: knob drag stays open`)
      assert.equal(await page.evaluate(() => calls.filter(c => c.body?.mode === 'manual' && c.body?.share).length), manualCount + 1,
        'knob-start drag sends exactly one manual REST update')
    } else {
      const input = page.getByRole('textbox', { name: 'Limit, thousands of tokens per minute' })
      await input.click()
      await input.fill('12')
      await input.press('Enter')
      await state(panel, true, `${name}: panel form Enter stays open`)
      assert.ok(await page.evaluate(() => calls.some(c => c.path === '/v1/throttle' && c.body?.cap_per_min === 12000)))
      await page.getByRole('button', { name: '1h', exact: true }).click()
      await state(panel, true, `${name}: panel range click stays open`)
    }
    await page.locator('#outside').click()
    await state(panel, false, `${name}: outside click after form closes`)
  }
  assert.deepEqual(errors, [])
  const events = await page.evaluate(() => events)
  assert.ok(events.filter(e => e.type === 'click').every(e => e.trusted), 'all clicks came from browser input')
  for (const target of ['llms-bar t', 'llms-knob']) {
    assert.ok(events.some(e => e.type === 'gotpointercapture' && e.target === target && e.trusted), `${target}: native pointer capture acquired`)
    assert.ok(events.some(e => e.type === 'pointerup' && e.target === target && e.trusted), `${target}: captured release delivered`)
  }
  console.log(JSON.stringify({ browser: browser.version(), playwright: require('playwright/package.json').version,
    passed: checks.length, failed: 0, checks, calls: await page.evaluate(() => calls), events }, null, 2))
} finally { await browser.close() }
