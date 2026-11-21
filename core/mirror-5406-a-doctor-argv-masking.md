# Fail-closed argv masking in holder diagnostics

Mirror entries: 5064, 5065, 5066, 5067, 5068, 5069, 5070, 5071, 5072, 5073, 5074.

## Behavior

- Holder descriptions retain recognizable command identifiers while masking secret-named values, credential pairs and generated-looking arguments across flags, assignments, shell text, paths and URLs.
- Nested command/flag values and wrapped/container programs receive the appropriate secret rules. Known user-selection flags remain distinct from credential flags.
- Malformed quoting/deep nesting are bounded or masked rather than crashing. The final classifier does not query filesystem path existence.

## How it works

`hermes_state_argv_secrets.py` evolves from shape-specific checks to an identifier allowlist with word/chunk, symbol, digit/case-flip and generated-key checks. Secret names override identifier-like values. Shell text is parsed with `shlex`; unchanged text keeps its original quoting. Env/flag values containing argv are judged recursively with a depth cap.

Path/URL handling judges components and percent-decoded query names/values, including bracketed secret names and OData names, while propagating masking across fragments/malformed split tails. Password flag handling follows known programs, wrapper option values and container image boundaries, including containers launched through wrappers. Generic `-u/--user` credential-pair masking applies except for known user/group or switch semantics. Globs, count flags and file-shaped secrets have narrowly scoped exceptions. The base64-neighbour pass is linear.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_state_argv_secrets.py`
- `hermes_state_holders.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

`tests/hermes_state/test_argv_secrets.py` contains named/program-secret cases, identifier controls, shell quoting, env/flag argv values, path/query/fragment handling, encoded/non-ASCII cases, wrapper/container handoff, nested-depth controls, long-path performance, and seeded token-shape/position/length rate tests. Doctor holder fixtures avoid accidental dependence on random temporary directory names. These suites are source coverage only. Live process scanning and actual doctor output on any OS are untested by this mirror task.

Source test/coverage paths:

- `tests/hermes_state/test_argv_secrets.py`
- `tests/hermes_cli/test_doctor_fts_deferral_holders.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

This is heuristic redaction, not a proof that every secret is hidden. Source fuzz tests allow nonzero leak rates; do not treat diagnostics as safe public uploads without review. Full hashes/digests or generated identifiers can be masked by design. No filesystem-existence shortcut should reveal a path's secret components or block on a stale mount. The final rate claim is scoped to measured shapes/positions, not arbitrary credentials.

## Why core instead of a plugin

Core holder/doctor diagnostics must sanitize arguments before exposing them to any consumer. A plugin cannot reliably intercept all diagnostic formatting paths and should not be entrusted with unredacted process argv.
