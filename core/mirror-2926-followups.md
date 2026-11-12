# Mirror follow-ups 2910–2925: update metrics, worker queue, priority migration, checkpoints, CI and Desktop notes

Patches: **2910–2925** (2926 is documented separately).

This page groups sixteen consecutive core patches by feature. Each group has the same sections: Behavior, How it works, Code, Tests, Applying, Gotchas and plugin status.

**Test status for this whole page.** The tests listed below are the regression tests included in the patches. **The mirror pass has not rerun them**; patch replay/tree verification is separate and does not show that any behavioral test passed. Claims below come from the patch diffs and the tests they add or change, not from a fresh run.

**Applying (all groups).** Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These patches share dependencies with the surrounding core series (for example 2910 and 2912 extend the `--up-to` work in [mirror-2909-c-update-up-to.md](mirror-2909-c-update-up-to.md), and 2911 extends [mirror-2909-c-always-on-rollover.md](mirror-2909-c-always-on-rollover.md)); do not apply them as independent cherry-picks.

**Plugin status (all groups).** Core/source-series changes. No separately installable hermes-mods plugin is required. Any plugin-looking path (for example `apps/desktop/src/plugins/...`) belongs to the core source tree, not to an independently deployed mod.

---

## 1. `--up-to` refusal metric class (patches 2910, 2912)

### Behavior

A refused `hermes update --up-to <sha>` is counted in the shared update-run metric with `failure_class = up_to_refused` instead of the generic `aborted_before_apply`. A refusal that happens before any update receipt is opened keeps that class and reports `outcome = refused`, `failed_stage = none`, without creating a receipt directory.

### How it works

**2910:** `up_to_refused` joins the closed `UPDATE_STOP_CLASSES` set, the v4 shared-metrics schema enum and the developer metrics doc. (The class covers a malformed target, one not first-parent ahead, or an unsupported flag combination.) A parametrized stop-reason case asserts the class maps to itself with outcome `failed`. A docstring now explains why `test_capability_marker_names_the_flag` reads source text: the literal `"--up-to"` in the update subcommand parser is the capability marker an external sync tool greps for, so it must not be renamed.

**2912:** the table case from 2910 is removed (it duplicated coverage) and replaced by a dedicated test that calls `record_stop_without_receipt("up_to_refused", "refused")`, checks the dimensions are valid, that no receipt context or `logs/update_receipts` dir exists afterwards, and that the row validates against the v4 JSON schema.

### Code

- `hermes_cli/observability/shared_metrics_contract.py`
- `hermes_cli/observability/schemas/hermes.shared_metrics.v4.schema.json`
- `website/docs/developer-guide/relay-shared-metrics.md`

### Tests

- `tests/hermes_cli/test_shared_metrics_update_stop_reasons.py`
- `tests/hermes_cli/test_update_up_to.py` (docstring only)

### Applying

See the shared procedure above.

### Gotchas

The enum is a closed set; any consumer of the schema must accept the new value. Do not rename the `"--up-to"` literal in the parser. Which concrete refusal paths emit `up_to_refused` comes from the earlier `--up-to` patch, not from these two.

---

## 2. Always-on rollover warms every unarchived board (patch 2911)

### Behavior

Before the always-on roller switches traffic to a newly started backend, it also warms the board endpoint of every unarchived Kanban board that backend lists, not only the paths replayed from the old backend's recent requests. This avoids a cold first read of a rarely-polled board queueing behind already-warm boards on the shared read slots right after a switch.

### How it works

`Roller.board_paths(port)` GETs `/api/plugins/kanban/boards` on the successor (using the warm request timeout). For each entry with a non-empty string `slug` and no truthy `archived`, it builds `/api/plugins/kanban/board?board=<url-quoted slug>`, de-duplicated. Any failure, non-200 status or malformed body returns an empty list (best effort; previous behavior). `roll` appends those paths to the replay paths, skipping ones already present, before calling `warm`.

### Code

- `hermes_cli/always_on_rollover.py`

### Tests

- `tests/hermes_cli/test_always_on_rollover.py`: `test_roll_warms_every_board_on_disk_not_only_the_replayed_ones` (listed, quoted-slug, archived, empty and malformed entries; default board not duplicated within a round).

