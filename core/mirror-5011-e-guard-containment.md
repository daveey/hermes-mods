# Containment rollback of generated and computed command scans

Mirror entries: 4783, 4784, 4785, 4786, 4804.

## Behavior

This history includes computed-executable hardening and its later containment rollback. At the end of this group, the computed-executable and generated-file protections described by those earlier changes are removed; direct lifecycle, file and budget checks remain.

## How it works

The earlier implementation located structural executable words, added literal default/simple-printer candidates and refused unresolved computed commands, while retaining executed substitution bodies, shadowing uncertainty and assignment-data boundaries. The containment entry deletes that module and generated-file tracking/preflight, removes the unresolved-computed refusal, and restores the prior file-reading policy. It addresses false promotion of inert destination text and whole-script rescanning that exhausted the line budget. Inert-destination tests replace the removed feature suites.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_computed.py`
- `cron/lifecycle_guard_generated.py`
- `cron/lifecycle_guard_variables.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

The historical computed-command patches contain harmless Bash differential matrices for executable defaults, producer outputs, substitution bodies, assignment arrays and safe twins. Those test files are subsequently deleted. The surviving inert-destination test proves that an unrelated shell sink does not turn quoted destination fragments into executable scripts; substitution-word expectations are restored with the rollback.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/cron/test_lifecycle_guard_computed_commands.py`
- `tests/cron/test_lifecycle_guard_generated_scripts.py`
- `tests/cron/test_lifecycle_guard_inert_destinations.py`
- `tests/cron/test_lifecycle_guard_substitution_words.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Do not advertise unresolved computed executables or unknown generated writers as protected by the final state here. Parameter-default or printed executable spellings can again evade the reverted checks. This is explicit regression containment, not a completed provenance repair or an automatic promise to restore the feature.
