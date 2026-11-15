# Lifecycle guard shell scanning: printf, comments, feeds and tokenization

Mirror snapshot 4142; ordinals 4076, 4081–4090, 4119, 4142.

## Behavior

The gateway-lifecycle guard (`scan_gateway_lifecycle`) walks shell command text to decide whether it would run a gateway lifecycle command. This series closes several gaps in that walk and keeps some false refusals from appearing:

- `printf` with `%q`/`%Q` and a precision now models Bash's ANSI-C (`$'...'`) quoting of ASCII control characters, so output that a shell sink would execute is walked in the form Bash produces.
- Full-line shell comments at the root of a command no longer count as output sinks. A `;` or backtick inside such a comment no longer promotes an unrelated quoted data path (for example a log path assignment) into a script traversal.
- A script or Python file fed to an interpreter through a pipe is still opened when the feed passes through: a descriptor duplication (`cat f.py 2>&1 | python3`), an output process substitution (`cat f.py > >(python3)`), a bare `cat`/`tee` in the middle of a pipeline, a pipe ending a line, a closed group feeding a `>(...)` body, or `&>` glued to the preceding word (`bash script.sh&>/dev/null`).
- Literal filename characters survive tokenization. A file whose name contains a private-use character is no longer confused with a sibling name containing `&`.
- A comment containing an apostrophe after a harmless `grep` is not parsed as a quote, and the same comment after a real lifecycle command does not hide it.

Commands that only mention a script, such as `cat f.py 2>&1 | wc -l` or `cat f.py > out; python3 c.py`, stay allowed.

## How it works

- 4076: `_quoted` (`%q`) returns `$'...'` with `\a \b \E \f \n \r \t \v \\ \'` and octal escapes when the argument has ASCII controls (below 32 or 127); otherwise it keeps backslash quoting. `printed_argument_lines` also yields a conservative variant (`_shell_quote_variant`) that rewrites complete `$'...'` quotes into `shlex.quote` form. The original output and the existing fallback variants are still walked. An unterminated quote is left unchanged.
- 4081: new `tools/shell_comments.py` provides `mask_root_comment_lines`. It blanks only full-line comments outside quotes, expansions, groups and heredocs, keeping offsets and newlines. Opaque regions stay visible, and masking is abandoned (the text is returned unchanged) for unmatched or unsupported regions, nesting deeper than 32, and heredocs other than simply quoted delimiters. `output_reaches_shell` in `tools/shell_heredoc.py` masks comments before its sink search.
- 4082: the wrapper fixture uses `#!/usr/bin/env bash`.
- 4083: masking is also abandoned when a pending heredoc exists and a following multi-line group (`( :`, `{ :`, `[[ -n x`, `f() ( :`) spans a newline, because the newline starts the heredoc body.
- 4084: `protect_redirection_ampersands` rewrites the `&` of unquoted `>&`, `<&`, `&>`, `&>>` before `shlex` so the redirection stays one word. `_REDIRECTION` accepts `&>`/`&>>`. A closed `( ... )` keeps its output across following redirection words. `>(...)` receives what its command writes. `_cat_relays_stdin` covers `tee` as well as `cat`, and `_LineWalk.writes` adds the feed for such a segment. `join_continued_lines` joins a line ending in `|`, `|&`, `&&` or `||` with the next line.
- 4085: `split_glued_redirections` puts a space before an unquoted `&>` glued to a word.
- 4086: the probe walk collects complete writer variants for each `>(...)`, so words and `<` redirects after the body are known. Opener tokens such as `>>`, `>|`, `1>`, `&>` count as process-substitution openers. `>|` stays one redirection. `_cat_operands` handles `tee` (its operands are outputs, its own redirected stdin is input). Compound commands start their groups once, so `{ tee >(python3); } < f.py` feeds the body.
- 4087: test updated to the shape of the main-branch fd normalizer, where `2>&1` is escaped as `2>\&1` and stays one token.
- 4088: the `>|` pipe is escaped as `\|` for `shlex` instead of using a second sentinel character.
- 4089: closed-group output, closing-redirection state and pending output are saved and restored around a substitution body, so a closed group feeds `> >(python3)` without leaking into a nested pipeline, and a `>(...)` body's stdout is combined with the closed group's output.
- 4090: the private-use sentinel is replaced by `shlex` escapes (`\&`, `\|`). `restore_redirection_ampersands` becomes an identity function kept for existing callers, so literal U+E000 input is never rewritten.
- 4119: tests only. Adds literal private-use word cases and redirects after a `>(...)` body.
- 4142: tests only. Pins the expanding-heredoc and comment-stripping behavior.

