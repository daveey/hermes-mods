# Mirror snapshot 4352: inventory and whole-tree proof

## Scope

Append **4143–4352**: 210 non-merge source commits through
`5c9e6833b35266d7989cf607b13cf6eef77b6219`. All 4142 previously published
mails, 49 bridges, three ancestry-only entries and the verifier remain unchanged.
There are no new merges in this range. The exclusion tip and merge-base remain
`ae1ff71c39d8527227400c8e71acf9c57a8a59c6`; replay starts at
`831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

The 41 feature documents indexed in README cover every new ordinal. The updater
TERM receipt fix is included as entries 4324–4325; its feature document separates
reported source testing from checks executed by this mirror pass.

## Normalization

All 210 exports completed in private staging before installation. Each retains its
source SHA header and one mail separator. Published patch bytes were checked against
the prior inventory's SHA-256 hashes and were not rewritten.

Six changed historical fingerprint paths were reconstructed from the previous
source/applied pairs. Their new applied blobs equal the normalized new source plus
those exact historical whole-line mappings. Independent review reconstructed the
same mappings.

A second privacy review caught incident identifiers and personal aliases not matched
by the private denylist. Ten unpublished mails were corrected. Thirteen applied
fingerprints were proved from the previously verified applied blobs plus exact
whole-line replacements in comments, documentation and test fixtures; the changed
Python blobs all parse. No runtime protocol identifier was changed. Historical
removed lines remain intact. Entry 4320 omits two leading context lines and adjusts
both hunk ranges rather than altering historical source context. Both snapshot
policies carry the same privacy fingerprints; independent review approved these
proofs and scanned the unpublished additions and documents.

The mandatory additive regex repair is snapshot-bound by
`repair_hostname_escapes_4352.py` and `mirror-policy-4352-escape-repair.json`.
Its placement preimage is `36660132cd420b1c5c20f1c37422233dc46506a5`; its repaired
postimage is `8ceed34bcd66bd027ccff470fa1d3cd4b442464e`.

Gitleaks flagged ordinary documentation prose in entry 4159. Inspection confirmed
it is not a credential; a file/rule/line-scoped ignore records only that false
positive. No broad scan bypass or published-patch change was used.

## Executed checks

- Baseline 4142 inventory and whole-tree verifier: exit 0.
- Final 4352 unchanged verifier, after freezing all privacy changes: exit 0.
- Mirror/helper unittest discovery (`core/test_*.py`, Git 2.51): **227 passed**.
- Snapshot repair-specific tests: **8 passed** (also included in discovery).
- Private denylist scan of 257 changed core files: zero matches.
- Published-mail SHA-256 preservation, exact inventory, coverage and doc/JSON/Python
  whitespace checks: passed.
- Unmodified pre-commit privacy/coverage hook, including gitleaks: exit 0.
- Actual README Bash recipe, including its full verifier, segmented replay and
  mandatory repair/tree comparison: **exit 0**. Marker compilation and repaired
  whole-tree comparison both passed.

Actual repaired-tree output:

```json
{"marker_compile": "pass", "overlay_blob": "8ceed34bcd66bd027ccff470fa1d3cd4b442464e", "source_tip": "5c9e6833b35266d7989cf607b13cf6eef77b6219", "whole_tree": "pass"}
```

## Full verifier output

```json
{"ancestry_only": 3, "base": "ae1ff71c39d8527227400c8e71acf9c57a8a59c6", "bridges": 49, "merge_base": "ae1ff71c39d8527227400c8e71acf9c57a8a59c6", "patches": 4352, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "5c9e6833b35266d7989cf607b13cf6eef77b6219"}
```

Feature test suites and live display/deployment were not run by this mirror pass.
Feature documents describe the source changes, not fresh feature-suite execution.
