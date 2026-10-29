# doctor dashboard import probe launches like the runtime

Patches:

- `patches/0981-fix-doctor-the-dashboard-import-probe-launches-like-.patch`

On a store install the interpreter runs isolated and `hermes_bootstrap` supplies the dependency path, so the bare `sys.executable -c` probe reported "No module named ruamel" while the dashboard served 200.

## Behavior

The probe uses `runtime_command(code=...)`, the same launch as the update import probe.

## How it works

One call-site change in `doctor_platform.py`.

## Code

`hermes_cli/doctor_platform.py`.

## Tests

`tests/hermes_cli/test_doctor_web_dashboard_import.py` pins the runtime command and runs it for real.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.
