# Bootstrap import compatibility audit

Candidate core: `82f288a1e596880b5d92fbe6cb58547e4d87730b` (PR #934).

The original `run_agent.py` pin `50f38d7bfd75e9366d3d30f2794ac4a6f043f90f83be76c619e9545797de98e3`
was located by hashing file history, at commit
`4101ef74427b9e980f463ec8f999b23dd3f42188`.
The full diff to the candidate includes already accepted browser lifecycle imports,
UI callback plumbing, memory-context/teardown changes, provider-profile routing
hooks, background-review gating, and delegation comments. These are covered by
existing accepted versions, including `e219e81c6183db22935fcc05bfe34823cbc313bd62a850363ae03fd254610b98`
on installed core `25b72fadef22b090f5e25b6c96cb9a1a25f03e38`.

Against that latest accepted file, the candidate changes only the
`ModuleNotFoundError` guard around `import hermes_bootstrap`: it also accepts a
missing dotted parent of the target module. For this top-level module there is no
nonempty dotted parent, so ordinary missing-dependency errors still propagate.
This changes no account credential or request route fence.

The wrapped request-client factories, `recover_with_credential_pool`,
`_failed_credential_identity`, `switch_model`, both interruptible API-call seams,
and `perform_api_call` retain accepted file digests. Selection's recovery wrapper
still forwards `billing_unverified` and accounts failures against the selected
snapshot before rotation; its terminal wrapper still rejects changed route/model
or identity headers. No new credential fence is bypassed by the bootstrap guard.
No `selection.py` change or fallback composition-pin refresh is required.

Added digest: `2da577406ece8b47552d0ebb8b604e59d23398bc48198f12a09baa34f6cfbce0`.
All prior digests remain accepted for mixed-version rollout.

## Verification

- Installed runtime dependency environment, Python 3.14.9:
  `scripts/test-all -k account-usage --hermes-core <installed-core>`:
  account-usage and repo-guards both PASS (exit 0).
- The install's separate Python 3.14.7 test environment also passes both suites.
- `python3 plugins/account-usage/check_compat.py --json`: exit 0,
  no drift or skipped checks against installed core.
- The same check against files materialized from the exact candidate: exit 0,
  no drift or skipped checks. Before this edit it reports only the run_agent pin.

These are local suite and candidate fingerprint receipts, not a claim that an
already-running gateway has reloaded. Host installation is verified separately.
