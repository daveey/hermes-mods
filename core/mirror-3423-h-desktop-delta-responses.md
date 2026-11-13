# Delta responses for large reads over the Desktop's SSH tunnel

Patches: **3401**.

## Behavior
- A Desktop client may ask for zstd delta encoding on large JSON GET responses from a remote backend over its tunnel; only the wire encoding changes, and the client ends with the exact bytes the handler produced. Unasked requests are untouched.
- The first ask gets the whole body plus a body hash; a repeat ask naming a still-remembered base gets a small zstd frame using the base as its prefix (a one-field change in a multi-MB body stays tiny). Unknown or evicted bases get the whole body; a delta no smaller than the body is not sent.
- Eligible: 200 `application/json` bodies of 16 KB–8 MB with a Content-Length and no Content-Encoding, on GET. Writes and HEAD are never encoded; without zstd support every request passes through.
- Bases are namespaced by credential (a base is usable only by the credential it was served to), and the store is bounded by bytes (LRU). Encoding failure sends the handler's answer unchanged.
- The client verifies the hash of every reconstructed body, retries a failed delta without a base, and never returns an unverified body; copies are kept per scope and path, so a tunnel rebuild keeps them and a torn-down tunnel stops being asked.

## How it works
- 3401 adds a pure-ASGI middleware, `hermes_cli/web_delta_response.py`, inside the dashboard's auth gates (request header `X-Hermes-Delta-Accept: zstd`, `X-Hermes-Delta-Base`; response `X-Hermes-Body-Hash`), with a 64 MB LRU store. On the Desktop side `delta-reads.ts` holds the per-scope base store and decoder, `api-transport.ts`/`json-fetch.ts` ask only on GETs to a bound remote backend (transcript page reads keep their own slim+gzip ask), and `slow-link.ts` binds and unbinds tunnels to scopes.

## Code
- `hermes_cli/web_delta_response.py`
- `apps/desktop/electron/delta-reads.ts`
- `apps/desktop/electron/api-transport.ts`
- `apps/desktop/electron/json-fetch.ts`
- `apps/desktop/electron/slow-link.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_web_delta_response.py`: `test_unasked_request_is_untouched`, `test_first_ask_gets_the_whole_body_with_its_hash`, `test_known_base_gets_a_small_delta_that_decodes_to_the_exact_current_body`, `test_unchanged_body_is_a_tiny_delta`, `test_unknown_base_gets_the_whole_body`, `test_base_evicted_from_the_store_gets_the_whole_body`, `test_ineligible_responses_pass_through_untouched`, `test_a_write_is_never_encoded`, `test_a_delta_no_smaller_than_the_body_is_not_sent`, `test_without_zstd_every_request_passes_through`, `test_store_is_bounded_by_bytes_and_drops_the_least_recently_used`, `test_frame_matches_the_fixture_the_desktop_test_decodes`, `test_the_dashboard_app_serves_through_the_middleware`, `test_chunked_body_with_a_content_length_is_encoded_as_one`, `test_other_headers_survive_a_delta_duplicates_included`, `test_head_is_untouched`, `test_a_base_is_only_usable_by_the_credential_it_was_served_to`, `test_credential_namespace_separates_credentials_and_is_empty_without_one`, `test_encoding_failure_sends_the_handler_answer_unchanged`, `test_a_one_field_change_stays_tiny_past_the_default_zstd_window`, `test_size_bounds_of_what_is_encoded`, `test_the_middleware_sits_inside_every_auth_gate_of_the_dashboard_app`.
- `apps/desktop/electron/delta-reads.test.ts`: includes `whole body, then a delta against it, then a delta against that: each returns the exact current body`, `decodes a frame made by the gateway side (Python compression.zstd, prefix dictionary)`, `never returns a body that fails verification, even when the frame decodes`, `a failed delta is retried by the transport without a base, and the caller gets the whole body`, `is bounded by entries and by bytes, dropping the least recently used`, `survives a tunnel rebuild: the copy is per scope and path, not per local port`, `passes an ordinary response through untouched: no headers, old backend`, and further cases for tunnel binding and request-asking rules.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Needs a Python with zstd support on the gateway; otherwise requests silently pass through unencoded.
- The patch's measurements (large polled reads shrinking from megabytes to kilobytes) come from the source commit message, not from this mirror pass.
