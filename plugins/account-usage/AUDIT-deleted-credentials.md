# Audit: deleted credential reconciliation

The added `agent/credential_pool.py` digest permits a persistence-only change:
a successful locked store merge discards known removed rows from the runtime
snapshot and clears a selection that no longer exists. New ephemeral seeded
rows without a persisted baseline remain available. The method signatures,
selection hook points, disablement gates and refresh boundaries are unchanged.

The corresponding store writer uses per-ID token bases to reject stale rows
absent from its locked disk snapshot, preserving both genuine new additions and
concurrent additions. Existing accepted digests are retained for mixed rollout.

Core regression coverage exercises a peer process deleting rows before a stale
cooldown flush, repeated flushes, concurrent login, tokenless rows and explicit
addition. The existing profile-singleton tests cover unsaved ephemeral rows.

## Revision: write-target scoping

The follow-up digest only changes how `load_pool` seeds the deletion baseline: a
profile reading a provider through the global-root fallback no longer treats
those root rows as known rows of its own store (they are additions there), and
a store with no `credential_pool` section (missing or corrupt) is no evidence of
peer deletions. Signatures, selection hook points, disablement gates and refresh
boundaries are unchanged. The earlier digest stays accepted for mixed rollout.
