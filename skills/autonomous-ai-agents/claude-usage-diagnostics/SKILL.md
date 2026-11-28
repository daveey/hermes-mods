---
name: claude-usage-diagnostics
description: "Use when quota is out, no route, or asked where tokens go."
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
- Kanban workers fail before starting with `Worker could not start: ModelRouting…` and errors.log says `No eligible <class> routes under billing/capability policy` (step 3), or pool cards sit `ready` with free slots while workers log `llm_paced` events (`references/pacing-governor.md`).
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
- `This request would exceed your account's rate limit. Please try again later.` — ambiguous: capture sanitized response headers. Explicit request resets identify short throttles; unified weekly rejection can legitimately carry a long `Retry-After`. For allowed ordinary usage but model rejection, read [model-specific headers](references/model-specific-weekly-headers.md).

The reset time in the user-facing "resets in ~2.6d" message derives from this error — reconcile the two before explaining anything.

### 2. Probe the live API with pooled tokens

Hermes pool tokens are in `~/.hermes/auth.json` → `credential_pool.anthropic[]` → `access_token` (opaque `sk-ant-oat01…`). Run `scripts/probe_anthropic_pool.py` (or inline the same request) to probe models × credentials:

- Auth sanity check: a nonexistent model name returns **404**, proving the token authenticates — a 429 is then a genuine limit, not a broken token.
- **Max-plan OAuth tokens need a Claude-Code-shaped request.** Include the system prompt `You are Claude Code, Anthropic's official CLI for Claude.` plus `anthropic-beta: claude-code-20250219,oauth-2025-04-20`. Without them Anthropic returns **429 on healthy accounts**, and that is indistinguishable from a real rate limit. The bundled script already does this. Never hand-roll a bare `/v1/messages` probe.
- **Calibrate the probe before trusting it.** Run it against a credential you know works right now: the one Hermes is actively serving from (`←` in `hermes auth list`), or the account the user says works in the desktop app. If the probe says 429 where real traffic succeeds, the probe is wrong. Fix it before reporting anything.
- `GET /v1/models` with the same Bearer token lists what the account can see.
- **Per-account quota windows:** `GET https://api.anthropic.com/api/oauth/usage` with `Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`, `User-Agent: claude-code/2.1.0` returns `five_hour` / `seven_day` `utilization` (0-100) + `resets_at` per account. **Check `expires_at_ms` before reading its errors** — an expired access token gets `429 "Rate limited"` here, not 401, so it masquerades as a quota limit. If the resetwatch plugin is installed, `cd ~/.hermes/plugins/hermes-resetwatch/desktop && PYTHONPATH=~/.cache/hermes-mods-test-site/python-3.14/site-packages "$HOME/Applications/Hermes Python.app/Contents/MacOS/python3.14" probe.py --fresh` prints a JSON list with one row per pooled Claude/Codex account (plan, windows, reset times) — the fastest full matrix. It exits 0 even when it read nothing: one `resetwatch` row with empty `windows` and `probe cannot import httpx` in `details` means the interpreter lacked its deps.

Probe **all models on all credentials** and report the matrix. Partial availability is normal: some accounts are limited, others aren't. **A uniform all-429 matrix is a red flag for a broken probe, not proof that everything is limited.**

### 3. Check the pool state and what actually happened

