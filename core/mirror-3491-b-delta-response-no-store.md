# Delta responses: Cache-Control no-store

Patches: **3484**.

## Behavior
- When the web server actually sends a delta (a zstd frame against a base the client holds), the response carries exactly one `cache-control: no-store`, replacing whatever the handler set (including multiple Cache-Control headers or none).
- A whole hashed body (no frame) keeps the handler's Cache-Control, and answers to clients that did not ask for a delta keep the handler's headers.
- The earlier `Vary` merge and ETag drop are unchanged.

## How it works
3484 edits `hermes_cli/web_delta_response.py`: a delta is meaningful only to the one client holding that base, and a CDN-style cache honors Cache-Control but ignores `Vary` names other than Accept-Encoding, so `Vary` alone could not stop a delta frame being replayed to a client that did not ask. The module docstring documents the rule.

## Code
- `hermes_cli/web_delta_response.py`
- `tests/hermes_cli/test_web_delta_response.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_web_delta_response.py`: `test_a_delta_is_no_store_and_a_whole_answer_keeps_the_handlers_cache_control` (parametrized over handler headers); the existing `test_other_headers_survive_a_delta_duplicates_included` was changed on purpose to expect `no-store` on the delta and the handler's value on the whole answer.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Builds on the earlier Vary/ETag delta patch; do not apply without it.
