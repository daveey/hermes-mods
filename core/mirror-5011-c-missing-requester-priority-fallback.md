# Optional missing-requester effective-priority fallback

Patches: **4607** (5011 snapshot).

## Behavior

Effective-priority lookup can opt in to `--fallback INT` when the board is readable but has no urgency observation for that requester. Existing requesters ignore the fallback. Missing or unreadable boards stay fatal. JSON emits `fallback_reason` only for this case.

## How it works

Typed `MissingRequester` remains `ValueError`-compatible. Strict callers keep exit nonzero. The shipping helper independently tolerates older failing modules.

## Code

- `hermes_cli/kanban_effective_priority.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_effective_priority.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

This is caller-selected recovery for advisory metadata, not a silent default. Review independence and landing authority are unchanged.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
