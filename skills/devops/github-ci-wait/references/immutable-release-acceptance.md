# Immutable release acceptance

Before publishing a release explicitly required to be immutable, check `GET /repos/OWNER/REPO/immutable-releases`; a fixed tag, checksum manifest and no-overwrite publisher do not establish GitHub's `immutable` flag. With scoped authorization, `PUT` on that endpoint enables future release immutability. An existing mutable release can be moved to draft and republished without changing its tag or assets; compare exact asset IDs, names, sizes and digests before/after, then require `immutable=true`, `draft=false` and the intended prerelease flag. The lock is permanent for that publication even if the repository setting is later disabled. Never replace assets or move a tag to repair this gap.

References: https://docs.github.com/en/rest/repos/repos#enable-immutable-releases and https://github.blog/changelog/2025-10-28-immutable-releases-are-now-generally-available/
