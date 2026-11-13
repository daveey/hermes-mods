# Delta responses: held-start flush, Vary and ETag handling, one origin per scope

Patches: **3438**.

## Behavior
- Gateway: when an eligible response start is held awaiting a complete body and a non-body message arrives (`pathsend`, `zerocopysend`), the held start is sent first, unchanged, then any collected body, then that message.
- Hashed and delta answers carry `Vary: X-Hermes-Delta-Accept, X-Hermes-Delta-Base`, merged with the handler's own Vary as one header (`*` left alone). A delta drops the handler's `ETag` (its bytes are a zstd frame); a whole hashed body keeps it. This keeps a caching proxy from replaying a delta to a client that did not ask.
- Desktop: `DeltaBodyStore.bindRemote` keeps one origin per scope; rebinding to a new origin drops the old one, while the scope's stored copies follow to the new origin.

## How it works
3438 changes `_DeltaSend` and header merging in `web_delta_response.py`, and `bindRemote` in `delta-reads.ts`. The pathsend case is latent today since the server does not advertise it. One existing test assertion was changed on purpose (delta no longer keeps the ETag).

## Code
- `hermes_cli/web_delta_response.py`
- `apps/desktop/electron/delta-reads.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_web_delta_response.py`: `test_a_file_the_server_sends_itself_still_gets_its_start_first`, `test_body_collected_before_a_non_body_message_goes_out_after_the_start`, `test_hashed_and_delta_answers_vary_on_the_ask_and_a_delta_drops_the_etag`, `test_vary_star_is_left_alone`.
- `apps/desktop/electron/delta-reads.test.ts`: modified for the rebind case (no named cases extractable from the patch).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Clients that relied on an ETag on delta answers no longer get one.
