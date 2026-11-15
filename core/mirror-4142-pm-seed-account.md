# PM seed account resolution under a fake HOME

Mirror snapshot 4142; ordinals 4064, 4065.

## Behavior

- `pm.environments.account_home_root()` now finds the account's default Hermes home through the passwd-backed resolver instead of following the `HOME` environment variable. A run with `HOME=<scratch>` and no `HERMES_HOME` therefore still borrows the real account's cache, tool entries and source snapshots by hard link. Before, the scratch data root was compared with itself and borrowing was disabled.
- `HERMES_DATA_DIR_SUFFIX` (for example `-canary`) is honored by that resolver: the account home is `.hermes` plus the suffix.
- `HERMES_PM_ACCOUNT_SEED=0` still opts out. With it set, `account_home_root()` returns `None` and the uv cache path stays under the active home.
- The desktop toolchain bootstrap environment now sets `HERMES_PM_ACCOUNT_SEED=0`, so build-owned caches and dependency state do not borrow the invoking account's.
- The patch message reports a clean fake-HOME run of `test_account_seed.py` dropping from 570431093 to 14156475 private logical bytes. That is the author's own measurement and is not reproduced here.
- Tests only: PM and shared-admission fixtures pin the account home so they stay isolated from the real account (4064, 4065).

## How it works

- 4064: `account_home_root()` calls `hermes_constants.account_default_hermes_home()` in place of `_get_platform_default_hermes_home()`. The samefile, device and opt-out gates are unchanged. The docstring now names `HOME=<scratch>` alongside `HERMES_HOME=<scratch>`.
- 4064: the tests monkeypatch `hermes_constants._account_home` next to `Path.home` so the passwd lookup returns the test directory. The PM `conftest.py` isolation fixture does the same.
- 4064: fixtures that run copied-install subprocesses, and the cold-manager-build test, set `HERMES_PM_ACCOUNT_SEED=0` so they do not borrow the real account.
- 4065: the `local_toolchain` fixture in the build-consumer tests gets the same `_account_home` pin. The patch message says three real-home guard failures appeared once account cache sharing stopped following `HOME`. The guard itself is unchanged.

## Code

- `pm/environments.py`
- `scripts/bundles/desktop_toolchain.py`
- `tests/hermes_cli/test_web_memory_provider_setup_install.py`
- `tests/pm/conftest.py`
- `tests/pm/test_account_seed.py`
- `tests/pm/test_plugin_survival_contract.py`
- `tests/pm/test_startup_recovery.py`
- `tests/pm/test_worker.py`
- `tests/scripts/test_bundle_native.py`
- `tests/scripts/test_pm_build_consumers.py`

Mail entries in the ordered series:

- 4064: [`9eb1e32c8dda2b6d9d8825aed33f251d64946d1e`](patches/4064-daveey-pm-resolve-seed-account-from-passwd-under-a-f.patch)
- 4065: [`3d5ff148fa4d624660f92a9b61405f14f26966b3`](patches/4065-test-pm-isolate-build-consumer-account-cache-daveey.patch)

## Tests

- `tests/pm/test_account_seed.py::test_fake_home_without_override_uses_passwd_account_cache` (new, parametrized with suffix `""` and `"-canary"`): with `HOME` and `Path.home` pointing at a fake directory, `HERMES_HOME` unset and the passwd account pinned elsewhere, `account_home_root()` equals the account's `.hermes<suffix>` and `uv_cache_path()` equals its `cache/uv`. With `HERMES_PM_ACCOUNT_SEED=0`, `account_home_root()` is `None` and the uv cache path is under the fake home.
- `tests/pm/test_account_seed.py::test_no_account_home_means_no_borrowing` (changed): also pins `_account_home` to a nonexistent directory; still asserts `account_home_root()` is `None`.
- Fixture-only changes, with no new assertions: `tests/pm/conftest.py` (`isolated_machine_home`), `tests/pm/test_plugin_survival_contract.py` (`admission_env`), `tests/pm/test_startup_recovery.py` (`isolated_machine_home`, opt-out env), `tests/pm/test_worker.py::test_cold_manager_build_does_not_bootstrap_a_worker`, `tests/hermes_cli/test_web_memory_provider_setup_install.py::test_setup_admits_real_provider_union_and_keeps_selection_on_failure`, `tests/scripts/test_bundle_native.py::test_native_dispatch_child_environment`, and the `local_toolchain` fixture in `tests/scripts/test_pm_build_consumers.py`.
- No test is listed for the `desktop_toolchain.py` opt-out; that path is untested in these patches.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- Tests that build their own fake HOME now also need to pin `hermes_constants._account_home`, or the resolver reads the real passwd account. 4064 and 4065 add that pin to the fixtures they touch; other fixtures are not covered.
- Hard-link borrowing still requires the same filesystem and the existing samefile/device gates; this change only fixes which home counts as the account.
- Desktop build bootstraps opt out of borrowing entirely; they do not share the account uv cache.
- Audit claims in the patch message (`_account_seed`, `account_environment`/`_snapshot_donor`, `PythonEnvironment._compile_bytecode` inheriting the corrected root) are not backed by code changes in these patches.

## Plugin?

No. The change is to a core path-resolution function and to core build and test fixtures, which a plugin cannot reach.
