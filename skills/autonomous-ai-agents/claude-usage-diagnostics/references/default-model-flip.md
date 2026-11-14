# The default model changed by itself while a provider was exhausted

- **Symptom:** `model.default` and `provider` flip to another provider's model, for example to
  `claude-sonnet-5` with `base_url` and `api_mode` written as well. No session asked for that model.
- **Mechanism:** when every credential of the main provider is exhausted and no fallback works, Desktop opens its
  provider picker. It looks like a first run ("getting started").
  - Choosing an already signed-in provider there finishes at once. Anthropic is an `external` flow, so
    "I've signed in" succeeds immediately.
  - Desktop then saves that provider's recommended default (`GET /api/model/recommended-default?provider=<slug>`)
    through `POST /api/model/set` before the confirm card appears. See `completeWithModelConfirm` in
    `apps/desktop/src/store/onboarding.ts`; this applies unless your core defers that write until the user
    confirms.
- **Rule out agents first:** search `state.db` `messages.tool_calls` in the window for the model id,
  `api/model/set` and `model.default`.
- **The backend doesn't log model saves,** so time a flip from config history instead. Per-host config snapshots
  from a sync tool (`git log -p -- <host>/config.yaml`) bracket each write. A shared-config re-apply that reverts
  the flip within one sync interval is that tool working as designed, not a second bug.
- **Prevention:** a working `fallback_providers` entry keeps `setup.runtime_check` ready, because it runs the
  session resolver including the fallback chain. The picker then doesn't open.
- **Log scans must stay bounded.** Desktop logs rotate at about 10 MB each (`gui.log*`, `desktop.log*`; `agent.log*`
  is about 5 MB).
  - Scanning several time windows across every rotation with awk in one call hit the 420 s tool timeout, which
    silently ended the turn.
  - First check `ls -la` sizes and each file's first and last timestamp. Then `rg` only the covering file with an
    anchored timestamp regex such as `'^2026-10-26 (15:0[3-9]|15:1[0-7])'`.
  - `desktop.log` records backend events, not renderer requests, so there is no request log for `/api/model/set`.
