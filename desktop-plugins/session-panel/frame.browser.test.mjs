// Real Chromium layout control for the session-status frame: the actual registered session-panel + user-steps
// composer contributions, painted in an owned headless page (not the user's Desktop, not native acceptance).
// HERMES_DESKTOP_DIR=<isolated npm deps: react, react-dom, esbuild, playwright> node --test this-file
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
const here = dirname(fileURLToPath(import.meta.url))
const deps = process.env.HERMES_DESKTOP_DIR
assert.ok(deps, 'provide isolated HERMES_DESKTOP_DIR dependencies')
const require = createRequire(resolve(deps, 'package.json'))
const { build } = require('esbuild'), { chromium } = require('playwright')

// The page: a chat column (transcript grows, composer at the bottom) whose composer-top area holds every
// registered composer.top contribution in order, like Desktop.
const harness = `
import React from 'react'; import {createRoot} from 'react-dom/client'
import panel from './plugin.js'; import steps from '../user-steps/plugin.js'
const SECTIONS = ['needs_you','stuck','running','waiting','ready','done']
const owner = { gatewayId: 'G1', profile: 'default', storedSessionId: 'S1' }
const id = n => '00000000-0000-4000-8000-' + String(n).padStart(12, '0')
const asks = Array.from({length: 6}, (_, i) => ({ schemaVersion: 2, owner, requestId: id(i + 1), revision: 1, kind: 'choice',
  title: 'Pick a deployment window for service ' + i + ' with a deliberately long title that has to wrap on narrow widths',
  scope: { complete: true, markdown: 'Pick one.', sourceRefs: [] }, limits: [], exclusions: [], outcomes: { answer: 'Record' },
  choices: [{ id: 'c0', label: 'Tonight', effect: 'answer' }, { id: 'c1', label: 'Tomorrow', effect: 'answer' }], aliases: [], state: 'open' }))
// Requests the model does not draw: companion rows (explicit "not shown yet" + Not needed).
const extra = Array.from({length: 8}, (_, i) => ({ ...asks[0], requestId: id(100 + i), title: 'Companion-only ask ' + i }))
const row = r => ({ key: 'ask:' + r.requestId, kind: 'ask', source: 'question', id: '', title: r.title, detail: '', details: [],
  options: [{ letter: 'A', label: 'Tonight', choice_id: 'c0', detail: 'After 22:00' }, { letter: 'B', label: 'Tomorrow', choice_id: 'c1', detail: 'Morning' }],
  actions: [{ kind: 'decide', owner, request_id: r.requestId, revision: 1 }], request_id: r.requestId, request_revision: 1 })
const lines = Array.from({length: 30}, (_, i) => ({ key: 'c' + i, kind: 'card', id: 't_' + i, title: 'Waiting card ' + i, detail: 'waiting on CI run ' + i, details: [], actions: [], options: [] }))
const sections = Object.fromEntries(SECTIONS.map(s => [s, []]))
sections.needs_you = asks.map(row); sections.waiting = lines
const counts = Object.fromEntries(SECTIONS.map(s => [s, sections[s].length]))
const model = { version: 1, revision: 'r1', header: { state: 'needs_you', label: 'Needs you', source: 'derived', status: null, counts,
  pips: SECTIONS.filter(s => counts[s]).map(s => ({ section: s, label: s, count: counts[s] })) }, sections }
window.hermesDesktop = { api: async ({ path, body }) => {
  if (path.startsWith('/api/plugins/session-panel/')) return { feed: 'hub:1', profile: 'default', epoch: 'E1', rev: 1, bundles: (body?.sessions || []).includes('S1') ? { S1: { session: 'S1', model } } : {} }
  if (path.endsWith('/ping')) return { features: ['withdraw'] }
  if (path.includes('/requests')) return { requests: window.__extras === false ? asks : [...asks, ...extra] }
  return {}
} }
const contributions = [], ctx = { register: a => contributions.push(a), onEvent: () => () => {} }
panel.register(ctx); steps.register(ctx)
function Column() {
  const top = contributions.filter(a => a.area === 'composer.top').sort((a, b) => a.order - b.order)
  return React.createElement('div', { id: 'chat', style: { display: 'flex', flexDirection: 'column', height: '100vh' } },
    React.createElement('div', { id: 'transcript', style: { flex: '1 1 auto', minHeight: 0, overflow: 'auto' } }, 'transcript'),
    React.createElement('div', { id: 'composer-top', style: { flex: '0 0 auto', minWidth: 0 } }, ...top.map(a => React.createElement(React.Fragment, { key: a.id }, a.render()))),
    React.createElement('textarea', { id: 'composer', rows: 3, style: { flex: '0 0 auto', boxSizing: 'border-box', width: '100%' } }))
}
createRoot(document.getElementById('root')).render(React.createElement(Column))
window.ready = true
`
const sdk = `const atom = v => ({ get: () => v })
export const host = { state: { focusedStoredSessionId: atom('S1'), connectionId: atom('C1'), profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' }) } }
export const COMPOSER_AREAS = { top: 'composer.top' }, TRANSCRIPT_DIRECTIVE_AREA = 'transcript.directives'
export const useValue = a => a?.get?.(), MessageTextContent = ({ text }) => text, Codicon = () => null, DisclosureCaret = () => null`

