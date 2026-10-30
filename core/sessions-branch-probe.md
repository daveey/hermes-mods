# Copy a session for a probe without writing into the original

Patch: **1134**, source commit `3c5a9b706d469de90c8a876a7cdcb3f84b90a7ac`.

## Behavior

`hermes sessions branch <id> [--title TITLE] [--source SOURCE] [--json]` creates a new root session containing the original transcript and system prompt. The default source is `oneshot`, an internal listing source; the copy has no parent link and carries `model_config._probe_copy_of` identifying the original. The original's messages and ended state are unchanged.

Plain output prints the new ID on stdout and a probe/cleanup hint on stderr. JSON output includes `session_id`, `copied_from`, `read_from`, message count, source, and cleanup command. Unique ID prefixes and custom titles are supported. An explicit `--source` overrides the default; visibility then depends on that source's listing policy.

## How it works

The command resolves the requested ID, follows compression to the resume tip, filters `session_meta` messages, creates a separate root row, and copies messages in batches. Failed copying/title assignment attempts to delete the partial copy and re-raises the failure. Cleanup itself is best effort.

`branch_rows` owns transcript field selection for both this command and interactive `/branch`, including tool-call identity, reasoning, timestamps and API-content sidecars. The standalone command does not switch the current interactive chat. Interactive `/branch` retains its own lifecycle behavior; only its row builder is shared.

## Code

- `hermes_cli/sessions_cmd_branch.py`: `BRANCH_COPY_KEYS`, `branch_rows`, `cmd_branch`.
- `hermes_cli/subcommands/sessions.py`: parser and optional flags.
- `hermes_cli/sessions_cmd.py`: database action dispatch.
- `hermes_cli/cli_commands_mixin.py`: interactive `/branch` uses the shared builder.
- `website/docs/user-guide/sessions.md`: copy/probe/delete workflow.

## Tests

Source regression file `tests/hermes_cli/test_sessions_branch.py`:

- `test_branch_copies_history_to_a_new_hidden_session`: JSON command dispatch, missing ID, copied messages/system prompt, default source, root relationship, marker, cleanup command, and original unchanged/not ended.
- `test_branch_plain_output_prints_id_and_accepts_prefix`: plain ID output, prefix resolution and custom title.
- `test_branch_source_override`: custom source in JSON mode.

The hidden-session test asserts the internal source and root-row invariants; it is not a mounted Desktop sidebar or interactive resume-picker test. The shared-builder change also touches interactive `/branch`; its existing tests are separate. Compression-chain resolution, stderr hint text, partial-copy cleanup failures and live model probes are not established by these three regressions alone. Executed checks are recorded in the snapshot verification document rather than inferred from source test names.

## Applying

Apply the complete ordered series using `git am -3 --empty=keep` from the snapshot's documented merge-base. This patch depends on the fork session store and command dispatcher. It is not an independent upstream drop-in. In this snapshot, entry 1134 also carries the final historical merge-context integration overlay; see [mirror-1134-verification.md](mirror-1134-verification.md). Its diff is therefore larger than the standalone session-copy feature.

## Gotchas

Copying a chat also copies its sensitive transcript. Keep the copy local and delete it when finished:

```sh
copy=$(hermes sessions branch SESSION_ID)
hermes chat --resume "$copy" --oneshot -q "your test prompt"
hermes sessions delete --yes "$copy"
```

Do not substitute the original chat ID into the probe command. Hidden does not mean deleted; the command leaves a durable session until explicitly cleaned up. Tests use example transcripts in the public patch.

## Could this be a plugin instead?

The public `user-chat-probe-guard` plugin directs agents to this command, but core owns session copying, parser dispatch and the interactive `/branch` row builder. A refusal plugin alone cannot provide the shared copying command.
