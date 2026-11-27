# Dependency mirror proof portability

Use when keying, restoring or reusing dependency-only mirror proof bundles across CI jobs and runs.

Key dependency-only bundles on their real compatibility inputs (pinned toolchains,
lockfiles, setup logic and normalized actual Maven version), not the whole hosted
`ImageVersion`. GitHub serves multiple Ubuntu image versions concurrently, even
between preparation and proof jobs in the same run; whole-image keys can make
valid independently stored bundles impossible to restore reliably. Strip ANSI
formatting and installation paths from Maven version identity, retain actual
version changes as misses, and test both image-rollout stability and tool-version
invalidation. Never relabel old archives under a new input key to hide a proof
miss: rerun reviewed preparation and validate new provenance/digests.

When a mirror runbook explicitly permits input-addressed reuse after a clean base
merge, compare the complete compatibility-input pattern set, not only root locks.
If every input is unchanged, preserve the original archive names, manifest source
SHA, actual preparation run/artifact IDs and digests in the new isolated candidate's
notes; never rewrite provenance or assign different keys. Independently validate
the source delta and run the actual denied-registry proof on the NEW exact head.
Source-only merges need not force redundant artifact downloads/publication loops,
but old proof success never establishes new-head acceptance.
