// node --test: user-steps desktop plugin. Real React + jsdom from the Desktop checkout; SDK stubbed via globals.
//   HERMES_DESKTOP_DIR=~/.hermes/hermes-agent/apps/desktop node --test test/plugin.test.mjs
import './fixtures/cache-hooks.mjs'
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { homedir } from 'node:os'
import { resolve } from 'node:path'

const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react')
const jsxRt = require('react/jsx-runtime')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<div id="root"></div>')
globalThis.window = dom.window
globalThis.document = dom.window.document
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const { createRoot } = require('react-dom/client')

// SDK stub: a focused-session atom + composer recorder the tests drive.
const focused = { v: 'S1', subs: new Set(), get() { return this.v }, set(v) { this.v = v; this.subs.forEach(f => f()) } }
const composer = { sent: [], drafts: [], ok: true, submit(sid, text) { this.sent.push([sid, text]); return this.ok }, async setDraft(sid, text) { this.drafts.push([sid, text]); return true }, focus() {} }
globalThis.__us = {
  React,
  jsxRt,
  sdk: {
    COMPOSER_AREAS: { top: 'composer.top' },
    TRANSCRIPT_DIRECTIVE_AREA: 'transcript.directives',
    host: { state: { focusedStoredSessionId: focused }, composer, notify: () => {} },
    useValue: atom => atom.get()
  }
}
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  const map = {
    '@hermes/plugin-sdk': 'const s=globalThis.__us.sdk;export const COMPOSER_AREAS=s.COMPOSER_AREAS,TRANSCRIPT_DIRECTIVE_AREA=s.TRANSCRIPT_DIRECTIVE_AREA,host=s.host,useValue=s.useValue,MessageTextContent=({text})=>text',
    'react': 'const R=globalThis.__us.React;export const useEffect=R.useEffect,useState=R.useState,useRef=R.useRef',
    'react/jsx-runtime': 'const J=globalThis.__us.jsxRt;export const jsx=J.jsx,jsxs=J.jsxs'
  }
  if (map[spec]) return { url: 'data:text/javascript,' + encodeURIComponent(map[spec]), shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')

test('connection switch during ledger fetch never publishes or caches the previous owner', async () => {
  let owner = 'owner-a'
  const h = globalThis.__us.sdk.host
  h.activeConnectionId = () => owner
  const calls = []
  const call = async (_path, opts) => {
    calls.push(opts.connectionId)
    if (calls.length === 1) {
      owner = 'owner-b'
      return { steps: [], card: { id: 'old-owner' } }
    }
    return { steps: [], card: { id: 'new-owner' } }
  }
  try {
    await m.refresh(call)
    await new Promise(resolve => setTimeout(resolve, 0))
    assert.deepEqual(calls, ['owner-a', 'owner-b'])
    assert.equal(m.getSnapshot().ledger.connectionId, 'owner-b')
    assert.equal(m.getSnapshot().ledger.card.id, 'new-owner')
  } finally { delete h.activeConnectionId }
})

// fake REST: /steps, /step, /resolve over an in-memory ledger
function fakeRest(steps, { card = null, down = false } = {}) {
  const calls = []
  const call = async (path, opts = {}) => {
    calls.push([path, opts.method || 'GET', opts.body])
    if (down) throw new Error('backend down')
    const u = new URL(path, 'http://x')
    if (u.pathname.endsWith('/steps')) return { steps: steps.filter(s => s.status === 'open'), card }
    if (u.pathname.endsWith('/step')) return { step: steps.find(s => s.id === u.searchParams.get('id')) || null }
    if (u.pathname.endsWith('/resolve')) {
      const s = steps.find(x => x.id === opts.body.id)
      if (s) Object.assign(s, { status: 'resolved', resolution: opts.body.resolution })
      if (!steps.some(x => x.status === 'open')) card = null
      return { ok: true, resolved: s ? [s.id] : [], card }
    }
    throw new Error('unexpected ' + path)
  }
  call.calls = calls
  return call
}

const step = (id, kind, extra = {}) => ({ id, kind, text: `text of ${id}`, status: 'open', created: 1, session: 'S1', ...extra })
const flush = () => new Promise(r => setTimeout(r, 0))
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
async function show(node) {
  await React.act(async () => { root.render(node) })
  await React.act(async () => { await flush() })
}

test('decodeAttr inverts the agent-side escapes; directiveIds validates ids', () => {
  assert.equal(m.decodeAttr('Reply \\qyes\\q or \\(no\\) \\\\ x\\nnext'), 'Reply "yes" or {no} \\ x\nnext')
  assert.deepEqual(m.directiveIds({ id: 'us_0123456789, us_abcdef12,evil' }), ['us_0123456789', 'us_abcdef12'])
})

test('directive preserves original text as passive receipt without approval controls', async () => {
  m.clearStepCache()
  const steps = [step('us_cccccc01', 'approve', { text: 'Approve the deploy' })]
  const call = fakeRest(steps)
  composer.sent = []
  await show(React.createElement(m.StepBox, { attrs: { id: 'us_cccccc01', s: 'S1', t: 'Want me to deploy build \\q412\\q?' }, streaming: false, deps: { call, composer, sessionId: 'S1' } }))
  const box = el().querySelector('[data-user-step="us_cccccc01"]')
  assert.ok(box, el().innerHTML)
  assert.equal(box.style.userSelect || box.style.webkitUserSelect, 'text')
  assert.equal(box.querySelector('[data-user-step-text]').textContent, 'Want me to deploy build "412"?')
  const buttons = [...box.querySelectorAll('[data-user-step-action]')].map(b => b.textContent)
  assert.deepEqual(buttons, [])
  assert.deepEqual(composer.sent, [])
  assert.equal(steps[0].status, 'open')
  assert.match(box.textContent, /Reference only/)
  assert.equal(el().querySelector('[data-user-step-action]'), null)
})

test('directive falls back to plain text when the ledger is unreachable; empty t renders nothing', async () => {
  m.clearStepCache()
  const call = fakeRest([], { down: true })
  await show(React.createElement(m.StepBox, { key: 'a', attrs: { id: 'us_dddddd01', s: 'S1', t: 'Plug in the \\(watch\\)' }, streaming: false, deps: { call } }))
  assert.equal(el().querySelector('[data-user-step]'), null)
  assert.equal(el().querySelector('[data-user-step-fallback]').textContent, 'Plug in the {watch}')
  await show(React.createElement(m.StepBox, { key: 'b', attrs: { id: 'us_dddddd02', s: 'S1' }, streaming: false, deps: { call } }))
  assert.equal(el().textContent, '')
})

test('linkSegments: a path with spaces links whole (\u201c~/d/Morning Digest Board.html\u201d), prose after it does not', () => {
  const t = 'Open ~/design/bb-digest/Morning Digest Board.html and review it. Also `/Users/me/My Notes/todo list.md` here.'
  const links = m.linkSegments(t).filter(x => x.kind !== 'text')
  assert.deepEqual(links, [
    { kind: 'path', value: '~/design/bb-digest/Morning Digest Board.html' },
    { kind: 'path', value: '/Users/me/My Notes/todo list.md' }
  ])
  assert.equal(m.linkSegments(t).map(x => x.value).join(''), t)
  // Lowercase prose after a directory never joins; a path that already has an extension stays as is.
  assert.deepEqual(m.linkSegments('see ~/code/foo and bar.txt').filter(x => x.kind !== 'text'), [{ kind: 'path', value: '~/code/foo' }])
  assert.deepEqual(m.linkSegments('ran /tmp/a.log Then Board.html').filter(x => x.kind !== 'text'), [{ kind: 'path', value: '/tmp/a.log' }])
  // No extension within 5 words: keep the short match.
  assert.deepEqual(m.linkSegments('in ~/design/Big Project Folder.').filter(x => x.kind !== 'text'), [{ kind: 'path', value: '~/design/Big' }])
})

test('linkSegments/linkHref: https, bare web addresses and paths link; file names, emails and fractions do not', () => {
  const t = 'Open github.com/settings/billing while signed in. See https://x.io/a?b=1, www.example.org. Edit plugin.js and README.md; mail someone@example.com; 7/10; ~/notes/a.md.'
  const links = m.linkSegments(t).filter(x => x.kind !== 'text')
  assert.deepEqual(links, [
    { kind: 'url', value: 'github.com/settings/billing' },
    { kind: 'url', value: 'https://x.io/a?b=1' },
    { kind: 'url', value: 'www.example.org' },
    { kind: 'path', value: '~/notes/a.md' }
  ])
  assert.equal(m.linkSegments(t).map(x => x.value).join(''), t)
  assert.deepEqual(links.map(m.linkHref), ['https://github.com/settings/billing', 'https://x.io/a?b=1', 'https://www.example.org', 'file://~/notes/a.md'])
  assert.deepEqual(m.linkSegments('example.company and foo.community').filter(x => x.kind !== 'text'), [])
})

test('register: strip in composer.top and the user-step directive', () => {
  const got = []
  const dispose = m.default.register({ register: c => got.push(c) })
  assert.deepEqual(got.map(c => c.area), ['composer.top', 'transcript.directives', 'transcript.directives'])
  assert.equal(got[1].data.name, 'user-step')
  assert.equal(got[2].data.name, 'decision-list')
  assert.equal(window.__userSteps.ownsComposer, true)
  dispose()
  assert.equal(window.__userSteps, undefined)
})

test('registered transcript links open URLs through the OS and Markdown paths in preview', async () => {
  const opened = [], previewed = [], areas = []
  const dispose = m.default.register({ register: a => areas.push(a), os: { openExternal: async u => (opened.push(u), true) }, openPreview: async p => (previewed.push(p), true) })
  try {
    m.clearStepCache()
    const directive = areas.find(a => a.data?.name === 'user-step')
    await show(directive.data.render({ attrs: { id: 'us_eeeeee77', s: 'S1', t: 'Visit github.com/settings/billing or https://example.com/step and read /Users/you/design/plan.md then /tmp/run.log' }, deps: { call: fakeRest([step('us_eeeeee77', 'do')]) } }))
    const links = [...el().querySelectorAll('a[data-user-steps-link]')]
    assert.deepEqual(links.map(a => a.textContent), ['github.com/settings/billing', 'https://example.com/step', '/Users/you/design/plan.md', '/tmp/run.log'])
    assert.equal(links[0].getAttribute('href'), 'https://github.com/settings/billing')
    await React.act(async () => { for (const a of links) a.click(); await flush() })
    assert.deepEqual(previewed, ['/Users/you/design/plan.md'])
    assert.deepEqual(opened, ['https://github.com/settings/billing', 'https://example.com/step', '/tmp/run.log'])
  } finally { dispose(); await show(null) }
})

test('done', () => { root.unmount(); dom.window.close() })
