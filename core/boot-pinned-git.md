# Boot-pinned imports resolve git through locate_command

Patch: [`patches/0709-fix-gateway-resolve-boot-pinned-git-through-locate_c.patch`](patches/0709-fix-gateway-resolve-boot-pinned-git-through-locate_c.patch)
(fork commit `1b95d56bae`)

## Why

`BootPinnedFinder` used `shutil.which("git")`. The rest of the process resolves commands through `hermes_platform.resolver.locate_command`, which can see a git that `which` misses, and the other way around.

## Behavior

- With no explicit `git=` argument, the finder takes `locate_command("git").command[0]`.
- If that resolution has no command, `_git` is None and `_run_git` returns None (blob reads stay off).
- An explicit `git=` path is stored as given and does not call `locate_command`.

## How it works

`gateway/boot_pinned_imports.py` `BootPinnedFinder.__init__` calls `locate_command` only when `git` was not passed.

## Code

`gateway/boot_pinned_imports.py`

## Tests

`tests/gateway/test_boot_pinned_git_resolution.py`:

- `test_boot_finder_uses_resolved_git` — the finder stores the resolved executable and calls `locate_command` once with `"git"`.
- `test_boot_finder_missing_git_disables_blob_reads` — a missing resolution makes `_run_git` return None.
- `test_boot_finder_explicit_git_skips_discovery` — `git="/explicit/git"` does not call `locate_command`.

A live gateway boot after an in-place checkout move is untested in this patch.

## Applying

`git am -3 core/patches/0709-fix-gateway-resolve-boot-pinned-git-through-locate_c.patch`. It only touches `gateway/boot_pinned_imports.py` and its new test.

## Gotchas

`locate_command(...).command` is a tuple. The finder keeps the first element. A resolution object with an empty command disables blob reads; it does not fall back to `shutil.which`.

## Plugin?

No. The finder is installed on the gateway import path.
