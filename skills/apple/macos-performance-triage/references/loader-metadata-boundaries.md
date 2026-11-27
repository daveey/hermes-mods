# Declared loader metadata versus runtime closure

When standalone Darwin system libraries return ENOENT, inspect the already-installed `dyld_info` usage before assuming cache tooling is absent. Its `-uuid -linked_dylibs -platform <exact paths>` can report cache-backed images; aliases may return the same UUID/dependencies, which does not prove distinct image identity.

Use `-all_dir <already inventoried root>` for extensionless-image discovery, but distinguish static archive members: retain architecture separately, map `archive.a(member.o)` to its backing archive pin, and never count archive objects or foreign-architecture slices as loaded libraries. Do not broaden discovery to unrelated installations.

Preserve full output; recover truncated metadata through one exclusive diagnostic gzip and verify the complete downloaded bytes. Utility metadata is not fresh file-hash binding, atomic capture, recursive shared-cache closure or observed imports. Keep per-executable/inherited-rpath candidates unresolved until their contexts are established. An offline extractor may accept incomplete sections; complete transport plus inventory coverage, not parser success alone, supports a complete snapshot claim.

A pinned source hash and matching cached module `__file__` do not authenticate the module's executed bytes. Branch fresh exact-byte execution from unresolved cache reuse. Classify source constants naming a file separately from an actual consumer read; declarations do not create runtime read edges.
