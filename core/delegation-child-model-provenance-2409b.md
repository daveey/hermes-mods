# Child model provenance recorded at reservation

Patches: **2405–2407**.

## Behavior

A delegated child records its model source at the moment its store is reserved, so lineage and provenance are visible even while queued. Profile-route children record `delegation_default`; the direct-build fallback checks the caller pin before the delegation default; compression continuation rows keep provenance. If the owner is older and accepts only `{'secret'}` it answers a bare `invalid_params`; the worker retries once without provenance, warns once without secrets, and propagates any other or second refusal. Restart replay behaves the same.

## How it works

`reserve_child_store(parent, *, model_provenance=None)` carries validated provenance (malformed or authority-bearing provenance is refused). `_strip_refused_child_provenance` durably removes provenance from the actual refused `child.reserve` receipt before retrying the same sequence and secret, shared by live writes and replay. Patch 2407 removes the earlier `reissue_pending_without` helper so a later caller cannot reissue another reservation's pending secret.

## Code

`agent/runtime_session_child.py`, `agent/runtime_session_store.py`, `agent/subagent_lifecycle.py`, `gateway/session_worker_child.py`, `tools/delegate_tool.py`, `tools/delegate_tool_child_run.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_child_model_provenance.py`, `tests/tools/test_delegate_model_source.py`, `test_delegate_class_routing.py`, `test_delegate_transcript_readiness.py`.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

The degrade path is version-skew specific: only an exact `{'secret'}` payload bare refusal is retried. Provenance is informational and carries no authority.

## Plugin

Core change; no plugin required.
