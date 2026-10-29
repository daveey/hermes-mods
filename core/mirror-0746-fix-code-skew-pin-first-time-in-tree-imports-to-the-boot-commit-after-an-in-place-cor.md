# fix(code-skew): [daveey] pin first-time in-tree imports to the boot commit after an in-place core update

Patch 0746, source `4b29ad8e4de5d216571212869d09e88d2d72bfdb`.

## Behavior

fix(code-skew): [daveey] pin first-time in-tree imports to the boot commit after an in-place core update. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+"""Keep a long-running process on ONE code revision after an in-place core update.`
- `def record_boot_fingerprint() -> None:`
- `async def start_gateway(config: Optional[GatewayConfig] = None, replace: bool =`
- `async def _lifespan(app: FastAPI):`
- `+"""A long-running process must not mix module revisions after an in-place core update.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A Desktop hermes serve booted at 2aa3397018 kept its old agent.model_routing_managed while
> hermes update moved the tree to abe20b1815; the first-time lazy import of the new
> kanban_model_assignment then failed with "cannot import name ManagedPinRejectedError" for
> three hours, because the skew watchdog only retires a backend once every Desktop session is
> idle (t_01a1824adc287c5a).
>
> gateway.boot_pinned_imports puts a meta-path finder in front of sys.meta_path at serve and
> gateway boot. Once the checkout leaves the boot commit (latched), every first-time import of a
> git-tracked module under the project root loads the boot commit's blob via git cat-file, so
> the process keeps one consistent revision until the existing watchdog restarts it. Untracked
> paths (venv, plugins), modules new since boot, and any git failure fall back to the normal
> import. HERMES_BOOT_PINNED_IMPORTS=0 disables it.

## Code

- `gateway/boot_pinned_imports.py`
- `gateway/code_skew.py`
- `gateway/run.py`
- `hermes_cli/web_server.py`

## Tests

Source test files changed by this commit:

- `tests/gateway/test_boot_pinned_imports.py`

New or changed test definitions present in the source diff:

- `test_unguarded_process_mixes_revisions_and_raises_the_reported_import_error`
- `test_lazy_import_after_update_loads_the_boot_revision_instead_of_raising`
- `test_a_module_deleted_by_the_update_still_imports_from_the_boot_commit`
- `test_a_module_new_since_boot_falls_back_to_disk`
- `test_no_update_means_normal_disk_imports`
- `test_an_unreadable_boot_commit_falls_back_to_the_old_behaviour`
- `test_skew_is_latched_even_if_the_tree_returns`
- `test_install_is_a_noop_under_pytest_and_when_disabled`
- `test_code_skew_wiring_installs_a_finder_bound_to_the_boot_commit`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