const measure = () => {
  const q = s => document.querySelector(s), box = e => e && e.getBoundingClientRect().toJSON()
  const frame = q('[data-session-panel]'), body = q('[data-panel-body]'), toggle = q('[data-panel-toggle]'), composer = q('#composer')
  const t = box(toggle), hit = t && document.elementFromPoint(t.x + t.width / 2, t.y + t.height / 2)
  return { vh: innerHeight, vw: innerWidth, frame: box(frame), body: box(body), toggle: t, composer: box(composer),
    toggleHit: !!hit && (hit === toggle || toggle.contains(hit)), bodyScroll: body && { top: body.scrollTop, h: body.scrollHeight, ch: body.clientHeight, sw: body.scrollWidth, cw: body.clientWidth },
    frameSW: frame?.scrollWidth, frameCW: frame?.clientWidth, docScroll: document.scrollingElement.scrollTop, docSW: document.scrollingElement.scrollWidth,
    asks: document.querySelectorAll('[data-ask-row]').length, companion: document.querySelectorAll('[data-action-request]').length,
    queues: document.querySelectorAll('[data-action-queue]').length, queueInBody: !!q('[data-action-queue]')?.closest('[data-panel-body]') }
}

test('session-status frame: bounded, header reachable, body scrolls rows + companion, collapse, composer usable', async () => {
  const bundle = await build({ stdin: { contents: harness, resolveDir: here, sourcefile: 'frame-fixture.mjs' }, bundle: true, format: 'esm', write: false,
    nodePaths: [resolve(deps, 'node_modules')], define: { 'process.env.NODE_ENV': '"production"' },
    plugins: [{ name: 'sdk', setup(b) {
      b.onResolve({ filter: /^@hermes\/plugin-sdk$/ }, () => ({ path: 'sdk', namespace: 'fixture' }))
      b.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: sdk }))
    } }] })
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) })
  try {
    for (const [name, viewport, scale] of [['tall', { width: 1000, height: 1000 }, 1], ['short', { width: 1000, height: 420 }, 1], ['narrow', { width: 360, height: 640 }, 1], ['zoom200', { width: 500, height: 400 }, 2]]) {
      const page = await browser.newPage({ viewport, deviceScaleFactor: scale })
      const errors = []; page.on('pageerror', e => errors.push(e.message))
      await page.route('http://localhost/**', r => r.fulfill({ body: '<!doctype html><html><body style="margin:0"><main id="root"></main></body></html>', contentType: 'text/html' }))
      await page.goto('http://localhost/frame-fixture')
      await page.addScriptTag({ type: 'module', content: bundle.outputFiles[0].text })
      await page.waitForFunction(() => document.querySelectorAll('[data-ask-row]').length === 6 && document.querySelectorAll('[data-action-request]').length >= 8, null, { timeout: 15000 })
      const m = await page.evaluate(measure)
      assert.deepEqual(errors, [], name)
      assert.equal(m.queues, 1, `${name}: exactly one decision queue`)
      assert.ok(m.queueInBody, `${name}: companion inside the frame body`)
      assert.ok(m.frame.height <= m.vh * 0.55 + 1, `${name}: one combined budget (${m.frame.height} of ${m.vh})`)
      assert.ok(m.toggle.y >= 0 && m.toggleHit, `${name}: collapse control hit-testable`)
      assert.ok(m.composer.y >= 0 && m.composer.y + m.composer.height <= m.vh + 1, `${name}: composer on screen`)
      assert.ok(m.bodyScroll.h > m.bodyScroll.ch, `${name}: body scrolls internally`)
      assert.ok(m.bodyScroll.sw <= m.bodyScroll.cw && m.frameSW <= m.frameCW && m.docSW <= m.vw, `${name}: no horizontal clipping`)
      // Scroll the body to the bottom: the last companion row is reachable; header still hit-testable.
      await page.evaluate(() => { const b = document.querySelector('[data-panel-body]'); b.scrollTop = b.scrollHeight })
      const bottom = await page.evaluate(() => {
        const b = document.querySelector('[data-panel-body]').getBoundingClientRect(), rows = document.querySelectorAll('[data-action-request]')
        const last = rows[rows.length - 1].getBoundingClientRect()
        return { lastVisible: last.bottom <= b.bottom + 1 && last.top >= b.top - 1 }
      })
      const after = await page.evaluate(measure)
      assert.ok(bottom.lastVisible, `${name}: last row reachable`)
      assert.ok(after.toggleHit && after.toggle.y === m.toggle.y, `${name}: header did not scroll away`)
      assert.equal(after.docScroll, 0, `${name}: page itself did not scroll`)
      // Deep keyboard focus scrolls only the body.
      await page.evaluate(() => { document.querySelector('[data-panel-body]').scrollTop = 0; const bs = document.querySelectorAll('[data-action-request] button'); bs[bs.length - 1].focus() })
      const focused = await page.evaluate(measure)
      assert.ok(focused.bodyScroll.top > 0 && focused.docScroll === 0 && focused.toggle.y === m.toggle.y, `${name}: focus scrolled only the body`)
      // Collapse with a real pointer click on the header control.
      await page.mouse.click(m.toggle.x + m.toggle.width / 2, m.toggle.y + m.toggle.height / 2)
      const collapsed = await page.evaluate(measure)
      // Rows and the 6 asks the header counts go. The 8 asks it does not count stay visible, inside the same
      // bounded body, with the header and the composer still reachable.
      assert.equal(collapsed.asks, 0, `${name}: canonical rows removed`)
      assert.equal(await page.evaluate(() => [...document.querySelectorAll('[data-action-request]')].filter(e => e.offsetParent).length), 8, `${name}: uncounted asks stay visible`)
      assert.ok(collapsed.frame.height <= collapsed.vh * 0.55 + 1, `${name}: collapsed frame still bounded`)
      assert.ok(collapsed.toggleHit, `${name}: expand reachable`)
      assert.ok(collapsed.composer.y >= 0 && collapsed.composer.y + collapsed.composer.height <= collapsed.vh + 1, `${name}: composer on screen while collapsed`)
      assert.equal(await page.evaluate(() => document.activeElement?.hasAttribute('data-panel-toggle')), true, `${name}: focus not stranded`)
      // With nothing uncounted left, the collapsed frame is just the header.
      await page.evaluate(async () => { window.__extras = false; await window.__userSteps.refreshRequests() })
      await page.waitForFunction(() => document.querySelectorAll('[data-action-request]').length === 0, null, { timeout: 15000 })
      const header = await page.evaluate(measure)
      assert.ok(header.frame.height < 60, `${name}: frame shrinks to the header (${header.frame.height})`)
      assert.ok(header.toggleHit, `${name}: expand reachable from the header-only frame`)
      await page.evaluate(async () => { window.__extras = true; await window.__userSteps.refreshRequests() })
      await page.waitForFunction(() => document.querySelectorAll('[data-action-request]').length === 8, null, { timeout: 15000 })
      await page.mouse.click(collapsed.toggle.x + collapsed.toggle.width / 2, collapsed.toggle.y + collapsed.toggle.height / 2)
      const expanded = await page.evaluate(measure)
      assert.equal(await page.evaluate(() => document.querySelector('[data-panel-body]').hasAttribute('data-body-collapsed')), false, `${name}: expanded again`)
      assert.equal(expanded.asks, 6, `${name}: rows restored`)
      assert.ok(expanded.frame.height <= expanded.vh * 0.55 + 1, `${name}: still bounded`)
      await page.close()
    }
  } finally { await browser.close() }
})
