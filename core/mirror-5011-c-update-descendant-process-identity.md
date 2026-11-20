# Retain completion-descendant process identity across exit races

Patches: **4635** (5011 snapshot).

## Behavior

Cancellation/cleanup of owned completion descendants no longer rebuilds `psutil.Process` from a PID after the child may have exited and the PID reused. Identity (PID + creation time) is captured at READY. Only `NoSuchProcess` means gone; `AccessDenied` is preserved. Failure cleanup must still reach a dead/zombie state.

## How it works

Helpers `descendant_exited` / `reap_descendant` use the retained `Process`. Tests force disappearance at the check/use seam and a retained-identity mismatch that must not signal a replacement.

## Code

- `tests/hermes_cli/completion_process_helpers.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_completion_process_helpers.py`
- `tests/hermes_cli/test_update_completion_process.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

`pid_exists` is not a kill/status guarantee. Three-second death deadline and real tree cancellation remain. Native Windows skips in the owning tests are unchanged.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
