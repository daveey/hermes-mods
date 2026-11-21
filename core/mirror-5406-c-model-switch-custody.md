# Model-switch publication respects live turn custody

Mirror entries: 5282, 5283, 5298, 5299, 5300. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Managed model switches publish runtime state without overwriting turn-custody fields. A foreign admitted turn prevents a runtime swap, including one admitted during slow switch resolution. Gateway model/config and slash requests can queue the original intent for the next turn instead of acknowledging an unapplied switch.

The currently executing turn may intentionally switch its own runtime only with revalidated local custody on its execution thread. Copied context, stale thread IDs, stopped/released leases and RPC callers do not gain that exemption. A refused one-shot MoA command does not queue a permanent pin or overwrite the active one-shot restore.

## How it works

Staging shallow-copies mutable containers and ultimately publishes the whole staged runtime except active/last turn holders and lease TTL. Entry 5283 supersedes the earlier changed-fields-only approach so intentional reset values still land. A shared reentrant turn-admission lock serializes check-and-publish with admission. Both in-place and managed publication refuse any active foreign holder.

`owns_turn` checks process, agent, active lease, stop flag, holder and execution thread together. Gateway deferral preserves raw request, confirmation, scope/persistence overrides, session pin intent and switch-count semantics. One-shot callers use `defer_if_busy=False`; restore state is installed only after success. Deferred reset and failed stash paths report deferral/refusal rather than falsely reporting completion or acknowledging an older queued pick.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `agent/agent_runtime_switch.py`
- `agent/origin_turn_custody.py`
- `agent/turn_facade_lease.py`
- `tui_gateway/methods_config_set.py`
- `tui_gateway/methods_slash.py`
- `tui_gateway/methods_tools.py`
- `tui_gateway/model_switch.py`
- `tui_gateway/session_compression.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests cover holder/runtime preservation across admission/release races, quiet publication and reset values, serialization of in-place publication, already-admitted turns, pool reload, actual config/slash handlers queueing the pick, replaying original intent, same-turn versus copied-worker authority, stale/stopped/released custody, user picks during MoA, refused second one-shot restore, deferred reset and stale-queue acknowledgement controls.

Relevant source suites/fixtures:

- `tests/agent/test_managed_switch_active_turn.py`
- `tests/agent/test_managed_switch_own_turn.py`
- `tests/agent/test_managed_switch_turn_custody.py`
- `tests/agent/test_switch_model_pool_reload.py`
- `tests/tui_gateway/test_model_switch_lost_turn_race.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

A busy-time queue is not a successful immediate swap. Preserve intentional reset values and all original persistence/once flags. Tests use controlled runtime/session fixtures; real provider connections, full Desktop composer UI and end-user tool/REST paths beyond the named gateway handlers are untested.

## Why core instead of a plugin

Runtime publication and turn-admission ownership must share the same lock and custody identities; plugin callbacks cannot atomically protect the agent object's live publication.
