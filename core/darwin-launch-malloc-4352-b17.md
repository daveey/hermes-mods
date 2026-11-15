# Space-efficient Darwin server allocation

Patches: **4329–4332** (4352 snapshot).

## Behavior

macOS serve, dashboard and foreground gateway launches request MallocSpaceEfficient=1 at exec time unless explicitly opted out. An operator-exported allocator value takes precedence. Hermes-injected handoff values do not leak to child commands.

## How it works

The stdlib-only launch_malloc module identifies server argv and supplies an exec environment delta with a private handoff marker. Bootstrap either carries it through venv exec or re-execs once so libmalloc consumes it. On the resumed launch the marker and injected setting are scrubbed before preparation can spawn children or fail into degraded startup.

The bootstrap module is included in packaging and upgrade-CI classification. Copied launcher, doctor, recovery and process-manager fixtures include the allocator dependency.

## Code

- `cli-config.yaml.example`
- `hermes_bootstrap.py`
- `hermes_cli/launch_malloc.py`
- `hermes_cli/resource_limits.py`
- `hermes_cli/venv_sync.py`
- `pyproject.toml`
- `scripts/ci/classify_changes.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_doctor_command_install.py`
- `tests/hermes_cli/test_launch_malloc_bootstrap.py`
- `tests/hermes_cli/test_launch_malloc_env.py`
- `tests/hermes_cli/test_source_launcher_publication.py`
- `tests/hermes_cli/test_update_recovery_closure.py`
- `tests/pm/test_startup_recovery.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Opt out with HERMES_MALLOC_SPACE_EFFICIENT=0 (also false/no/off); config is not loaded early enough for this decision. Non-Darwin and non-server commands are unchanged. A denied exec withdraws injected settings without breaking startup. Portable environment/fixture tests ship; real macOS allocation behavior was not measured here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
