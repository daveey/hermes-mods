/**
 * Hide Date Dividers — removes the chronological "Earlier today" / "Yesterday" /
 * "Last week" / "<Month>" separators from the session sidebar, leaving one flat
 * recency-ordered list. Status dividers ("Working" / "Done") are kept.
 *
 * Core has no "no grouping" option (SIDEBAR_GROUPING_ORDER = date|project|status|profile),
 * so this hides the rendered SidebarDateDivider (app/chat/sidebar/chrome.tsx). The virtual
 * list measures rows with a ResizeObserver, so a hidden divider collapses to 0px cleanly.
 * A divider that was collapsed is expanded first so its sessions aren't stranded.
 */

const PLUGIN_ID = 'hide-date-dividers'
const MARK = 'data-hdd-hidden'
// SidebarDateDivider's hairline: <span aria-hidden class="h-px min-w-4 flex-1 ...">
const RULE_SELECTOR = 'span.h-px.min-w-4.flex-1[aria-hidden="true"]'
const KEEP = new Set(['working', 'done'])

function scan(root = document) {
  for (const rule of root.querySelectorAll(RULE_SELECTOR)) {
    const divider = rule.closest('div.select-none')
    if (!divider) continue
    const label = (divider.querySelector('span.uppercase')?.textContent || '').trim().toLowerCase()
    if (!label || KEEP.has(label)) {
      if (divider.hasAttribute(MARK)) {
        divider.removeAttribute(MARK)
        divider.style.removeProperty('display')
      }
      continue
    }
    const toggle = divider.querySelector('button[aria-expanded="false"]')
    if (toggle) toggle.click()
    if (!divider.hasAttribute(MARK)) {
      divider.setAttribute(MARK, '')
      divider.style.setProperty('display', 'none', 'important')
    }
  }
}

function unhideAll() {
  for (const el of document.querySelectorAll(`[${MARK}]`)) {
    el.removeAttribute(MARK)
    el.style.removeProperty('display')
  }
}

export default {
  id: PLUGIN_ID,
  name: 'Hide Date Dividers',
  register(ctx) {
    if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') return
    let raf = 0
    const schedule = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        scan()
      })
    }
    const observer = new MutationObserver(schedule)
    observer.observe(document.body, { childList: true, subtree: true, characterData: true })
    scan()
    ctx.onDispose?.(() => {
      observer.disconnect()
      if (raf) cancelAnimationFrame(raf)
      unhideAll()
    })
  }
}
