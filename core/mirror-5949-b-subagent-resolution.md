# Evidence-qualified subagent resolution and shutdown checkpoints

Mirror ordinals: **5937, 5938**.

## Behavior

- A lost subagent remains unresolved after an unrelated successful parent turn. Open linked work does not itself acknowledge the loss.
- Consumed completion delivery, a later complete terminal child transcript, or an explicit evidence-bearing acknowledgment can resolve the retained failure without requiring an empty board.
- Acknowledgment follows a child's compression continuations. A live continuation cannot be acknowledged through its ended ancestor, and a separate branch/reset/delegated fork cannot lend terminal proof.
- Backend exit preparation marks detached children resumable before transcript flushing, including children of idle parents. A person's explicit Stop remains stronger than a subsequent shutdown.
- A settled child result is written to the delegation ledger before slow resource teardown, so recovery can retain a known outcome.

These are source-level contracts, not a claim of installed-client or live shutdown acceptance.

## How it works

Ordinal 5937 adds `hermes_state_subagent_resolution.py`. The canonical status snapshot reads resolution evidence through its caller's database/profile and subtracts resolved IDs from current and retained losses. Successful successor turns no longer erase subagent failures implicitly.

Delivery updates write owner-qualified `state_meta` receipts in the same transaction. Receipts survive delegation-ledger pruning, are revoked when that delivery is reopened, and must cover the child's latest activity. Pending, foreign-parent and stale delivery cannot resolve a loss. Terminal reconciliation requires an ended `agent_close`/`completed` child and a transcript classified as complete.

The supported `hermes sessions outcome` command accepts repeatable `--ack-subagent` and required `--evidence`, validates ownership and liveness, stores receipts and refreshes the outcome. It handles a loss; it does not rewrite the transcript or declare unknown work successful. Generic usage after independent verification:

```bash
hermes sessions outcome PARENT_SESSION --ack-subagent CHILD_SESSION \
  --evidence "Independently verified the requested result" --json
```

Ordinal 5938 introduces `_child_tip_row`, walking only subagent compression successors while excluding canonical explicit forks. Both reads and acknowledgment writes use that walk; the writer performs its check on the transaction snapshot. Activity on a later continuation makes an older receipt insufficient.

Shutdown hooks invoke `interrupt_all` with resumable shutdown semantics before flush. Session-specific `tui_shutdown` preserves resume obligations rather than superseding them as a person-stop would. `_ChildRun.settle` records the final entry before cleanup.

## Code

- `hermes_state_subagent_resolution.py`: resolution evidence, acknowledgment, compression-tip walk and transactional delivery receipts.
- `hermes_state_session_status.py`, `hermes_state_session_status_snapshot.py`: retained-failure and canonical outcome integration.
- `hermes_cli/sessions_cmd_outcome.py`, `hermes_cli/subcommands/sessions.py`: command handler and parser flags.
- `tools/async_delegation.py`, `tools/delegate_tool_child_run.py`, `tools/delegate_tool_resumable.py`: receipt updates, early settlement and shutdown/Stop precedence.
- `tui_gateway/server.py`, `tui_gateway/session_reaper.py`: normal shutdown and signal preparation.
- `website/docs/user-guide/sessions.md`: supported acknowledgment workflow.

## Tests

**Source coverage only; none of these tests were executed in this mirror.**

| Source test file | Covered entry or contract |
|---|---|
| `tests/hermes_state/test_subagent_status_resolution.py` | Actual delivery-ledger writes and canonical refresh; acknowledged versus unacknowledged loss with open cards; unrelated successful turns; late terminal transcript; foreign/pending/reopened delivery; pruning survival; activity newer than receipt; direct `cmd_outcome` invocation and evidence/ownership/liveness validation |
| `tests/hermes_state/test_subagent_rotation_resolution.py` | Late completed compression tip; explicit acknowledgment refusal for live tips; stale explicit/delivery receipts; branch/reset/delegated-fork exclusion; multiple rotations; acknowledgment of ended continuation activity |
| `tests/tools/test_delegate_settle_checkpoint.py` | Real `_ChildRun.settle` checkpoint followed by abandoned-owner recovery before cleanup |
| `tests/tools/test_delegate_resumable.py` | Gateway/TUI shutdown resumes a child, while explicit user Stop, including Stop followed by TUI shutdown, does not |
| `tests/tui_gateway/test_shutdown_delegation_checkpoint.py` | Normal shutdown and direct signal-handler call prepare children before flush; session-specific shutdown preserves successor debt; explicit Stop supersedes it |

The CLI test calls the command handler with constructed arguments; a subprocess invocation through the full command parser is **untested by these additions**. The signal test invokes the handler directly, not an operating-system signal sent to a deployed backend. REST/installed Desktop presentation, real backend replacement and restart recovery are **untested here**. Live deployment is unproved.

## Applying

Use the **whole segmented [README Applying](README.md#applying) recipe**, including all bridges, repair and verification steps. These ordinals depend on the earlier append-only series; do not replay them independently or rewrite published mails.

## Gotchas

- A receipt's existence is insufficient: parent ownership and activity coverage matter. New activity can invalidate previously adequate evidence.
- A compression child inherits delegation markers; those markers alone must not classify it as a fresh fork.
- Explicit acknowledgment requires exact child IDs and meaningful evidence. It is not an automatic success repair.
- Shutdown preparation is bounded by the existing process lifecycle. This change does not prove successful completion under every hard-kill timing.
- Running linked cards can still determine the parent verdict; resolving a child loss does not force the whole parent session to completed.

## Why not a plugin

Canonical outcome reduction, transactional delivery acknowledgments, child session lineage and shutdown ordering are core ownership boundaries. A plugin could display a separate status but could not atomically qualify receipts or preserve resumability before the backend exits.
