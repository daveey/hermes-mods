// Real-Chromium geometry for the compact Lanes SidebarView: actual React render + the plugin's own CSS,
// laid out at 480/360/280px. Fixture counts are test-only; no production counters or user Chrome profile.
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const pw = join(homedir(), '.cache/ms-playwright')
const playwrightChrome = existsSync(pw) ? readdirSync(pw).filter(d => d.startsWith('chromium-')).map(d => join(pw, d, 'chrome-linux64/chrome')) : []
const CHROME = [process.env.CHROME_BIN, '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium',
  '/usr/bin/chromium-browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', ...playwrightChrome].find(p => p && existsSync(p))
const WIDTHS = [480, 360, 280, 232, 212]

const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top', SIDEBAR_SECTIONS_AREA: 'sidebar.sections', SESSION_FILTER_AREA: 'sidebar.filter' })
const T0 = Date.UTC(2026, 10, 27, 0, 0) / 1000
const cells = Array.from({ length: 48 }, (_, i) => ({ t: T0 + i * 1800, state: i % 5 ? 'partial' : 'full', running: i % 5, cap: 4, rejected: 0,
  rejection_unknown: false, visual: { height: .5, opacity: 1 } }))
const lane = (name, i) => ({ share: 20, actual: .2, target: .3, status: { run: 2, you: 1 }, governor_held: 0, not_ready: 3,
  top_reason: i ? 'waiting on a long reason that must truncate' : '', limits: { max_workers: 4 }, boards: ['a'],
  spend: { hour: 123.45, day: 1234.5, week: 98765.43 }, ticks: { window: '24h', bucket_s: 1800, cap: 4, cells, previews: {} } })
const names = ['babybear-long-lane-name', 'games', 'askeve', 'hermes']
const snap = { configured: true, fleet_slots: 39, status: { run: 12345, you: 678 }, totals: { not_ready: 9999 },
  spend: { hour: 1.5, day: 20.25, week: 140 }, sidebar: { cost_today: 20.25, idle_lanes: [] },
  governors: { idle_total: 31, rows: [], counts: { on: 1, off: 8 } },
  lanes: Object.fromEntries(names.map((n, i) => [n, lane(n, i)])) }

