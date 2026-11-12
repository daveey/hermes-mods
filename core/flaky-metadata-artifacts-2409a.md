# Metadata-only flaky-test Actions artifacts

Mirror entries: [2277](patches/2277-ci-ingest-metadata-only-flaky-Actions-artifacts-without-checks-permission-1111.patch), [2298](patches/2298-daveey-Classify-cancelled-non-test-workflows-before-flaky-availability-1124.patch).

## Behavior

- Export canonical pass-on-retry test identities and bounded run provenance, not raw failure output, parameter values or logs.
- Ingest Actions artifacts without check-run permission; validate an entire run attempt before creating deflake cards.
- Distinguish complete-empty evidence, incomplete/unavailable evidence and workflows that are not applicable. Cancelled non-test workflows are not applicable, but cancelled test attempts remain unavailable.

## How it works

Each Python slice exports and uploads one flaky-metadata.json member with a versioned name bound to run/attempt/slice. Missing or malformed runner reports produce complete=false. The strict schema removes parameter suffixes, limits identities and rejects unknown fields. The consumer pages jobs/artifacts, requires a complete unique matrix for the same attempt and validates ZIP/JSON before filing. Downloads use the canonical GitHub API path; authorization is removed on allowlisted signed HTTPS redirects, archives stay in memory and size/member/compression limits apply. A run-wide 200-entry bound precedes card side effects. Existing run/attempt dedupe and card delivery ownership remain. 2298 classifies test relevance before cancellation availability, so unrelated cancelled workflows do not pollute flaky availability.

## Code

- `.github/workflows/tests.yml`
- `docs/plans/flaky-artifact-metadata-design.md`
- `scripts/ci/flaky_artifact.py`
- `scripts/ci/flaky_artifact_poll.py`
- `scripts/ci/flaky_kanban.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/ci/flaky_artifact_fakes.py`
- `tests/ci/test_flaky_artifact.py` — `test_export_keeps_only_canonical_identity_and_provenance`, `test_unrepresentable_identity_makes_the_report_incomplete_not_truncated`, `test_missing_or_malformed_source_report_is_incomplete_never_empty_success`, `test_oversize_source_report_is_incomplete` (additional cases in this file).
- `tests/ci/test_flaky_artifact_poll.py` — `test_artifacts_become_cards_per_job_with_safe_context`, `test_a_processed_run_attempt_is_not_read_again`, `test_complete_empty_report_is_processed_as_no_flakes`, `test_no_python_test_jobs_is_not_applicable_and_distinct_from_empty` (additional cases in this file).
- `tests/ci/test_flaky_kanban.py` — `test_annotations_still_render_locally_with_escaping`, `test_artifacts_round_trip_through_the_github_poll`.
- `tests/ci/test_flaky_kanban_overlap.py`
- `tests/ci/test_flaky_protocol_boundaries.py` — `test_api_get_retries_http_read_failures`, `test_exhausted_http_reads_have_fixed_receipts_and_persist_retryable_state`, `test_download_http_read_failure_is_fixed_and_not_retried`, `test_pages_maps_protocol_failure_without_raw_content` (additional cases in this file).
- `tests/ci/test_stable_release_commit_threading.py`

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Missing/expired artifacts are not proof of zero flakes. Partial reruns cannot borrow earlier-attempt artifacts. Upload failure is advisory to CI but keeps polling unavailable and retryable. No raw logs fallback or token-scope expansion is introduced. Local report/annotation behavior is retained. This pass did not download live artifacts or observe a cron execution.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
