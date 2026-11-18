# Nix cache verification

On Blacksmith, inspect runner setup for transparent cache before treating an empty `gh cache list` as failed save. Its colocated actions/cache store is separate from GitHub's cache quota and Docker gha exports. Prove save and survival with exact key/upload and subsequent restore logs; report budgets separately.

Before granting actions:write for cache-nix purge, inspect the pinned action: a purge exception may abort save while only warning. Removing optional purge can preserve read-only tokens.

A normal fixed-output build can accept stale cached output because its path depends on the declared hash. Materialize then `nix build --rebuild`; bound the whole validation below the job deadline. Test with real Nix: seed correct output, mutate input without changing hash, confirm ordinary build passes from cache but the shipped validator rejects it. A minimal fixed-output fixture proves this semantic boundary, not full package-manager integration.
