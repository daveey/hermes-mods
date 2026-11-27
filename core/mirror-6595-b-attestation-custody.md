# Reviewed import ordering and generated-code producer custody

Patch coverage: 6268, 6269, 6270, 6271, 6272, 6273, 6274, 6275, 6276.

## Behavior

Executable reviewed directory-plugin closure is registered before eager imports can load an enabled sibling through an alias. Adopted dependency environments are receipted before exposing paths or processing .pth files, but attestation is not installed into a process that booted without it.

Unknown generated execution may report its verified producer source without gaining generated-code trust. A narrowly source-pinned attrs producer is supported only with observed compile/exec and exact installer-root custody.

## How it works

Plugin registration keeps existing source, enablement, portability and review gates; manifests alone confer no review. Producer diagnostics require observed code-object identity, matching component/source receipt and bounded reporting.

The attrs descriptor binds package/version/source bytes and the executed module code graph. Compile/eval instruction sites and finite internal caller identities are checked; output must match observed script bytes, filename, frame and reconstructed bytecode. Each event consumes pending output evidence, preventing causal skew or helper-name exemptions. Installer RECORD is rechecked. Dependency indexing tracks root-qualified distribution ownership; overlapping claims for one source path are latched ambiguous and cannot supply identity or verified bytes. Same-version roots retain distinct used receipts instead of overwriting one another.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/code_attestation.py`
- `hermes_cli/plugins.py`
- `hermes_cli/plugins_loader.py`
- `pm/environments_adopt.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_bootstrap_registration_order.py`
- `tests/hermes_cli/test_code_attestation_loader_order.py`
- `tests/hermes_cli/test_code_attestation_producer_sources.py`
- `tests/hermes_cli/test_code_attestation_attrs.py`
- `tests/hermes_cli/test_code_attestation_attrs_custody.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

This is not a generic attrs/package/generated-filename exemption. Producer-source diagnosis leaves unknown output unknown. Dependency installer receipts are not a claim of audited source or coverage. A direct late attestation ledger cannot retroactively erase modules imported before bootstrap.

## Why core, not a plugin?

Import hooks, dependency environment exposure and process-wide audit-event custody must be established before plugins execute.