### Applying

See the shared procedure above; needs the always-on rollover code from the 2881–2884 series.

### Gotchas

Warming is best effort: a failed board list silently skips the extra warms. The read-slot count (two shared admission slots) is the motivating assumption in the source comments, not something this patch changes.

---

## 3. Worker-queue placement for courier and origin-bound creates; `move --pool` (patch 2914)

Builds on [kanban-worker-pool-queue-2678.md](kanban-worker-pool-queue-2678.md) and [kanban-worker-first-placement.md](kanban-worker-first-placement.md).

### Behavior

On a gateway configured with a worker queue:

- A courier create that the hub admission judge denies is routed through the same pool route a local deny uses (`runs_on` = the pool label plus coordination labels such as `capability:hub-write`). Worker-first enrollment is used only when no live queue label exists; the create is refused only when neither a queue nor a worker pool entry is available, or when the card is pinned to the hub.
- An origin-bound create (one carrying an origin session) takes the worker queue like a local create. Chat ownership pins storage, not execution.
- A replayed courier request rebuilds the queued `runs_on`, so a lost reply does not raise `origin_creation_conflict`.
- An explicit caller `runs_on` or `placement` pin still wins.
- `hermes kanban move <id> --pool` can repin any ready, unleased worker-first card created on this gateway, including one that already started. The Board receives `[pool label, *coordination labels]`. The origin repin (`--runs-on machine:<origin>`) keeps its stricter rule: unstarted cards only. A card naming an action no pool worker is labelled for is refused `repin_unavailable`.

### How it works

`courier_plan` calls `pool_route` with the queue for a deny. `_prior_landing` restores a stored `runs_on` for replays. `queue_decision` no longer excludes `origin_intent`. `_origin_enrollment` stops passing `queue=False`. New `_landing_placement` (tool placement module) runs origin enrollment then courier landing and promotes a queued `runs_on` to the card's `runs_on` unless the caller supplied one. `kanban_worker_repin.eligibility` accepts the pool target, computes labels via `_pool_target`, drops the "already started" refusal for the pool path only, and threads the chosen labels and `matches` (empty for the queue) through the Board repin, the moved event and the readback comparison.

### Code

- `hermes_cli/kanban_hub_admission_placement.py`
- `hermes_cli/kanban_worker_placement.py`
- `hermes_cli/kanban_worker_repin.py`
- `hermes_cli/kanban_move.py` (comment)
- `tools/kanban_tools.py`
- `tools/kanban_tools_placement.py`

### Tests

- `tests/hermes_cli/test_kanban_pool_queue.py`: courier deny queues (with and without worker pools) and replays; worker-first only without a live label; refusal with neither; explicit machine pin wins; hub-pinned/host-workspace deny refuses; origin-bound tool create queues; explicit pins on origin-bound creates; `queue_decision` accepts courier intent; caller `runs_on` beats queued landing.
- `tests/hermes_cli/test_kanban_worker_repin.py`: `kanban move --pool --json` through the CLI slash path repins a started card; coordination labels preserved; Board-refused states still refuse; started card reaches the Board for `--pool` but not for the origin repin; other single-machine targets refuse; unlabelled action refuses.

### Applying

See the shared procedure above.

### Gotchas

Scheduled (not ready) cards stay refused; move them after they wake. A queued card has no named machine (`matches` is empty). The Board, not this repo, owns the final placement validity check.

---

## 4. Priority migration tolerates the sync clamp (patch 2916)

Builds on [kanban-priority-bounds-and-global-migration-2409b.md](kanban-priority-bounds-and-global-migration-2409b.md) and [kanban-priority-migration-churn-2563.md](kanban-priority-migration-churn-2563.md).

### Behavior

The operator priority migration (`supervisor-requests --priority-operation migrate/apply/restore`) no longer refuses with "priorities changed; capture a new manifest" when a replica's stored priority was silently clamped to the cap (1000) while the manifest recorded the raw above-cap original. Restore without `--allow-legacy-rollback` restores an above-cap original at the cap rather than refusing or raising a card above the cap; with the flag it restores the exact legacy value. Any other drift still refuses.

