# Plugin module eviction walks a snapshot of sys.modules

Patches:

- `patches/1046-fix-plugins-evict-plugin-modules-from-a-sys.modules-.patch`

A concurrent import no longer drops a random plugin at load.

## Behavior

- 1046: `_evict_modules` and the same pattern in `plugin_host_child`, `_launchers` and `plugin_dev` iterate a snapshot of `sys.modules`, so another thread importing no longer raises `dictionary changed size during iteration` and leaves the plugin unregistered.

## How it works

The live dict is copied before iteration; eviction then deletes by name.

## Code

`hermes_cli/plugins_loader.py`, `hermes_cli/plugin_host_child.py`, `hermes_cli/_launchers.py`, `hermes_cli/plugin_dev.py`.

## Tests

`tests/hermes_cli/test_plugins_loader.py` loads a directory plugin end to end while another thread churns `sys.modules`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.
