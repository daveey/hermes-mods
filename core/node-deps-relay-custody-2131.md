# Node dependency watcher covers a stopped relay before PID registration

Mirror ordinal: **2120**.

## Behavior

On POSIX, losing the dependency-install lock terminates the relay and its reachable installer descendants even if job control stopped the relay immediately after it spawned the installer, before the installer PID could be written to the watcher. A resumed old installer must not write into the next owner's dependency tree.

The watcher remains outside the stopped foreground job. Windows retains the installer-PID handoff and `taskkill /T /F` path; the new stopped-job regression is POSIX-only.

## How it works

The relay starts the watcher before starting any installer. On POSIX it supplies its own PID in the watcher's launch arguments, so a kill root exists before installer spawn. The asynchronous stdin PID handoff remains as an installer fallback and as Windows' root.

The watcher enumerates the relay tree, excluding its own PID. On lock loss it stops the tree before enumerating/killing descendants, preventing ordinary forks during traversal. If enumeration fails, known relay/installer IDs remain fallback targets. The watcher still exits when the relay's stdin closes. The installer remains in the foreground job's normal signal/job-control environment; the watcher has its own session.

## Code

- `scripts/build/node-deps-relay.mjs`: early relay identity, watcher-first spawn and retained child handoff.
- `scripts/build/node-deps-watcher.mjs`: preknown root, self exclusion and enumeration fallback.
- `scripts/build/node-deps-lock.mjs`: updated custody comments explaining the process tree.

## Tests

`tests-js/node-deps.test.mjs` was inspected, **not executed here**. Its stopped-owner test is parameterized for normal behavior, delayed relay registration and failed enumeration. It confirms the owner/relay process group relationship, an independent unstopped watcher, and absence of the old installer's sentinel write in the next owner's tree before and after the old job could resume. Cleanup handles stopped jobs and the watcher even on assertion failure.

This source regression is skipped on Windows. No new Windows stopped-job proof or live dependency install was performed for this doc.

## Applying

Apply the full snapshot using [README.md](README.md)'s replay recipe, including existing dependency-lock infrastructure. Use the repository's supported Node version and lockfile-matched JavaScript test environment to run the node-deps test file in an isolated checkout. Do not induce lock theft or suspend installers against a live runtime to validate this documentation.

## Gotchas

- A child that daemonizes and leaves the reachable parent tree remains out of scope; this is not a cgroup/job-object sandbox.
- The watcher must exclude itself or tree shutdown can kill its own cleanup before descendants are handled.
- Passing the relay PID before spawn is the fix; merely speeding up stdin PID delivery does not close the registration gap.
- Enumeration failure fallback can only act on known IDs, not magically discover every detached descendant.

## Could this be a plugin?

No. The lock owner, relay and watcher are build-time dependency preparation infrastructure that runs before agent plugins can supply lifecycle safety. A plugin-level observer would have the same late-registration gap.
