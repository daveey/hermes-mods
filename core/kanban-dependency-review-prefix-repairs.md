# Kanban dependency review: mixed prefix reviews resume repair, never approval

Patch **2031** (`27a70c3aa5d3da545961670227b7c66fdbbec250`, `fix(kanban): resume mixed prefix review repairs without approval [daveey]`). Extends the repair-continuation contract in [kanban-dependency-review-continuation.md](kanban-dependency-review-continuation.md).

## Behavior

- A review run may cover a PR and its stacked follow-up in one handoff, recording one exact head per prefix (`reviewed_sha_prefix1`, `reviewed_sha_prefix2`, ...) and a `verdicts` map (`{"prefix1": "APPROVE", "prefix2": "REQUEST_CHANGES"}`) instead of one `reviewed_sha`/`verdict`.
- When such a mixed review requests changes for at least one prefix, the parked owner gets the same once-only repair resume an exact-head `changes_required` review gives it. The next claim consumes it; a second respawn check returns `active_pr` again.
- A mixed review is never treated as approval: the prefix form is accepted only on the repair (`_reviewed_prs`) path, not on the approval path (`_approval_prs`).
- Anything incomplete or ambiguous stays held (`active_pr`): a non-40-hex or non-string head, a trailing newline, a `prefix0` key, `verdicts` that is a list or misses a prefix, all-`APPROVE` verdicts, an unknown verdict string, a top-level `verdict` alongside the prefixes, no PR, or a PR outside the parked checkpoint scope.
- Prefix heads without a `verdicts` map (including a `verdict_prefix1` key) cannot fall through to the ordering/lost-review handoff.
- The dependency-review hold diagnostic no longer reports `exact_head refused` for a well-formed prefix review.
- Unrelated plural `verdicts` (for example a benchmark `PASS` on an implementation handoff) do not mark a prerequisite as a review result.

## How it works

`hermes_cli/kanban_dependency_review.py` adds `_prefix_repair_head(data)`. It requires: no top-level `verdict`; `verdicts` is a dict; the set of `reviewed_sha_prefix<N>` suffixes equals the `verdicts` keys; a `pr` or `reviewed_pr`; every key matches `prefix[1-9][0-9]*` with a full 40-hex SHA; every verdict is `APPROVE` or `REQUEST_CHANGES`; and at least one `REQUEST_CHANGES`.

`_reviewed_prs` accepts `_exact_head(data) or _prefix_repair_head(data)` before applying its existing approval/changes test and named-PR scoping. `is_review_result` treats any `reviewed_sha_prefix*` metadata key as a review result, so the run is evaluated as a review rather than an ordinary prerequisite. `kanban_dependency_review_notice._refusal` uses the same disjunction for its `exact_head` refusal line.

## Code

- `hermes_cli/kanban_dependency_review.py`: `_prefix_repair_head`, `_reviewed_prs`, `is_review_result`.
- `hermes_cli/kanban_dependency_review_notice.py`: `_refusal`.

## Tests

`tests/hermes_cli/test_kanban_dependency_review_prefixes.py` (new; reuses fixtures from `tests/hermes_cli/test_kanban_dependency_review.py`):

- `test_mixed_prefix_review_resumes_once`
- `test_incomplete_or_unbound_prefix_review_stays_held` (15 parameter cases)
- `test_prefix_review_is_not_approval_or_ordering_handoff`
- `test_prefix_review_retains_checkpoint_scope` (unauthenticated and authenticated parks)
- `test_unrelated_verdicts_do_not_mark_prerequisite_as_review`
- `test_no_prefix_heads_is_not_repair`
- `test_prefix_hash_without_verdicts_cannot_use_ordering`
- `test_integer_pr_mixed_review_uses_repair_not_approval`

```sh
scripts/run_tests.sh tests/hermes_cli/test_kanban_dependency_review_prefixes.py tests/hermes_cli/test_kanban_dependency_review.py
```

Not rerun in this mirror pass; listed from the patch.

## Applying

Requires the dependency-review modules from 0383/0384 and later. Apply through the snapshot-bound replay in [README.md](README.md), not alone.

## Gotchas

- This is permission to repair, not merge approval and not CI evidence for the reviewed heads.
- The canonical single-head handoff (`reviewed_sha` + `verdict`) remains the preferred producer format. Prefix reviews must name every prefix in both places.
- Only `APPROVE`/`REQUEST_CHANGES` (exact case) are accepted inside `verdicts`; the top-level verdict path's case-insensitive approvals do not apply here.

## Plugin?

No. Respawn guarding is core Kanban dispatch state; a plugin cannot intercept `check_respawn_guard`.