## Code

Source paths touched by this feature group (including regression fixtures):

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_groups.py`
- `cron/lifecycle_guard_printf.py`
- `tools/shell_comments.py`
- `tools/shell_heredoc.py`
- `tests/cron/test_lifecycle_guard_comment_corpus.py`
- `tests/cron/test_lifecycle_guard_comment_sink.py`
- `tests/cron/test_lifecycle_guard_feed_routing.py`
- `tests/cron/test_lifecycle_guard_literal_ampersand_marker.py`
- `tests/cron/test_lifecycle_guard_printf_quote.py`
- `tests/cron/test_shell_guard_mutation_regressions.py`

Mail entries in the ordered series:

- 4076: [`338e04f645bbb4586b96cc9feebb33d670b74d30`](patches/4076-daveey-Model-printf-control-character-quoting-before.patch)
- 4081: [`3b6b5c57f5d4ee34b2765d9940a7de89b6d58fbc`](patches/4081-daveey-Keep-root-shell-comments-out-of-lifecycle-out.patch)
- 4082: [`8dca071fb5d38e4f152b669a9c412e61e047d4cb`](patches/4082-daveey-Use-portable-Bash-shebang-in-comment-sink-fix.patch)
- 4083: [`960a4ea3247478ff553cf0e581b36caf916b5fd0`](patches/4083-daveey-Keep-pending-heredoc-multiline-groups-unmaske.patch)
- 4084: [`99cfe20e67e3bcf93ee2184bcfae6060793cefbf`](patches/4084-daveey-lifecycle-guard-keep-stdin-feeds-through-2-1-.patch)
- 4085: [`376ef93435676db5a03f00d20234a231b30b4852`](patches/4085-daveey-lifecycle-guard-split-a-glued-to-the-word-bef.patch)
- 4086: [`c15db543f4c6d509b185d6157163696514df25a8`](patches/4086-fix-lifecycle-resolve-complete-process-substitution-.patch)
- 4087: [`2d983f9782798aa196ca4ca994e51ef775e3f26e`](patches/4087-test-lifecycle-retain-main-fd-normalizer-token-contr.patch)
- 4088: [`6e2606724c0a64f0eaea36f6b00de49dce1d627e`](patches/4088-fix-lifecycle-escape-clobber-pipes-without-a-new-sen.patch)
- 4089: [`3e11cab383ba3804a25da594bd38cba484597c46`](patches/4089-fix-lifecycle-feed-output-substitutions-from-closed-.patch)
- 4090: [`72232abb8fb8f7450a25d933665003055f055c50`](patches/4090-fix-lifecycle-preserve-literal-filename-characters-d.patch)
- 4119: [`3059f72ea6fd494160c2c2d7e04c295d117ec433`](patches/4119-daveey-test-literal-marker-and-trailing-substitution.patch)
- 4142: [`133dd0ff8e225eb161d6934e19d45bc22e4e5f78`](patches/4142-daveey-Pin-expanding-heredoc-and-comment-stripping-r.patch)

## Tests

All tests call the guard functions directly (`scan_gateway_lifecycle`, `output_reaches_shell`, and helpers); none exercises a terminal, code-execution, REST or Desktop entry path, so those paths are untested here.

- `tests/cron/test_lifecycle_guard_printf_quote.py` (4076): `test_control_quote_matches_bash` compares the printf model with real Bash for control characters across `%q`/`%Q` precision and width forms; `test_quoted_control_model_keeps_fallbacks` asserts the original and fallback lines are still produced; `test_control_quote_precision_under_shell_sink` runs a printf producer through `| bash`, `eval "$(...)"` and `bash <(...)` and asserts the scan walks the executed script (an oversized script yields a scan-cap refusal); `test_quote_variant_preserves_word_contents` asserts decoded contents round trip through `shlex` and an unterminated quote is unchanged.
- `tests/cron/test_lifecycle_guard_comment_sink.py` (4081, 4082): `test_comment_is_not_an_output_sink`; `test_wrapper_does_not_execute_oversized_data_assignment` (a wrapper with a comment followed by a data path scans as `(False, None)`); `test_comments_and_real_execution_with_bash` and `test_bash_comment_does_not_run_substitutions` (real Bash markers show comments are inert and real substitutions run); `test_quoted_expanded_and_heredoc_hashes_remain_conservative`; `test_root_comment_mask_preserves_opaque_prefix`; `test_uncertain_regions_abandon_masking`; `test_unquoted_heredoc_false_close_runs_real_substitution`.
- `tests/cron/test_lifecycle_guard_comment_corpus.py` (4081, 4083): `test_comment_mask_preserves_generated_executed_payloads` (generated comment, wrapper and nesting combinations using a fake executable all still refuse); `test_pending_heredoc_multiline_groups_preserve_executed_hash` (delimiter, group and comment combinations keep executed hash-line payloads unmasked).
- `tests/cron/test_lifecycle_guard_feed_routing.py` (4084–4090, 4119): `test_routed_stdin_feeds_are_scanned` asserts `(True, None)` for the routed feeds listed above; `test_routing_mentions_stay_allowed` asserts `(False, None)` for mention-only cases; `test_redirection_ampersands_survive_tokenizing` asserts redirection tokens and control operators; `test_glued_ampersand_redirection_splits_from_its_word`; `test_closed_group_feeds_output_substitution`, `test_substitution_output_survives_closed_group`, `test_closed_group_redirected_stdin_survives_substitution`, `test_closed_group_does_not_leak_into_substitution_pipeline`.
- `tests/cron/test_lifecycle_guard_literal_ampersand_marker.py` (4090): `test_literal_marker_and_ampersand_siblings` (a file named with U+E000 and its `&` sibling are distinguished for `bash`, `.` and `cat | bash`, quoted and unquoted); `test_tokenizer_preserves_literal_characters`.
- `tests/cron/test_shell_guard_mutation_regressions.py` (4142): `test_expanding_heredoc_substitution_fails_closed`; `test_comment_apostrophe_does_not_turn_grep_data_into_lifecycle`; `test_comment_after_real_lifecycle_command_does_not_hide_it`.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- Comment masking covers only full-line root comments. Inline comments, quoted or expanded `#`, heredoc bodies and anything the masker cannot prove are left visible, so the guard errs toward refusing.
- Only simply quoted heredoc delimiters are traversed by the masker; unquoted backslash-newline delimiter joins and ANSI or mixed-quote delimiters keep masking off.
- The printf quote variant does not infer surrounding shell quote context; it is deliberately conservative and adds walked text rather than replacing it.
- The 4084 message notes that a compound command spread over several lines (for example `{` newline `cat f.py` newline `} | python3`) is still walked line by line; later patches in this series do not claim to change that.
- 4087, 4119 and 4142 are test-only; 4119 and 4142 state production semantics are unchanged.
- `restore_redirection_ampersands` remains as a no-op for compatibility.

## Plugin?

No. This is the core guard's command-text analysis and its regression tests, which a plugin cannot hook into.

See also: [nested shell scan](mirror-4059-f-nested-shell-scan.md), [printf sink scan](mirror-4059-f-printf-sink-scan.md), [comment word boundaries](mirror-4059-f-comment-word-boundaries.md).
