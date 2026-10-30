# F3 cross-repository integration

This suite loads this repository's `ask-gate` and `user-steps` through the **real
core PluginManager**, using normal discovery of copies installed in a temporary
Hermes home. It runs `AIAgent.run_conversation`, including actual gate dispatch,
output transformation, canonical request reconciliation, SQLite message persistence
and stream delivery. It does not import plugins directly or borrow core test fixtures.

## Run

```sh
scripts/test-all -k integration_f3 --hermes-core /absolute/path/to/hermes-agent
```

The core checkout needs its provisioned test Python. The repo runner also accepts
`HERMES_CORE_CHECKOUT`, then looks for `~/.hermes/hermes-agent`. F3 runs whenever a
core is found; it is skipped with a reason only when none is available. Each pytest
process receives the selected core explicitly. Direct pytest invocation requires
`--hermes-core` or `HERMES_CORE_CHECKOUT`; there is no implicit fixture fallback:

```sh
PYTHONPATH="$CORE" HERMES_DISABLE_LAZY_INSTALLS=1 "$TEST_PYTHON" -m pytest \
  --confcutdir=tests/integration_f3 --rootdir=tests/integration_f3 \
  tests/integration_f3 --hermes-core="$CORE" -q
```

Run this directory separately from ordinary plugin/core unit tests. `--confcutdir`
prevents unrelated parent conftests from installing core-only fixtures and I/O
guards. Core import provenance is asserted. An absent checkout in direct pytest
is a usage error (exit 4), not a skip or fallback.

## Boundaries and assertions

- The main LLM response, judge result and extractor response are scripted. External
  decision delivery/readback is substituted. Hooks, `process_reply`, canonical
  ActionStore creation/decision/dispatch, ledger locking, IDs, framing, turn-loop
  logic and message persistence remain real.
- Environment credentials/config are removed before importing core. HOME,
  HERMES_HOME and runtime state are temporary; plugins are copied, never symlinked
  to a live profile. Prewarming, title generation and background review are off.
  `HERMES_DISABLE_LAZY_INSTALLS=1` remains set even after environment isolation,
  preventing bootstrap from provisioning dependencies for the temporary home.
  No tools are enabled. A Python audit guard forbids socket network operations,
  and teardown asserts that none were attempted.
- New/open/resolved source-action controls run with both framing and plain text.
  Existing actions are first created through a real agent turn, then resolved
  through `ActionStore.decide` and `dispatch` with a confirmed synthetic receipt.
  Repeating an open action must reuse its ID and canonical request unchanged;
  a new action must persist exactly one open canonical request. No legacy rolling
  Kanban card may be created.
- A resolved exact `(kind, text, verbatim_span, ordered options)` must not allocate
  another step, change card generation or the canonical request, publish a card,
  or survive in the final response, current-turn returned/persisted messages or
  emitted stream. Canonical retirement may update the legacy step's status and
  retirement timestamp; this is reconciliation, not creation. Prior historical
  messages are not retroactively erased.
- A held first draft may undergo **read-only approval-readiness extraction** in
  the publication gate. Only the accepted draft may publish a source action.
  The scripted extractor returns a real candidate for both drafts, so accidental
  publication of the rejected draft is observable in the ledger and ActionStore.
  Events require `judge(first), preview(first), judge(accepted), preview(accepted),
  extract(accepted)`; the rejected text must not reach stream or persisted history.
- Standalone user-steps discovery (ask-gate disabled) repeats a resolved request
  with plain/framed replies, with and without a verification continuation. Its
  buffering declaration must prevent raw stale stream/interim emission; returned
  and SQLite messages agree with the held reply and no new requests/cards appear.
- Both verification continuation seams are exercised, including budget exhaustion.
  Open-step checks use the public canonical-aware reader: a legacy ledger mirror
  may remain physically open until the next source reconciliation.
- Compatibility tests simulate missing runtime capabilities and registration
  failure through normal discovery; neither plugin may load a partial unsafe path.

This does not prove fuzzy/semantic identity, account/project retirement, trusted
reopen, external resolution/publication races, real decision transport, or live
Desktop rendering. Native Windows execution is unverified.

## Failure classification and prevention

The original stream regression (core `6f3b176e29505e387fb4c3daef17f86bfc54a138`,
mods `fde339255bd228f17758303fdb8be1f0f5a4a6dd`) published a retired request before
output transformation. The core's post-transform buffered-publication fix remains
covered by ordinary assertions, not xfails.

At hub core `a308b75c5fc`, mods `c855940`, the repo runner reproduced **15 failed,
6 passed**. These were stale integration assumptions, not a reproduced regression
in buffered publication:

| Cases | Reason and correction |
|---|---|
| Four new/open plain/framed cases | Expected one extraction and a rolling card. The approval-readiness gate now previews extraction, and canonical requests replaced rolling cards. Assert the preview/transform sequence, real persisted canonical request and ID reuse. |
| Two resolved plain/framed cases | Legacy `resolve` deliberately ignores steps backed by a `requestId`. Retire through canonical decision + confirmed dispatch; retain stream/history/creation suppression assertions. |
| Held-draft case | Expected no extraction for a held draft and a rolling card for the accepted draft. Allow read-only readiness preview but assert only the accepted candidate reaches persisted source state. |
| Four standalone retirement cases | Same obsolete legacy-resolution setup. Use canonical retirement and assert canonical state stays unchanged. |
| Four verification/budget cases | Same obsolete resolution setup, plus legacy raw-ledger reads. Use canonical retirement/public reader and retain exact final/stream/history assertions. |

Two harness issues explain environment-dependent errors: clearing the bootstrap
opt-out allowed dependency network attempts (which the audit guard caught), and
inheriting core parent conftests could fail all 21 setups in its home-I/O guard
on SQLite `:memory:`. Preserve the opt-out and bound conftest discovery; do not
remove the network guard or weaken core's guards.

Why it escaped detection: F3 required explicit opt-in, so ordinary repo gates did
not exercise it while the request model changed. The repo runner now includes F3
when it discovers a core. Runner regressions cover implicit discovery, explicit
CLI/environment selection, conftest isolation and the genuine missing-core skip.
