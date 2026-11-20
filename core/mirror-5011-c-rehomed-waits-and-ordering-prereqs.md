# Rehomed wait preservation and mixed review/ordering parents

Patches: **4553, 4555** (5011 snapshot).

## Behavior

During owner rehome, task rows can become visible before their event history. An unanswered human request stays a wait for an answer rather than promoting because history has not arrived yet. A scheduled park keeps waiting while its first park event is still being imported; missing-first-park grace is anchored on the local restore receipt, not old imported clocks. Existing explicit unblocks still clear the ask.

A PR owner parked on both an exact-head review and an ordering prerequisite (a done card whose latest result is not a review verdict) can resume once the review parents satisfy exact-head approval or repair. A still-open ordering parent, a review without valid exact-head approval, and an all-ordering park keep their previous hold/handoff rules.

## How it works

Restore/chat-sub recovery reads stored asks as sticky. Missing imported parks use a stable bounded backstop from the local restore receipt. Unheld imported failures can still recover.

`resumes_review` treats awaited parents that are done and whose latest result is not a review result as completed ordering prerequisites: they neither authorize nor refuse. Only parents the checkpoint itself named as awaited qualify. Historical parents reopened after the park, late-linked parents, and legacy checkpoints without an awaited list stay on the per-parent review rules. At least one review parent must remain.

## Code

- `hermes_cli/kanban_block_routing.py`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_restore_chat_subs.py`
- `hermes_cli/kanban_dependency_review.py`
- `hermes_cli/kanban_dependency_review_ordering.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_rehomed_waits.py`
- `tests/hermes_cli/test_kanban_dependency_review_ordering_parent.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Do not infer a human answer or waiver from missing history. One continuation is consumed on the next claim; a crash does not grant another. Foreign review/ordering parents resume on the importing worker via the parent-results mirror. Mixed awaited sets are not all-review and not all-ordering.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
