# Plugin code attestation bound to boot-pinned review records (mirror 5706, 5716)

## Behavior
- Reviewed directory plugins are registered before import and approved files are revalidated on strict observations. Unknown plugins and entry-point plugins mark the attestation ledger unknown, withholding verified-code/capsule capability evidence; this is not a general import prohibition. Identify startup is charged to its deadline.
- The boot-pinned registry maps plugin identities to approved per-file SHA256 digests. Snapshot 5716 adds two frozen fixture sets and consistency controls; public fixture identities and hashes will be normalized to illustrative sanitized bytes, not original private archive attestation.

## How it works
`hermes_cli/plugin_review_records.py` and `data/plugin-review-records.json`; `code_attestation.py`, `plugins_loader.py` and `gateway/capsule_contract.py` consult them. Fixtures under `tests/fixtures/plugin_reviews/` hold sanitized illustrative sources with a SHA256 list and provenance. Their public names/digests describe only those normalized bytes and must not be cited as verification of an original private archive.

## Code
- `hermes_cli/plugin_review_records.py`
- `hermes_cli/code_attestation.py`
- `hermes_cli/plugins_loader.py`
- `gateway/capsule_contract.py`
- `hermes_cli/data/plugin-review-records.json`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_plugin_review_records.py` checks registry pinning/schema/file bounds and marks an unreviewed entry point before still calling its loader. `test_plugin_review_attestation.py::test_supported_plugin_boot` checks boot attestation/capability outcomes. Snapshot 5716 adds fixture-manifest consistency, frozen-byte validation and one-byte mismatch controls in its fixture regression suite; the public normalization must keep those assertions internally consistent with the sanitized fixture names and bytes. `tests/gateway/test_capsule_identify_budget.py::test_advisory_charges_startup_and_rejects_completed_late_positive` checks deadline accounting. These tests are not independent review of the original private plugin archive; deployment/import-policy enforcement beyond attestation is untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files. For real deployment, independently review the actual plugin sources and pin their own approved digests. The normalized public snapshot is illustrative only and cannot authorize the original private plugins.

## Gotchas
- A digest mismatch makes attestation unknown. Recompute public fixture hashes only from the sanitized fixture bytes; do not present their agreement as original private archive attestation.

## plugin?
No. Core loader feature that governs plugins.
