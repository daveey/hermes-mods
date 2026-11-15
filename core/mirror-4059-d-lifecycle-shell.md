# Lifecycle guard redirections and arithmetic shell sinks

Mirror snapshot 4059; covered ordinals: 3905, 3906, 3922, 3935.

## Behavior

- Descriptor-variable redirections such as `{fd}>file`, array descriptor forms, and numeric `>&`/`<&`/`>|` words no longer hide the real command from lifecycle scanning.
- Substitution output anywhere inside a Bash arithmetic context counts as a shell sink, not only substitutions immediately following an arithmetic opener.
- Test-only coverage pins closed-group output reset and date-producer rebindings.

## How it works

`cron/lifecycle_guard.py` accepts variable descriptors. `cron/lifecycle_guard_groups.py:split_glued_redirections` preserves descriptor words and escapes operator-pair punctuation for tokenization. When a descriptor subscript is split, its tail becomes a placeholder descriptor while the original text is re-emitted as a separate command; an unterminated head without a glued tail remains an ordinary word.

`tools/shell_arith_sink.py` adds a frame-based scan for arithmetic expansion/commands, `let`, array subscripts, substring offsets, integer comparisons, and integer declarations. `tools/shell_heredoc.py:output_reaches_shell` combines that scan with existing sink regexes. The narrow `date +%s` numeric-output exemption is disabled when rebinding evidence is present. The corpus gains redirection-position cases and checks both missed unsafe commands and false refusals.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_groups.py`
- `tools/shell_arith_sink.py`
- `tools/shell_heredoc.py`

### Mirrored commits

- 3905: `a3fb42d03bc35a4e34341862410917264cda4afc` — Pins closed-group output reset across shell operators.
- 3906: `16331b64cd0c41e1438ac491829cb8e642eacd1f` — Preserves variable/numeric descriptor redirections in lifecycle scanning.
- 3922: `d9e85d65e6e375b3408ca0c97107ae68e97c7ca2` — Detects substitution output anywhere inside arithmetic shell contexts.
- 3935: `c10255bcb0fcef9f2fdd192a828f26f1ffab43d2` — Pins date rebindings that disable the numeric-producer exemption.

## Tests

- `tests/cron/test_lifecycle_guard_closed_output_reset.py`: after group-closing operators, subsequent shell/Python pipelines retain their own input; controls ensure the earlier group's output is not spuriously fed into a later pipe.
- `tests/cron/test_lifecycle_guard_fd_redirections.py`: real Bash must actually execute the dangerous fixture before the guard refusal assertion counts. Clean-command controls remain allowed, and direct splitter assertions pin descriptor arrays, quoted/escaped operators, incomplete heads, comments, and pipeline-safe text carrying.
- `tests/cron/test_lifecycle_guard_heredoc_walk.py`, supported by `tests/cron/heredoc_shape_corpus.py` and its `fixtures/lifecycle_guard_heredoc/main_verdicts.json`: coverage assertions include every redirect prefix/place/target and wrapper/nesting family; evaluated cases must have neither missed dangerous execution nor false refusal of clean Python.
- `tests/cron/test_lifecycle_guard_arith_sink.py`: operator-preceded substitutions in each arithmetic family are sinks and cause over-cap executable input to fail closed. Non-arithmetic/constant/numeric-date controls remain nonsinks. Mixed read-only and dangerous text cannot hide a sink; `typeset`, command-not-found handlers, and `ENV` rebindings disable the date exemption.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

This tokenizer is conservative, not a complete shell interpreter. Malformed arithmetic can widen scanning rather than allow unknown execution. Descriptor handling does not claim to fix every pre-existing ordinary backtick or quoted-substitution walk limitation. A corpus fixture is recorded reference evidence, not a fresh execution by this documentation pass.

## Plugin?

No. The lifecycle guard must inspect the exact core execution path before it runs.
