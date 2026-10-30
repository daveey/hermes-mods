# Session move, resume fence, delegate routing and Stop attribution after the merge

Patches: 1042, 1043, 1044, 1034.

## Behavior

- `hermes sessions move` can bring a chat back, and a retry after a failed move or a discarded unrevealed push can import the chat again; handover's own replace/discard deletes lift only the id marker (1042).
- `hermes -z --resume <chat>` for a chat moved to another gateway exits with `SESSION_MOVED` before any turn (1043). `latest` is excluded from this check.
- An unavailable default delegation route no longer refuses a task that pins its own model/provider; pinned tasks resolve only their own route, and a pinned batch is still preflighted as a whole (1044).
- A remote Stop reports the login that pressed it, not the host owner's OS subject (1034).

## How it works

1042 adds `_lift_deletion_fence(db, session_ids)`, deleting `RETIRED_PREFIX + id` keys from `state_meta` for the replaced and discarded ids only; receipt and media retirement stay. 1043 calls `_refuse_moved_session` in the `-z` path before `launch_gateway_chat`. 1044 changes `tools/delegate_tool_routes.py` so pinned tasks come back unresolved and resolve their own route lazily. 1034 reads the transport's `auth_identity` (via `is_authenticated_identity`) in the Stop label, returning `Desktop` for the local credential, while row ownership keeps upstream's owner subject.

## Code

- `hermes_cli/session_handover.py` (1042)
- `hermes_cli/main.py` (1043)
- `tools/delegate_tool.py`, `tools/delegate_tool_routes.py` (1044)
- `tui_gateway/methods_session_interrupt.py` (1034)

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. 1043: `tests/hermes_cli/test_session_handover.py` (edited; stubs `hermes_cli.main_resume_args.import_foreign_resume` and `hermes_cli.gateway_chat_startup.launch_gateway_chat` to check "before the turn" on the real route). 1044: `tests/tools/test_delegate_per_task_model.py`; the message names `test_unavailable_default_does_not_block_explicit_provider` as red before, green after (author's claim). 1042 and 1034 add no tests.

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

1042 intentionally leaves user-initiated deletes fenced. 1044 relies on a resolver-call-order assertion matched by content, not position. No test is claimed for the move-back replace path or the Stop label.

## Could this be a plugin instead?

No: they restore fork behaviour inside core session, CLI and delegation code.
