---
name: claude-usage-diagnostics
description: "Use when Claude/Codex quota is out or no route is eligible."
version: 1.0.0
author: Hermes Agent (curator)
license: MIT
metadata:
  hermes:
    tags: [Claude, Anthropic, rate-limits, credential-pool, diagnostics]
    related_skills: [claude-code, token-broker, hermes-agent]
---

# Claude Usage-Limit Diagnostics

## When to Use

- The user reports being "out of Claude usage", or desktop sessions stick on "rate limited — resets in ~X" and endlessly retry.
- New Hermes sessions silently fall back to another provider (e.g. codex) instead of using Claude.
- Kanban workers fail before starting with `Worker could not start: ModelRouting…` and errors.log says `No eligible <class> routes under billing/capability policy` (step 3).
- A Claude account "should have plenty left" per the usage modal but calls still 429 — the contradiction needs reconciling with evidence.

Diagnose from **evidence, in this order**: (1) the verbatim API error in session transcripts, (2) a live model×account probe **calibrated against a known-good client**, (3) the running session inventory. Consult the usage modal last, and never rely on it alone. Every claim you report must point to a verified measurement. If the claim can't be measured, say it's unverified.

## Procedure

### 1. Get the verbatim error from transcripts, not the UI

Session transcripts live at `~/.claude/projects/<munged-cwd>/<session-id>.jsonl`. Map running desktop sessions to IDs with `ps aux | grep 'MacOS/claude'` (the `--resume=<id>`, `--model`, and `--effort` flags are in the command line), then grep the day's transcripts:

```bash
find ~/.claude/projects -name "*.jsonl" -newermt "today" -exec grep -h -oE "[^\"]{0,120}rate_limit, HTTP 4[^\"]{0,150}" {} + | sort -u
```

The message names the exact bucket:
- `You've hit your session limit · resets <time>` — 5-hour rolling window, per account.
- `You've hit your weekly limit · resets <date> <time>` — **per-model** weekly cap; the model is named at the end (`model sent to the API: claude-<model>`).
- `You've hit your <Model> limit` — that model's weekly bucket.
- `This request would exceed your account's rate limit. Please try again later.` — short-window request-rate throttle, not quota.

The reset time in the user-facing "resets in ~2.6d" message derives from this error — reconcile the two before explaining anything.

### 2. Probe the live API with pooled tokens

Hermes pool tokens are in `~/.hermes/auth.json` → `credential_pool.anthropic[]` → `access_token` (opaque `sk-ant-oat01…`). Run `scripts/probe_anthropic_pool.py` (or inline the same request) to probe models × credentials:

- Auth sanity check: a nonexistent model name returns **404**, proving the token authenticates — a 429 is then a genuine limit, not a broken token.
- **Max-plan OAuth tokens need a Claude-Code-shaped request.** Include the system prompt `You are Claude Code, Anthropic's official CLI for Claude.` plus `anthropic-beta: claude-code-20250219,oauth-2025-04-20`. Without them Anthropic returns **429 on healthy accounts**, and that is indistinguishable from a real rate limit. The bundled script already does this. Never hand-roll a bare `/v1/messages` probe.
- **Calibrate the probe before trusting it.** Run it against a credential you know works right now: the one Hermes is actively serving from (`←` in `hermes auth list`), or the account the user says works in the desktop app. If the probe says 429 where real traffic succeeds, the probe is wrong. Fix it before reporting anything.
- `GET /v1/models` with the same Bearer token lists what the account can see.
- **Per-account quota windows:** `GET https://api.anthropic.com/api/oauth/usage` with `Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`, `User-Agent: claude-code/2.1.0` returns `five_hour` / `seven_day` `utilization` (0-100) + `resets_at` per account. **Check `expires_at_ms` before reading its errors** — an expired access token gets `429 "Rate limited"` here, not 401, so it masquerades as a quota limit. If the resetwatch plugin is installed, `cd ~/.hermes/plugins/hermes-resetwatch/desktop && <gateway venv python> probe.py --fresh` prints a JSON list with one row per pooled Claude/Codex account (plan, windows, reset times) — the fastest full matrix.

