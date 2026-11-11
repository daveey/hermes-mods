# Required-context contract

## Build the manifest before waiting

1. Read the target branch's current protection and applicable rulesets. Enumerate every required context, including its app identity where policy pins one. Use an independently validated complete manifest only when policy discovery is unavailable.
2. Map each context to its actual run and literal job name. Preserve version suffixes; a similarly named aggregate is not an alias. Include required contexts outside the primary CI run separately.
3. Verify the current PR head, then invoke the exact-run probe with a full lowercase commit pin:

   ```bash
   python3 -B scripts/wait_for_ci.py --gh-repo OWNER/REPO \
     --run-id RUN --head-sha FULL_SHA \
     --expect 'RUN:REQUIRED_JOB=success' --once
   ```

   Add an expectation for **every** required context. The probe is not a policy evaluator: omitted contexts are unchecked, not implicitly satisfied.
4. Re-read the PR head and state before landing. `--head-sha` binds the probe's own run read, not the PR's current head. Keep both checks: a wrapper-only head read leaves a race before the probe reads the run.
5. Use the same complete manifest for the final proof. Do not maintain a separate hardcoded aggregate-name filter; it can drift independently from the waiter.

## Interpret results without weakening policy

- Reject a missing expected job; first compare the manifest with current policy before calling the CI run failed. An obsolete literal name can produce a false-negative monitor result even when the real required checks passed.
- Require each expected context to succeed independently. Failure, skipped, cancelled and neutral are not success. An optional path-gated native lane may be skipped, but that does not prove the native workload ran or replace its required context.
- Reject multiple jobs matching an expected name, regardless of their order or conclusions; a name-to-job dictionary can otherwise hide a red job behind a later green one.
- Reject conflicting repeated `RUN:JOB` expectations before querying GitHub. Identical repeats are harmless; conflicting repeats must not become last-value-wins policy.
- Check listing completeness and required app identity in the caller where applicable. The exact-run probe does not establish either automatically.
- Correcting a stopped monitor's source does not restart it. Reconcile its terminal result with current run/head evidence before deciding whether another check is necessary.

## Regression recipe

Run from the skill directory:

```bash
python3 -B -m unittest discover -s tests
```

Exercise the probe and its wrapper separately:

- Stale aggregate name fails; the validated complete manifest succeeds.
- Each required context independently fails when red, missing or skipped.
- Duplicate expected jobs fail in both red/green orders and when both are green.
- Conflicting expectations fail before any `gh` call.
- Malformed pins, absent run heads and moved run heads fail closed.
- A run-head change between the wrapper's read and the probe's read fails. Remove only the probe's pin as a negative control: that case must then expose the missing protection. A wrapper harness that passes unchanged against the old probe does not prove the new flag is exercised.
- Incomplete listings and moved PR heads are rejected by the wrapper's own checks; do not attribute that coverage to the probe.

Keep fixture jobs and SHAs synthetic. A passing exact-run probe establishes only the supplied manifest at the pinned run head—not review approval, current PR identity, merge authorization or deployment.
