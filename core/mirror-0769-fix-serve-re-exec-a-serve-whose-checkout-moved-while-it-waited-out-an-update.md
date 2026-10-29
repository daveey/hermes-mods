# fix(serve): re-exec a serve whose checkout moved while it waited out an update [daveey]

Patch 0769, source `463b78b4e48a1e05838f00bfd02be2e1bf3822c3`.

## Behavior

fix(serve): re-exec a serve whose checkout moved while it waited out an update [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _read_git_revision_fingerprint(repo_root: Path) -> str | None:`
- `def cmd_dashboard(args):`
- `def wait_for_update_clear(*, update_probe: Optional[Callable[[], bool]] = None,`
- `def serve_harness(monkeypatch):`
- `+"""[daveey] A serve that waited out a core update must run and report only the new commit.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A Desktop serve that boots during `hermes update` waits in wait_for_update_clear, but by then
> hermes_cli.main, hermes_cli.config and the provider plugins are already imported and
> get_version_info() is cached (the fireworks plugin reads it at import). David-MBP 2026-10-28:
> serve pid 50733 started 07:02:38, cached commit cc29d783ed by 07:02:39 (the source:git stamp
> still matched HEAD), the checkout moved to 42ea812e27 at 07:02:50, the stamp at 07:03:36, the
> wait cleared at 07:03:40.6, and /api/health reported cc29d783ed for the process's whole life
> while code_skew's boot fingerprint (read after the wait) said 42ea812e27. The stamp lag was not
> the cause: the stamp was current 4 s before the wait cleared.
>
> cmd_dashboard now reads the checkout revision just before the wait. After a wait that ended
> because the update finished, the serve re-execs itself in place (same pid, argv and fds, before
> the one-shot SSH token is read) when the checkout moved during the wait or the cached identity
> names a commit other than HEAD (it moved between launch and the wait). Capped at 2 re-execs;
> POSIX only.
>
> Tests: tests/hermes_cli/test_serve_update_wait_reexec.py runs cmd_dashboard in a child process
> while a fake update moves HEAD then the stamp then releases the marker; /api/health commit must
> equal the code_skew boot fingerprint and HEAD (fails before this change with cc29d783ed).

## Code

- `hermes_cli/main.py`
- `hermes_cli/serve_update_wait.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_serve_update_wait.py`
- `tests/hermes_cli/test_serve_update_wait_reexec.py`

New or changed test definitions present in the source diff:

- `test_serve_that_waited_out_an_update_reports_the_commit_it_runs`
- `test_serve_with_no_update_does_not_reexec`
- `test_checkout_moved_or_identity_stale_reexecs`
- `test_no_reexec_unless_the_wait_saw_the_update_finish`
- `test_no_reexec_when_nothing_moved_or_no_checkout`
- `test_reexec_is_capped_and_the_counter_never_leaks`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
