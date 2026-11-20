# Actionable macOS shared-hook refusals

Mirror entries: 4770.

## Behavior

The shared push checker names the refused candidate shape and the applicable recovery rather than presenting every refusal as a core-testing problem.

## How it works

Darwin admission still fails closed. It distinguishes core candidates, missing/nonregular checkers, unknown layouts, stale state layouts and unreadable candidates. Messages direct portable core testing to a worker and suggest rebasing stale state layouts or publishing from an appropriate worker. The complete current state layout remains admitted through its own checker; Linux admission is unchanged.

## Code

- `scripts/check`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Mac/state admission tests assert classifications and remedies, including real-Git push fixtures, instead of checking only the refusal exit code.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/scripts/test_check_mac_admission.py`
- `tests/scripts/test_check_state_admission.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

This is diagnostic wording, not a checker-less admission path. A clearer refusal does not authorize bypassing a gate or running portable core checks on a Mac.
