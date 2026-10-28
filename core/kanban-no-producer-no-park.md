# Kanban: no producer, no park

Patch **0529** (source `57094b7a60`).

## Behavior

A Kanban park (a card that leaves `running` to wait: `kanban_schedule`, `kanban_block` with
`kind='time'` or a parent-less `dependency`, `kanban_continue` with `resume_after_seconds`, and the
`hermes kanban schedule|block` CLI twins) used to be defined only by how it wakes: a clock, a wake probe,
or a parent. Nothing asked who makes the awaited condition true. So "waiting on something only an agent
can do, with no agent on it" was legal and looked like healthy waiting. In practice cards parked for
a day on an IAM grant "applied by the supervisor" while another machine held the rights, and an ask
for the user was hidden behind a wake probe where nobody could see it.

Now every park records a producer on its parking event:

```
awaiting = {kind: card | ci | deploy | external | clock | human, ref, why}
```

| kind | accepted when |
|---|---|
| `card` | `ref` is a task id that exists and is live (not archived, not in triage, not blocked with no human ask, not itself parked on an orphan wait). `gateway` (or `ref: "<gw>:t_…"`) names another host: it is confirmed with `hermes kanban wait-check task <id> --gateway <gw>` before the write transaction, and an archived/triage remote card is refused. A wake probe (`hermes kanban wait-check task …`) is filled in when none is given. |
| `ci` / `deploy` | `ref` holds a run, job or release id or URL. |
| `external` | `ref` names the vendor or ticket and `why` gives the evidence. |
| `clock` | a real window (a soak, a cooldown, a release slot). |
| `human` | **always refused** (`human_wait`): a wait on the user must be a visible `blocked` card with `human_request`. If the ask-gate ledger already holds an answer for it, the error quotes that answer and says not to re-ask. |

Refusals are typed tool errors (`code`), also on the CLI:

- `agent_action_wait`: the condition only changes when an agent acts (a grant, a permission, an install,
  a fix) and no producing card is declared. The error names both remedies: create the owner card on the
  gateway that holds the capability and point `awaiting` at it, or `kanban_block(kind='assignment')`.
- `human_wait`: see above.
- `invalid_awaiting`: malformed record (a CI wait with no run id, an unconfirmed remote card, …).

A park with **no** `awaiting` is classified from its reason text (the same two shapes the
session-idle checker used as interim guards): a pending step for the user, or an agent-only wall, is
refused; a task id merely mentioned in the prose does not count as the producer of an agent-only
wall, it must be declared. Everything else is stored as an inferred `clock` (no probe) or `external`
(probe) wait with `inferred: true`, so checkers can tell guesses from declarations. Short pacing
continuations (`resume_after_seconds` ≤ 2 h) are not judged by their checkpoint prose.