### How it works

New `kanban_priority.at_value(stored, recorded)` is true when equal, or when `recorded > PRIORITY_CAP` and `stored == PRIORITY_CAP`. `apply_attached` uses it for the "holds the original" check on apply and the "already restored" check on undo, computes the restore value as `old` (flag) or `min(PRIORITY_CAP, old)` (no flag), and adds `manifest_priority` to the `reprioritized` event when the written value differs from the manifest value. The old up-front refusal for above-cap restore without the flag is removed. Canonical `verify` accepts the capped value in the restore direction. The rollout doc is updated: prefer omitting the flag.

### Code

- `hermes_cli/kanban_priority.py`
- `hermes_cli/kanban_priority_executor.py`
- `hermes_cli/kanban_priority_prepare.py`
- `docs/kanban-priority-rollout.md`

### Tests

- `tests/hermes_cli/test_kanban_priority_clamp_equivalent.py` (new): simulated equal-version re-send clamps silently; apply after clamp; capped guarded restore then re-apply; legacy flag restores originals; never-applied clamped row restore is a no-op; real edits still refuse; `at_value` truth table; canonical verify.
- `tests/hermes_cli/test_kanban_priority_cli.py` and `tests/hermes_cli/test_kanban_priority_executor.py` (updated expectations: restore without the flag caps instead of refusing).

### Applying

See the shared procedure above.

### Gotchas

Clamp equivalence is exactly `PRIORITY_CAP` against an above-cap original; `PRIORITY_CAP - 1` or a raise of an in-band value is an edit. A sync pull may clip a legacy-restored value again on any replica. The migration's effective ranking is unchanged because effective priority is capped at read time.

---

## 5. Update checkpoint census agrees with the checkpoint (patch 2917)

### Behavior

- The unscoped gateway `active-work` control-socket answer carries a `checkpoint` object (`active`, `preservable_managed`, `cron`, `preservable`, computed with cron cut), so an updater's pre-flight census uses the same predicate as the gateway's instant-update checkpoint. If it cannot be read, the field is omitted. A profile-scoped answer carries no `checkpoint`.
- A checkpoint refusal now names units outside its probes as "N other unit(s) (an unmapped turn, a side agent or a managed turn that is not a Kanban card)", and names chat-turn session keys again.
- When every gateway refused the pause (so none was stopped), the posix pause path prints "No gateway was stopped …; nothing to restart" instead of announcing a recovery restart. The settle call still runs.

### How it works

`update_checkpoint.checkpoint_work(runner, cron_cut=...)` returns the count check as one dict and is reused by `checkpoint_for_update`. `_unpreservable_work` now accepts any `Mapping` (the live `_running_agents` is a view, not a dict) and counts named kinds to compute the "other" remainder. `_pause` in `update_cmd_posix_pause` branches on whether the token still holds posix units, unmapped entries or profiles.

### Code

- `gateway/update_checkpoint.py`
- `gateway/run_active_work_verb.py`
- `hermes_cli/update_cmd_posix_pause.py`

### Tests

- `tests/gateway/test_update_checkpoint_census_agreement.py` (new): real runner accounting for cards plus cut cron plus one unmapped turn (census and refusal agree), the same turn once mapped, cards and cron alone preservable, non-card managed turn unpreservable, unreadable verdict omitted, profile-scoped answer without a verdict.
- `tests/hermes_cli/test_update_preswap_deadline.py`: `test_a_refused_pause_announces_no_restart`.

### Applying

See the shared procedure above.

### Gotchas

The census field is additive; consumers that ignore it keep kind-based behavior and the checkpoint stays the atomic gate. Whether any external updater actually reads `checkpoint` is outside this repo and untested here.

---

## 6. Cron aborted-drain latch and shared shutdown predicate (patch 2918)

### Behavior

After a restart-wait drain is aborted and the owner keeps serving, a cron run that already received a forwarded cancel keeps its per-run "owner shutdown" latch. The ambiguous cancel is treated as the safe-side single restart-cut rerun rather than booked as a lost-ownership failure. Legacy interrupt-only stops use the same "is the owner draining" predicate as the latch code.