- `hermes auth list` — per-credential status and cooldown timers. `model_cooldowns` in `auth.json` are **per model**: a credential benched for opus still serves other models.
- `hermes auth refresh anthropic <n>` re-mints tokens and clears stale cooldowns — run it once to rule out drift, then re-probe. It will not lift genuine API 429s.
- When every credential is benched for the requested model, resolution raises "No Anthropic credentials found" and the gateway falls back to the configured fallback provider. That is correct behavior — the pool DID rotate through everything first; say so plainly instead of implying rotation failed. A card pinned to one model does not fall back: once that model's pool is exhausted, its worker dies before inference with `No eligible <class> routes under billing/capability policy` while cards on other providers keep starting. Treat that pattern as a possible quota wall, not proof of a routing bug: check every account's `seven_day` on the usage endpoint (step 2), alongside the verbatim error and model×account probe, before attributing it to routing code or recent commits, even when a deploy landed minutes before the first failure. If quota evidence does not explain it, inspect billing/capability policy and route eligibility rather than assuming exhaustion. Pools are per gateway, so a chat on another host still serving that model says nothing about this host's accounts. Cards that gave up during the wall need a way back: count them from `gave_up` events, not from an owner card's list, and follow the kanban-orchestrator skill's stuck-card recovery reference (provider quota).

### 3a. Healthy live account skipped by Hermes

- Inspect `model_cooldowns` as well as credential-wide status: `hermes auth list` can show `ok` while a model-specific cooldown blocks selection. Compare with a correctly shaped live completion before declaring quota exhausted.
- After an exact account×model completion succeeds despite a local cooldown, use `hermes auth reset <provider> <exact-entry-id>` (`anthropic` or `openai-codex`), not a token rotation or whole-pool reset. Read back the affected worker profile's effective auth store; confirm the target cleared and genuinely exhausted entries stayed unchanged. Run a one-turn smoke through that profile with the tested provider/model and verify no fallback. Output followed by a teardown timeout proves a response, not successful job completion; worker recovery additionally requires a normal-gate run with served-model, tool activity and result evidence.
- An enabled `fast-rate-limit-fallback` plugin may still be inactive: run its `check.py` with the managed Hermes Python and inspect `wrappers_active`. After upgrades, AST compatibility guards can disable wrappers while the plugin list still reports enabled. Do not replace compatibility fingerprints without auditing changed core behavior and rerunning regression tests.

For managed routing or unavailable independent reviewers, read `references/managed-routing-evidence.md`.
Kanban and delegate review recovery must use capability-only routing: no selector or an optional
`class:deep`, `class:standard` or `class:fast`, with provider omitted and never mandatory. Remove legacy
exact-model/provider restrictions through supported interfaces, not a scheduled restoration of exclusivity.
Keep interactive chat model guidance, diagnostic account×model probes and benchmark/judge auxiliary calls
separate from worker routing. Review independence requires genuine run/session custody and the supported
review handoff, not different model identities; preserve quota, pacing, funding and billing gates.

### Managed workers versus direct CLI

When a direct CLI call rotates successfully but a Kanban/managed turn repeats one account's 429, inspect the agent-construction handoff before changing grants or relay state. Carry the resolver's `credential_pool` along with its bearer into the managed agent; preserve explicit launch-key authority, fallback-provider boundaries and deferred class routing. Test real managed construction followed by real pool recovery for both providers. A CLI probe can persist cooldowns and make later managed probes pass without fixing rotation: live acceptance must begin with an authorized reset of a known-walled first row and show 429 → account switch → completion within the same managed run. Do not treat an unscoped `all exhausted or empty` log line as evidence that the requested provider is out.

### Successful Codex probe but exhaustion survives reload

Test successful-probe recovery through select → persisted auth row → fresh pool reload in an isolated HERMES_HOME, not only the selected in-memory entry. Clearing last_status_at to None can let the persistence recency merge reinstate a still-future disk exhaustion. Preserve the observed failure generation when designing the fix, and test that a newer concurrent 429 and unrelated model cooldowns survive; do not broadly bypass recency with status_cleared_ids. An external HTTP200 proves capacity, not that the pool observed or persisted recovery. Keep the incident's historical cause separate from a reproduced persistence defect.

### 3b. Verify account-selection and rotation plugins together

Before recommending activation of account-selection plus fast-rate-limit-fallback, read `references/plugin-composition.md`.

### 3c. "Codex provider quota exhausted" even though a fallback is configured

