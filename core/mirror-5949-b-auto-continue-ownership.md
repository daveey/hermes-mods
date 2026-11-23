# Quiet ownership retries for automatic continuation

Mirror ordinal: **5936**.

## Behavior

Automatic continuation may pass its first ownership check and lose custody before actual submission. That second-gate refusal now preserves the interrupted-turn marker and retry backoff without publishing a terminal ownership error that asks the person to take over another owner's turn. Custody is still refused: no agent turn runs.

This reporting opt-out is specific to automatic continuation. Ordinary typed submissions retain the submission gate's default reporting policy.

## How it works

`_maybe_schedule_auto_continue` passes `report_ownership_refusal=False` to `_run_prompt_submit`. The existing refusal handling keeps the durable marker, clears scheduled/running state and imposes the existing retry delay. Resuming during that backoff cannot dispatch again or consume another attempt.

Both ordinary interrupted markers and markers paused for a core update use the automatic path.

## Code

- `tui_gateway/session_auto_continue.py`: automatic caller's submission policy.
- `tests/tui_gateway/test_auto_continue.py`: real kickoff/submit regression with ownership admission replaced only at the custody boundary.

## Tests

**Source coverage only; not executed in this mirror.**

`test_second_gate_ownership_race_keeps_continuation_without_card` in `tests/tui_gateway/test_auto_continue.py` crosses paused/unpaused markers with started, unknown and absent ledger status. It lets preflight pass and makes the actual submit refuse. Assertions cover two gate checks, no terminal error, no agent invocation, cleared running/scheduled flags, preserved marker, refusal reason and backoff, and no resubmission during backoff.

The ordinary typed-submit reporting path is **untested by this added regression**; its default is preserved by leaving other callers unchanged. Installed Desktop takeover-card behavior and a live ownership race are **untested here**. Live deployment is unproved.

## Applying

Follow the **whole segmented [README Applying](README.md#applying) recipe**, including integration bridges, repair and verification. The append-only mail series is dependent; this ordinal is not an independent installation command.

## Gotchas

- Silence is not permission to take custody. The automatic submission still refuses and retries later.
- Do not clear the durable marker just because a retry lost ownership.
- This does not certify retirement of historical or foreign holders; it changes the automatic caller's reporting policy only.

## Why not a plugin

Automatic continuation and the actual prompt admission gate are core lifecycle operations. Their caller-specific reporting choice must be passed at submission; a plugin that hides an emitted card would be downstream of the custody decision and could mask unrelated explicit errors.