### How it works

`latch_stop` documents that the latch is permanent for the run. In `execute`, the `_stopped_by_own_shutdown` fallback now returns `_owner_draining(authority)` instead of re-reading `_interrupting_remaining_work` and `_draining` directly.

### Code

- `gateway/session_cron.py`

### Tests

- `tests/gateway/test_session_cron.py`: `test_forwarded_cancel_during_owner_restart_stays_a_restart_cut_after_the_drain_ends`, parametrized over `shutdown`, `aborted_drain` and `interrupt_only`.

### Applying

See the shared procedure above; related to the cron latches in [mirror-2909-c-cron-shutdown-latches.md](mirror-2909-c-cron-shutdown-latches.md).

### Gotchas

A genuine lost-claim cancel forwarded during the drain is indistinguishable and is deliberately also treated as a restart cut (one rerun, no failure).

---

## 7. Lifecycle-scan depth exhaustion is reported as an incomplete scan (patch 2923)

### Behavior

When the gateway-lifecycle guard scan hits its referenced-script depth limit while examining text that would be executed, the refusal says the scan could not complete ("the scan depth limit was reached (depth N)") rather than reading as if a gateway restart/stop command was detected. Inert (non-executed) text at the depth limit does not set a refusal. A chain that really reaches a lifecycle command keeps the true "cannot restart, stop, or uninstall the gateway" message.

### How it works

New `_depth_limit_reached(budget, depth, executed=...)` sets `budget.refusal` only for executed text and returns `executed`; `_contains_unsafe_gateway_action` calls it at the depth limit. The refusal text reaches the scan result, the terminal guard and the cron guard.

### Code

- `cron/lifecycle_guard.py`

### Tests

- `tests/cron/test_lifecycle_guard_depth_refusal.py` (new): a 12-deep harmless script chain via `scan_gateway_lifecycle`, the terminal guard and `check_gateway_lifecycle` (cron path); inert depth cap does not taint a later true refusal; a short real lifecycle chain keeps the true refusal.

### Applying

See the shared procedure above.

### Gotchas

The verdict (blocked) is unchanged for deep chains; only the explanation changes. Depth 8 is the limit exercised by the tests, taken from the module constant.

---

## 8. macOS state CI lane on main only for state-surface pushes (patches 2920, 2921)

Related: [ci-macos-state-required.md](ci-macos-state-required.md).

### Behavior

The standalone macOS state workflow runs on a push to `main` (or `ci-probe/**`) only when changed files match a paths filter that equals the PR classifier's selection for that lane. A scheduled run every four hours tests main HEAD as a backstop for changes outside the filter and for pushes whose diff GitHub cannot fully evaluate. Scheduled and push runs on the same ref share a concurrency group and collapse. PR selection, manual dispatch and release runs are unchanged.

### How it works

**2920:** `hermes-state-macos.yml` gains `on.push.paths` (state modules and tests, shared test fixtures, test-runner scripts, dependency manifests, `.github/**`, `justfile`, the python launcher) and `on.schedule` (`23 */4 * * *`); the concurrency group expression treats `schedule` like `push`. `ci.yaml` comments are updated. The test emulates GitHub's `*`/`**` glob semantics and asserts the filter agrees with the classifier for edge paths and for every tracked file, and that the schedule and concurrency group collapse with pushes. **2921:** corrects the comment about the large-diff limit (GitHub misses matches beyond 3,000 changed files) and makes the test assert patterns use only `*` and `**` (no `?`, `+`, `[`, `]` or leading `!`).

### Code

- `.github/workflows/hermes-state-macos.yml`
- `.github/workflows/ci.yaml` (comments only)

### Tests

- `tests/ci/test_macos_state_routing.py`: edge-path equality, whole-tree equality (`git ls-files`), schedule/concurrency collapse, glob-syntax guard.

### Applying

See the shared procedure above. Requires a self-hosted macOS runner label already configured by the CI series; the workflow files are inert on a fork without it.

### Gotchas

If you add a path to the classifier's state surface you must add the matching `paths` entry or the equality test fails. The four-hour schedule means a regression outside the filter can take up to four hours to surface.

