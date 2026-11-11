# Desktop remembers a backend that refuses submission_id

Patches:

- `patches/2184-daveey-perf-desktop-remember-a-backend-that-refuses-.patch`

A backend whose `prompt.submit` contract has no `submission_id` refuses it before admission (error 4000). Desktop downgraded per submission, so every message paid a refused request plus an identityless resend (one extra round trip).

## Behavior

- The refusal is remembered per submission destination (connection + profile) for 10 minutes; later sends go identityless in one request.
- An accepted identity clears the memo; it expires so an upgraded backend gets identity back; it is valid only within the gateway activation epoch it was recorded in, and a refusal answered after a route/source swap is not stamped on the new backend.
- Explicit retries of an already identified (retained) entry always offer their identity again, so dedupe is never bypassed. Only a pre-admission refusal downgrades, and a lost identityless ACK is never auto-retried.

## How it works

`submission-identity-support.ts` holds the memo keyed by destination with an expiry and activation epoch; `submit.ts` consults it before attaching `submission_id`.

## Code

`apps/desktop/src/app/session/hooks/use-prompt-actions/submission-identity-support.ts`, `submit.ts`, `apps/desktop/vitest.setup.ts` (global memo reset).

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`submission-identity-support.test.ts` (per destination, expiry, accepted-clears, epoch swap, late refusal), `submit-legacy-identity.test.tsx` (one-request send after refusal; retained retry keeps identity; legacy 4000 retry), `index.test.tsx`.

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

The 10-minute memo means a backend upgrade is noticed up to 10 minutes late within one epoch. Legacy-identity tests moved to their own file because the index test file hit its line cap.

## Plugin

None.
