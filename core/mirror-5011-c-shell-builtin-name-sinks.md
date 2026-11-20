# Shell builtin-name subscript sinks and shared case boundaries

Patches: **4587–4591** (5011 snapshot).

## Behavior

Literal builtin name operands are normalized before arithmetic evaluation. Quote removal leaves real expansions opaque. Data-only builtin options are not treated as variable destinations. Scanning continues past lexical case pattern parentheses, quoted/heredoc regions, and line continuations.

`x=1 case` is a simple command, not a case compound: its closing substitution parenthesis is not a pattern delimiter. Arithmetic command frames share cached whole-expansion case boundaries and include enclosing depth when recursing into executable heredocs.

## How it works

`LiteralNameScanner` walks each simple command once. Expansion boundaries are cached across frames, including failed walks. `CaseWords` consumes case syntax and skips ANSI literals and heredoc bodies. Assignment prefixes end reserved-word position while keeping builtin lookup open. Arithmetic sinks populate shared pattern boundaries before walking a body in its own frame.

## Code

- `tools/shell_arith_sink.py`
- `tools/shell_builtin_names.py`
- `tools/shell_prefix_expansions.py`
- `tools/shell_case_words.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/tools/test_shell_builtin_names.py`
- `tests/cron/test_lifecycle_guard_case_sink.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Existing quoted DATA lists stay unchanged. Uncertain prefixes cannot hide a literal name and have bounded scan work. Conservative nesting budget still applies. Failed word boundaries are not retried.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
