# Kanban wake_check stored verbatim; redactor keeps identifier keys

Patches: **2633, 2634**.

## Behavior

- A park's `wake_check` (`kanban_block`, `kanban_schedule`, `kanban_continue`) is stored byte-identical to what the worker passed. Before, it went through the display redactor first, and the assignment rule masked a value such as `idempotency_key='review:pr1305:<sha>'`, so the stored probe could never match and the card waited for its fallback wake although its conditions held.
- A probe the redactor would change is refused with `wake_check_secret` before the dry run executes it, and nothing is stored or run. The error names the redacted assignment (or the word before a bare token) and tells the worker to read the secret from an environment variable or a file. `human_request.resolved_check` already refused the same way.
- The redactor no longer masks the values of these exact key names, in any case: `idempotency_key`, `cache_key`, `sort_key`, `partition_key`, `dedupe_key`, `dedup_key`. Credential names (`api_key`, `secret_key`, `private_key`, `access_key`, `AWS_SECRET_ACCESS_KEY`, `password`, `token`) still mask.
- `primary_key` (and `secondary_key`) are not on the list: Azure names its shared-access keys that way, so `PRIMARY_KEY=`, `export PRIMARY_KEY=`, `primary_key = "..."` and `cosmos.primary_key=` still mask and still trigger the `wake_check_secret` refusal. 2633 first allowlisted `primary_key`; 2634 removes it after review found that this unmasked real keys.
- Comment redaction is unchanged; with the allowlist an `idempotency_key='...'` survives in comments too.

## How it works

- 2633: `tools/kanban_tools_park_args._wake_check_args` returns the normalized command as is instead of `_redact(cmd)`. The new `_wake_check_secret_error` redacts a copy, compares it with the original, and on a difference diffs the word lists to name up to three redacted assignments (or the word before a bare token). It is called before `kwc.dry_run_error`, so the probe is never executed. The message names only the assignment, never the value.
- 2633: the new `agent/redact_identifier_keys.py` holds `IDENTIFIER_KEY_NAMES`. `agent/redact._should_redact_assignment` returns False for a key whose lowercase form is in the set, next to the existing env-lookup exception. Matching is on exact names, so `my_cache_key_secret` is not exempt.
- 2634: removes `primary_key` from `IDENTIFIER_KEY_NAMES`; its docstring records why and that a dotted name is judged on its last segment.

## Code

`agent/redact.py`, `agent/redact_identifier_keys.py` (new), `tools/kanban_tools_park_args.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/agent/test_redact_identifier_key_names.py` (identifier names survive, an already elided value is not masked further, credential names and Azure-shaped 88-character base64 values under `primary_key`/`secondary_key` variants still mask; this drives `agent.redact` directly) and `tests/tools/test_kanban_probe_verbatim.py` (`kanban_block` and `kanban_schedule` store the probe byte-identical, `kanban_block` and `kanban_continue` refuse inline secrets including the Azure-shaped value, the refusal names a bare token by its neighbour, comments keep an identifier key and still mask real secrets; these drive the park tool handlers). The `kanban_continue` case covers only the refusal. Other redactor callers are not covered by these tests beyond the shared assignment rule (untested).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Tasks parked before 2633 keep their already-masked stored probes; they are not repaired.
- A probe containing a secret-shaped value now fails to park instead of parking with a masked value; workers must read secrets from env or a file.
- The allowlist is for display redaction globally: any text with one of the six names is no longer masked by the assignment passes, whatever the value. Other redaction passes (URLs, headers, connection strings) are unchanged.

## Plugin

Core change; no plugin required.