Probe **all models on all credentials** and report the matrix. Partial availability is normal: some accounts are limited, others aren't. **A uniform all-429 matrix is a red flag for a broken probe, not proof that everything is limited.**

### 3. Check the pool state and what actually happened

- `hermes auth list` — per-credential status and cooldown timers. `model_cooldowns` in `auth.json` are **per model**: a credential benched for opus still serves other models.
- `hermes auth refresh anthropic <n>` re-mints tokens and clears stale cooldowns — run it once to rule out drift, then re-probe. It will not lift genuine API 429s.
- When every credential is benched for the requested model, resolution raises "No Anthropic credentials found" and the gateway falls back to the configured fallback provider. That is correct behavior — the pool DID rotate through everything first; say so plainly instead of implying rotation failed. A card pinned to one model does not fall back: once that model's pool is exhausted, its worker dies before inference with `No eligible <class> routes under billing/capability policy` while cards on other providers keep starting. Treat that pattern as a possible quota wall, not proof of a routing bug: check every account's `seven_day` on the usage endpoint (step 2), alongside the verbatim error and model×account probe, before attributing it to routing code or recent commits, even when a deploy landed minutes before the first failure. If quota evidence does not explain it, inspect billing/capability policy and route eligibility rather than assuming exhaustion. Pools are per gateway, so a chat on another host still serving that model says nothing about this host's accounts. Cards that gave up during the wall need a way back: count them from `gave_up` events, not from an owner card's list, and follow the kanban-orchestrator skill's stuck-card recovery reference (provider quota).

### 3a. Healthy live account skipped by Hermes

- Inspect `model_cooldowns` as well as credential-wide status: `hermes auth list` can show `ok` while a model-specific cooldown blocks selection. Compare with a correctly shaped live completion before declaring quota exhausted.
- After an exact account×model completion succeeds despite a local cooldown, use `hermes auth reset <provider> <exact-entry-id>` (`anthropic` or `openai-codex`), not a token rotation or whole-pool reset. Read back the affected worker profile's effective auth store; confirm the target cleared and genuinely exhausted entries stayed unchanged. Run a one-turn smoke through that profile with the tested provider/model and verify no fallback. Output followed by a teardown timeout proves a response, not successful job completion; worker recovery additionally requires a normal-gate run with served-model, tool activity and result evidence.
- An enabled `fast-rate-limit-fallback` plugin may still be inactive: run its `check.py` with the managed Hermes Python and inspect `wrappers_active`. After upgrades, AST compatibility guards can disable wrappers while the plugin list still reports enabled. Do not replace compatibility fingerprints without auditing changed core behavior and rerunning regression tests.

### Managed workers versus direct CLI

When a direct CLI call rotates successfully but a Kanban/managed turn repeats one account's 429, inspect the agent-construction handoff before changing grants or relay state. Carry the resolver's `credential_pool` along with its bearer into the managed agent; preserve explicit launch-key authority, fallback-provider boundaries and deferred class routing. Test real managed construction followed by real pool recovery for both providers. A CLI probe can persist cooldowns and make later managed probes pass without fixing rotation: live acceptance must begin with an authorized reset of a known-walled first row and show 429 → account switch → completion within the same managed run. Do not treat an unscoped `all exhausted or empty` log line as evidence that the requested provider is out.

### Successful Codex probe but exhaustion survives reload

Test successful-probe recovery through select → persisted auth row → fresh pool reload in an isolated HERMES_HOME, not only the selected in-memory entry. Clearing last_status_at to None can let the persistence recency merge reinstate a still-future disk exhaustion. Preserve the observed failure generation when designing the fix, and test that a newer concurrent 429 and unrelated model cooldowns survive; do not broadly bypass recency with status_cleared_ids. An external HTTP200 proves capacity, not that the pool observed or persisted recovery. Keep the incident's historical cause separate from a reproduced persistence defect.

### 3b. Verify account-selection and rotation plugins together