test('compact sidebar: 38px two-line rows, no overflow/wrap, day-only at 360, toolbar+totals fit', { skip: !CHROME && !process.env.CI && 'no Chrome' }, async () => {
  assert.ok(CHROME, 'CI must provide Chrome for the compact layout test')
  const view = await mount(React.createElement(P.SidebarView, { snap, open: true, setOpen() {}, write: async () => ({}), onWindow() {}, windowHours: 24 }))
  let html
  try { html = view.container.querySelector('.lanes-sidebar').outerHTML } finally { await view.close() }
  assert.equal((html.match(/data-sidebar-lane=/g) || []).length, names.length)
  const css = readFileSync(new URL('../plugin.js', import.meta.url), 'utf8').split('const CSS = `')[1].split('`')[0]
  const page = `<html><head><style>*{box-sizing:border-box}body{margin:0;font:13px Arial}${css}</style></head><body>
<div id="pane">${html}</div><script>
const r = e => e.getBoundingClientRect(), vis = e => e && getComputedStyle(e).display !== 'none' && r(e).width > 0
const inside = (c, p) => c.left >= p.left - .5 && c.right <= p.right + .5 && c.top >= p.top - .5 && c.bottom <= p.bottom + .5
document.title = JSON.stringify(${JSON.stringify(WIDTHS)}.map(w => {
  document.getElementById('pane').style.width = w + 'px'
  const pane = r(document.getElementById('pane'))
  const rows = [...document.querySelectorAll('[data-sidebar-lane] > .lanes-row')].map(row => {
    const box = r(row), kids = [...row.children]
    const cost = row.querySelector('.lanes-cost')
    return { h: box.height, name: row.querySelector('.lanes-row-name').textContent,
      kidOverflow: kids.filter(k => vis(k) && !inside(r(k), box)).map(k => k.className),
      deepOverflow: [...row.querySelectorAll('*')].filter(k => vis(k) && !inside(r(k), box)).map(k => k.className.baseVal ?? k.className),
      nameLines: Math.round(r(row.querySelector('.lanes-row-name')).height / parseFloat(getComputedStyle(row.querySelector('.lanes-row-name')).lineHeight === 'normal' ? 15 : getComputedStyle(row.querySelector('.lanes-row-name')).lineHeight)),
      nameWrapped: row.querySelector('.lanes-row-name').getClientRects().length > 1 || r(row.querySelector('.lanes-row-name')).height > 20,
      chipWrapped: vis(row.querySelector('.lanes-row-chip')) && r(row.querySelector('.lanes-row-chip')).height > 20,
      costWrapped: [...cost.children].filter(vis).filter(c => r(c).height > 19.5).map(c => c.className),
      costScrollOverflow: cost.scrollWidth > cost.clientWidth + .5,
      cost: ['h', 'd', 'w'].map(k => vis(cost.querySelector('.lanes-cost-' + k))),
      tickH: Math.round(r(row.querySelector('.lanes-ticks')).height), slotsH: Math.round(r(row.querySelector('.lanes-slots')).height),
      chartOverflow: [...row.querySelector('.lanes-row-chart').querySelectorAll('*')].filter(k => vis(k) && !['defs','pattern','rect','title','g'].includes(k.tagName) && !inside(r(k), r(row.querySelector('.lanes-row-chart')))).map(k => k.className.baseVal ?? k.className),
      tickAbovePips: r(row.querySelector('.lanes-ticks')).bottom <= r(row.querySelector('.lanes-slots')).top + .5,
      nameLeftOfChart: r(row.querySelector('.lanes-row-name')).right <= r(row.querySelector('.lanes-row-chart')).left + .5,
      chartLeftOfCost: r(row.querySelector('.lanes-row-chart')).right <= r(cost).left + .5 }
  })
  const bar = document.querySelector('.lanes-sb-window'), sum = document.querySelector('.lanes-sb-summary'), fleet = document.querySelector('[data-fleet-spend]')
  return { w, rows, toolbarFits: inside(r(bar), pane) && bar.scrollWidth <= bar.clientWidth + .5 && bar.offsetHeight < 60,
    totalsFits: inside(r(sum), pane) && r(sum).width > 0, totalsHeight: r(sum).height, fleetFits: inside(r(fleet), pane),
    pageOverflow: document.getElementById('pane').scrollWidth > w + .5 }
}))</script></body></html>`
  const dir = mkdtempSync(join(tmpdir(), 'lanes-compact-'))
  try {
    writeFileSync(join(dir, 'compact.html'), page)
    const dom = execFileSync(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu', `--user-data-dir=${join(dir, 'profile')}`,
      '--window-size=1000,900', '--dump-dom', 'file://' + join(dir, 'compact.html')], { encoding: 'utf8', timeout: 90000, stdio: ['ignore', 'pipe', 'ignore'] })
    const res = JSON.parse(dom.match(/<title>(.*?)<\/title>/s)[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&'))
    assert.deepEqual(res.map(r => r.w), WIDTHS)
    for (const r of res) {
      const at = `@${r.w}: `
      assert.equal(r.rows.length, names.length, at + 'lane rows')
      assert.ok(r.toolbarFits, at + 'window toolbar fits the pane')
      assert.ok(r.totalsFits, at + 'totals summary fits the pane')
      assert.ok(r.fleetFits, at + 'fleet spend fits the pane')
      assert.ok(!r.pageOverflow, at + 'pane content overflows horizontally')
      for (const row of r.rows) {
        const id = at + row.name + ' ' + JSON.stringify(row)
        assert.ok(Math.abs(row.h - 38) <= 1, 'row ~38px: ' + id)
        assert.deepEqual(row.kidOverflow, [], 'row child overflows row: ' + id)
        assert.deepEqual(row.deepOverflow, [], 'row descendant overflows row: ' + id)
        assert.deepEqual(row.chartOverflow, [], 'chart descendant overlaps sibling column: ' + id)
        assert.ok(!row.nameWrapped && !row.chipWrapped && row.costWrapped.length === 0 && !row.costScrollOverflow, 'wrapping/overflow: ' + id)
        assert.equal(row.tickH, 14, 'chart ticks 14px: ' + id)
        assert.ok(row.tickAbovePips, 'ticks on top, pips below: ' + id)
        assert.ok(row.nameLeftOfChart && row.chartLeftOfCost, 'columns ordered name | chart | cost: ' + id)
        if (r.w <= 360) assert.deepEqual(row.cost, [false, true, false], 'only day visible at <=360: ' + id)
        else assert.deepEqual(row.cost, [true, true, true], 'hour/day/week visible above 360: ' + id)
      }
    }
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
