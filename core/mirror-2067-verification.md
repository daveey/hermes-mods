# Mirror 2067 verification

Frozen source: `bae8bbe067b0fd44a9a482076b87534da63a5f37`; upstream exclusion tip: `9dcf7714df7a0d49c335925d12d4b017274ee355`.

## Inventory and replay

The unchanged `verify_mirror.py` exited **0** as the first command of the README's actual Applying recipe:

```json
{"ancestry_only": 3, "base": "9dcf7714df7a0d49c335925d12d4b017274ee355", "bridges": 39, "merge_base": "8c1a9c0b6da872cca5083b98a7a1022edc3dfb4b", "patches": 2067, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "bae8bbe067b0fd44a9a482076b87534da63a5f37"}
```

The README Bash block was extracted and executed verbatim in a shared bare source clone. Its separate applied tree also passed `verify_mirror.compare` under the frozen policy. This establishes contiguous ordinals, unique SHA headers, exact non-merge source inventory, source-parent proofs for ancestry-only entries and policy-normalized full-tree equality. Successful application alone was not accepted as proof.

All 2065 previously published patches, all 38 earlier bridges, the older policies/replay plans and the verifier are unchanged. The only missing non-merge headers at freeze were:

| Patch | Source |
|---|---|
| 2066 | `ee2d235b148f075c2baf11506c5a195f23be8c8f` |
| 2067 | `bae8bbe067b0fd44a9a482076b87534da63a5f37` |

Index preflight found three unavailable historical abbreviated objects: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`. Both full applications succeeded without needing those fallback preimages. Their presence is not claimed.

## Bridge provenance and privacy review

The single new bridge before 2066 is the binary first-parent diff `ed77044e2648b76cfc7b51f09af888d02c9d4802^1..ed77044e2648b76cfc7b51f09af888d02c9d4802`. This integration merge immediately follows the prior snapshot and precedes both new patches. Its SHA-256 is pinned in [mirror-replay-2067.json](mirror-replay-2067.json). The policy's literal/regex normalization leaves this upstream diff unchanged.

Independent review regenerated the bridge byte-for-byte, checked inventory and README slice boundaries, compared the source and applied full trees and confirmed preservation of historical bytes. No new ancestry-only or empty-mail entries were introduced.

Three new exact fingerprints in [mirror-policy-2067.json](mirror-policy-2067.json) cover privacy-only substitutions: example chat identifier suffixes in `hermes_cli/kanban_origin.py` and `tests/hermes_cli/test_kanban_origin_adoption.py` docstrings; those suffixes and a private fixture chat title in `tests/hermes_cli/test_kanban_notify_subscribe_target.py`. Fixture setup and assertions use the same generic replacements. Independent review confirmed unchanged executable logic and assertion meaning. Existing fingerprints and substitution rules are unchanged.

## Executed tests

A fresh disposable Python environment was provisioned for the README-replayed public tree using `python3 -m pm.build_env --source . --out ../test-venv --group dev --group test`. The canonical isolated runner executed:

```sh
scripts/run_tests.sh -j 1 tests/cron/test_shutdown_interrupt.py tests/hermes_cli/test_kanban_origin_adoption.py tests/hermes_cli/test_kanban_notify_subscribe_target.py
```

Result: **57 passed, 0 failed**, three files, no retries: shutdown interruption 28; origin adoption 26; subscription-target output 3. These test the public normalized fixture, real isolated cron/session stores, turn-local refusal hints and CLI parser/handler output. Full cron/all-Python suites, live gateway shutdown, production recovery, native Desktop, terminal-tool/execute_code end-to-end flows and source-vs-public runtime parity were not tested in this pass.

## Publication gates and reviewed scanner finding

The unchanged coverage checker and normal pre-commit privacy gates both exited **0** before publication. Docs/JSON whitespace checks also passed, separately from intentional diff context/mail trailer whitespace.

The staged gitleaks check initially returned exit 1 for one `generic-api-key` finding at `core/bridges/upstream-before-2066.diff:30559`.

- Why: the upstream mixed-case-provider test uses a synthetic credential-shaped value; source `da0b4467c86d9f5911f1dc796bdc205d48e8e69a`, `tests/hermes_cli/test_user_provider_mixed_case_key.py`, runs against an isolated home and loopback HTTP fixture.
- Root (happened): the generic scanner recognizes credential syntax, not fixture provenance; newly mirrored bridge paths require their own reviewed exception.
- Root (not caught): it was caught before publication by both independent review and the unchanged staged scanner, not missed by a delivery gate.
- Recurrence: earlier bridge fixture findings are documented in the 2029 and 2065 reports and existing `.gitleaksignore`; their line-specific exceptions intentionally do not authorize new locations.
- Countermeasure/proof: retain the upstream test bytes, add only the reviewed file/rule/line fingerprint, then rerun the normal staged scanner and pre-commit gate. No broad scanner exclusion or hook bypass is used.

The private denylist also scans new patches, the bridge and documentation. Feature docs explicitly separate tests executed here from untested entry paths.
