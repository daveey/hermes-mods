# PM refuses a target gap in-process, not through a worker

Patch **0601** (source `992410c317`).

## Behavior

`hermes_cli._subprocess_compat.selected_git_env()` asks `pm.ensure("git")` on every internal git call.
On POSIX, PM deliberately provides no git: `Git.gaps` maps every Linux and macOS target to "POSIX uses
system git by choice". The client's lazy pre-check (`_missing_or_refuse`) still counted git as missing,
so every call spawned a PM worker (`pm/worker.py`, a fresh interpreter). The worker could only answer
`git: unavailable on darwin-arm64`, and the caller fell back to system git.

On 2026-10-27 one busy kanban agent on a Mac started about 53 PM workers a minute this way, each
taking 0.7–2.3 s. That was a large share of the machine's ~20 process spawns per second.

Now `pm.client.ensure()` checks the package's static `missing_reason(current_target())` before it builds
any worker request. On a gap it raises the same `InstallError(name, "unavailable on <target>: <reason>",
"none")` the worker would have raised. This applies to lazy and explicit calls; the gap is a property of
the package and the target, not of the install state. Packages without a gap behave as before.

## How it works

- `pm/client.py`: `_raise_target_gap(package)` runs right after the `is_runtime()` direct path in
  `ensure()`. `missing_reason` is a pure lookup (`gaps.get(target)`, or the termux-docker target test).

## Tests

`tests/pm/test_client_target_gap.py`:

- git on `darwin-arm64` and `linux-x64`, for lazy and explicit calls, raises the gap error without
  requesting a worker;
- a package with no gap on the target (git on `win32-x64`) still goes to the worker;
- `selected_git_env()` returns the base environment unchanged and spawns no process.

Without the fix, four of these fail. The two that pass on both versions guard against over-applying it.

## Applying

`git am -3`. It is independent of other patches.

## Gotchas

- A new package gap now fails fast in the client. Anything that relied on the worker writing a receipt for
  a refused lazy ensure no longer gets one; none in the tree did.