- Test actual configured PluginManager discovery with both account-selection and fast-rate-limit-fallback enabled, not just each plugin's isolated suite. Wrapping a shared recovery function can make another plugin's fingerprint guard refuse installation even when core source is unchanged.
- Require combined recovery and unload/reload tests before recommending activation: verify selected-account wire identity, cooldown preservation, long-reset rotation/fallback, and both installers active. Never bypass compatibility by indiscriminately unwrapping `__wrapped__` or accepting new hashes; explicitly audit supported wrapper composition and reject foreign wrappers.
- Distinguish on-disk implementation, fresh-process discovery, and activation in the owning live gateway/compute child. A mocked browser harness proves UI interactions, not native-app deployment or real provider dispatch.

### 3c. "Codex provider quota exhausted" even though a fallback is configured

- **The surfaced error is the primary's, not the real failure.** When every fallback entry fails, session init and cron resolve re-raise the primary AuthError and log the fallback's exception at debug level at most. Don't explain the toast as "Codex is out". Find out why the fallback failed.
- **Read `fallback_providers` on every host and profile** (`sed -n '/^fallback_providers:/,/^[a-z]/p' config.yaml`). An entry that repeats the primary provider, or `fallback_providers: []`, means there is no real fallback. Prepend a working route (back up the file first), then prove it with `hermes chat --oneshot --ignore-rules -t '' --max-turns 1 -Q -q 'Reply with exactly: fb-ok'`. Expect "switching to fallback: <provider>" followed by `fb-ok`. Leave profiles with an explicit `[]` alone; that is a deliberate pin.
- **Re-run the resolver in-process to tell transient from config.** Use the gateway's venv python with `sys.path.insert(0, <hermes-agent>); import hermes_bootstrap` before importing `tui_gateway.server` or `cron.scheduler`, because bare imports miss the bootstrap's site setup. Then call `_resolve_agent_model_runtime(model_override, provider_override)` or `_resolve_job_runtime(job, id, _load_cron_job_config(job, id, name))`. Run it from a script file in scratch, not `python - <<EOF` (the bootstrap treats `-` as a path and fails), and use it to prove a per-job pin resolves before the job next fires. If it resolves to the fallback now, the earlier failure was transient and its cause was lost.
- **Cron has its own route and no reroute.** Cron jobs run on `cron.model` / `cron.model_provider` (`hermes config get cron`), not `model.default`. When that provider's pool is out for days, Kanban reroutes cards by itself (`run_route` shows `tier_reason: default+quota_reroute`), but every unpinned cron job that needs an agent fails `Subscription quota unavailable` and parks until the reset the first time it fires. Count the parked ones from `quota_hold_until` in `cron/jobs.json`. One job now: `hermes cron edit <id> --model <working model> --provider <its provider>`, then re-set its schedule (the "Release cron quota holds" bullet below). The fleet fix is the class route in the next bullet: a fallback chain is not walked on a quota wall. Put it on the quota-wall alert card if one is open rather than filing another.
- **A concrete cron.model walled on subscription quota never walks `fallback_providers`.** `SubscriptionQuotaUnavailable` is not an AuthError, so `_resolve_job_runtime` re-raises it. Route cron around the wall with a class selector instead: `cron.model: class:<level>` plus `cron.model_provider: auto` (both keys are required, because a class with any other provider is refused).
- **Fleet keys come from `shared-config.json` on the `state` branch of daveey/hermes.** That covers `cron.model`, `kanban.default_model` and `fallback_providers`. The next shared-config pass reverts a local `hermes config set` within minutes; check for `config cron.model: 'X' → Y` in `hermes-sync.log`. Edit those two lines with sed in a worktree at `origin/state` (re-serializing the JSON reformats it), push `HEAD:state`, then confirm with `~/.hermes/bin/hub-shared-config --dry-run` (0 keys would change). Before overriding a pinned value, read `git log -G'<key>' origin/state -- shared-config.json`: it may be David's decision, and then the outage change needs a scheduled revert.
- **Prove a cron route with a throwaway job.** Create it paused with `--deliver local --repeat 1`, resume it, `hermes cron run` it, then remove it. Read the served model from `state.db` `sessions.model`/`billing_provider` (source `cron`). A core-update drain hold defers scheduled fires but not `cron run`.
- **Release cron quota holds after fixing the chain.** The re-raised primary 429 makes cron park jobs for the full Codex window (`quota_hold_until` in `cron/jobs.json`). Re-setting the same schedule clears the hold: `hermes cron edit <id> --schedule "<current schedule>"`. Then check `Next run` in `hermes cron list`.

