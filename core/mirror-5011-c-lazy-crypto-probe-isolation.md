# Keep lazy crypto probe files out of the product checkout

Patches: **4612** (5011 snapshot).

## Behavior

The lazy secrets main-level crypto probe no longer creates then unlinks a root `.py` in the product checkout. Parallel routing ownership scans cannot enumerate it then fail `FileNotFoundError` after cleanup. Dispatch and crypto assertions are unchanged.

## How it works

The probe runs as `python -c` with the checkout first on `sys.path`. A regression pauses the scanner after enumeration until the real probe and cleanup exit, with uncached reads.

## Code

- `tests/ci/test_update_probe_isolation.py`
- `tests/hermes_cli/test_lazy_secrets_dispatch.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/ci/test_update_probe_isolation.py`
- `tests/hermes_cli/test_lazy_secrets_dispatch.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

No product-code, hub-threshold, scanner-exemption, retry, or timeout changes. Tracked-only scanning was rejected because it would hide untracked product edits.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
