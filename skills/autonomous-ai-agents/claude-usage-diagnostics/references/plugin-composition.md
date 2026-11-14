# Verify account-selection and rotation plugins together

- Test actual configured PluginManager discovery with both account-selection and fast-rate-limit-fallback enabled, not just each plugin's isolated suite. Wrapping a shared recovery function can make another plugin's fingerprint guard refuse installation even when core source is unchanged.
- Require combined recovery and unload/reload tests before recommending activation: verify selected-account wire identity, cooldown preservation, long-reset rotation/fallback, and both installers active. Never bypass compatibility by indiscriminately unwrapping `__wrapped__` or accepting new hashes; explicitly audit supported wrapper composition and reject foreign wrappers.
- Distinguish on-disk implementation, fresh-process discovery, and activation in the owning live gateway/compute child. A mocked browser harness proves UI interactions, not native-app deployment or real provider dispatch.
