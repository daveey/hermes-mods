# Denial-substitution guard — unpublished M1 containment

This is a policy mistake guard, not an authorization sandbox. Full-goal acceptance is incomplete. Do not enable or publish this candidate before independent exact-head review and the remaining acceptance work.

## Rule
An observed denial of action/resource stays held on recognized repeat invocations. Same identity, different identity, elapsed time, switching client, owner-terminal reason text, or a writable JSON file never establishes subsequent authority. There is no TTL and no grant-import protocol. `verify_receipt` returns UNAVAILABLE. No independently protected exact-denial authority producer is integrated; the genuine-authorized route remains unproven. No reset or override API is offered.

## Observation and state
Known terminal/execute-code/tailnet output envelopes are decoded before parsing; multiple denial lines retain local qualifiers and evidence hashes. Text is conservative observation, not authenticated AWS evidence. Missing action/resource uses UNKNOWN. With both UNKNOWN, only the correlated invocation fingerprint is held. Deduplication includes that fingerprint for UNKNOWN/UNKNOWN; otherwise key is action/resource/principal/explicit/execution host.

Production captures `account_default_hermes_home()` at registration, sharing one ledger across this OS account's profiles on this host. It ignores later HERMES_HOME/HOME/cwd changes. Core platform suffix/default behavior remains relevant across process startup; cross-user/host history is not shared. Tests inject explicit synthetic roots, not a production env override. Legacy per-profile inventory/migration remains incomplete.

JSONL rows retain action, resource, principal, qualifier, timestamp, invocation hint and correlation/evidence hashes. `at` is a validated timestamp alias for compatibility; it must agree with observed_at. Valid v0 rows are read without expiry. Invalid/malformed/unreadable state causes a narrow hold on recognized AWS mutations, preserving explicitly known reads and non-AWS calls.

Dedicated advisory flock guards read/append. Reads create the root and lock file; lock acquisition has no local timeout. Append validates, deduplicates, writes O_APPEND and fsyncs before return, including the root directory on first ledger creation. This is not a crash-durability proof. No lock spans command dispatch. Cap defaults: 8MiB/20000 rows; ledger rows are not dropped or expired. Overflow writes a persistent cap marker; marker presence is unhealthy even with later higher cap constants. Only the latest 128 overflow observations per root are retained as diagnostic samples; these samples are not authorization state. Write failure is latched in-process and logged, not claimed durably safe. Complete crash/failure characterization remains required.

Legacy rows with both action and resource UNKNOWN and no invocation fingerprint make the ledger unhealthy, rather than silently losing the denial. This holds recognized AWS mutations, while known positive reads and non-AWS calls remain allowed. No repair/reset or grant path is provided; legacy inventory and operator disposition remain an open deployment prerequisite.

## Tested entry paths
`tests_guard.py` uses synthetic explicit roots and covers terminal CLI, literal execute_code terminal and boto3 forms, direct tool_call envelope classification, tailnet classification, user-terminal reason rejection, unrelated-action/resource controls, and callback recording/block responses. These are offline fixtures, never cloud probes. Native local multi-entry tool_call is rejected by current core; direct classifier envelopes are not proof that native batch execution exists.

Independent immutable adversarial tests retain all49 cases; M1 targets13 previous gaps, not the whole goal. Native26, actual resolver, UNKNOWN fingerprint and fresh-process cap controls are separate gates. Exact-head review and installed isolated plugin-manager proof remain pending.

## Known limits — still open
- Parser remains the prototype bounded shell/literal parser: command substitutions, dynamic arguments, aliases, script files, some SDK forms, raw SigV4/HTTP and transformed resources are not contained.
- Resource matching currently uses short-id tokens, not complete account/region equivalence.
- Same-uid code can alter ledger, marker, plugin or hooks; tool-text tamper checks are best effort, not filesystem protection.
- Concurrent tools already admitted before a denial, background process results and cross-host activity are not retroactively prevented.
- Full legacy migration, batch attribution, crash durability, strict complete schema validation and failure-path coverage are not yet accepted.
- No genuine subsequent owner-authority route has been proven. M1 is not completion and not permission to deploy.

## Why the prototype failed
Problem: original33 tests passed while independent49 found34 gaps.
Root (happened): TTL, identity exemptions and agent-writable grant claims were treated as authority.
Root (not caught): prototype tests asserted those bypasses and omitted native envelope/trust-negative behaviors; missing-module errors were not behavioral RED.
Recurrence: correction of the same unpublished prototype, not a claim of deployed regression.
Countermeasures: explicit no-authority contract, behavioral baseline assertions, preserved independent probes and independent review gates. Full chain remains open until complete acceptance and deployment proof.
