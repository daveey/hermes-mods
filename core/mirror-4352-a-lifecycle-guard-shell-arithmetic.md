# Lifecycle guard: shell arithmetic sinks, comments, heredocs and multi-line feeds

Mirror snapshot 4352; ordinals 4143, 4144, 4155, 4156, 4157, 4168, 4181, 4182, 4184, 4185, 4197, 4211, 4225, 4236.

Entries:

- **4143**: test(shell): cover expanding heredoc substitution fallback
- **4144**: Test quoted comments in lifecycle data-sink masking
- **4155**: fix(shell): inherit arithmetic evaluation through parameter fallbacks
- **4156**: fix(shell): inherit arithmetic after subscripts and replacements
- **4157**: fix(shell): preserve parameter word delimiters in arithmetic
- **4168**: fix(guards): keep literal prose quotes out of arithmetic sinks
- **4181**: test(shell_comments): unclosed opaque opener after root comment abandons masking
- **4182**: lifecycle guard: keep stdin feeds through compound commands spanning lines
- **4184**: fix(shell): recognize arithmetic backticks, literal apostrophes and $$
- **4185**: fix(shell): keep arithmetic apostrophes as frame delimiters
- **4197**: lifecycle guard tests: pin a process substitution closed on the next line (#2393 follow-up)
- **4211**: fix(cron): bound repeated raw quote expansion in lifecycle walks
- **4225**: shell_arith_sink: quoted subscript substitutions in `let` / `[[ -eq ]]` operands are sinks
- **4236**: fix(cron): scan scripts behind escaped subscript substitutions in arithmetic words

## Behavior

The lifecycle guard decides whether a shell command (or a script it runs) could restart or stop the gateway. These entries close fail-open gaps found by running real Bash on marker scripts and comparing with the scanner's verdict.

- **Arithmetic evaluation inherits through parameter words.** Fallback words (`${v:-...}`, `=`, `+`, colon forms, nested) inside `$(( ))`, `let` or `[[ -eq ]]` keep the enclosing arithmetic context, so a command substitution hidden in them is treated as an executable sink. Literal brackets and parentheses in those words no longer unwind the enclosing frame; replacement patterns stay lexical until the unescaped delimiter (4155-4157).
- **Lexical corner cases** (4184, 4185): `$$` is tokenized before `${`; a backtick inside an arithmetic frame is a sink; apostrophes remain frame delimiters (so a quoted `]` cannot close a subscript early) but an apostrophe span containing `$(` or a backtick inside arithmetic fails closed. The date producer exemption still applies.
- **Quoted operands of `let` / `[[ -eq ]]`** (4225): quotes are removed before bash evaluates the text, so a quoted subscript like `a[$(cmd)]` is a sink; ANSI-C spans with escapes fail closed. Plain data such as `let '$(P)'` stays allowed.
- **Escaped operands** (4236): a new module scans the referenced script behind escaped or backtick subscript substitutions in `let`, `[[ -eq ]]` and `declare -i` operands, refusing only when that file holds a lifecycle command or cannot be scanned.
- **Prose quotes** (4168): nested literal bodies with no expansion introducers are not treated as sinks; unknown top-level quoting and code-like nested bodies still fail closed.
- **Multi-line feeds** (4182): a group or compound command whose body spans lines keeps its stdin feed (`cat f.py | while ...` across lines, `{ ... } | python3`), because one walk now covers the whole command and a newline ends a command like `;`. Heredoc bodies are walked as scripts of their own.
- **Bounded quote expansion** (4211): overlapping raw quoted spans are no longer re-enumerated by every parent, which had exhausted the shared line budget on a benign wrapper graph.
- **Test-only pins** (4143, 4144, 4181, 4197): expanding-heredoc substitution fallback, a quoted comment with an unmatched apostrophe not unmasking data arguments, an unclosed opaque opener after a root comment abandoning masking, and process substitutions closed on the next line.

## How it works

- `tools/shell_arith_sink.py` carries the arithmetic scanner. Parameter-word dispatch is extracted into its own helper to keep complexity headroom; frames record where `let`/`[[` operands start and search for an earlier `[`, resuming past the last miss so the scan stays linear.
- `cron/lifecycle_guard_arith_words.py` (new, 4236) tokenizes (dequotes) each segment, picks integer-operator operands, `let` arguments (behind reserved words and transparent wrappers such as `builtin`) and integer declarations, spells backtick spans as `$( ... )`, and hands operands that hold both `[` and a substitution opener back to the walk as shell text. A cheap prefilter skips most commands. `iter_recursive_payloads` yields them next to `sh -c` payloads.
- `cron/lifecycle_guard.py` / `lifecycle_guard_groups.py` / `tools/shell_heredoc.py` (4182): `iter_script_segment_contexts` walks the whole command with one line walker; `heredoc_scripts` (built on `heredoc_body_spans`) walks the command without its bodies, then each body as its own script. An unterminated or unparsable heredoc splits nothing; a line shlex rejects falls back to physical lines that join the same walk.
- `cron/lifecycle_guard_output.py` (4211): output expansion moved to a topical sibling; raw-quote provenance and an immutable interpretation snapshot let unchanged raw spans in the same cwd/execution/alias/masking scope skip contained enumeration. Decoded strings, printer assembly and shell command payloads keep full expansion; caps and path safety are unchanged.

## Code

Shipped non-test paths (union over these entries):

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_arith_words.py`
- `cron/lifecycle_guard_groups.py`
- `cron/lifecycle_guard_output.py`
- `tools/shell_arith_sink.py`
- `tools/shell_heredoc.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/cron/test_arith_literal_prose.py`
- `tests/cron/test_arith_quote_desync.py`
- `tests/cron/test_lifecycle_guard_arith_escaped_words.py`
- `tests/cron/test_lifecycle_guard_arith_lexical.py`
- `tests/cron/test_lifecycle_guard_arith_parameter.py`
- `tests/cron/test_lifecycle_guard_arith_word_quotes.py`
- `tests/cron/test_lifecycle_guard_comment_sink.py`
- `tests/cron/test_lifecycle_guard_comment_words.py`
- `tests/cron/test_lifecycle_guard_multiline_feed.py`
- `tests/cron/test_lifecycle_guard_quote_coverage.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Everything is fail-closed: known coarse false positives remain (a `[` in an earlier separate `let` argument, syntax-error shapes). 4236 does not cover a later assignment to a variable declared integer earlier.
- Some guard-level shapes (backtick substitution in general) were tracked as strict xfail in test files that ship with these entries; check the shipped tests for their current marks.
- Heavy timing assertions in the lifecycle family (a 5 s wall on a large heredoc scan) can flake under host load.

## Plugin?

None.
