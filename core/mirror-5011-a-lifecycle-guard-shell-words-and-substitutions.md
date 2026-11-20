# Lifecycle guard: command substitutions in words, printed pipelines, nested login shells and data-sink patterns

Mirror snapshot 5011; ordinals 4369, 4383, 4392, 4429, 4430, 4431, 4432, 4444.

Entries:

- **4369**: Scope printed startup-shell pipelines with unknown alias names
- **4383**: fix(lifecycle-guard): a quoted data-sink pattern's | bash is no output sink
- **4392**: Walk compound-command stdin redirects and heredocs into nested login shells
- **4429**: lifecycle guard: keep $(…), $((…)) and backticks inside their shell word
- **4430**: separate substitution quote frames before stripping comments
- **4431**: fix(cron): recognize continued substitution openers per quote frame
- **4432**: preserve lexical redirection metadata after substitution tokenizer rebase
- **4444**: test substitutions in parameter and redirection-target words

## Behavior

- **Substitutions (4429-4432, 4444)**: the tokenizer used to cut a word at the parentheses of a substitution, so the rest of the command fell into a segment with no program and a script restarting the gateway could slip through (for example as a redirect target or word prefix). Now each executable expansion becomes a placeholder inside its word and its body is scanned as its own command text, recursively, bounded at 32 nesting levels (fail closed). Comment stripping and line-continuation joining move to a per-frame tokenizer so a quoted hash inside nested substitutions can no longer hide a following command; continued openers are recognized per quote frame; lexical redirection metadata survives the tokenizer extraction. 4444 pins parameter-expansion and redirection-target contexts with real-Bash regressions.
- **Nested shells (4369, 4392)**: printed shell payloads from connected pipelines (`printf ... | bash -l`) are walked even under unknown alias names, and compound commands (brace groups, subshells, if/while/for/case) with stdin redirections or heredocs feeding a nested login shell are walked in nested scope (fail closed; `/dev/null` is no longer refused).
- **Data-sink patterns (4383)**: a quoted pattern containing `| bash` (for example a grep expression) is no longer treated as an output sink; sinks are still required after masking data-sink arguments, and lines carrying substitutions, process substitutions or dot-commands are never masked.

## How it works

- New `cron/lifecycle_guard_substitutions.py` (span matcher tracking quotes, ANSI-C quotes, comments, heredoc bodies and case-pattern parentheses) and `cron/lifecycle_guard_tokens.py` (tokenization of the original text, the reading where an empty unquoted expansion vanishes, and an ANSI-C-normalized form so the change only adds segments); `lifecycle_guard_words.py`, `_output.py` and `_nested_shells.py` consume them.
- The generated heredoc shape corpus gains substitution prefixes across every redirect place, wrapper, nesting and target, with its verdict fixture regenerated.

## Code

Shipped non-test paths (union over these entries):

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_nested_shells.py`
- `cron/lifecycle_guard_output.py`
- `cron/lifecycle_guard_substitutions.py`
- `cron/lifecycle_guard_tokens.py`
- `cron/lifecycle_guard_words.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/cron/fixtures/lifecycle_guard_heredoc/main_verdicts.json`
- `tests/cron/heredoc_shape_corpus.py`
- `tests/cron/test_lifecycle_guard_data_sink_pipe.py`
- `tests/cron/test_lifecycle_guard_heredoc_walk.py`
- `tests/cron/test_lifecycle_guard_output_sink.py`
- `tests/cron/test_lifecycle_guard_session_aliases.py`
- `tests/cron/test_lifecycle_guard_substitution_adversarial.py`
- `tests/cron/test_lifecycle_guard_substitution_bounds.py`
- `tests/cron/test_lifecycle_guard_substitution_comments.py`
- `tests/cron/test_lifecycle_guard_substitution_contexts.py`
- `tests/cron/test_lifecycle_guard_substitution_words.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- The scan can only add segments, so previously refused commands stay refused; benign forms stay allowed.
- A pre-existing nested-case false positive remained and is held as a strict expected failure.
- Deep nesting beyond 32 levels is refused by design.
