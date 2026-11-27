# Runner verification evidence

Use this procedure to verify a runner/host with the runner tools, whether you are diagnosing it or checking a fix. It establishes what the evidence supports, not a recovery recipe.

1. Use `runners_inspect` with the exact runner identifier and a bounded history. Preserve the returned owner, per-dimension observations, collection IDs, timestamps, expiry, readiness requirements/results, job-query coverage, and source errors. Filter large saved responses locally to the target runner, host and provider scope; unrelated scope errors are not evidence of this runner's failure.
2. Compare required readiness checks with passed checks. A fresh wrapper and passing disk/service/tool probes do not satisfy a required operational check whose state or timestamps are unknown. Keep the failed, missing and unknown categories distinct.
3. Request an independent recheck through `runners_verify`, using the exact repair identifier and currently observed owner. Treat `pending` as an acknowledgement only. Read the target back through `runners_inspect` after a bounded wait; never turn submission success into verification success.
4. Success needs fresh registration, host/service and readiness evidence from two distinct collection cycles. Compare observation times and collection identities, not tool-call count. Re-reading the same cached collection is not a second cycle. A refreshed host probe may coexist with an older provider observation. Record each dimension separately and do not attribute a refresh to a verification request unless the returned provenance establishes that linkage.
5. Inspect existing build/job evidence. When the card or user limits the investigation to diagnosis-only, use that evidence rather than dispatching a build; otherwise run the build the gate needs. Require a successful build attributable to the target runner, not a chat completion claim or an unrelated sibling's success. An empty bounded query means no matching jobs were returned within its coverage, not that the host is idle.
6. Report the verdict first, then a compact dimension-by-cycle table, the supported why-chain, and the unmet acceptance gates. Give freshness as of the readback time, not as a permanent health claim. If verification remains pending or readiness/build proof is missing, explicitly leave success unproven.

Two completed collections can demonstrate persistence of an evidence gap without establishing its root cause. To distinguish absent production from ingestion or mapping failures, obtain authorized evidence from those specific stages rather than inferring a cause from null timestamps alone.

## GitHub inventory and display freshness

- Compare the configured scope's exact repository with the workload's Actions runner endpoint. Organization-level lists omit repository-level registrations; two similarly named repositories may both remain valid, so rename maintenance cannot infer workload ownership. Preserve scope identities instead of relabeling old registrations.
- Trace source timestamps through the real service envelope into the renderer. A fresh collector tick or transport receipt does not renew an older provider observation. Build producer-shaped regressions (including provenance metadata), not only hand-authored runner rows.
- Compare runner IDs as well as names. Across separate GitHub and collector reads, report busy/idle transitions and ephemeral registration churn explicitly; a total-count mismatch alone does not prove missing persistent Macs. Preserve both read times and compare every requested persistent registration.

## Reconciling a dynamic worker pool

- Verify instance-to-gateway identity before treating a successful instance listing as exhaustive absence evidence: an ASG can give several slot identities the same generic Name tag.
- Compose enrollment and periodic label-publisher transitions in tests; independent green suites do not prove compatible label ownership.
- Retain the exact admission/removal reason before cleanup overwrites the slot, and distinguish a successful canary from successful pool admission.
- Exercise both initial admission and planned idle-drain cancellation across a publisher pass: a held row may correctly lose its admission marker, so returning demand must revalidate and restore the owner-controlled marker before declaring ACTIVE; initial scale-up coverage alone misses this return path.
