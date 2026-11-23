# Loader-owned Session Tasks provenance and hermetic integration input

Mirror ordinals: **5939, 5940**.

## Behavior

The Desktop SDK adds `host.diagnostics.readSessionTasksLoadedIdentity()`, a synchronous, by-value observation associating a currently bound Session Tasks surface with loader-owned source/dependency evidence. A qualifying observation reports `MATCH`; unavailable evidence, changed surface, pending registration, unload or navigation reports `NOT_READY` with a reason.

This is provenance association, **not** same-renderer security confinement, mutable closure-state attestation, backend/controller authorization or installed-app proof. A plugin's own global receipt is not accepted as authority.

Ordinary plugins still load if cryptographic identity is unavailable. Session Tasks can remain functional without qualifying as `MATCH`. The integration suites gain a complete pinned, test-only companion text file, so their default collection no longer requires a neighboring checkout or environment variable. An explicit companion input still selects exactly that file and fails rather than falling back on an invalid path.

## How it works

Ordinal 5939 adds private activation state, source identities and SDK shim evaluation capture. The loader hashes the original UTF-8 source argument and the rewritten source passed to its Blob. Cached shims retain the references and evidence captured at evaluation, rather than reconstructing them from later globals. Served evidence hashes the fetched renderer manifest and its static JavaScript dependency closure; dynamic/lazy behavior is outside this proof.

The companion may call `ctx.bindSessionTasksDiagnostic?.(surface)` once during synchronous registration. Settlement, dependency evidence and exact current own descriptors decide qualification. Core compares the same surface object, own string/symbol keys and per-name descriptor flags/value/get/set references without invoking getters. Returned records omit captured object references and source strings and cannot mutate private custody.

Plugin ID/file claims are reserved before identity awaits. A newer same-file request supersedes its predecessor, another file cannot steal the pending ID, and stale completion/finally cleanup cannot release a replacement's reservation. Disk reads/imports have per-entry request tokens, distinguish cancellation from failure and do not mark cancelled bytes loaded. Reload invalidates old evidence before asynchronous replacement work; unload invalidates before disposers. Navigation retires an activation even if history later returns to the prior URL.

Unavailable/rejected WebCrypto preserves loading but yields empty identity digests and `DIAGNOSTIC_IDENTITY_UNAVAILABLE`, never fabricated proof. Unsupported dependencies or asset-read failure also prevent qualification.

Ordinal 5940 shares `readK01Companion` across the integration suites. With no explicit input, it checks the retained full companion's byte length and SHA-256 before returning its text. With `HERMES_K01_COMPANION` set, it reads the supplied path without substituting the pin. The bundled `.txt` is an offline input, not an installed plugin. Its origin and license are retained beside it.

## Code

Under `apps/desktop/`:

- `src/contrib/runtime-loader.ts`, `src/contrib/plugin.ts`: activation binding, ID reservation, cancellation and disk request custody.
- `src/contrib/session-tasks-identity.ts`: private activation registry and by-value diagnostic.
- `src/sdk/runtime.ts`, `src/sdk/loaded-assets.ts`, `src/sdk/index.ts`: evaluation captures, served-byte identities and supported SDK access.
- `src/sdk/profile-activation.ts`: unchanged profile-activation helper extracted from the SDK facade; not a new routing behavior.
- `src/contrib/k01-companion.test-helpers.ts`, `src/contrib/fixtures/k01/`: explicit/default test-input resolver, pinned complete text and attribution.
- `scripts/k01-built-replay.mjs`, `scripts/k01-mutants.py`: owned offline production-bundle replay and isolated mutation harness.

`docs/desktop/session-tasks-loaded-identity.md` in the source tree details the fields and later-consumer contract.

## Tests

**Source coverage only; tests, replay and mutation harness were not executed in this mirror.** Paths below are relative to `apps/desktop/` in the Hermes source tree.

| Test or harness | Coverage supplied by source |
|---|---|
| `src/contrib/session-tasks-identity.test.ts` | Whole-companion production loader/SDK registration; descriptor and method-reference substitution; by-value responses; late/duplicate/reentrant/invalid binding; pending/rejected async registration; unload/disable/navigation; source changes; closed dependency set; generation and cleanup ownership |
| `src/contrib/session-tasks-capture.test.ts` | SDK evaluation rather than replacement globals; cached captures; missing/forged capture; temporary-hook reference counting and restoration; asset-read failure; disk refresh invalidation before read completion |
| `src/contrib/k01-review-probes.test.ts` | Pending ID/file races, unload/disable cancellation, same-file supersession, missing/rejected crypto compatibility and companion rollback resource cleanup |
| `src/contrib/k01-disk-lifecycle.test.ts` | Actual discovery/watch/refresh with fixture filesystem transport; superseding reads/imports/hashes, stale errors, entry retirement and retry of cancelled bytes |
| `src/sdk/loaded-assets.test.ts` | Actual local HTTP reads; exact raw bytes including BOM; manifest/static closure; missing/redirected assets; abort forwarding; unsafe paths; cycles and graph limit |
| `src/contrib/k01-companion.test.ts` | Offline default identity, exact explicit input, invalid path without fallback, corrupt default length/digest refusal |
| `scripts/k01-built-replay.mjs` | Production Vite loader/SDK build and whole-companion import in owned jsdom with file/blob transport; independently checked emitted asset bytes; surface change and unload invalidation |
| `scripts/k01-mutants.py` | Copies inputs to an isolated fixture and records tests/mutations; failed executed assertions can kill a mutant, whereas collection/setup/report/timeout failure is invalid evidence |

Full installed Desktop/Electron behavior, native UI, live watcher delivery and any downstream controller/mutation runner are **untested here**. The offline replay is not a native acceptance test. Live deployment and the installed companion's identity are unproved.

## Applying

Follow the **whole segmented [README Applying](README.md#applying) recipe** with its bridges and repair/verification steps. The mirror is append-only and these mails are not independent installations. Replaying core does not separately publish or install a companion plugin.

After reconstructing the source tree, its pinned dependency toolchain is required for tests. Ordinary integration uses the verified default fixture; `HERMES_K01_COMPANION=/absolute/path/to/plugin.js` opts into a separately supplied input. Keep outputs and mutations in owned disposable directories, not a live install.

## Gotchas

- `MATCH` is an observation to compare against an independently reviewed immutable source/build inventory, not a blanket permission. A later consumer must reread identity and re-admit the current controller immediately before each operation; that consumer is not implemented by these ordinals.
- Served bytes are observed at shim evaluation, not certified JavaScript-engine compiled bytes. A mutable URL alone is not an identity.
- The static asset graph has bounded traversal and aggregate I/O deadline. Lazy chunks, CSS semantics and mutable captured objects remain out of scope.
- A missing disk path is represented as `null`, not guessed or presented as a realpath proof.
- The pinned test companion qualifies only that source pair, not the latest plugin or an installed application. Preserve its exact bytes and attribution when refreshing it.
- The binding hook is optional for older hosts. Compatibility without diagnostic readiness is intentional.

## Why not a plugin

The companion remains a plugin, but cannot be the authority for its own source, imported dependencies, activation ownership or returned provenance. Core owns the loader and SDK shim evaluation boundaries needed to make the association. A plugin-only global receipt would merely repeat the claim under examination.