### 3d. The default model changed by itself while a provider was exhausted

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

### 3e. Revoked-token burst leaves false quota walls

The symptom is many cards crashing within a minute or two on several hosts with `HTTP 401: OAuth access token has been revoked`, while every hub account answers `/api/oauth/profile`; a Mac's chat turns and notice replays fail meanwhile with `Subscription OAuth credential is unavailable; no selected pool entry`. The workers are holding hub-minted rows that the hub's refresh already rotated, and they only pull new rows on their own timer. On one host the same false wall shows as that 401 burst in agent.log just before a `SubscriptionQuotaUnavailable`, while `/api/oauth/usage` shows headroom on other accounts: `quota_unavailable()` skips accounts benched for auth and judges the pool from the walled rest. Size the burst in failed calls: count the WARNING `API call failed (attempt 1/` lines, one per call; the 401 text itself also repeats in ERROR tracebacks and notice-replay lines, several per call.
1. Force the pull on every worker now: over instance-role SSM, run `sudo -u ubuntu -H XDG_RUNTIME_DIR=/run/user/1000 systemctl --user start hermes-oauth-relay.service`. A Mac outside the hub's fan-out pulls on a 2-min LaunchAgent: `launchctl kickstart gui/$(id -u)/com.daveey.hub-oauth-pull`. Don't wait for the timer.
2. **Fresh tokens alone don't finish the fix.** Retries during that window fail at start with `SubscriptionQuotaUnavailable … resets at <days away>`, and that failure persists in three places:
   - an `anthropic` entry in each affected host's `~/.hermes/provider_walls.json`. If another provider is genuinely walled, that host then runs no LLM work at all.
   - a `quota_episode` event on each card, which holds it with the `subscription_quota_reset` guard even after it's unblocked to ready. Stall notices print that hold as a bare `until HH:MM:SSZ` with no date, and it can be days out. Before telling the user when it lifts, read the full date from the episode's `until` (`kanban_quota_episode.hold_until`).
   - a Kanban notice to a chat that failed then with `HTTP 429 … Retrying once at <that reset>`: its only retry is days away. Act now on each card such a notice names.
   Read every gateway's walls file (Macs included), and query each board's DB (the hub's, and each Mac's local `kanban.db`) for `quota_episode` events with provider `anthropic` from the window.
3. Disprove the wall before removing it:
   - `/api/oauth/usage` should show accounts under 100 on `seven_day`;
   - a one-shot `hermes chat --oneshot -m <model> --provider anthropic` on the worker should answer.
   Then drop only that provider's key from `provider_walls.json`: back the file up and write it atomically (tmp file + `os.replace`).
