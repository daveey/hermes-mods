# Lifecycle guard shell-flow analysis

Mirror entries: 4463, 4464, 4465, 4468, 4487, 4496, 4502, 4506, 4507, 4508, 4509, 4523, 4532, 4537, 4546, 4548.

## Behavior

- **4463** — Execution-proof fixtures select Bash consistently and separate optional version/tool premises from unconditional scanner assertions.
- **4464** — Printed pipeline units retain heredoc producer bodies and compound input flow without treating literal braces as groups.
- **4465** — Case subjects and pattern punctuation no longer corrupt compound and pipe tracking.
- **4468** — Piped files and multiline compound stdin reach nested login, privilege and remote shell scans.
- **4487** — Regression coverage pins referenced-script execution inside backticks and double-quoted command substitutions.
- **4496** — Quoted and escaped shell words retain provenance, so literal operators and keywords do not alter compound state.
- **4502** — Duplicate preprocessed token readings and repeated substitution bodies are reused within one depth-aware traversal.
- **4506** — Additive literal variable bindings are paired with execution operands across function bodies and sourced files.
- **4507** — Variable discovery distinguishes original executable syntax from printed/quoted candidates and distinguishes mention from execution visits.
- **4508** — Boundary regressions cover execution/provenance gating, binding shapes, redirection data, eval reconstruction and reassignment.
- **4509** — Substitution sentinels are excluded from literal binding candidates.
- **4523** — Nesting exhaustion becomes a conservative match at boolean boundaries and a structured refusal at the background-chat guard.
- **4532** — The large no-sink heredoc regression bounds work counts and input growth instead of elapsed scheduler time.
- **4537** — Case-pattern closing parentheses stay inside command-substitution frames during arithmetic-sink analysis.
- **4546** — The executed-path walk masks truly literal unquoted heredoc data behind the same consumer/shadow/sink fences as quoted data.
- **4548** — Output-sink detection follows pipes into compound commands whose shell consumer appears after a list separator.

## How it works

The printed-unit walker models lexical group/case boundaries and inherited stdin. A command within a pipe-fed group may read that pipe even after an earlier command and semicolon; a separate inner pipeline must not leak into a later command. Heredoc producer programs belong to their opener's unit. File-fed startup shells are scanned under their own alias uncertainty, rather than inheriting the session's alias guarantees.

`ShellWord` carries quote/escape provenance without changing decoded argv. Only actual syntax opens/closes compounds or advances case phases. Arithmetic substitution analysis uses the case-aware command-substitution boundary parser before inspecting each body, preserving date policy and recursion bounds.

Variable candidates are intentionally additive, not a shell-state interpreter: retain possible literal assignments and pair them only with command, eval or shell operands. Dynamic assignments stay with the ordinary substitution walk. Original syntax, nested programs and referenced files permit discovery; conservative printed/quote payloads do not turn redirect data into executable bindings. Mentioning a library does not cover a later executed scan.

Token reuse is traversal-local and keyed by body plus remaining nesting budget. Cached tuples are replayed as fresh lists in occurrence order. Public boolean guard predicates fail closed on nesting exhaustion while low-level iterators keep their typed exception so the main scanner retains the refusal reason.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_nested_shells.py`
- `cron/lifecycle_guard_output.py`
- `cron/lifecycle_guard_tokens.py`
- `cron/lifecycle_guard_variables.py`
- `cron/lifecycle_guard_words.py`
- `tools/shell_arith_sink.py`
- `tools/shell_heredoc.py`
- `tools/terminal_tool_guards.py`

## Tests

Source test files listed below cover nested login-shell aliases, multiline and grouped pipelines, heredoc producers, clean twins, Bash marker proofs, quote metadata round trips, command-substitution scripts, variable function/source discovery and data controls, deduplication and mutation isolation, arithmetic case boundaries, parser limits, terminal prescan and execute_code refusal before backend acquisition. Large-input tests retain semantic assertions; a separate regex-backtracking test remains necessary because Python call counts cannot measure regex-engine work. These are source coverage descriptions, not newly run shell or tool probes.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/cron/heredoc_shape_corpus.py`
- `tests/cron/test_lifecycle_guard_case_sink.py`
- `tests/cron/test_lifecycle_guard_function_variables.py`
- `tests/cron/test_lifecycle_guard_grouped_pipe_shell.py`
- `tests/cron/test_lifecycle_guard_heredoc_walk.py`
- `tests/cron/test_lifecycle_guard_login_heredoc_data.py`
- `tests/cron/test_lifecycle_guard_nesting_entrypoints.py`
- `tests/cron/test_lifecycle_guard_output_sink.py`
- `tests/cron/test_lifecycle_guard_printed_compounds.py`
- `tests/cron/test_lifecycle_guard_session_aliases.py`
- `tests/cron/test_lifecycle_guard_subscript_linear.py`
- `tests/cron/test_lifecycle_guard_substitution_bodies.py`
- `tests/cron/test_lifecycle_guard_substitution_comments.py`
- `tests/cron/test_lifecycle_guard_token_dedupe.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

This is conservative analysis, not complete shell evaluation. Unknown aliases, malformed input and exhausted budgets can refuse. Literal unquoted heredoc masking applies only to the executed-path walk and requires every body to contain no dollar sign, backtick or backslash; text scanning remains unchanged and sink/consumer safety checks still apply. Test proof portability must not weaken scanner refusal requirements. Runtime timing and historical corpus claims are not independently re-established here.
