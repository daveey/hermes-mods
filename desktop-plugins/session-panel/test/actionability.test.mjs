import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve } from 'node:path'
import { homedir } from 'node:os'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime'), { JSDOM } = require('jsdom')
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.test' })
globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.actionabilityHarness = { React, J }
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(s,c,n) {
 const m = {
 'react': 'const R=globalThis.actionabilityHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef',
 'react/jsx-runtime': 'const J=globalThis.actionabilityHarness.J;export const jsx=J.jsx,jsxs=J.jsxs',
 '@hermes/plugin-sdk': 'export const host={},Codicon=()=>null,DisclosureCaret=()=>null,COMPOSER_AREAS={top:"composer.top"},useValue=a=>a?.get?.()'
 }; return m[s]?{url:'data:text/javascript,'+encodeURIComponent(m[s]),shortCircuit:true}:n(s,c)
}
export async function load(u,c,n) {
 if(u.endsWith('/plugin.js')) { const fs=await import('node:fs/promises');return {format:'module',source:await fs.readFile(new URL(u),'utf8'),shortCircuit:true} }
 return n(u,c)
}`))
const m = await import('../plugin.js'), { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const cases = JSON.parse(execFileSync(process.env.HERMES_MODS_PYTHON || 'python3', [fileURLToPath(new URL('./actionability-fixtures.py', import.meta.url))], { encoding: 'utf8' }))
const paint = async (name, onAction) => {
 const { model, now } = cases[name]
 await React.act(async () => root.render(m.renderModel(model, { now, onAction })))
 return { model, tooltip: [...document.querySelectorAll('[data-ask-title], [data-row-detail]')].map(n=>n.title).join('\n') }
}
for (const name of ['owner','blocker','unavailable','unsupported','legacy','replica','terminal','human']) {
 test(`persisted reader -> reducer -> actual DOM: ${name}`, { skip: !process.env.HERMES_CORE_SOURCE }, async () => {
  const { model, tooltip } = await paint(name)
  const row = Object.values(model.sections).flat()[0]
  assert.ok(row?.actionability, 'backend additive diagnostic required')
  const rendered = name==='human' ? document.querySelector('[data-ask-details]').textContent : tooltip
  assert.ok(rendered.includes('Actionability'), 'actual renderer carries backend strings')
  for (const line of row.details.filter(x=>x.startsWith('Actionability'))) assert.ok(rendered.includes(line), line)
  if (name==='owner') { assert.ok(tooltip.includes('executor')); assert.ok(tooltip.includes(row.actionability.instruction)) }
  if (name==='blocker') { assert.ok(tooltip.includes('Recorded clock reaches')); assert.ok(tooltip.includes('1800000700')) }
  if (['unavailable','unsupported','legacy'].includes(name)) assert.ok(tooltip.toLowerCase().includes('unavailable'))
  if (name==='replica') assert.equal(row.actionability.evidence_state,'unknown')
  if (name==='terminal') { assert.ok(tooltip.includes('n/a')); assert.ok(!tooltip.includes('owner_missing')) }
  if (name==='human') { assert.equal(model.sections.needs_you.length,1);assert.ok(document.querySelector('[data-ask-row]'));assert.ok(document.body.textContent.includes('Details')) }
 })
}
test('exact owner-qualified Open target remains backend-owned', { skip: !process.env.HERMES_CORE_SOURCE }, async () => {
 const actions=[]
 const { model }=await paint('replica',(...args)=>actions.push(args))
 document.querySelector('[data-open-own-card]').dispatchEvent(new window.MouseEvent('dblclick',{bubbles:true}))
 assert.equal(actions.length,1)
 assert.deepEqual(actions[0][1].target,model.sections.waiting[0].actions.find(a=>a.kind==='open_card').target)
 assert.equal(actions[0][1].target.gateway,'owner')
})
test('legacy wire payload renders without a Desktop classifier', { skip: !process.env.HERMES_CORE_SOURCE }, async () => {
 await paint('old_wire')
 assert.ok(document.querySelector('[data-panel-row]'))
})