4. A card's quota episode ends at a run that ends without hitting a wall, or at an operator release; unblock, priority and `move` don't lift it. Release a disproven hold on the card's home board: `HERMES_KANBAN_BOARD=default hermes kanban quota-release <id> --reason "<why the hold is false>"` (`--probe` releases only when a live usage read shows headroom on every walled provider). It records `quota_episode_released`, and `kanban show`'s `queue_note` then calls the card admissible. Spec, links and workspace stay intact, unlike re-filing it.
   - Only hosts whose core honours the release drop the hold. The claiming host's own core computes it, and a core without `released_run` in `hermes_cli/kanban_quota_episode.py` keeps holding the card until the episode's `until` (its gateway.log keeps logging `dispatcher stuck … subscription_quota_reset=N (until …)`). Grep that marker on each host matching `runs_on`. If they are all stale, re-place the card onto hosts with the marker and free slots (`hermes kanban move <id> --runs-on <labels> --reason … --checkpoint … --json` lists `matches`; `hermes kanban dispatch --dry-run --json` on a full host prints only `at_capacity`), or wait for the stale host's core update.
   - Operator claim + reclaim is no substitute: a hub-homed card pinned to a worker can't be claimed from either host (the worker answers `cannot claim <id>: status=ready lock=(none)` and nothing changes). Use it only from the owning Mac for a `machine:macbook` card: `claim <id> --ttl 120`, then `reclaim <id> --reason …`; that reason becomes the run's error text, so keep quota/auth words out.
   - A Kanban worker's shell is fenced from board-writing CLI verbs (`claim` answers "child contexts cannot mutate Kanban tasks"). Don't route around the fence over ssh. File the release or the code fix as a card instead.
   - A hold that is not false but too broad cannot be released this way either. Example: a `class:deep` retry card (`retry_after_run_crashed`) held for one provider's real wall while the class has a free provider. `decision()` only tries alternate routes for the default/off/profile_out_of_scope reasons, so that hold is a core bug: fix it in code, not by reclaiming.
   - If the guard then says `active_pr` (a PR URL in a recent comment) and the card owns that PR, `hermes kanban resume <id> --reason "<what it must do on the PR>"` lets it continue.
   - Do this only after step 3 shows no gateway's walls file still lists the provider; otherwise the next run hits the wall again.
5. Until a host's live core contains the fix for this false wall (`git -C ~/.hermes/hermes-agent merge-base --is-ancestor <fix> HEAD`), each hub refresh can write it there again. Run any guard for that from another host (a pool worker with `ssh:<host>`), parked with `watch=true` on a probe of that host's walls file and core ancestry, never on the host itself: while the wall stands that host may run no LLM work at all (step 2), so its own card can't act.

### 4. Find what burned the quota

Grep transcripts for 429 `task-notification` storms — each names a dead subagent and its model. A parallel subagent fleet pinned to one model can drain that model's weekly bucket in a day. `ps aux` shows which desktop sessions are open and their `--model` flags; an idle session spends nothing, so only sessions actively retrying or spawning count.

### 5. Fixes, in order of speed

1. Switch affected sessions to a model that passes the probe matrix.
2. Wait out the named reset from the verbatim error (session limits: hours; per-model weekly: days).
3. Only if the user asks for capacity beyond their Max plans: a pay-per-use API route on a dedicated identity. Two token-broker options, in the order to try:
   - `anthropic.inference` vends `ANTHROPIC_API_KEY` — but **verify with a minimal `/v1/messages` completion before promising capacity**: a key with no credit balance still lists models on `/v1/models` yet returns HTTP 400 "credit balance too low" on every completion. Model-list success ≠ working key.
   - `openrouter.inference` vends `OPENROUTER_API_KEY` and serves real Claude models as `anthropic/<name>` IDs with dots (e.g. `anthropic/claude-opus-5.5`, not dashes) via `/v1/chat/completions`. Before relying on it, `GET https://openrouter.ai/api/v1/key` shows `usage`/`limit` — the shared key has a hard spend cap and can be nearly exhausted. If it is, `openrouter.manage` (approval tier) can mint a fresh capped key. This route is verified working for opus completions.

## Pitfalls

- **Kanban rate_limited storm with tier_reason `default` on a walled default provider: read `~/.hermes/provider_walls.json` first.** When *every* fallback provider is also listed there, dispatch stops rerouting (`default+wall:<p>`) and spawns on the walled default, so each card burns one rate_limited run before `default+quota_reroute`. A revoked-401 burst can record a false provider-wide wall (§3e); per-account `model_cooldowns` in auth.json that are empty on some accounts mean the provider isn't walled. Before removing any entry, confirm that provider serves live. Afterwards confirm new cards show `default+wall:` and that `rate_limited` events for this host's claimer stop. Don't override David's pinned `kanban.default_model` for this.

