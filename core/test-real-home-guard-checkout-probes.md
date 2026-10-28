# Test real-home guard: a checkout's own identity probes are readable

Patch **0629** (source `a40af33743`). Test infrastructure only.

## Behavior

The test suite's real-home guard (`tests/home_io_guard.py`) refuses file access under guarded roots (the live
Hermes home, the scratch TMPDIR). A checkout that itself lives under a guarded root (a worktree in the scratch
TMPDIR, or the live install) still has to read its own identity files: `pm.environments.store_root` walks the
checkout and its ancestors for `install-stamp.json`, and `owning_home_root` reads
`<checkout parent>/installs/<key>/facts.json`. Those reads are now allowed; writes and other siblings stay refused.

## How it works

`HomeIOGuard` exempts read access to `install-stamp.json` in the checkout or an ancestor and to
`<checkout parent>/installs/*/facts.json`, the same way it already exempted the payload `manifest.json` beside a
guarded checkout.

## Code

`tests/home_io_guard.py`.

## Tests

`tests/hermes_cli/test_real_home_tripwire.py::test_checkout_identity_probes_are_readable_not_writable` (reads
allowed, writes and other siblings refused).

## Applying

Test-only; apply with the rest of the series.

## Gotchas

None known. Could this be a plugin? No: it is the test harness itself.
