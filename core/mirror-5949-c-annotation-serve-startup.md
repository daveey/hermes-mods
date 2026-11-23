# Opt-in native annotation startup for serve

Patches: **5944–5946**. Wires the native annotation fact runtime into the serve lifespan, then narrows it: opt-in default, optional review metadata that cannot gate startup, and exact remote-wait marker parity.

## Behavior

Serve can own the native annotation runtime for its lifetime. When enabled on Linux, startup prepares the runtime, cuts over from the legacy annotation holder, and closes the runtime on shutdown (the legacy holder resumes). While the runtime is owned, the standalone prewarm and legacy holder starters are skipped, so annotation work is not started twice.

After 5945 the switch is **opt-in**: `HERMES_FACTS_ANNOTATION` unset behaves as off, and only a value outside `0/off/false/no` enables it. 5944 introduced it default-on; 5945 reverses that "until asynchronous activation can guarantee non-blocking serve". Net result of the range: default serve keeps the legacy starters exactly once.

Only profiles with seeded consumers gate cutover. Config-only profiles without a store, and sessions without PR information, no longer prevent startup. Review (PR) metadata is optional: when its fact is cold or unavailable, the review entry returns the rows unchanged, keeps the fact health `unknown`, demands background recovery, never certifies an empty result and never runs legacy I/O. Once the provider completes, the fact becomes fresh and later requests show the review wait.

A failed native startup is not a silent legacy fallback: the exception propagates and no custody is left installed. A failed close retains custody and a second start is refused.

Remote clock/CI/deploy waits produce the same replica markers in the native producer as in the legacy path, so the sidebar external count and unknown count match.

## How it works

`fact_annotation_lifespan.lifespan(app)` is an async context manager composed in `web_server_app.app_lifespan` right after the links lifespan and before sessions. It raises if `app.state.annotation_facts` is already held, reads the switch, requires `sys.platform` to start with `linux`, and runs `fact_annotation_boot.prepare` in a worker thread. `prepare(on_created=...)` hands the runtime to a callback before `runtime.start(...)`, so a failed or timed-out start still leaves the caller able to close it. The close runs in a thread in `finally`; custody is cleared only after close succeeds.

`standalone_lifespan` checks `app.state.annotation_facts` before starting prewarm and holder.

`AnnotationRuntime.start` now builds readiness keys only from seeded rows and skips the `review` family; `AnnotationFacts` returns rows when the review family is unavailable instead of raising. `fact_annotation_kanban._remote` merges `replica_markers(card)` for parked cards rather than a single `external_unknown` marker.

## Code

- `hermes_cli/fact_annotation_lifespan.py` (new), `fact_annotation_boot.py` (`on_created`), `web_server_app.py` (composition, starter guards).
- `hermes_cli/fact_annotation_runtime.py`, `kanban_annotation_facts.py`: seeded-only gating, optional review.
- `hermes_cli/fact_annotation_kanban.py`: replica marker parity.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_annotation_prepare_lifespan.py`: real prepare/lifespan with canonical request and legacy drain/resume order; real serve composition (healthy, unused profile, no PR info) answering a sessions request.
- `tests/hermes_cli/test_annotation_serve_startup.py`: explicit-on serve has no legacy starter; off starts legacy once; failed startup is not fallback; cleanup failure retains custody; repeated lifespan and non-Linux gating (platform is monkeypatched, not a native non-Linux run).
- `tests/hermes_cli/test_annotation_activation_review.py`: optional absence cases, review becoming fresh after provider completion, clock/CI/deploy marker parity, default startup never admits native.
- `tests/hermes_cli/test_annotation_native_runtime.py`, `test_annotation_native_sources.py`: unready runtime with a seeded consumer; review entry on a cold source; transition restored in `finally`.
- `tests/hermes_cli/test_serve_unloaded_sweeper.py`, `test_sessions_native_startup.py`: quiet-boot fixtures set the switch off.

Untested entry paths: a real non-Linux start, a real provider outage, a live legacy-to-native cutover on an existing home, and any run with the switch on in a production-sized profile set.

## Applying

Use the complete segmented replay recipe in `core/README.md` (Applying); do not apply these patches standalone with `git am`. Apply 5944, 5945 and 5946 together; 5944 alone leaves the default-on state that 5945 removes. Enable only deliberately with `HERMES_FACTS_ANNOTATION=1`. Live deployment is unproved.

## Gotchas

Opt-in means the new path is dormant by default. The Linux-only gate silently yields a no-op elsewhere. Unknown review health is intentionally not "no review needed". A retained runtime after a failed close blocks restarts of the lifespan in that process.

## Why not a plugin

The lifespan composition, runtime readiness gating and replica markers live inside core serve startup and canonical fact producers. A plugin cannot own app lifespan ordering or intercept the legacy holder starters it must suppress.