- **The surfaced error is the primary's, not the real failure.** When every fallback entry fails, session init and cron resolve re-raise the primary AuthError and log the fallback's exception at debug level at most. Don't explain the toast as "Codex is out". Find out why the fallback failed.
- **Read `fallback_providers` on every host and profile** (`sed -n '/^fallback_providers:/,/^[a-z]/p' config.yaml`). An entry that repeats the primary provider, or `fallback_providers: []`, means there is no real fallback. Prepend a working route (back up the file first), then prove it with `hermes chat --oneshot --ignore-rules -t '' --max-turns 1 -Q -q 'Reply with exactly: fb-ok'`. Expect "switching to fallback: <provider>" followed by `fb-ok`. Leave profiles with an explicit `[]` alone; that is a deliberate pin.
- **Re-run the resolver in-process to tell transient from config.** Use the gateway's venv python with `sys.path.insert(0, <hermes-agent>); import hermes_bootstrap` before importing `tui_gateway.server` or `cron.scheduler`, because bare imports miss the bootstrap's site setup. Then call `_resolve_agent_model_runtime(model_override, provider_override)` or `_resolve_job_runtime(job, id, _load_cron_job_config(job, id, name))`. Run it from a script file in scratch, not `python - <<EOF` (the bootstrap treats `-` as a path and fails), and use it to prove a per-job pin resolves before the job next fires. If it resolves to the fallback now, the earlier failure was transient and its cause was lost.
- **A chat refuses every turn on a far quota wall while its class has a free model:** read `references/walled-pin-not-deferred.md`.
- **Cron has its own route and no reroute.** Cron jobs run on `cron.model` / `cron.model_provider` (`hermes config get cron`), not `model.default`. When that provider's pool is out for days, Kanban reroutes cards by itself (`run_route` shows `tier_reason: default+quota_reroute`), but every unpinned cron job that needs an agent fails `Subscription quota unavailable` and parks until the reset the first time it fires. Count the parked ones from `quota_hold_until` in `cron/jobs.json`. One job now: `hermes cron edit <id> --model <working model> --provider <its provider>`, then re-set its schedule (the "Release cron quota holds" bullet below). The fleet fix is the class route in the next bullet: a fallback chain is not walked on a quota wall. Put it on the quota-wall alert card if one is open rather than filing another.
- **A concrete cron.model walled on subscription quota never walks `fallback_providers`.** `SubscriptionQuotaUnavailable` is not an AuthError, so `_resolve_job_runtime` re-raises it. Route cron around the wall with a class selector instead: `cron.model: class:<level>` plus `cron.model_provider: auto` (both keys are required, because a class with any other provider is refused).
- **Fleet keys come from `shared-config.json` on the `state` branch of daveey/hermes.** That covers `cron.model`, `kanban.default_model` and `fallback_providers`. The next shared-config pass reverts a local `hermes config set` within minutes; check for `config cron.model: 'X' → Y` in `hermes-sync.log`. Edit those two lines with sed in a worktree at `origin/state` (re-serializing the JSON reformats it), push `HEAD:state`, then confirm with `~/.hermes/bin/hub-shared-config --dry-run` (0 keys would change). Before overriding a pinned value, read `git log -G'<key>' origin/state -- shared-config.json`: it may be David's decision, and then the outage change needs a scheduled revert.
- **Prove a cron route with a throwaway job.** Create it paused with `--deliver local --repeat 1`, resume it, `hermes cron run` it, then remove it. Read the served model from `state.db` `sessions.model`/`billing_provider` (source `cron`). A core-update drain hold defers scheduled fires but not `cron run`.
- **Release cron quota holds after fixing the chain.** The re-raised primary 429 makes cron park jobs for the full Codex window (`quota_hold_until` in `cron/jobs.json`). Re-setting the same schedule clears the hold: `hermes cron edit <id> --schedule "<current schedule>"`. Then check `Next run` in `hermes cron list`.

