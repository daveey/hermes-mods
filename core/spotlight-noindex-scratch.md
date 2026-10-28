# Spotlight-free scratch and uv cache on macOS

Patch **0664** (`c04d111c0e`).

## Behavior

- On macOS, `get_scratch_dir()` keeps the scratch dir's real contents in `<home>/cache/scratch.noindex` and makes
  `<home>/cache/scratch` a relative symlink to it. Spotlight skips any directory whose name ends in `.noindex`, so
  the TMPDIR churn of every Hermes process (pytest tmp dirs, uv/pip builds, review worktrees) is no longer indexed.
  `TMPDIR`, `HERMES_SCRATCH_DIR` and every documented `cache/scratch` path stay the same.
- An existing real `cache/scratch` directory migrates with one atomic `rename` followed by the symlink, so open
  files and working directories inside it keep working. A dangling link gets its target recreated. When both a real
  `cache/scratch` and a `cache/scratch.noindex` exist, nothing is moved or merged.
- PM's machine uv cache (`uv_cache_dir()`, `<root>/cache/uv`) gets the same treatment (`cache/uv.noindex`).
- `hermes doctor`'s unpruned-cache warning treats `cache/scratch.noindex` as the reaped scratch dir.
- Other platforms, and `HERMES_SPOTLIGHT_INDEX_CACHE=1`, keep a plain directory.

## Why

On one MacBook, `cache/scratch` held 339,508 Spotlight-indexed items and `cache/uv` 39,196, and Spotlight
(`mds_stores` + `mds` + `mdworker_shared`) used about a core around the clock. macOS's own per-user temp dir is not
indexed. Pointing TMPDIR into the home folder made all that temp churn indexing work. `.metadata_never_index`
marker files inside the home folder are not a documented exclusion. Renaming to `*.noindex` needs no Spotlight setting and no sudo.

## Code

`hermes_constants.py` (`ensure_spotlight_noindex_dir`, called from `get_scratch_dir`), `pm/packages.py`
(`uv_cache_dir`), `hermes_cli/doctor_state.py` (`_PRUNED_CACHE_DIRS`).

## Tests

- `tests/test_scratch_dir.py::TestSpotlightNoindexScratch`: fresh home, migration of a real dir with an open file
  plus pruning through the link, dangling-link repair and conflict left alone, non-darwin and opt-out.
- `tests/pm/test_uv_cache.py::test_uv_cache_dir_lives_in_a_spotlight_noindex_dir_on_macos`.
- `tests/hermes_cli/test_doctor_cache_hogs.py::test_unpruned_cache_hogs_skips_the_noindex_scratch_dir_and_symlinks`.

## Gotchas

- Anything that `realpath`s a scratch path sees `scratch.noindex`; the scratch reaper compares realpaths on both
  sides, so it is unaffected.
- The migration runs lazily on the first `get_scratch_dir()`/`uv_cache_dir()` call after the update. A host can also
  be migrated by hand: `mv scratch scratch.noindex && ln -s scratch.noindex scratch` in `<home>/cache`.
