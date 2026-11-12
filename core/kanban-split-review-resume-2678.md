# Split per-PR review resumes a parked owner

Patches: **2566, 2567, 2649**.

## Behavior

- A PR owner that parks on a review (`dependency_wait` with an authenticated `review_checkpoint`) naming several PRs (for example one core PR and one plugin PR) can be reviewed by one exact-head review card per PR, each naming only its own PR. Previously every awaited parent had to approve every holding PR, so such an owner stayed held `active_pr` with no worker, even after all reviews finished.
- Now the owner resumes (one continuation, no merge approval) when every awaited parent left exact-head evidence on a checkpointed PR and the union of that evidence covers every holding PR.
- Mixed verdicts also resume: one parent requests changes on its PR (exact-head repair evidence) while a sibling approves the other PR. The owner gets one run to repair.
- Still held (`active_pr`): a sibling that never reviewed, an approval of a PR outside the checkpoint, an inexact head, or any holding PR left uncovered.
- A parent only counts toward a union when the checkpoint's own `awaited` list names it. A parent present only because its history could not be proven finished before the park (legacy checkpoint without `awaited`, reopened, replica-unverified) must itself approve every holding PR; its approval of one PR is no evidence for another.

## How it works

All three entries change `resumes_review` in `hermes_cli/kanban_dependency_review.py`.

- 2566: the old rule `all(parent approves every holding PR)` becomes: every parent must approve at least one checkpointed PR, and the union of approvals must cover every holding PR. The change-request (repair) check that already existed is unchanged and still short-circuits when repair evidence from the parents covers every PR.
- 2567: approvals are collected per parent. Parents not in the checkpoint's `awaited` list (when that field is a list; otherwise none are named) must each cover every holding PR on their own; the union rule applies only among named parents.
- 2649: repair evidence is also collected per parent. Each parent must contribute either an approval or repair evidence, the unnamed-parent rule stays, and the final cover test is `holding PRs ⊆ (union of repairs ∪ union of approvals)`.

## Code

`hermes_cli/kanban_dependency_review.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_dependency_review_split.py` (2566, 2567), `tests/hermes_cli/test_kanban_dependency_review_mixed.py` (2649). Entry path: `resumes_review` called directly on a board fixture built with the shared review helpers (the split test file also imports the dispatcher): split approvals resume once; approvals must cover every PR and every parent; the live shape (holding PR set is one PR, the sibling approves the other) resumes; a legacy checkpoint without `awaited` with split approvals keeps the hold (2567); change request on one PR plus approval of the other resumes once, mixed reviews must still cover every PR, need exact-head evidence from every parent, an unrelated-PR approval does not cover, and a legacy checkpoint keeps the per-parent rule (2649). The CLI, REST and Desktop paths are **untested**, as is a real Board replica.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Resumption is still one continuation only; claiming any new run spends it.
- Cards parked before the `awaited` field existed fall back to the strict per-parent rule, so a split review of such a card does not resume automatically.
- Parents whose replica history is unverified (not proven finished before the park) are not named in `awaited` and so still need to approve every holding PR themselves; that case keeps the hold.

## Plugin

Core change; no plugin required.
