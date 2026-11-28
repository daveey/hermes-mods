# Code-attestation inventory cost, diagnostics and quiet-start review records

Patch coverage: 6605, 6633, 6669, 6674, 6675, 6676, 6801, 6802, 6803, 6804, 6805, 6806, 6807, 6829, 6830, 6831, 6832, 6833, 6834, 6945, 7014.

## Behavior

The executed-code attestation (strict module inventory used by the "quiet" preflight and serve startup) was too slow for fixed client deadlines. This series measures where the time goes and removes avoidable cost without relaxing any trust decision.

- **Attribution.** The quiet-check control reports payload-free wall and thread-CPU timings per phase (coverage, registry + strict inventory, update, window, route, extension report, predicates), joined by process/thread/request start. No new endpoint, parameter, deadline or cache. Requested inventory phase timing is split further, and a diagnostic-only observer adds bounded per-flight scalar inventory counters.
- **Cheaper ownership test.** Module-to-component-root classification compares already-normalized POSIX path parts instead of building and searching pathlib ancestors per root (the Windows pathlib branch remains).
- **Deferred comparison at serve.** `hermes serve` queues the comparison of imported modules against approved source until after startup; every ledger read settles the queue first, a bounded reader that cannot settle in time raises, and component-root registration settles pending modules first. Expected compilations are cached only after observed reuse (one-shot imports no longer pollute the bounded LRU). A deferred generator origin is bound to the execution present at the call.
- **Diagnostics without callbacks.** Diagnostic inventory scans module namespaces and subclasses without invoking callbacks, refuses opaque subclass file absence, inspects metadata across the full MRO, emits its logging outside the ledger lock (a logging/ledger lock inversion), bounds optional observer setup, and is loaded before audit activation and blob-checked at bootstrap.
- **Quiet-start review records.** Plugins with independent exact-source approval but no boot-pinned record are enrolled in `hermes_cli/data/plugin-review-records.json`; a later commit enrolls two further reviewed plugins whose old bytes were refused. Missing records still fail closed.

## How it works

`gateway/quiet_check_diagnostics.py` (`CheckDiagnostics`, `phase`) and `quiet_control.py` expose the timings; `hermes_cli/code_inventory_diagnostics.py` holds `InventoryDiagnostics`/`InventoryObservation`; `hermes_cli/code_provenance_progress.py`, `git_fingerprint.py` (capsule boot identity) and `version_info.py` (`prewarm_version_info`) cut serve-boot waits. The strict inventory's trust decisions are intended to be unchanged (owner statement), but its code is not: `code_attestation.py` changes component-root owner classification (6669, 6804), generator-origin binding and the deferred settle queue (6674, 6676), the requested-inventory timing wrapper (6829), observer preload ordering (6830, 6831) and lock-order handling (6833), and `code_attestation_inventory.py` changes subclass/MRO metadata inspection (6801, 6805-6807). Equivalence of verdicts rests on the named suites, not on the diff being diagnostic-only. Deferral at serve is a behavior change in timing: the comparison of imported modules against approved source happens after startup, with every ledger read settling the queue first.

## Code

`gateway/quiet_check_diagnostics.py`, `gateway/quiet_control.py`, `gateway/code_provenance_control.py`, `hermes_bootstrap.py`, `hermes_cli/code_inventory_diagnostics.py`, `code_provenance_progress.py`, `git_fingerprint.py`, `version_info.py`, `data/plugin-review-records.json`, `website/docs/developer-guide/plugins/quiet-start-review-records.md`, `code_attestation*.py` (inventory/snapshot paths).

## Tests

Read from source; **not executed here**. Suites cover whole-inventory unchanged/no-progress, changed identity/filename, saturation, file refusal, immediate post-import expiry before any lock attempt, subclass/MRO inventory, observer bootstrap closure, and the settle-hook registration fake ledger. Measured timings quoted in commit messages are owner observations from specific hosts and are **not** claims about other hosts. No live quiet preflight was run here.

## Applying

Apply in ordinal order; 6829-6834 are corrections of one another and 7014 touches the same record file as 6945.

## Gotchas

- Review-record enrollment is a trust change: bytes must match exactly; edited plugins need re-enrollment.
- Diagnostic code must never run callbacks or take the ledger lock while emitting logs.

## Why core, not a plugin?

Attestation gates plugin loading itself.
