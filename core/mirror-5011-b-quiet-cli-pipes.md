# Quiet CLI stdout cancellation

Mirror entries: 4538.

## Behavior

- **4538** — CLI non-TTY text output handles broken stdout writes and buffered shutdown flushes quietly, including the fast version path.

## How it works

`quiet_stdout_pipe` wraps the CLI entry and pre-import version output. For non-TTY stdout it proxies only write/flush boundaries. A BrokenPipeError there redirects the failed file descriptor to the null sink and exits 1, preventing a second interpreter-shutdown flush diagnostic. The original stdout object is restored in all cases.

Normal returns and argparse/SystemExit paths flush guarded output; TTY output is unchanged. No process-wide SIGPIPE behavior is modified, and unrelated exceptions—including an internal transport BrokenPipeError—continue to propagate.

## Code

- `hermes_cli/main.py`
- `hermes_cli/stdio_pipe.py`

## Tests

The source suite launches real help, version and Kanban-show subprocesses with closed readers, isolated home/database and both buffered/unbuffered modes. Normal-output prechecks ensure a missing fixture cannot masquerade as pipe handling. Separate controls preserve normal output/return values and nonstdout failures. No subprocess tests were freshly executed for these docs.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_cli_broken_pipe.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

A quiet early stdout closure is still a nonzero exit (1), not successful completion of all requested output. The wrapper is intentionally limited to CLI text stdout and must not become a global BrokenPipeError suppressor.
