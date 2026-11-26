/**
 * Hide Sidebar Nav — drops nav rows from the top of the session sidebar
 * (Messaging, Artifacts, Scheduled jobs and plugin pages like Kanban,
 * Session Transfer, Session Lens, Tailscale, Resetwatch).
 *
 * Uses core's `sidebarNav.prefs` contribution area: core merges every `hide`
 * list at render and drops those rows; unknown ids are inert, and disabling
 * this plugin disposes the contribution so every row comes straight back.
 * Core never hides `capabilities` (it hosts the Plugins tab, the way back to
 * this plugin's off-switch). Hidden pages stay reachable from ⌘K / hotkeys.
 *
 * Ids: core rows are bare ('messaging', 'artifacts', 'cron'); a plugin row is
 * `<pluginId>:<contribution id>` — every current plugin registers it as 'nav'.
 */

const PLUGIN_ID = 'hide-sidebar-nav'

export const HIDDEN_NAV_IDS = [
  'messaging',
  'artifacts',
  'cron',
  'kanban:nav',
  'session-transfer:nav',
  'session-lens:nav',
  'hermes-tailscale:nav',
  'resetwatch:nav'
]

export default {
  id: PLUGIN_ID,
  name: 'Hide Sidebar Nav',
  register(ctx) {
    ctx.register({
      id: 'prefs',
      area: 'sidebarNav.prefs',
      data: { hide: HIDDEN_NAV_IDS }
    })
  }
}
