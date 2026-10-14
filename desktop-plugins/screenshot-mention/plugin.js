/**
 * Screenshot Mention — type `@ss` in the composer to attach your latest screenshot.
 *
 *   @ss      the newest screenshot
 *   @ss3     the newest 3 screenshots (any digit 1–9)
 *
 * As soon as the token is complete (you pick it from the @ popover, or type a space after
 * it) it turns into a normal image chip, exactly as if you had pasted the file: core copies
 * the bytes into Hermes's own composer-images dir. When you SEND, the originals are moved to
 * the Trash. Remove the chip before sending and the original stays where it was.
 *
 * Screenshots are found by name (`Screenshot …`, `Screen Shot …`, `CleanShot …`) in
 * ~/Desktop, ~/Pictures/Screenshots and ~/Documents/Screenshots. Override with
 * localStorage `screenshot-mention.dirs` = JSON array of folders. The Desktop bridge's
 * readDir has no mtimes, so recency comes from the timestamp macOS puts in the file name
 * ("Screenshot 2026-10-13 at 11.34.27 PM.png").
 */

import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'

const PLUGIN_ID = 'screenshot-mention'
const DEFAULT_DIRS = ['~/Desktop', '~/Pictures/Screenshots', '~/Documents/Screenshots']
const NAME_RE = /^(screenshot|screen shot|cleanshot|bildschirmfoto|skärmavbild)\b.*\.(png|jpe?g|gif|webp|heic|tiff?)$/i
// A complete token: `@ss` / `@ssN` followed by whitespace (or a chip boundary).
const DONE_TOKEN_RE = /(^|\s)@ss([1-9])?(?=\s)/
const ANY_TOKEN_RE = /(^|\s)@ss([1-9])?(?=$|\s|[.,;:!?)])/g
const EDITABLE = '[contenteditable="true"][role="textbox"], [data-slot="composer-rich-input"] [contenteditable="true"], [contenteditable="true"][aria-multiline]'
const MIME = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', heic: 'image/heic', tif: 'image/tiff', tiff: 'image/tiff' }

const bridge = () => (typeof window !== 'undefined' ? window.hermesDesktop : null)
const fail = (error, title = 'Screenshot not attached') => host.notifyError?.(error instanceof Error ? error : new Error(String(error)), title)

function screenshotDirs() {
  try {
    const list = JSON.parse(localStorage.getItem(`${PLUGIN_ID}.dirs`) || 'null')
    if (Array.isArray(list) && list.length) return list.map(String)
  } catch {
    /* default */
  }
  return DEFAULT_DIRS
}

/** Epoch ms from a macOS / CleanShot screenshot name, or 0. Handles 12h (incl. U+202F before PM) and 24h. */
export function stampFromName(name) {
  const s = String(name || '')
  const m = /(\d{4})-(\d{2})-(\d{2})\D+?(\d{1,2})[.:](\d{2})[.:](\d{2})(?:\s*([AaPp])\.?[Mm])?/.exec(s)
  if (!m) return 0
  let hour = Number(m[4])
  const ap = (m[7] || '').toLowerCase()
  if (ap === 'p' && hour < 12) hour += 12
  if (ap === 'a' && hour === 12) hour = 0
  const dup = /\((\d+)\)\.[a-z]+$/i.exec(s) // "… (2).png": same second, taken later
  const t = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), hour, Number(m[5]), Number(m[6])).getTime()
  return Number.isFinite(t) ? t + (dup ? Number(dup[1]) : 0) : 0
}

export function pickLatest(entries, n = 1) {
  return entries
    .filter(e => e && !e.isDirectory && NAME_RE.test(e.name || ''))
    .map(e => ({ name: e.name, path: e.path, at: stampFromName(e.name) }))
    .sort((a, b) => b.at - a.at || b.name.localeCompare(a.name))
    .slice(0, n)
}

export async function findScreenshots(n = 1) {
  const b = bridge()
  if (!b?.readDir) throw new Error('This Hermes build cannot list folders for plugins')
  const lists = await Promise.all(screenshotDirs().map(dir => b.readDir(dir).then(r => (Array.isArray(r?.entries) ? r.entries : [])).catch(() => [])))
  return pickLatest(lists.flat(), n)
}

export function stripTokens(text) {
  return String(text || '').replace(ANY_TOKEN_RE, '$1').replace(/[ \t]{2,}/g, ' ')
}

function dataUrlToBytes(dataUrl) {
  const bin = atob(dataUrl.slice(dataUrl.indexOf(',') + 1))
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

async function toFile(shot) {
  const b = bridge()
  const read = b.readFileDataUrlForAttach || b.readFileDataUrl
  const res = await read(shot.path)
  const dataUrl = typeof res === 'string' ? res : res?.dataUrl || ''
  if (!dataUrl.startsWith('data:')) throw new Error(res?.message || `Could not read ${shot.name}`)
  const ext = (/\.([a-z0-9]+)$/i.exec(shot.name)?.[1] || 'png').toLowerCase()
  return new File([dataUrlToBytes(dataUrl)], shot.name, { type: MIME[ext] || 'image/png', lastModified: shot.at || Date.now() })
}

/** Mirrors core's writeComposerImage name sanitizing (electron/main.ts). */
export const copyStem = name =>
  String(name || '')
    .replace(/\.[^.]+$/, '')
    .replace(/[^\p{L}\p{N}._-]+/gu, '_')
    .replace(/^[._-]+|[._-]+$/g, '')
    .slice(0, 80)

// ── live conversion: token → image chip ─────────────────────────────────────

/** Originals whose chips are in a composer, trashed on the next send. path → name */
const pending = new Map()

/** Find the text node + offsets of the first complete token inside `root`. */
function locateToken(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue || ''
    // Token at the end of a text node, followed by a chip/nothing, counts once a space follows anywhere.
    const m = /(^|\s)@ss([1-9])?(?=\s|$)/.exec(text)
    if (!m) continue
    const start = m.index + m[1].length
    const end = start + 3 + (m[2] ? 1 : 0)
    const after = text.slice(end)
    if (!after.length && !node.nextSibling) continue // still typing
    return { node, start, end: after.startsWith(' ') || after.startsWith('\u00a0') ? end + 1 : end, count: Number(m[2] || 1) }
  }
  return null
}

