# Exact named-profile multiplexer homes

Patch: `patches/1985-fork-follow-up.patch`, source
`2ae1c978d138e8a0580a7f3b5c0c44ff1e07fed9`, "[daveey] Restrict multiplexer
routing to exact named-profile homes". Part of the
[1980–1988 mirror batch](mirror-1988-features.md). The patch diff (ignoring
`index` lines) is byte-identical to the source commit's diff.

## Behavior

The default gateway multiplexes named profiles. Only a home that is exactly a
named-profile home (`<root>/profiles/<name>`) is routed to it.

A custom `HERMES_HOME` nested below a profile is an independent home, for
example a pytest or scratch home at
`<root>/profiles/<name>/cache/scratch/…/home`. For such a home:

- `multiplexer_root_for` returns `None`;
- `implied_host_root` returns `None`, so there is no implied host gateway;
- `multiplexer_serves_home` returns `None`, so there is no offline served
  verdict;
- discovery (`gateway_runtime._served_by_multiplexer`) does not query any
  multiplexer control socket.

The home is handled like any ordinary home with its own gateway.

Exact profile homes are unchanged: they still resolve to their root. That
holds for the default `.hermes` root and for a custom root that is a real
Hermes home (it has `config.yaml`). An ordinary home outside any profile
still has no multiplexer.

**Before the fix.** `named_profile_home(path)` checks `path` *or any
ancestor*. A nested home therefore passed the old `is None` test. Its
"multiplexer root" was then computed as `home.parent.parent`, which is a
directory inside the profile and not a Hermes root. Clients could probe or
start a gateway against the wrong root, and could take the profile's
served-profile verdict for an unrelated home.

## How it works

`hermes_cli/gateway_runtime_multiplex.multiplexer_root_for(home)` changes its
guard from `named_profile_home(home) is None` to
`named_profile_home(home) != home`. A comment notes that the ancestry helper
also recognizes descendants. `named_profile_home` itself (in
`hermes_constants`) is unchanged and still finds the owning profile of any
path under it, which other callers rely on.

Every multiplexer routing decision goes through `multiplexer_root_for`, so
they all get the fix. The callers are:

- `implied_host_root` and `multiplexer_serves_home` in the same module;
- `_served_by_multiplexer`, `_starting_multiplexer_root` and the ensure loop
  in `hermes_cli/gateway_runtime.py`;
- `_identify` in `hermes_cli/gateway_runtime_replace.py`;
- the s6 service path in `hermes_cli/gateway_runtime_service.py`;
- `_gateway_home` in `hermes_cli/webapp_launcher.py`.

## Code

- `hermes_cli/gateway_runtime_multiplex.py` (`multiplexer_root_for`, a
  one-line predicate change plus a comment).

## Tests

New file `tests/hermes_cli/test_gateway_runtime_multiplex_home.py`, with four
test cases. `Path.home` is patched to `tmp_path`.

- `test_exact_profile_home_and_nested_custom_home[.hermes]` and
  `[custom-root]`. For a home nested at `profiles/test/cache/scratch/pytest/home`,
  `named_profile_home(nested)` is still the profile. The exact profile
  resolves to its root. For the nested home, `multiplexer_root_for`,
  `implied_host_root` and `multiplexer_serves_home` are all `None`.
- `test_ordinary_home_has_no_multiplexer`.
- `test_nested_home_discovery_does_not_probe_ancestor`. Here
  `gateway_runtime_discovery.query_identify` fails the test if it is called,
  and `gateway_runtime._served_by_multiplexer(nested)` returns `None`.

These tests call the module functions directly. The ensure loop, the s6
service path, the replace and Webapp launcher callers, and a real gateway or
socket are covered only through the shared function. They are untested here.

**Implementation-owner results (reported by the implementer, not run for
this doc):**

- **Unit tests.** Before the fix, 3 failed and 1 passed (the ordinary-home
  case). After the fix, 35 passed, including bootstrap identity tests. With
  the old predicate restored (mutant check), 3 failed and 1 passed again.
- **Coach CLI.** Four synthetic coach CLI controls were run with homes under
  `.hermes/profiles/test/cache/scratch`, all on the same interpreter. All 4
  failed before the fix and all 4 passed after.
- **Full suite.** The full coach suite was still red because of unrelated
  lifecycle and live-source tests. No all-green claim is made.

Execution for the mirror snapshot is reported separately in the 1988 mirror
verification (`core/mirror-1988-verification.md`).

## Applying

Apply this patch as part of the 1988 snapshot replay
(`core/mirror-replay-1988.json` with `core/mirror-policy-1988.json`, see
[mirror-1988-features.md § Applying](mirror-1988-features.md#applying)). Its
change is a self-contained hunk in one function. It assumes the
`named_profile_home` semantics (path-or-ancestor, `.hermes` stop, real
profiles root) present at its source parent
`ba6a058cff179cd9e8d970ec0253e2cf09fb34e5`.

## Gotchas

- **Equality is exact path equality.** The check doesn't resolve paths. A
  profile home reached through a symlink or an unnormalized path (`..`,
  trailing components) is compared as given. Most callers pass
  already-resolved homes, but some don't. Untested here.
- **A nested home is now standalone.** If you meant a nested directory to
  share the profile's multiplexer, set `HERMES_HOME` to the profile home
  itself.
- `named_profile_home` still returns the owning profile for descendants. New
  multiplexer routing code must use the exact-equality guard, or call
  `multiplexer_root_for`, not the ancestry helper alone.

## Plugin boundary

There is no plugin boundary solution. Gateway discovery, ensure/start
targeting and service selection decide which daemon owns a home before
plugins load, and they run inside CLI/TUI/Desktop bootstrap. A plugin can't
intercept or correct that routing. The fix must stay in core.
