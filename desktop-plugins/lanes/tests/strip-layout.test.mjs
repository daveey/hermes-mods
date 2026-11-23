// Real-browser geometry for the lanes strip row: rendered Lanes DOM + the plugin's own CSS, laid out in
// headless Chrome at wide and narrow main-pane widths with a populated throughput chip in StripRight.
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const CHROME = [process.env.CHROME_BIN, '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium',
  '/usr/bin/chromium-browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(p => p && existsSync(p))
const WIDTHS = [1900, 1477, 1200, 900, 600, 480, 360]

const P = await loadPlugin()
const lane = () => ({ share: 20, strip: { blocked: 11, running: 7, other: 127, cap: 2, in_share: 2, over: 5, tokens: Array(36).fill(1000) },
  status: { you: 1, ext: 0, worker: 0, run: 1, dep: 0, triage: 0, other: 0 } })
const snap = { configured: true, lanes: Object.fromEntries(['babybear', 'games', 'askeve', 'hermes', 'other', 'research', 'personal'].map(n => [n, lane()])),
  status: { you: 18, ext: 56, worker: 24, run: 15, dep: 95, triage: 23, other: 0 } }

test('strip row: controls top-right when they fit, never overlapping lane pills, nothing off-pane', { skip: !CHROME && !process.env.CI && 'no Chrome' }, async () => {
  assert.ok(CHROME, 'CI must provide Chrome for the strip layout test')
  const view = await mount(React.createElement(P.Lanes, { ctx: { rest: async () => snap } }))
  let html
  try { html = view.container.querySelector('.hermes-lanes-strip').outerHTML } finally { await view.close() }
  // Populated throughput contribution (the real chip mounts here; ~sparkline + "194k tok/m · 0 cards/m").
  html = html.replace(/(<span class="hermes-lanes-strip-right"[^>]*>)/, '$1<span id="chip" style="display:inline-block;width:180px;height:22px"></span>')
  assert.match(html, /id="chip"/)
  const css = readFileSync(new URL('../plugin.js', import.meta.url), 'utf8').split('const CSS = `')[1].split('`')[0]
  const page = `<html><head><style>*{box-sizing:border-box}body{margin:0;font:13px Arial}${css}</style></head><body>
<div id="pane">${html}</div><script>
const box = e => e.getBoundingClientRect(), hit = (a, b) => a.left < b.right - .5 && b.left < a.right - .5 && a.top < b.bottom - .5 && b.top < a.bottom - .5
document.title = JSON.stringify(${JSON.stringify(WIDTHS)}.map(w => {
  document.getElementById('pane').style.width = w + 'px'
  const pills = [...document.querySelectorAll('.hermes-lanes-pill')].map(box)
  const ctl = ['.hermes-lanes-strip-right', '.hermes-lanes-status', '.hermes-lanes-toggle'].map(s => box(document.querySelector(s)))
  const wrap = box(document.querySelector('.hermes-lanes-pills'))
  return { w, pills: pills.length, firstPillTop: Math.round(pills[0].top), ctlTops: ctl.map(r => Math.round(r.top)),
    overlaps: pills.flatMap((p, i) => ctl.map((c, j) => hit(p, c) ? i + ':' + j : null)).filter(Boolean),
    offPane: [...pills, ...ctl].filter(r => r.right > w + .5 || r.left < -.5 || r.width < 1).length,
    outsideWrap: pills.filter(p => p.right > wrap.right + .5 || p.left < wrap.left - .5).length }
}))</script></body></html>`
  const dir = mkdtempSync(join(tmpdir(), 'lanes-strip-'))
  try {
    writeFileSync(join(dir, 'strip.html'), page)
    const dom = execFileSync(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu', `--user-data-dir=${join(dir, 'profile')}`,
      '--window-size=2000,600', '--dump-dom', 'file://' + join(dir, 'strip.html')], { encoding: 'utf8', timeout: 90000, stdio: ['ignore', 'pipe', 'ignore'] })
    const rows = JSON.parse(dom.match(/<title>(.*?)<\/title>/s)[1].replace(/&quot;/g, '"'))
    for (const r of rows) {
      const at = JSON.stringify(r)
      assert.equal(r.pills, 7, at)
      assert.deepEqual(r.overlaps, [], `lane pill overlaps a control: ${at}`)
      assert.equal(r.offPane, 0, `element outside the pane: ${at}`)
      assert.equal(r.outsideWrap, 0, `pill painted outside its box: ${at}`)
    }
    // Wide panes (the normal Desktop) keep chip, filters and toggle on the first row beside the pills.
    for (const r of rows.filter(r => r.w >= 900)) assert.deepEqual(r.ctlTops, r.ctlTops.map(() => r.firstPillTop), JSON.stringify(r))
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
