# Required-context contract: names are policy, not aliases

## Failure analysis

Problem: a completed successful run was reported as terminal failure by a wrapper requesting
`All required checks pass`, while branch protection required `All required checks pass (v2)` and
`macOS state required (v1)`, both successful. A native path-gated lane was skipped, not executed.
The live observation is intentionally not embedded here; the publication's private handoff retains its run/head.
The public regression uses synthetic run 11 and head `a` repeated 40 times.

Why 1: the exact-run probe rejected an absent literal job — proof:
`test_stale_name_false_failure_and_validated_manifest_green` reproduces the old false-negative
and passes with both validated names. The probe's strict missing-job behavior is correct.
Why 2: the wrapper supplied an obsolete name and separately duplicated that obsolete single-job
lookup for its final proof — evidence: the inspected wrapper's `--expect` and aggregate filter
both used the unversioned name, neither included the second required gate.
Why 3: the landing instructions presented an aggregate display name as the whole readiness
contract — evidence: the prior SKILL.md landing section named `All required checks pass` alone.

Root (happened): a copied display-name example was treated as authoritative policy, with two
independent hardcoded filters instead of a validated complete expectation manifest.
Root (not caught): the existing subprocess suite covered missing jobs, skips and expected-red
experiments, but not the caller's stale-name-to-current-two-context contract. Its fixtures
proved strict matching, not that the wrapper selected the correct required jobs. Evidence:
prior `test_wait_for_ci_runs.py`; the new stale-name fixture supplies the missing contract case.

Recurrence: searched this skill's context-name references and script history. The earlier exact-run
change (`e4ff1f31`) fixed an empty-manifest false positive, not this stale-name false negative.
Existing skip handling (`d3968895`) applies to discovered workflows, not required job proof.
No earlier identical stale-name fix found in that searched scope; broader incident history is unverified.

## Countermeasures and proof

- Symptom: use both exact context names validated from protection in the wrapper's expectation
  manifest and final proof; retain its before/after PR head/state checks. Never retry or restart
  a stopped waiter as part of changing its source. Proof: private corrected-wrapper fixture receipt.
- Root: the landing instructions now require reading current protection and applicable rulesets,
  or an independently validated complete manifest. Do not infer equivalent names by stripping
  version suffixes or accept only the currently present subset. Proof: updated SKILL.md recipe
  plus `test_stale_name_false_failure_and_validated_manifest_green`.
- Detection: each required context independently fails on missing, failure, skipped, cancelled
  or neutral. An optional skipped native lane does not replace a required context. Proof:
  `test_each_required_context_must_be_present_and_successful`.
- Exact-head binding: optional `--head-sha` rejects moved or absent run heads before judging jobs;
  malformed/short pins and mixing with containment mode fail before any gh call. Proof:
  `test_exact_head_valid_green`, `test_exact_head_rejects_moved_or_missing_sha`,
  `test_head_pin_must_be_full_sha_and_exact_run_only`.

Run from the skill directory:

```bash
python3 -B -m unittest discover -s tests
```

This remains an exact-run job probe, not automatic branch policy discovery, PR-head tracking,
review approval, app-identity verification or merge authorization. Required contexts outside the
listed run(s) still need explicit separate proof. A green required gate does not prove that an
optional path-gated native workload ran. No new watcher, policy weakening or merge is part of this fix.