- **A Kanban quota hold can come from another worker's pool.** A hinted `quota_episode` (respawn_guard `subscription_quota_reset`) holds the card on every worker until its `until`. That deadline is the `min(waits)` of the pool on the worker that exited 75, and pools differ per gateway. Before calling a provider walled, check `kanban_provider_walls.live_quota_wall(provider, model)` on the admitting host: run a script from scratch with the hermes tools python and `import hermes_bootstrap`; heredoc Python is blocked in worker runs. Use `kanban_quota_episode._outstanding`/`hold_until` to list held cards. A card skips wall rerouting only when its body has a line of its own reading `require_model: true` (`kanban_provider_walls.requires_model()`; `yes`/`1` also count, case-insensitive). The word anywhere else in the title or body no longer opts it out. The rate-limit storm alert counts only this host's rate_limited runs (claims prefixed `hermes-<host>:`) and prints wall times as UTC `YYYY-MM-DD HH:MMZ`.

- **A red Codex row can be a stale duplicate login, not a dead account.** Re-adding an account leaves the old pool
  entry holding its last `usage_limit_reached` until `last_error_reset_at`, while the new entry serves fine. Decode
  each `credential_pool.openai-codex[].access_token` JWT (`https://api.openai.com/auth.chatgpt_account_id`, `exp`) to
  find entries sharing an account, check live with `GET https://chatgpt.com/backend-api/wham/usage` (Bearer token +
  `ChatGPT-Account-Id`; `rate_limit.limit_reached`, `primary_window.used_percent`), then `hermes auth remove
  openai-codex <stale id>` and prove with a one-line `hermes chat -q ... --provider openai-codex`. No re-auth needed.
- **Grey / "0/N" usage chips (any usage widget) usually mean expired tokens, not no capacity.** The pool only refreshes a token when it selects that entry, and every usage fetcher (core and community plugins alike) is read-only, so idle accounts' ~8h access tokens lapse → state `unknown`. Check `expires_at_ms` in auth.json first; `hermes auth refresh anthropic <n>` fixes it now (model cooldowns survive the refresh). The `oauth-keepalive` user plugin (`~/.hermes/plugins/oauth-keepalive`) prevents recurrence; if chips grey out again, confirm it's enabled (`hermes plugins list`).

- **`hermes auth refresh anthropic` requires a target** when the pool holds more than one credential — pass an index, entry id, or exact label (`hermes auth refresh anthropic 1`); the bare command just prints usage. Loop `for i in 1 2 3 4 5; do hermes auth refresh anthropic $i; done` to refresh all.
- **Never argue from the usage modal** — it shows session limit, weekly all-models, and only *some* per-model buckets; per-model weekly caps and request-rate throttles can be invisible while calls still 429. Quote the transcript error, then verify live.
- **When the user's direct observation contradicts your measurement, distrust the measurement first.** "It works in the desktop app on account X" while your probe says X is 429 means your probe differs from the real client. Find that difference. Don't invent a server-side theory (per-token buckets, hidden throttles) to explain away what the user is seeing.
- **Never kill processes on a theory.** Before killing anything as "feeding the rate limit", confirm it actually calls the Anthropic API: check its command line for `claude`, an SDK, or an API host. SSH/staging loops and watchers don't consume quota. Killing them destroys the user's work and helps nothing.
- **The Claude desktop app is single-account** — no pool, CLI, or credential tool can rotate its login. Stuck desktop sessions need a model switch inside the app or patience, nothing else.

## User-specific rules

- No account-swap CLIs (claude-swap/cswap were deliberately removed). The Hermes credential pool (`hermes auth add anthropic`) is the sanctioned multi-account path; do not suggest or reinstall swapping tools.
- For burn-rate reduction, balance new work across eligible Astra, Claude, Gemini and Grok routes within the existing model classes, across all gateways and Babybear; Claude-first fallback still concentrates consumption on Claude.
- Prefer subscription capacity over metered API routes; Claude uses the Max pool and Astra uses Codex. Verify each route's actual funding rather than treating an API key or OAuth login alone as subscription entitlement.