---

## 9. Desktop update pause withdraws prepared approvals (patch 2922)

### Behavior

When a Desktop turn is asked to pause for a core update, unanswered gateway approval requests for that session are withdrawn (not approved, not denied) so the turn can reach its durable boundary even if the approval wait started before the tool was registered as in flight. The model-facing notice says the approval was withdrawn unanswered, the command was not approved, and to ask again after the turn continues. A later continuation publishes a fresh, unanswered request. Nothing is withdrawn when no pause is requested, the agent has no step-boundary hook, the turn opted out of auto-continue, the interrupt flag is set (Stop), or the approval belongs to another session.

### How it works

`mid_tool_cancel_step` calls new `tui_gateway.update_pause_approval.withdraw_for_update` once a pause is requested. It lists pending gateway approvals for the session key and calls `approval.withdraw_gateway_approval(..., NOTICE)` for each (the queue's first-settlement lock). Later polls keep withdrawing approvals a batch publishes afterwards.

### Code

- `tui_gateway/update_pause.py`
- `tui_gateway/update_pause_approval.py` (new)

### Tests

- `tests/tui_gateway/test_update_pause_approval.py` (new): a real two-command terminal batch needing consent pauses without executing either command and leaves no pending approvals; a blocked approval wait is withdrawn on pause and a continuation gets a fresh request id; non-pausing cases (`no_pause`, `no_hook`, `opted_out`, `other_session`, `stop`) keep the approval.

### Applying

See the shared procedure above; depends on the Desktop update-pause series.

### Gotchas

Withdrawal is not denial: the command is not run and nothing is persisted as answered. The path covered by tests is the Desktop (`tui_gateway`) mid-tool poll; other surfaces are untested here.

---

## 10. Desktop unread-dot rationale comments (patches 2924, 2925)

### Behavior

No runtime change. Source comments in the profile switcher and the bot row now say why an unread dot there stays the plain unread class instead of an outcome tone, which two summary builders and which contract type would need an `outcome` field to change that, and that per-session outcome tones appear on visible session rows (single-profile scope shows no rows for background profile squares; all-profiles mode can show rows from several profiles).

### How it works

**2924:** comment text in `profile-switcher.tsx` and `bot-row.tsx` (names `_bot_sessions` in `gateway/session_group_controls.py`, `_canonical_session_row` in `tui_gateway/methods_profiles.py`, and `ProfileCanonicalSession`). **2925:** qualifies the profile-switcher comment for all-profiles scope.

### Code

- `apps/desktop/src/app/chat/sidebar/profile-switcher.tsx`
- `apps/desktop/src/plugins/hermes-bots/bot-row.tsx`

### Tests

None; comment-only (the source patch notes a whitespace diff check only, which the mirror did not rerun).

### Applying

See the shared procedure above.

### Gotchas

Documentation only: do not infer new behavior from the comments.

---

## 11. Test-only hygiene (patches 2913, 2915, 2919)

### Behavior

No production behavior changes.

- **2913:** the node-deps "owner stopped during its pnpm install" test now proves the pnpm shim was still blocked at its read gate when the job was stopped. The shim touches `owner.passed` after the gate; the stop-wait ends early if it appears; the test asserts it is absent after the stop-wait and again before releasing the gate. Without this, a broken gate would let the test pass without detecting the bug.
- **2915:** remove a redundant local `import os` in the foreign-namespace session takeover test.
- **2919:** the clarify-timeout test waits (up to 30 s) for the viewer to receive the `request.cancel` frame instead of racing the asynchronous fanout writer, then asserts the exact cancellation payload (`id`, `method: clarify`, `reason: timeout`).

### How it works

Test-source edits only; production deadlines and retry policy are unchanged.

### Code

None beyond the tests.

### Tests

- `tests-js/node-deps.test.mjs`
- `tests/tui_gateway/test_session_takeover.py`
- `tests/gateway/test_clarify_lock_keeps_locked_answers.py`

### Applying

See the shared procedure above.

### Gotchas

The node-deps test is skipped on Windows and parametrized; the new assertions only matter on platforms where the shim runs.
