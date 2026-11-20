# Fairness evidence: roster validation and strict capture envelopes

Mirror entries 4983–4989. Offline evidence tooling; no scheduler change, no capture activation, no fairness claim.

## Behavior

- **4983** a read-only roster manifest validator plus an inventory `roster` CLI. Tree scans only describe respondents, so complete fleet coverage needs a manifest that keeps expected / opening / closing / coverage source identities separate, retains unknowns, and refuses duplicates, offline sources, gaps and changing membership. Identity is gateway + profile + board UID + database UID; serving process, source and config pins must agree with capture receipts. Membership continuity needs its own receipt; version 1 refuses changing membership. Read-only discovery validates INDETERMINATE because no authorized capture window or attestations exist.
- **4984** isolates CLI artifact assertions in the test. **4985** enforces the roster version as a strict integer, gives each guard its own specific problem code, rejects an empty roster as complete, and turns unreadable manifests into clean tool errors.
- **4986** strict Phase A envelopes and a segmented writer: validated creation/urgency/lifecycle payloads, bounded JSON-safe payloads, `make_record`/`decode_record`, and segment files with a manifest. **4987** pins explicit portable text encoding. **4988** fails closed on malformed capture evidence and byte limits (bounded reads, source-document validation, loss payloads, gap de-duplication). **4989** replaces oversized lifecycle rows with explicit oversize loss while atomically advancing the journal cursor, uses finite source-time validation for huge JSON integers, and distinguishes unknown trigger integrity from observed trigger loss.

## How it works

`kanban_fairness_roster.py` + `scripts/kanban_fairness.py` for roster; `kanban_fairness_payloads.py` (field/shape checks), `kanban_fairness_schema.py` (record envelope), `kanban_fairness_segments.py` (`write`, `read_bounded`, `load_records`, `dedupe_gaps`), `kanban_fairness_store.py`, `kanban_fairness_journal.py`, `kanban_fairness_metrics.py`.

## Code

The modules above plus tests under `tests/hermes_cli/test_kanban_fairness_*`.

## Tests

Source tests (not executed here): `test_kanban_fairness_roster`, `_envelope`, `_payloads`, `_segments`, `_journal`, `_metrics`, `_vertical`, `_resource_bounds`, `_store_hardening`.

## Applying

Use only the full segmented recipe in the core mods [README](README.md) ("Applying"); the entries build on each other and on `4996+` capture runtime in [the capture runtime doc](mirror-5011-g-fairness-capture-runtime.md).

## Gotchas

- Registry eligibility is not proof of actual serving.
- Arbitrary malformed source bytes are never persisted; unknown evidence stays unknown, not zero.
- 4986 is a source `wip` commit; later entries finish it.
