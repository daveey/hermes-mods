# Unittest runs cannot touch the live Hermes home; config backups keep a last-full anchor

Patches:

- `patches/1573-daveey-Prevent-unittest-discovery-from-overwriting-l.patch` (`02b548294a`, `Prevent unittest discovery from overwriting live config`)
- `patches/1574-daveey-Read-retained-config-backups-with-BOM-toleran.patch` (`eb83fcf3c3`, `Read retained config backups with BOM tolerance`)
- `patches/1575-daveey-Narrow-backup-parsing-failures-to-IO-and-YAML.patch` (`4b82a74b17`, `Narrow backup parsing failures to IO and YAML errors`)
- `patches/1577-daveey-Refuse-unittest-runs-of-the-core-suite-agains.patch` (`2745a3e2eb`, `Refuse unittest runs of the core suite against the live Hermes home`)

`python -m unittest` never loads `tests/conftest.py`, so it skips pytest's temporary `HERMES_HOME`, credential stripping and live-state write fences. In the incident behind these patches, one test (`test_image_generate_schema`) wrote `config.yaml` into the inherited home and wiped a gateway's live config. In a later incident, fixture failures (a fake EACCES) reached the live `errors.log` and were filed as real defects.

## Behavior

- **Repository discovery refuses (1573).** `tests/__init__.py::load_tests` raises `RuntimeError("Use scripts/run_tests.sh; unittest discovery bypasses test isolation")`, so `python -m unittest discover` from `.` or `tests` stops before it imports child modules.
- **Any unittest run against a live home refuses (1577).** When the process was started as `python -m unittest`, importing the `tests` package raises `SystemExit` before any child module is imported. This applies when `HERMES_HOME` is unset, is the real state root, is a profile, or is nested anywhere under the root (for example `<root>/cache/scratch`). It covers named modules, discovery below `tests/`, and repository discovery. The message suggests `scripts/run_tests.sh <paths>` or `HERMES_HOME=$(mktemp -d -p /tmp) python -m unittest <modules>`. pytest, and code that only imports `tests` (evals, desktop e2e seeds), is unaffected. An isolated unittest run still works and still logs its warnings to the isolated home.
- **The offending test owns its home (1573).** `TestDynamicParamGating.test_krea_3_advertises_edit_wording_and_its_own_controls` now writes its config in a `TemporaryDirectory` under `patch.dict(os.environ, HERMES_HOME=...)`.
- **Backup retention keeps one pre-collapse copy (1573).** For `reason == "good"`, `backup_config` exempts one anchor from rotation. The anchor is the backup with the most top-level keys, kept only when it has at least 10 keys and the newest copy has fewer than half as many. With this rule, repeated loads of a valid but collapsed config cannot rotate out every full copy. When the config regains at least half the sections, normal `keep` rotation resumes and the anchor is deleted. Only retention changes: `load_newest_good_backup` recovery selection is untouched.
- **1574** reads backups with `utf-8-sig`, so a file with a BOM counts its keys instead of scoring 0. **1575** narrows the per-file parse guard from `Exception` to `(OSError, UnicodeError, YAMLError)`, so other errors propagate.

## How it works

`_running_under_unittest_cli()` checks `sys.modules["__main__"].__spec__.name` in `{"unittest", "unittest.__main__"}`. `_live_hermes_home()` follows the same rule as conftest's `_hermes_home_points_at_production`. The root comes from `hermes_state_guard._real_platform_state_root()`, falling back to `~/.hermes`. `HERMES_HOME` counts as live when it is unset, cannot be resolved, or resolves under that root. `_last_full_good_backup` parses each candidate with `utils.fast_safe_load` and scores each one as `len(doc)` when the result is a dict, else 0. Ties go to the newest copy.

## Code

`tests/__init__.py`, `hermes_cli/config_backups.py` (`backup_config`, `_last_full_good_backup`), `tests/tools/test_image_generate_schema.py`.

## Tests

In the patches; not run for this mirror.

- `tests/test_unittest_safety.py`: `test_explicit_image_unittest_preserves_inherited_home` (named unittest run leaves a sentinel config untouched); `test_repository_unittest_discovery_refuses_before_test_writes[.|tests]`. Both use a disposable home: under 1577 the sentinel home sits outside the faked root, so it counts as isolated.
- `tests/test_unittest_home_isolation.py`: `test_unittest_against_production_home_refuses_before_writes[named|discover_below_tests × unset|production_root|production_profile]` (non-zero exit, the `mktemp` hint, byte-identical canary home); `test_unittest_with_isolated_home_runs_and_keeps_warning`; `test_non_unittest_importers_of_tests_package_still_work`; `test_production_logging_still_records_warnings` (control).
- `tests/hermes_cli/test_config_backups.py::test_good_rotation_retains_last_full_config_after_collapse`: 20-key config, 8 collapsed writes with `keep=2`, then the anchor survives with ≤ 3 kept, and restoring the config retires it.
- Untested: the BOM read (1574) and the narrowed exception set (1575) have no dedicated test. The "nested under root" case (for example a scratch dir) has no dedicated parameter.

## Applying

Depends on the preceding series (the existing `config_backups` module, `hermes_state_guard`, conftest isolation). Apply the full ordered series per [README.md](README.md) "Applying". Standalone application of these four is not certified.

## Gotchas

- A unittest run inside a Hermes shell whose `TMPDIR` is under the state root is refused. Use `mktemp -d -p /tmp`, as the message says.
- The guard checks only the unittest CLI. A custom runner that imports test modules directly is not covered; use `scripts/run_tests.sh`.
- The anchor counts only top-level keys. A collapse that keeps the top-level keys but empties their contents is not detected.

## Plugin?

No. Test-harness and config-backup core changes.
