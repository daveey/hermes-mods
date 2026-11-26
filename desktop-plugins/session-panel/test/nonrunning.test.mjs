import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve, dirname, join } from 'node:path'
import { readFileSync, mkdtempSync, rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { homedir } from 'node:os'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime'), { JSDOM } = require('jsdom')
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.test' })
globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.reasonHarness = { React, J }
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(s,c,n) {
 const m = {
 'react': 'const R=globalThis.reasonHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef',
 'react/jsx-runtime': 'const J=globalThis.reasonHarness.J;export const jsx=J.jsx,jsxs=J.jsxs',
 '@hermes/plugin-sdk': 'const R=globalThis.reasonHarness.React,F=p=>R.createElement(R.Fragment,null,p.children);export const host={},Codicon=()=>null,DisclosureCaret=()=>null,COMPOSER_AREAS={top:"composer.top"},useValue=()=>null,ContextMenu=F,ContextMenuTrigger=F,ContextMenuContent=()=>null,ContextMenuItem=()=>null'
 }; return m[s]?{url:'data:text/javascript,'+encodeURIComponent(m[s]),shortCircuit:true}:n(s,c)
}
export async function load(u,c,n) {
 if(u.endsWith('/plugin.js')) { const fs=await import('node:fs/promises');return {format:'module',source:await fs.readFile(new URL(u),'utf8'),shortCircuit:true} }
 return n(u,c)
}`))
const m = await import('../plugin.js'), { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const paint = tree => React.act(async () => root.render(tree))
let families
if (process.env.HERMES_PANEL_NONRUNNING_CASES) {
 families = [['explicit-export', JSON.parse(readFileSync(process.env.HERMES_PANEL_NONRUNNING_CASES, 'utf8'))]]
} else {
 const desktop = resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`)
 const core = resolve(desktop, '../..')
 const output = mkdtempSync(join(process.env.TMPDIR || homedir(), 'panel-nonrunning-'))
 try {
  const producer = join(dirname(fileURLToPath(import.meta.url)), 'nonrunning_producer_export.py')
  const run = spawnSync('bash', [join(core, 'scripts/run_tests.sh'), producer, '--junitxml=' + join(output, 'producer.xml')],
   { cwd: core, encoding: 'utf8', timeout: 180000, maxBuffer: 4 * 1024 * 1024, env: { ...process.env, HERMES_TEST_FILE_RETRIES: '0' } })
  assert.equal(run.status, 0, `real producer failed: ${run.error || ''}\n${run.stdout}\n${run.stderr}`)
  families = ['local', 'transport', 'local-gateway_draining', 'transport-gateway_draining',
   'local-update_admission_hold', 'transport-update_admission_hold', 'local-start_gate', 'transport-start_gate',
   'capacity', 'schedule', 'human', 'startup', 'unobserved_review', 'chat_activity']
   .map(name => [name, JSON.parse(readFileSync(join(output, name + '.json'), 'utf8'))])
 } finally { rmSync(output, { recursive: true, force: true }) }
}
for (const [family, cases] of families) for (const kind of (cases.expected?.lifecycle ? ['current'] : ['current', 'expired', 'cached_expired'])) {
 test(`real ${family} producer -> reducer -> mounted renderer: ${kind}`, async () => {
  const { model, now } = cases[kind === 'cached_expired' ? 'current' : kind]
  const before = JSON.stringify(model)
  const clock = kind === 'cached_expired' ? now + 181 : now
  await paint(m.renderModel(model, { now: clock }))
  if (cases.expected?.lifecycle) {
   await paint(m.renderModel(model, { now: model.freshness.computed_at + model.freshness.ttl_s + 1 }))
   assert.match(document.body.textContent, /status unknown · last known:/)
   assert.ok(document.querySelector('[data-row-pip="stuck"]') === null)
   assert.ok(document.querySelector('[data-ask-row]') === null, 'stale native wait is not an actionable ask')
   assert.equal(JSON.stringify(model), before)
   await paint(m.renderModel(model, { now: clock }))
   const text = document.body.textContent
   assert.ok(text.includes(cases.expected.summary), text)
   if (cases.expected.header) {
    assert.ok(text.includes(cases.expected.header), text)
    assert.doesNotMatch(text, /running · this chat/)
    assert.equal(model.header.counts.running, 0)
   }
   assert.ok(document.querySelector('[data-row-pip="stuck"]') === null)
   assert.equal(document.querySelector('[data-ask-row]') !== null, cases.expected.lifecycle === 'human')
   assert.equal(JSON.stringify(model), before)
   return
  }
  const text = document.querySelector('[data-row-detail]').textContent
  if (kind === 'current' && cases.expected?.waiting) {
   assert.ok(text.includes(cases.expected.summary), text)
   assert.doesNotMatch(text, /blocked|worker to claim/)
   assert.ok(document.querySelector('[data-row-pip="stuck"]') === null)
  } else if (kind === 'current') {
   assert.match(text, /blocked · (?:Observing dispatcher: )?Run usage accounting is unsettled/)
   assert.ok(document.querySelector('[data-row-pip="stuck"]'))
  } else {
   assert.match(text, /execution reason unknown/)
   assert.doesNotMatch(text, /blocked|worker to claim/)
   assert.ok(document.querySelector('[data-row-pip="stuck"]') === null, 'expired reason has no stuck indicator')
  }
  assert.ok(document.querySelector('[data-ask-row]') === null, 'agent refusal must not fabricate a human ask')
  assert.equal(JSON.stringify(model), before)
 })
}