### 3d. The default model changed by itself while a provider was exhausted

When `model.default`/`provider` flipped with no session asking (Desktop's provider picker after a pool wall), read `references/default-model-flip.md`.

### 3e. False provider-wide quota walls (revoked tokens, stale cooldowns)

Read `references/false-quota-walls.md` before acting on this skill's steps (moved verbatim from SKILL.md).

### 4. Find what burned the quota

Grep transcripts for 429 `task-notification` storms — each names a dead subagent and its model. A parallel subagent fleet pinned to one model can drain that model's weekly bucket in a day. `ps aux` shows which desktop sessions are open and their `--model` flags; an idle session spends nothing, so only sessions actively retrying or spawning count.

**Fleet attribution (Kanban era).** Every host's `~/.hermes/state.db` `sessions` row carries `model`, `billing_provider`, `source` (kanban/desktop/subagent/cron/oneshot), `title` (Kanban: card title plus ` #N` per extra run) and token columns. Read them with `~/.hermes/bin/hermes-core-python` (system `sqlite3` is refused on live DBs) and collect workers with `hub-worker-exec all` over SSM, not ssh; include `~/.hermes/profiles/*/state.db`. Recipe, output-cap workaround and context-composition method: `references/token-attribution.md`, scripts `scripts/token_attribution_agg.py` and `scripts/context_carry.py`. Cache reads dominate (≈200× in+out), so rank by a weighted sum (in + 5×out + 1.25×cache_write + 0.1×cache_read), not in+out. Join to `task_runs` (hub kanban.db) by title prefix + start time within 3 min to cost run transitions (continued→continued, scheduled→scheduled). Per day, compare against the pool's sustainable rate before blaming any single family: a worker-pool scale-up multiplies burn.

**Read `seven_day` first.** When it is 100% with `seven_day_opus`/`seven_day_sonnet` null, the account-wide bucket is spent, and short Sonnet/Haiku cooldowns on that account will 429 again. A `[disabled]` row with no cooldowns is not capacity.

### 5. Fixes, in order of speed

1. Switch affected sessions to a model that passes the probe matrix.
2. Wait out the named reset from the verbatim error (session limits: hours; per-model weekly: days).
3. Only if the user asks for capacity beyond their Max plans: a pay-per-use API route on a dedicated identity. Two token-broker options, in the order to try:
   - `anthropic.inference` vends `ANTHROPIC_API_KEY` — but **verify with a minimal `/v1/messages` completion before promising capacity**: a key with no credit balance still lists models on `/v1/models` yet returns HTTP 400 "credit balance too low" on every completion. Model-list success ≠ working key.
   - `openrouter.inference` vends `OPENROUTER_API_KEY` and serves real Claude models as `anthropic/<name>` IDs with dots (e.g. `anthropic/claude-opus-5.5`, not dashes) via `/v1/chat/completions`. Before relying on it, `GET https://openrouter.ai/api/v1/key` shows `usage`/`limit` — the shared key has a hard spend cap and can be nearly exhausted. If it is, `openrouter.manage` (approval tier) can mint a fresh capped key. This route is verified working for opus completions.

## Pitfalls

Read `references/pitfalls.md` before acting on this skill's steps (moved verbatim from SKILL.md).

## User-specific rules

- No account-swap CLIs (claude-swap/cswap were deliberately removed). The Hermes credential pool (`hermes auth add anthropic`) is the sanctioned multi-account path; do not suggest or reinstall swapping tools.
- For burn-rate reduction, balance new work across eligible Astra, Claude, Gemini and Grok routes within the existing model classes, across all gateways and Babybear; Claude-first fallback still concentrates consumption on Claude.
- Prefer subscription capacity over metered API routes; Claude uses the Max pool and Astra uses Codex. Verify each route's actual funding rather than treating an API key or OAuth login alone as subscription entitlement.
