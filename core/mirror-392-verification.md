# Public mirror verification: 392 ordered patches

## Exact snapshot

- Core source: `2228cfe8dada6912c5f8966f967d12870d029c2d` (core PR #48).
- Merge base: `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`.
- Applied public tree: `6a98e37e5fd65d2c3ad134f45c35bbe8669779cf`.

Two patches were added after [the 390 export](mirror-390-verification.md): **0391** (retention observation slice, core PR #82) and **0392** (unresolved-goal fallback / `pre_final_gate` finalization boundaries, core PR #48). Patches 0001–0390 are unchanged. Author identity is the public noreply address and co-author trailers with private emails are dropped, as in earlier patches.

## Full apply validation

In a fresh detached worktree at the merge base, `git am -3 --empty=keep core/patches/*.patch` exited 0 with **392 commits**.

## Tests executed on the applied public series

`scripts/run_tests.sh -j 3` over nine files (`tests/agent/test_pre_final_gate*.py`, `test_pre_final_delivery_channels.py`, `test_pre_final_console_privacy.py`, `test_turn_finalizer_unresolved_fallback.py`, `tests/hermes_cli/test_retention_plan.py`, `test_retention_cli.py`): **69 passed, 0 failed, 38 skipped** (platform-gated retention cases). Not a full-suite, Desktop or runtime-activation certification.
