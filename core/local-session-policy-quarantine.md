# Quarantine invalid stored local-session policies

Patches: 1618 1619 1620

- `patches/1618-daveey-Quarantine-deterministically-invalid-local-se.patch`
- `patches/1619-daveey-Assert-terminal-policy-quarantine-in-recovery.patch`
- `patches/1620-tests-satisfy-code-health-rules-in-policy-quarantine.patch`

## Behavior

- **1618:** Local-session recovery quarantines deterministically invalid stored policies once, preserves their policy data and message history, excludes them from subsequent boot-recovery candidates, and refuses explicit restoration with `invalid_params`. Tests cover empty/missing model, invalid toolsets, malformed embedded config JSON, and missing policy.
- **1618:** A transient policy-restore storage error remains retryable. A failed quarantine write or a concurrently changed receipt also stays eligible for inspection rather than being marked quarantined prematurely.
- **1618:** A pre-admission Kanban `run_task` retry respects the same quarantine: repeated invalid-policy attempts retain one receipt and create no admissions. This is tested at the gateway handler, not through a live client transport.
- **1619:** Recovery diagnostics distinguish terminal policy refusal from other damaged receipts: invalid policy emits its first warning, then explicit restoration refuses without another diagnostic; other tested receipt failures retain their prior repeated-diagnostic behavior. Here “terminal” means non-retryable, not the terminal tool.
- **1620:** Test-only code-health changes preserve the same damage cases using a dispatch table and annotate the intentionally imported pytest fixture parameter with a scoped F811 allowance. No runtime behavior changes in this patch.

## How it works

`_restore_receipt_policy` is shared by local recovery and the saved-receipt branch of Kanban `run_task`. An existing non-null `quarantine` marker immediately raises `_ReceiptRefused('invalid_params', field='policy', detail='stored policy quarantined')`.

Policy decoding catches `KeyError`, `TypeError`, and `ValueError`. Since `RuntimeStoreError` is itself a `ValueError`, the helper examines its cause: only a validation-type cause that is not itself a `RuntimeStoreError` is terminal. Generic store/epoch refusals propagate instead of being recorded as invalid policy.

`quarantine_local_policy` writes inside the database write transaction. It validates the owner's epoch, reads the current receipt, returns false if already quarantined, and compares the receipt against the exact inspected value before updating it. Missing data raises `storage_unavailable`; changed data raises `admission_conflict`. The marker contains `reason: invalid_stored_policy`, the validation exception's `error_type`, and an `at` timestamp. A first successful write produces a warning; policy and history are not deleted or replaced.

Recovery validates the policy before compression catch-up and validates the adopted receipt again if catch-up changed it. Boot receipt selection filters out non-null quarantine markers using guarded JSON extraction. These changes do not turn malformed outer receipt JSON or unrelated identity/lineage errors into policy quarantine.

## Code

**1618:**

- `gateway/session_kanban.py`
- `gateway/session_local_recovery.py`
- `hermes_state_local_quarantine.py`
- `tests/gateway/test_kanban_policy_quarantine.py`
- `tests/gateway/test_local_policy_quarantine.py`
- `tests/gateway/test_local_policy_quarantine_write.py`

**1619:**

- `tests/gateway/test_local_recovery_pre_profile_origin.py`

**1620:**

- `tests/gateway/test_kanban_policy_quarantine.py`
- `tests/gateway/test_local_policy_quarantine.py`

## Tests

**1618:**

- `tests/gateway/test_local_policy_quarantine.py::test_invalid_policy_is_quarantined_once`: parametrizes the five invalid-policy cases, checks preserved policy/history, one warning, explicit restore refusal, exclusion from boot candidates, and continued exclusion after another owner restart.
- `tests/gateway/test_local_policy_quarantine.py::test_transient_policy_restore_failure_retries`: injects `storage_unavailable`, verifies repeated attempts, then restores successfully after removing the fault.
- `tests/gateway/test_local_policy_quarantine_write.py::test_quarantine_write_failure_keeps_receipt_retryable`: parametrizes unavailable writes and concurrent receipt changes; verifies no premature marker and successful quarantine after the interference is removed.
- `tests/gateway/test_kanban_policy_quarantine.py::test_kanban_invalid_policy_retries_keep_one_receipt`: injects an empty model, retries the handler, and checks one preserved invalid receipt, its quarantine marker, no admissions, and exclusion from recovery candidates.

**1619:**

- `tests/gateway/test_local_recovery_pre_profile_origin.py::test_a_refused_receipt_names_its_failing_check_in_the_log`: changes the terminal-policy expectation to `invalid_params`, no repeated diagnostic, and no recovery candidate; other damage cases still expect `storage_unavailable` and a diagnostic identifying the failing check.

**1620:** Refactors `test_invalid_policy_is_quarantined_once` to use `DAMAGE` and `_set_policy`, and annotates `test_kanban_invalid_policy_retries_keep_one_receipt` for fixture injection. It adds no new test entry path.

Untested:

- End-to-end client/UI or wire-protocol behavior; these tests exercise gateway recovery and handlers directly, not terminal-tool execution.
- A stale owner epoch at the quarantine write itself, a disappearing receipt, and direct repeated calls to the already-quarantined write helper are not dedicated cases in these patches.
- Invalid policy introduced specifically during compression catch-up, and all possible wrapped validation-exception shapes.
- Automated repair or removal of a quarantine marker; no such recovery workflow is added.
- Execution of the code-health gate for 1620 during this documentation pass.

These were not rerun by the mirror pass.

## Applying

Apply 1618, then 1619, then 1620 on the preceding fork series with local receipts, owner epochs, compression recovery, and finite Kanban admission support. The follow-ups adjust tests for 1618 rather than replacing its implementation. No new configuration or schema migration is added: the marker is stored inside the existing receipt JSON in `state_meta`.

## Gotchas

- Quarantine is fail-closed and persistent. It is not a retry backoff, deletion, or automatic policy repair; changing policy fields alone does not bypass an existing quarantine marker.
- Only policy validation errors are classified here. A generic storage failure is not evidence that a stored policy is invalid.
- The transaction fences the exact receipt inspected. A changed receipt must be inspected again; the old validation result cannot quarantine new data.
- Diagnostic expectations differ deliberately: terminal policy failures stop producing repeated warnings, while unrelated receipt failures retain their existing recovery behavior.

## Plugin?

No. Quarantine is enforced in core receipt restoration, boot selection, and epoch-fenced database writes shared with Kanban admission.
