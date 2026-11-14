# Desktop resubmits after an explicit stale-runtime refusal

Patches: 3818.

## Behavior

After a backend roll, Desktop can resume and resend a prompt refused by the legacy sidecar's exact stale-runtime response, even when that backend does not support submission identities. Ambiguous sends still require caution.

## How it works

`isStaleRuntimeRefusal` accepts only a `JsonRpcGatewayError` with code 4001, no canonical reason field, and the exact trimmed case-insensitive message `session not found`. It joins the existing pre-admission rejection path so resume may resend. The sidecar contract tests establish that this refusal occurs before DB/admission side effects, and that unsupported `submission_id` is rejected before handlers run.

Renderer tests drive a simulated backend roll through remembered and fresh identityless capability state, as well as identified submissions. Identified retries retain their identity. Codeless, differently worded or canonical-reason failures do not acquire this new safe-resend status.

## Code

- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-backend-roll.test.tsx` (patch 3818).
- `tests/tui_gateway/test_prompt_submit_stale_runtime.py` (patch 3818).

- **3818:** Renderer roll fixtures require exactly one admitted identityless or identified turn after resume, retaining a supplied submission ID; ambiguous failures are not automatically resent and explicit stale-runtime retries remain safe. Sidecar tests pin exact pre-admission 4001 and capability rejection before handlers.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

Only an explicit no-admission refusal is safe to replay without an identity. A lost/ambiguous acknowledgement is not the same event; the tests require one attempted send and visible uncertainty rather than a duplicate automatic send. No live rolling-backend deployment is established here.

## Plugin?

No. Prompt admission/retry classification is part of core Desktop submission state.
