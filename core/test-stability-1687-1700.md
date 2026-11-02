# Test stability: offline image catalog, receipt clock, reaper on a live host

Patches: 1687 1688 1689 1700

- `patches/1687-test-image_gen-catalog-ownership-test-no-longer-depe.patch`
- `patches/1688-test-image-gen-isolate-offline-catalog-contract-from.patch`
- `patches/1689-test-kanban-control-receipt-age-before-publication-d.patch`
- `patches/1700-fix-tests-session-daemon-reaper-matches-HERMES_HOME-.patch`

All four change tests or the suite conftest only; no production behavior changes.

## Behavior

- **1687 (image-gen catalog ownership):** `test_catalog_models_are_offered_only_by_openrouter` failed on every branch
  after OpenRouter dropped a model from its live `GET /images/models` list. The test now stubs `requests.get` to raise
  `OSError("offline")`, so it checks the curated offline tables (`_IMAGE_API_MODELS`), not the vendor catalog.
- **1688 (image-gen live catalog isolation):** an autouse fixture in the same file makes any unmocked `requests.get`
  fail the test, so no image-gen unit test reads live inventory. `test_picker_merges_image_api_and_chat_catalogs` now
  asserts the exact merged OpenRouter list from its fake responses, that the Nous provider makes one call (to
  `https://openrouter.ai/api/v1/models`) and offers exactly `openai/gpt-5.4-image-2` and `google/gemini-3-pro-image`, and that a successful
  live response does not bring back snapshot-only models.
- **1689 (Kanban dispatcher health receipt):** `test_scoped_expiring_receipt_and_invalidation` replaces the health
  module's `time` with a fake monotonic clock. It checks that a receipt is still valid exactly at its TTL, expires
  0.001 s later, is valid again after republication, and is gone after `invalidate()`. Previously, time spent
  descheduled on CI could age the receipt before the first assertion.
- **1700 (session daemon reaper):** on a Linux host that runs live gateways with
  `HERMES_HOME=<real hermes home>/profiles/<p>`, `test_basetemp_isolation` and `test_session_daemon_reaper` failed with
  "TEST BUG: file I/O against the REAL hermes home". The session-teardown reaper's `/proc` scan called
  `Path(HERMES_HOME).resolve()` for every process, and `realpath` `lstat`s each component of the real home while the
  real-home I/O guard is active. The scan now resolves only the session's own roots and compares each process's
  `HERMES_HOME` lexically. The real-home guard is unchanged.

## How it works

- **1687/1688:** `patch("requests.get", side_effect=OSError("offline"))` around the two `list_models()` calls; a
  module-level `_no_unmocked_catalog_requests` autouse fixture monkeypatches `requests.get` to `pytest.fail(...)`.
  Tests that need HTTP patch it explicitly, which overrides the fixture.
- **1689:** `monkeypatch.setattr(health, "time", SimpleNamespace(monotonic=lambda: clock[0]))` replaces only the health
  module's clock; pytest and other modules keep real time.
- **1700:** `tests/conftest.py::_processes_with_hermes_home_under(roots)` builds the root set from both
  `os.path.abspath(r)` and `Path(r).resolve()` for this session's roots, so a child given either spelling is still
  found. For each `/proc/<pid>/environ` `HERMES_HOME`, it skips relative values (relative to that process's cwd), takes
  `os.path.normpath` of absolute ones, and checks `path == root or path.is_relative_to(root)`. No filesystem call is
  made on another process's home.

## Code

`tests/plugins/image_gen/test_openrouter_compat_provider.py` (1687, 1688), `tests/gateway/test_kanban_dispatcher_health.py`
(1689), `tests/conftest.py` and `tests/test_session_daemon_reaper.py` (1700).

## Tests

- 1687: `tests/plugins/image_gen/test_openrouter_compat_provider.py::TestImageApiSurface::test_catalog_models_are_offered_only_by_openrouter`.
- 1688: `TestLiveCatalog::test_picker_merges_image_api_and_chat_catalogs` plus the autouse fixture that applies to every
  test in that file.
- 1689: `tests/gateway/test_kanban_dispatcher_health.py::test_scoped_expiring_receipt_and_invalidation`.
- 1700: `tests/test_session_daemon_reaper.py::test_session_teardown_skips_a_live_gateway_on_the_real_hermes_home_without_touching_it`
  (Linux; `live_system_guard_bypass`): spawns a detached stand-in process whose `HERMES_HOME` is
  `<first real-root candidate>/profiles/live-gateway`, runs `_reap_session_hermes_processes`, and asserts the
  stand-in is neither returned nor signalled and the guard is not tripped. The commit message reports it RED on the
  live host before the fix and GREEN after. The existing
  `test_session_teardown_terminates_daemons_under_the_session_basetemp_only` still covers reaping a daemon under the
  session root.

The mirror pass did not rerun these tests.

Untested: 1700's skip of relative `HERMES_HOME` values and the matching of a child given the unresolved spelling of a
symlinked root (both are in the code, neither has a dedicated case); non-Linux hosts, where the scan returns an empty
list as before.

## Applying

Apply in series order. 1688 edits the same test file as 1687 and expects it. 1700 expects the session-daemon reaper and
`_REAL_HERMES_ROOT_CANDIDATES` real-home guard already in `tests/conftest.py`.

## Gotchas

- New image-gen tests in that file must mock `requests.get` explicitly or they fail by design.
- The reaper still only finds processes whose absolute `HERMES_HOME` is lexically under a session root; a process given
  a path through a different symlink than either recorded spelling is not found.

## Plugin?

No. These are core test-suite changes.
