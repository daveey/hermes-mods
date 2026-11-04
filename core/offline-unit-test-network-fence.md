# Unit tests: offline catalogs and a loopback-only name fence

Patches: **1775** (`a5010c0d23`, `fix(tests): stub incidental video catalog transport [daveey]`); **1776** (`b4bb0d87b0`, `fix(tests): isolate metadata clients at transport boundary [daveey]`); **1777** (`002f01e17e`, `fix(tests): default models.dev registry fetch offline in unit tests [daveey]`); **1778** (`a05a1226f5`, `fix(tests): default CLI model catalog manifests offline in unit tests [daveey]`); **1779** (`9f01157678`, `test: give SSRF-guard and PM-build tests offline resolvers and toolchains [daveey]`); **1780** (`e8cb93504b`, `test: refuse every non-loopback name lookup in unit tests [daveey]`); **1781** (`c34694e325`, `test: add unittest-discovery guard line to new fence/offline test modules [daveey]`); **1788** (`6f251c65a8`, `fix(tests): offline transport fixtures stand down when httpx/requests are absent`); **1796** (`e52e00c5d6`, `ci: start the bootstrap lane on every file its own steps run [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Video, model-metadata, models.dev and CLI catalog transports are stubbed offline by autouse fixtures (1775–1778).
- Every non-loopback name lookup in unit tests raises `socket.gaierror` naming the host (1780); SSRF-guard and PM-build tests use an opt-in `public_dns` fixture and pinned toolchains (1779); discovery guard lines added (1781).
- The fixtures stand down when httpx/requests are absent (1788), and the bootstrap CI lane starts on every file its own steps run (1796).

## How it works

`tests/conftest.py`, `tests/_fixtures/offline_catalogs.py`, `offline_cli_catalogs.py`, `fake_dns.py`; `scripts/ci/classify_changes.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1775: (subject only)
- 1776: (subject only)
- 1777: (subject only)
- 1778: Adds a manifest transport seam in hermes_cli.model_catalog and an autouse fixture that refuses non-loopback manifest/provider-catalog fetches unless a test stubs them. Pins the OpenRouter catalog in the non-Nous portal-feed test, which only passed because the live docs manifest had dropped the model.
- 1779: With the loopback-only fence, tests whose code under test resolves a URL's host itself (url_safety, browser CDP/eval guards, Slack file URLs, web extract) saw a refusal and failed closed. They now request the opt-in public_dns fixture: IP literals resolve numerically, localhost through the real resolver, other names to a fixed public address; nothing is dialled. PM build tests pin the host uv/interpreter instead of downloading python-build-standalone, and the subdirectory plugin update test publishes through admission_env. Real pypi.org canaries in the plugin sandbox suite are marked live,...
- 1780: The autouse fence listed two Nous domains to block, so any test that forgot to stub its transport read live third-party data (main went red when the OpenRouter catalog changed). Only loopback and unspecified binds resolve now; everything else raises socket.gaierror naming the host and saying to stub the HTTP layer or mark the test live. Nous keeps its anon_portal hint.
- 1781: (subject only)
- 1788: The bootstrap-installer lane runs test_fresh_source_install.py in a pytest-only interpreter; the autouse metadata fixture's 'import httpx' (b4bb0d87b09) errored setup for all three tests, turning main red at c34694e325. With no client library there is no in-process client to fence.
- 1796: The lane's pytest step runs tests/scripts/test_fresh_source_install.py in a bare interpreter (pytest, pytest-asyncio, ruamel.yaml, packaging), so a harness edit the full suite tolerates can break this lane alone. b4bb0d87b0 added an autouse fixture in tests/_fixtures/offline_catalogs.py that imports httpx; its PR skipped the lane because only scripts/install.sh, setup-hermes.sh and apps/bootstrap-installer/ started it, and main went red on the next push (run 41556904515). #727's fix skipped the lane the same way.

## Code

`agent/model_metadata.py`, `agent/model_metadata_http.py`, `hermes_cli/model_catalog.py`, `pyproject.toml`, `scripts/ci/classify_changes.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/_fixtures/fake_dns.py`
- `tests/_fixtures/offline_catalogs.py`
- `tests/_fixtures/offline_cli_catalogs.py`
- `tests/agent/test_model_metadata_http.py`
- `tests/agent/test_vision_routing.py`
- `tests/ci/test_classify_changes.py`: `test_bootstrap_lane_starts_on_every_file_its_own_steps_run`
- `tests/ci/test_plugin_validation_sandbox.py`: `test_a_dial_that_lands_on_this_host_is_refused`, `test_egress_policy_denies_internal_targets_and_other_ports`, `test_nat64_of_a_public_ipv4_stays_allowed`
- `tests/conftest.py`
- `tests/gateway/test_media_download_retry.py`
- `tests/hermes_cli/test_model_validation.py`
- `tests/hermes_cli/test_plugin_update_scan_override.py`: `test_subdirectory_update_asks_before_a_caution_scan_and_never_accepts_dangerous`
- `tests/plugins/web/test_web_search_provider_plugins.py`: `test_extract_passes_timeout_ms_to_scrape`
- `tests/pm/test_pip_check.py`
- `tests/test_external_network_fence.py`: `test_explicit_test_resolver_can_supply_a_fake_hostname`, `test_external_name_lookup_requires_a_transport_stub`, `test_live_marker_leaves_the_installed_resolver_untouched`, `test_loopback_http_server_is_reachable`
- `tests/test_offline_catalog_defaults.py`: `test_explicit_video_catalog_transport_preserves_cache_and_expiry`, `test_offline_catalog_default_preserves_real_loopback_http`, `test_video_catalog_default_exercises_bundled_fallback_without_dns`
- `tests/test_offline_cli_catalog_defaults.py`: `test_cli_catalog_default_does_not_resolve`, `test_cli_catalog_loopback_http_still_works`, `test_explicit_cli_catalog_transport_runs_parser`
- `tests/test_offline_fixtures_without_transports.py`: `test_autouse_fixtures_skip_absent_transport_libraries`, `test_probe_runs_under_every_autouse_fixture`
- `tests/test_offline_metadata_defaults.py`: `test_explicit_metadata_client_fake_still_observes_real_request`, `test_metadata_get_is_offline_before_dns`, `test_ollama_probe_uses_offline_transport`
- `tests/test_offline_registry_defaults.py`: `test_explicit_registry_response_runs_validation`, `test_registry_default_fails_before_dns`, `test_registry_loopback_transport_still_fetches`
- `tests/tools/test_browser_cdp_tool.py`: `test_frame_id_is_judged_by_its_own_target_not_the_first_page`, `test_non_page_target_fails_closed_while_any_page_is_private`, `test_page_url_probe_reads_the_target_over_the_same_endpoint`, `test_tab_management_stays_usable_while_a_tab_is_private`
- `tests/tools/test_browser_eval_ssrf.py`
- `tests/tools/test_browser_use_cli.py`
- `tests/tools/test_web_tools_config.py`: `test_extract_through_keyless_client_forwards_server_timeout`
- `tests/tools/test_web_tools_perplexity.py`: `test_extract_dispatch_snippets_per_url_and_missing_key`
- `tests/tools/test_web_tools_tavily.py`: `test_redirected_page_caches_under_requested_source_url`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- Tests that need the network must stub the HTTP layer or be marked live.

## Plugin?

No: these change core modules that plugins cannot hook.
