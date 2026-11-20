# Remote notice and outcome passes gated on semantic inputs

Patches: **4592** (5011 snapshot).

## Behavior

Replica task liveness writes no longer invalidate settled notice and card-change proofs every host pass. Quiet notice inputs survive irrelevant row deltas. Stale seeded observations are distinguished from deliverable late notices. Outcome fanout keys per-task content cursors plus status/owner/chat-link fingerprints, not rebuilt ledger object identities.

## How it works

Remote probes run when an unlinked terminal card arrives, when a swept card is rewritten or unreadable, when rows vanish or the sequence moves backwards, and when signatures are unreadable. Liveness writes to cards this host does not owe are ignored. Expired unobserved notices are not work; late notices still are. Identity rearms equal aggregate proofs; expiry proofs rearm on a backward clock. Concurrent replica writes do not publish.

## Code

- `tui_gateway/kanban_unloaded_gates.py`
- `tui_gateway/notice_fanout.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/tui_gateway/test_h2_gate_mutations.py`
- `tests/tui_gateway/test_kanban_unloaded_gates.py`
- `tests/tui_gateway/test_remote_change_semantic_gate.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Unreadable equals unknown equals hot. Commit counters are excluded, but replacement, observer recovery, and same-inode restore still retire a semantic proof. Heartbeat and rebuilt-ledger skips use real cursors. Deployed CPU acceptance is not claimed here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
