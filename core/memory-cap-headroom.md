# Memory capacity feedback and consolidation headroom

Patches: `0098`, `0125`.

## Behavior

Near-full `memory` and `user` blocks show the number of free characters in their
system-prompt header and advise putting removes or shortening patches in the SAME
batch as growth. Overflow errors for add, patch, replace, and batch writes suggest
freeing enough space to leave roughly 10% headroom, rather than only enough for the
current write. Limits and atomic batch semantics are unchanged.

Unattended background memory reviews (`0125`) get advice they can actually follow. Such a review may only append:
its `patch`/`remove` operations are staged for the user's approval, not applied. So:

- The review prompt carries a **live** budget block read from disk at spawn time, per enabled store:
  `MEMORY.md (target='memory'): 150/200 chars — a new entry of at most 47 chars fits`, plus the staging rule. The
  system-prompt header the review fork replays was frozen at session start and may be days stale while other
  sessions keep writing.
- An over-budget error inside an unattended review appends the staging rule: only a plain add that fits lands now;
  if the fact does not fit, skip it (or put it in a skill) and do not retry a bare add; only if it clearly matters,
  send one batch with the removes/patches and the add, which is staged as one proposal.
- Foreground sessions, explicit (user-requested) reviews and skill-only reviews are unchanged.

## How it works

`HEADROOM_FRACTION = 0.10`; `_headroom_floor(limit)` is
`int(limit * (1 - HEADROOM_FRACTION))`. The header warning appears only when usage
exceeds that floor and the limit is positive. Free space is clamped to zero.
Overflow hints calculate the amount to free from the proposed total minus that
floor. The tool schema's IF FULL guidance tells the model to inspect the header
and consolidate on the first call. Nothing automatically removes stored facts.

`0125`: `MemoryStore.live_usage(target)` re-reads the store file and returns `(used, limit)` (entries de-duplicated
and joined with the delimiter, as the store counts them). `MemoryStore.review_budget_note()` formats one line per
enabled target; the "fits" figure subtracts one delimiter when the store is not empty. `agent/background_review.py`
appends the note (via `_live_memory_budget_note`, which swallows errors and returns "" without a store) to the
prompt of any non-explicit review that includes memory. `memory_tool` appends `UNATTENDED_REVIEW_FULL_HINT` to a
failed write's error when `FAILURE_CLASS` is `over_budget` and `skill_provenance.is_unattended_review()` is true.

## Code

- `tools/memory_tool_store.py`: headroom helpers, `MemoryStore` mutation failures,
  and `format_for_system_prompt`.
- `tools/memory_tool.py`: `MEMORY_SCHEMA` capacity guidance; `_is_unattended_review` and the over-budget hint
  (`0125`).
- `tools/memory_tool_store.py` (`0125`): `UNATTENDED_REVIEW_FULL_HINT`, `MemoryStore.live_usage`,
  `MemoryStore.review_budget_note`.
- `agent/background_review.py` (`0125`): `_live_memory_budget_note`, appended in `spawn_background_review_thread`.
- `tests/tools/test_memory_tool.py`: overflow, batch rollback, and header tests.

## Tests

```sh
scripts/run_tests.sh tests/tools/test_memory_tool.py
```

The diff extends `TestMemoryStoreReplace` to check both the minimum space and
headroom suggestion, including unchanged state after an over-budget batch.
`TestMemoryStoreSnapshot.test_header_calls_out_free_chars_when_nearly_full`
checks roomy versus tight headers. This file passed in the full-series regression run (October 16, 2026 PT).

`0125` (both files passed on the full 0001–0127 series, Oct 17, 2026 PT):

```sh
scripts/run_tests.sh tests/tools/test_memory_tool.py tests/agent/test_background_review_memory_scope.py
```

- `tests/tools/test_memory_tool.py::TestUnattendedReviewAtCap`: an over-budget single add and an over-budget batch
  under the `background_review` write origin both carry the staging rule; a foreground over-budget add does not
  (control); `review_budget_note` reports another session's on-disk writes, not this store's snapshot, and the
  exact fits figure.
- `tests/agent/test_background_review_memory_scope.py::TestReviewPromptLiveBudget`: memory and combined reviews get
  the live block; skill-only and explicit reviews do not; an agent without a store gets the plain prompt.
- Untested: whether a review model then skips instead of retrying (prompt text only), and the `replace` action and
  `user`-target overflow paths under a review (only `memory` adds and batches are exercised).

## Applying

Apply the ordered patch prefix `0001–0098` from this mirror (or the complete
`0001–0105` series) to the fork's documented merge-base in a disposable checkout.
This feature is patch `0098`; isolated application is not claimed or tested here. `0125` builds on `0098`'s
over-budget failure class and on the fork's staged-proposal review gate (unattended reviews stage `patch`/`remove`);
verified: 0001–0127 apply with `git am -3 --empty=keep` at merge-base `4957bbd0a0` (Oct 17, 2026 PT).
Run the tests above from the resulting Hermes source root, not this mods repository.

## Gotchas

- The 10% target is advice, not reserved capacity or a lower hard limit.
- Character usage includes the stored entry delimiters; count more than just the
  new text. The header reflects the memory snapshot being formatted, not a live
  refresh after every tool call. Only unattended reviews get a live figure (`0125`); a long foreground session
  still sees its start-of-session header.
- In an unattended review the 10% headroom advice cannot be followed directly (removes are staged), so the error
  says to skip or stage one whole batch instead (`0125`). Staged proposals pile up until someone approves them.
- Memory batches check the limit on the final result. Do not confuse this with
  skill-file batches, whose growing write must follow the shrinking write.

## Plugin?

No. Both prompt formatting and capacity errors belong to the core memory store;
a plugin cannot replace these paths through an ordinary tool handler.
