# Discord stand-in rate-limit expiry

Patch **1487** (`4c557992d57`, PR #498).

## Behavior

The test HTTP stand-in supplies an immediately replenished rate-limit bucket on JSON responses and empty 204 responses. Cancellation during discord.py's bucket-release await must not strand the next channel lookup or typing request.

## How it works

`X-RateLimit-Limit: 1`, `X-RateLimit-Remaining: 0` and `X-RateLimit-Reset-After: 0` give the SDK an expiry without throttling the fake. No production Discord adapter changes.

## Code

`tests/fakes/platforms/discord_standin.py` and `tests/e2e/core/platforms/test_discord_rate_limit_contract.py`.

## Tests

`test_cancelled_response_does_not_strand_next_request` runs channel and typing variants in cold children using the real SDK. It injects cancellation after parsing the first response, asserts cancellation occurred and bounds the next request by five seconds. Executed mirror-pass results appear in [snapshot 1492](mirror-1492-verification.md). Full nightly acceptance belongs to PR #498; this document does not claim this mirror pass reran the nightly suite.

## Applying

Apply the complete [mirror series](README.md#applying) with its bridges.

## Gotchas / plugin?

Test-only contract behavior, not a runtime plugin. The cold process deliberately avoids the parent pytest Discord stub.
