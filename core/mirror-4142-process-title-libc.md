# Darwin process-title libc by absolute path

Mirror snapshot 4142; ordinals 4126.

## Behavior

On macOS, the CLI's best-effort process-title fallback now loads `/usr/lib/libc.dylib` instead of the bare name `libc.dylib`. The following are unchanged: `use_errno=True`, the `pthread_setname_np(b"hermes")` call, and the blanket exception handling that makes a loader failure nonfatal. Linux still loads `libc.so.6` and calls `prctl`; Windows makes no call.

The reason given in the patch is that a bare loader-search name has no resolvable provenance, so strict code attestation reported `native-load:libc.dylib` as a blocking unknown during startup. An absolute path is reported as informational instead. No entry path (terminal, execute_code, tool, REST, Desktop) is changed beyond CLI startup.

## How it works

4126: `_set_process_title` in `hermes_cli/main.py` passes the absolute path in the Darwin branch. The attestation code's existing rule is untouched: unresolved names, invalid paths and names that alias core paths still block. The new tests replace `ctypes.CDLL` with a fake that emits the same `ctypes.dlopen` audit event, so no host library is loaded and no OS call is made.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_cli/main.py`
- `tests/hermes_cli/test_process_title_attestation.py`

Mail entries in the ordered series:

- 4126: [`e2e836ca72579d142e69636e9c9169018619720e`](patches/4126-fix-cli-identify-Darwin-process-title-libc-by-absolu.patch)

## Tests

`tests/hermes_cli/test_process_title_attestation.py` (new file), using the `run_probe` helper from the code-attestation tests:

- `test_process_title_native_provenance[Darwin|Linux|Windows]`: with `setproctitle` forced absent and a mocked `CDLL`, Darwin loads `/usr/lib/libc.dylib` with `use_errno=True`, calls `pthread_setname_np("hermes")`, adds no blocking `native-load:` entry, and records the path as informational and in the raw latched unknowns. Linux loads `libc.so.6` and calls `prctl(15, "hermes", 0, 0, 0)`. Windows makes no calls.
- `test_unresolved_and_invalid_native_names_still_block`: `libc.dylib`, `arbitrary.dylib`, a name with a NUL byte and a path through a symlink alias of the core root remain blocking (the NUL name yields `audit-error:ValueError`) and none are informational.
- `test_darwin_title_loader_failure_is_nonfatal`: if the loader raises `OSError` on Darwin, `_set_process_title` returns normally.

All of these use a mocked loader and a mocked platform name; no real Darwin loader was exercised.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- Whether the absolute path exists or loads on every macOS version is not tested; the patch cites the system library layout and relies on the existing nonfatal exception handling if loading fails.
- The attestation rules are not relaxed; only this one producer now names its library explicitly.
- No quiet-gate override and no live Mac behavior is claimed or tested.

## Plugin?

No. It is a one-line change to CLI startup code, and the attestation behavior it satisfies lives in core.
