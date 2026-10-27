# F3 cross-repository integration (opt-in)

This suite loads this repository's `ask-gate` and `user-steps` through the **real
core PluginManager**, using normal discovery of copies installed in a temporary
Hermes home. It runs `AIAgent.run_conversation`, including actual gate dispatch,
output transformation, source reconciliation, SQLite message persistence and
stream delivery. It does not import plugins directly or borrow core test fixtures.

## Run

Use a compatible Hermes core source checkout and its provisioned test Python.
The repositories may live anywhere; no sibling checkout or live installation is
inferred. Supply the core explicitly:

```sh
CORE=/absolute/path/to/hermes-agent
MODS=/absolute/path/to/hermes-mods
HERMES_PYTHON=/absolute/path/to/test-environment/bin/python \
  "$CORE/scripts/run_tests.sh" \
  "$MODS/tests/integration_f3/test_final_publication.py" -- \
  --hermes-core="$CORE" -q --tb=short
```

The fixture also accepts `HERMES_CORE_CHECKOUT` when invoking pytest in an already
provisioned environment. The canonical core runner sanitizes environment variables,
so prefer `--hermes-core` with that runner. An absent checkout is a pytest usage
error (exit 4), not a skip or fallback. Run this optional directory separately from
ordinary plugin unit tests; importing unrelated core modules first can contaminate
the interpreter. Core import provenance is asserted.

## Boundaries and assertions

- The main LLM response, judge result and extractor response are scripted. Only
  external card delivery is substituted. Hooks, `process_reply`, ledger locking,
  IDs, framing, `sync_card`, turn-loop logic and message persistence remain real.
- Environment credentials/config are removed before importing core. HOME,
  HERMES_HOME and runtime state are temporary; plugins are copied, never symlinked
  to a live profile. Prewarming, title generation and background review are off.
  No tools are enabled. A Python audit guard forbids socket network operations,
  and teardown asserts that none were attempted.
- New/open/resolved source-action controls run both with framing and plain text.
  Existing actions are first created through a real agent turn, then resolved
  through the real source API. Repeating an open action must reuse its ID without
  another card call; a new action must publish a card.
- A resolved exact `(kind, text, verbatim_span, ordered options)` must not allocate
  another step, change the card generation, publish any card operation, or survive
  in the final response, returned assistant message, latest persisted SQLite
  message, or emitted stream. Prior historical messages are not retroactively
  erased. Framing-only controls permit either plain or framed streamed text;
  suppression controls require only the safe replacement.
- A held first draft must never reach extraction, cards, stream or persisted
  history. Events require `judge(first), judge(accepted), extract(accepted)`.

This does not prove fuzzy/semantic identity, account/project retirement, trusted
reopen, external resolution/publication races, old-core compatibility or live
Desktop rendering.

## Baseline finding and corrective verification (not release acceptance)

Against core `6f3b176e29505e387fb4c3daef17f86bfc54a138` and mods
`fde339255bd228f17758303fdb8be1f0f5a4a6dd`, the suite returns **5 passed, 2 failed**.
Both failures are the resolved-action stream assertion (plain and framed):

```text
shown: Please choose the blue or green case for the device.
final/stored: This reply has been held because it repeats a retired request. No new action is requested.
```

The assertions preceding that failure establish that the real source transform
suppressed creation and card delivery, and sanitized returned and persisted
messages. However, `agent/turn_stop_gates.py:194-199` releases accepted text before
`agent/turn_final_response.py` calls `apply_llm_output_transform` (around line 352).
The original request had already reached the stream callback. A companion core
fix defers buffered final delivery through output transformation and preserves
context scrubbing and paragraph separation. With that fix, all **7 tests pass**
on Linux. The assertions remain ordinary regressions, not xfails. Native Windows
execution has not been verified. This result is not full F3 or deployment acceptance.

The missing-checkout collection check was also exercised: it fails clearly with
`integration_f3 requires --hermes-core=/path/to/core or HERMES_CORE_CHECKOUT; no live checkout fallback`.