**Re-parks escalate.** The third park in a row on the same premise (same declared
kind/gateway/ref/why, or the same reason text for an inferred probe wait) is not parked again. The card
goes to `todo`, waiting on one new owner card: "Unstick <id>: parked 3x on the same premise". That card
is agent-owned work (never a human ask). It goes to a capable supervisor
(`kanban.orchestrator_profile` or the card's creator, never the worker that keeps parking), or to
triage when none is configured. The count restarts after an escalation. Card waits and inferred
clock waits never escalate.

The guidance changed with it: the `kanban_block` / `kanban_schedule` / `kanban_continue` descriptions,
the new `awaiting` parameter, the `agent_owned_block` remedy text and the long-park guard help all say
"a wait needs a producer; agent-action conditions are work, not waits".

## How it works

- `hermes_cli/kanban_awaiting.py` (new): `normalize`, `resolve` (a per-kind check table `_CHECKS`),
  `_infer` (text classification: `HIDDEN_ASK` / `AGENT_WALL`, judged per clause with
  negation/past-citation/cleared context), `card_orphan_reason`, `confirm_remote` (ssh, outside the
  txn; `resolve` only accepts a process-local confirmation ≤ 300 s old), `answered_ask` (read-only
  ask-gate ledger), `gate` (resolve + premise hash + re-park count), `escalate`, `card_probe`.
- Park chokepoints call `gate` inside their write txn and store `awaiting` on the event:
  `kanban_db_schedule.schedule_task`, `kanban_timed_park.park` (all timed `block_task` paths, which now
  thread `awaiting`), `kanban_continuation.continue_task` (only when it waits).
- `tools/kanban_tools.py`: `_awaiting_arg` parses the arg, refuses `human` early, fills the card probe and
  runs `confirm_remote`; `AwaitingError` maps to `tool_error(code=…)`; `_escalated` reports
  `code: repark_escalated` with the owner card id.
- CLI: `--awaiting KIND:REF|JSON` and `--awaiting-why` on `schedule` and `block`; `_guarded_park` resolves
  before the `SCHEDULED:`/`BLOCKED:` comment is written, so a refused park leaves no comment.

## Tests

- `tests/hermes_cli/test_kanban_awaiting.py` (42):
  - replays of the four real orphan parks, refused with `agent_action_wait` / `human_wait` through the
    tool path, and two of them through the CLI path;
  - a tracker card that exists but produces nothing (triage, agent-owned block), refused declared or inferred;
  - an answered ask quotes the ledger answer; an unrelated ledger topic does not match;
  - controls that still park: a running card (declared, probe derived), an in-progress CI run (declared
    and inferred), pure clock soaks, a wait on a card blocked on the user with a visible ask, a
    confirmed cross-gateway card;
  - ordinary prose that must not read as a wall or an ask (vendor 403/401, "no permissions changes
    needed", "fixed earlier", "per David's decision"), and pending shapes that must ("once David's
    approval lands", "the supervisor was asked to apply the grant");
  - re-park escalation: third park escalates to an owner card (triage when no supervisor; a configured
    supervisor gets it ready), a new premise parks, the count restarts after the owner card is done;
    pacing continues and distinct clock windows never escalate;
  - unconfirmed remote card refused inside the txn; continue refusals keep the typed code.
- `tests/tools/test_kanban_tools.py::test_schedule_parks_current_worker_with_reason` asserts the stored
  inferred `awaiting`.
- Untested: the dashboard drag-to-scheduled path (goes through the same `schedule_task` gate).

## Applying

`git am -3` after the condition-wake (`kanban_wake_check`) and timed-park patches: it extends
`validate_park` callers and the `scheduled` / `continued` event payloads.

## Gotchas

- The text classifier is a backstop for parks that declare nothing. A worker that declares
  `awaiting={kind:'external', ref:'<ticket id>', why:…}` is trusted on an agent wall. A human wait is
  refused even when that ref contains a number or URL (0707).
- A remote producer is only checked for existence and archived/triage status; its own orphan chain
  is not followed across gateways.
- The re-park count only sees parks written by this patch (events carrying `awaiting.premise`).
- With no `kanban.orchestrator_profile` and a creator equal to the worker, escalations land in triage:
  configure a supervisor profile so they are worked.

## Follow-up patches (0608, 0626)

**0608** (`2f6f37d635`): `kanban_wait_info` treats an ask-less `needs_input` block as agent-owned, matching `AGENT_OWNED_KINDS`. Tests: `tests/hermes_cli/test_kanban_wait_info.py`, `tests/hermes_cli/test_kanban_block_kinds.py` (`_cmd_block` refusal).

**0626** (`1ad73c1350`, self-heal kanban-92e96c16fd): a card's own worker (`HERMES_KANBAN_TASK` == card) parking on a wake_check that names no card or CI run (`kanban_wait_info.producer_from_wake_check` -> `probe`) with no declared awaiting is refused with `opaque_probe_wait`. Exempt: continues <= 2 h, `clock_only`, operator parks (dashboard/terminal; still recorded as inferred so the checker flags them). Repeat counting also keys on the whitespace-normalised probe (`awaiting.probe`), so the 3rd park on the same probe escalates however the reason is worded. Code: `hermes_cli/kanban_awaiting.py` (`opaque_probe`, `parked_by_own_worker`, `opaque_probe_error`, `probe_premise`, `prior_same_premise(probe=)`, gate), `tools/kanban_tools_schemas.py` wake_check text. Tests: `tests/hermes_cli/test_kanban_awaiting.py` (`test_opaque_probe_with_no_producer_is_refused` x4 [schedule, block, continue, tool], `test_opaque_probe_with_a_declared_external_producer_parks`, `test_named_producers_and_short_continues_still_park`, `test_same_probe_reparked_with_new_wording_escalates`, `test_worker_cli_park_on_its_own_card_is_refused_but_an_operator_park_is_not` [CLI + direct `kb.schedule_task`]); `test_kanban_wake_check.py` fixtures now declare awaiting. The dashboard REST path is covered only as "unchanged" by `test_kanban_dashboard_plugin.py::test_dashboard_scheduled_park_guard`.

## Follow-up patch (0707)

**0707** (fork `85fbaa93b5`): a numbered ref or URL no longer excuses a human wait.

- `_check_external` still lets a vendor ticket excuse an agent wall. If the text is a human wait, it raises `human_wait` whether or not the ref matches `_TICKET`.
- An inferred producer excuses a human wait only when that card is `blocked` or `triage` and has a `human_request` or `block_kind=needs_input` (`_carries_visible_ask`). A merely live card does not.
- The human-wait error still says to block with `needs_input`. It also says an ask that already exists elsewhere (a pending approval, a PR review) is still a visible block, and that `approval_reconciliation.preflight` must not file a second approval for the same scope. That preflight path is untested in this patch.
- `HIDDEN_ASK` also matches `daveey`, `approving review`, and `his ok`. Those tokens are the detector; this doc does not narrate a particular park.

Tests in `tests/hermes_cli/test_kanban_awaiting.py`:

- `test_external_park_naming_a_human_wait_is_refused_whatever_the_ref`
- `test_external_park_on_a_vendor_ticket_still_parks`
- `test_vendor_ticket_ref_still_excuses_an_agent_wall_why`
- `test_merge_queue_wording_is_a_human_wait`
- `test_inferred_wait_on_a_running_card_does_not_excuse_a_human_wait`

The dashboard drag-to-scheduled path remains untested.
