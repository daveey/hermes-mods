# Update: backup progress and copy-on-write state.db clone

Mirror snapshot 5406; ordinals 5369, 5370, 5371, 5372, 5373, 5374.

Entries:

- **5369**: feat(update): show backup progress and clone state.db before a Desktop update
- **5370**: test: forward HERMES_TEST_REFLINK_DIR through run_tests.sh
- **5371**: fix(update): keep main.ts within its size cap for backup progress
- **5372**: fix(update): the clone snapshot never checkpoints state.db on close
- **5373**: fix(update): review findings on the pre-update backup progress
- **5374**: fix(i18n): French, German and Spanish copy for the update backup progress

## Behavior

- **5369**: `backup_sqlite.py` takes a short write lock, clones the database and WAL (clonefile on macOS, FICLONE on btrfs/xfs), then lightly verifies; failures fall back to the page copy with progress; JSON progress lines on stderr; `HERMES_PREFLIGHT_CLONE=0` disables the clone. Desktop shows backup progress.
- **5370**: test runner forwards a reflink directory variable.
- **5371**: size-cap refactor in the desktop main process.
- **5372/5373**: the clone never checkpoints on close; clone skipped on Python <3.12 or non-cloning filesystems; lock-wait event; stuck snapshot is killed after a grace.
- **5374**: French, German and Spanish copy.

## How it works

- `hermes_cli/backup_sqlite.py`; desktop updater, overlay and i18n files.

## Code

Shipped non-test paths (union over these entries):

- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/updater/checkout.ts`
- `apps/desktop/electron/updater/state-db-preflight.ts`
- `apps/desktop/src/app/updates-overlay.tsx`
- `apps/desktop/src/global.d.ts`
- `apps/desktop/src/i18n/en.ts`
- `apps/desktop/src/i18n/overlay-gaps.json`
- `apps/desktop/src/i18n/types.ts`
- `apps/desktop/src/store/updates.ts`
- `hermes_cli/backup_sqlite.py`
- `locales/_keys.desktop.json`
- `scripts/run_tests.sh`
- `apps/desktop/electron/updater/checkout-source.ts`
- `apps/desktop/src/i18n/de.ts`
- `apps/desktop/src/i18n/es.ts`
- `apps/desktop/src/i18n/fr.ts`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `apps/desktop/electron/updater/checkout-handoff.test.ts`
- `apps/desktop/electron/updater/state-db-preflight.test.ts`
- `apps/desktop/src/app/updates-overlay.backup.test.tsx`
- `tests/hermes_cli/test_backup_preflight_progress.py`
- `tests/hermes_cli/test_backup_preflight_staging.py`
- `apps/desktop/electron/updater/checkout-backup-progress.test.ts`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- A corrupt database is backed up as-is on the clone path.
- Clone tests skip on filesystems without reflink.

## Why core instead of a plugin

The snapshot runs before a core update and reads core's own store.