let converting = false

async function convert(editable) {
  if (converting) return
  const hit = locateToken(editable)
  if (!hit) return
  converting = true
  try {
    // Remove the token through the editor's own input path so core's draft stays in sync.
    const range = document.createRange()
    range.setStart(hit.node, hit.start)
    range.setEnd(hit.node, Math.min(hit.end, (hit.node.nodeValue || '').length))
    const sel = window.getSelection()
    editable.focus()
    sel.removeAllRanges()
    sel.addRange(range)
    if (!document.execCommand('delete')) range.deleteContents()

    const shots = await findScreenshots(hit.count)
    if (!shots.length) return fail(new Error(`No screenshots found in ${screenshotDirs().join(', ')}`))
    const files = await Promise.all([...shots].reverse().map(toFile)) // oldest first
    const dt = new DataTransfer()
    for (const f of files) dt.items.add(f)
    editable.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
    for (const s of shots) pending.set(s.path, s.name)
    cache.at = 0
  } catch (error) {
    fail(error)
  } finally {
    converting = false
  }
}

function onInput(event) {
  const target = event.target
  if (!(target instanceof HTMLElement)) return
  const editable = target.isContentEditable ? target.closest('[contenteditable="true"]') || target : null
  if (!editable || !DONE_TOKEN_RE.test(`${editable.innerText || ''} `.replace(/\u00a0/g, ' '))) return
  // Let the @ popover's own insert settle first.
  setTimeout(() => void convert(editable), 30)
}

// ── typeahead ────────────────────────────────────────────────────────────────

let cache = { at: 0, shots: [] }
function refresh() {
  if (Date.now() - cache.at < 3000) return
  cache.at = Date.now()
  void findScreenshots(9).then(shots => (cache = { at: Date.now(), shots })).catch(() => undefined)
}

function ago(ms) {
  if (!ms) return ''
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export function completionItems(query, shots) {
  const q = String(query || '').toLowerCase()
  if (!/^ss[1-9]?$/.test(q)) return []
  const latest = shots[0]
  const items = [
    {
      insert: '@ss',
      display: 'Latest screenshot',
      meta: latest ? `${latest.name}${latest.at ? ` · ${ago(latest.at)}` : ''}` : 'No screenshots found',
      icon: 'image'
    }
  ]
  const n = /^ss([2-9])$/.exec(q)?.[1] ? Number(q[2]) : Math.min(3, shots.length)
  if (n > 1 && shots.length > 1) items.push({ insert: `@ss${n}`, display: `Latest ${Math.min(n, shots.length)} screenshots`, meta: 'Moved to Trash when you send', icon: 'image' })
  return items
}

// ── plugin ───────────────────────────────────────────────────────────────────

export default {
  id: PLUGIN_ID,
  name: 'Screenshot Mention',
  register(ctx) {
    refresh()
    pending.clear()

    document.addEventListener('input', onInput, true)
    ctx.onDispose?.(() => document.removeEventListener('input', onInput, true))

    ctx.register({
      id: 'screenshot-mention-completions',
      area: COMPOSER_AREAS.atCompletions,
      data: {
        provide: query => {
          refresh()
          return completionItems(query, cache.shots)
        }
      }
    })

    ctx.register({
      id: 'screenshot-mention-middleware',
      area: COMPOSER_AREAS.middleware,
      data: {
        handler: async draft => {
          if (!draft || typeof draft.text !== 'string') return draft

          // A token that never got converted (sent too fast): attach now, ask for one more Enter.
          if (new RegExp(ANY_TOKEN_RE.source).test(draft.text)) {
            const editable = document.activeElement?.closest?.('[contenteditable="true"]') || document.querySelector(EDITABLE)
            if (editable) {
              setTimeout(() => void convert(editable), 0)
              host.notify?.({ kind: 'info', title: 'Screenshot attached', message: 'Press Enter again to send it.' })
              return null
            }
            draft = { ...draft, text: stripTokens(draft.text) }
          }

          // After use: the chips' bytes are already copied into Hermes, so trash the originals.
          // Only originals whose chip is still on screen (core names the copy `<sanitized name>_<rand>.<ext>`).
          if (pending.size) {
            const page = document.body?.innerText || ''
            for (const [p, name] of [...pending]) {
              pending.delete(p)
              if (page.includes(copyStem(name))) void bridge()?.trashPath?.(p).catch(() => undefined)
            }
            cache.at = 0
          }
          return draft
        }
      }
    })
  }
}
