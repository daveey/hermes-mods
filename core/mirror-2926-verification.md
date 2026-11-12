# Mirror snapshot 2926: inventory and whole-tree proof

## Scope

Append **2910–2926**: 17 non-merge source commits through
`7c2041e566300d382437e3c0fd8976219a110f14`. PR #1733 is **2926**.
No merges occur after the preceding source snapshot. Published patches 0001–2909,
44 bridges, three ancestry-only entries and the verifier remain unchanged.

## Normalization

Exports succeeded in per-entry staging directories before copying. Existing policy
substitutions and the public no-reply author were retained. The existing
`tui_gateway/update_pause.py` fingerprint was deterministically re-pinned using its
previous source/applied line mapping, recovered and checked by a full 2909 replay.

Five new path-specific fingerprints retain only reviewed privacy changes:
- `tests/hermes_cli/test_kanban_owner_chat_relay.py`: replace two personal historical
  actions with generic examples preserving article-free named-owner-chat grammar;
  remove the private event-reference comment. All test functions and assertions stay intact.
- `gateway/update_checkpoint.py`, `hermes_cli/update_cmd_posix_pause.py`,
  `tests/gateway/test_update_checkpoint_census_agreement.py`, and
  `tests/hermes_cli/test_update_preswap_deadline.py`: normalize only the newly added
  incident hostname in comments/docstrings. Existing routing identifiers and published
  historical contexts are untouched.

Mail descriptions additionally omit private historical event details and normalize
incident hostnames. The mandatory two-boundary regex repair is carried forward with
unchanged exact preimage/postimage, snapshot-bound policy, helper and tests.

## Executed checks

- Full unchanged CLI verifier: exit 0, including exact inventory and whole-tree equivalence.
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge
  replay and mandatory repaired-tree/marker compilation proof. Final slice: 671 entries
  starting at offset 2255.
- Mirror/helper unittest discovery: **107 tests passed**.
- Direct isolated pytest smoke of the sanitized owner-chat regression on the fully
  replayed public tree: **37 passed**. This bypassed project conftest and is not the
  full project test-runner gate. Other feature suites and live deployment were not run.
- Private denylist scan of the new mail files: zero matches.

## Full verifier output

```json
{"ancestry_only": 3, "base": "e3bf4be5df04cf72dab4914b073361c71af1dbe1", "bridges": 44, "merge_base": "78abaafcccec20a546c94e8734a70e38c08a6a69", "patches": 2926, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "7c2041e566300d382437e3c0fd8976219a110f14"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "a2599cdac65221b6aca848f8ec8d5abf5a9e687d", "source_tip": "7c2041e566300d382437e3c0fd8976219a110f14", "whole_tree": "pass"}
```
