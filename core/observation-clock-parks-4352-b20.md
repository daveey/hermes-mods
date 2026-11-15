# Observation clock parks require explicit card opt-in

Patches: **4349–4352** (4352 snapshot).

## Behavior

Nominal soak or long observation-window clock parks are refused with soak_wait unless the card itself records the exact explicit opt-in. Run the check now and report the actual elapsed evidence window instead.

Real quota resets, non-observation cooldowns, short continuation pacing and non-clock producers remain distinct allowed cases.

## How it works

Both inferred and declared awaiting records pass through the soak checker. It checks reason plus awaiting why, not an opaque reference. Observation wording with at least one hour, or soak wording, triggers the rule; quota-reset wording is exempt. The short-continuation exemption uses the existing at-most-two-hour admission rule.

Opt-in must be a complete implementation-defined request line in this card's body or comments. Another card's request and assertions of approval inside the park reason do not qualify. Follow-ups keep comment-atomicity controls and successful clock-park fixtures on allowed cooldowns and add scoped mutation-oriented boundary cases.

## Code

- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_soak_wait.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_awaiting.py`
- `tests/hermes_cli/test_kanban_cli_outside_park_live_claim.py`
- `tests/hermes_cli/test_kanban_refused_op_comments.py`
- `tests/hermes_cli/test_kanban_soak_wait.py`
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`
- `tests/hermes_cli/test_kanban_sweep_gate.py`
- `tests/hermes_cli/test_kanban_timed_park.py`
- `tests/hermes_cli/test_kanban_triage_park.py`
- `tests/hermes_cli/test_kanban_wake_check_home.py`
- `tests/hermes_cli/test_kanban_wake_episode_metadata.py`
- `tests/hermes_cli/test_kanban_wake_episode_probeless.py`
- `tests/hermes_cli/test_kanban_wake_episode_triage_sync.py`
- `tests/tools/test_kanban_outside_park_live_claim.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

This is a scoped text/admission rule, not universal natural-language authorization. A deploy or natural-event precondition still needs its genuine producer/probe, not a renamed observation timer. CLI schedule and tool refusal cases ship; tests and mutation campaigns were not executed here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
