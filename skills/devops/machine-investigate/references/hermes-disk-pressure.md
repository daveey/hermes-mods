# Hermes host disk pressure (Linux hub)

Read when a Hermes host (not a Mac) is low on disk, including the "Low disk · /autoprune on|off|later" toast.

## Order

1. **Find open disk cards first.** Search every board for open cards whose title mentions disk, gc, prune or retention.
   An approved cleanup card can carry an exact scope (paths it must never touch), and a blocked capacity ask may
   already exist. Comment your findings on those cards; don't open duplicates.
2. **Check whether the toast's fix would help at all.** The toast comes from `hermes_cli/prune_advisor.py`. Run
   `python -c 'from hermes_cli import prune_advisor as p; a=p.advise(); print(a)'` in the hermes-agent venv.
   `prunable_sessions=0` means `/autoprune` frees nothing: something else is filling the disk.
3. **Get sizes without timing out.** Never run `du` or `find` over `/` or `$HOME` in a foreground call: on a
   ~400 GB root it runs past the 300 s tool limit. Background `collect.py report` (with `notify`), then `du` only
   the large subtrees it names, at depth 2 and under `timeout`. The big Hermes subtrees are
   `~/.hermes/kanban/{workspaces,evidence}`, `~/.hermes/cache/{scratch,gc-trash}` and `~/.hermes/installs`.
4. **Map each directory to its owning card's status.** Workspace and evidence directories are named by task id.
   Read the `tasks` table from `~/.hermes/kanban.db` and every `~/.hermes/kanban/**/kanban.db`, opened
   read-only (`file:...?mode=ro`). A directory with no task row is unknown, not abandoned.
5. **Find live writers** by sampling `/proc/<pid>/io` `write_bytes` twice, 20 s apart. Directory mtimes alone
   don't show which process is writing.

## Worker nodes (worker-N)

- Reclaim is the `hermes-worker-disk-pressure` systemd timer (`~/bin/worker-disk-pressure`, code in
  `~/.hermes/repos/hermes-state/hosts/hermes-workers/`). It only reclaims below `--warn-gib` (default 30) and
  otherwise keeps done-card workspaces for the 7-day retention. Map `kanban/workspaces/<tid>` to status with
  `~/.hermes/kanban.db`, `kanban/boards/*/kanban.db` AND `kanban/sync-shadow/*.db` (hub-board rows live only in
  the shadows; without them most dirs look row-less).
- **Never run `worker-disk-pressure` (or anything that calls core `remove_archived`) from a Kanban worker
  shell.** The worker's descendants carry `HERMES_DELEGATED_CHILD_CONTEXT`, so every detach fails with
  `error:PermissionError` AFTER the archive is built: nothing is freed, and each tree leaves a complete archive
  with a stale `detach-*.json` intent (custody then refuses it `upload:recovery_intent`). If that happened:
  confirm each intent's `original` exists and its `detached` path does not, then move those archive dirs out of
  `~/.hermes/archive/scratch/<board>/` (recursive rm is blocked headless; moving into a task-named
  `cache/scratch` dir lets the 24 h prune drop them). Don't bypass the guard via `systemd-run`.

## Producers the existing GC misses

- **Test-run homes in `cache/scratch`** (for example `coach-collection-*`, ~400 MB each). A conftest that uses a
  `TemporaryDirectory` as `tempfile.tempdir` puts the pytest basetemp inside it, and the directory leaks when the
  run is killed. Only the 24 h idle scratch prune removes it. Delete one only after checking that no process has
  it as cwd or an open fd. Fix it at the source with a pid-tagged name plus a sweep of dead siblings.
- **`cache/gc-trash`**: `purge_trash` only runs when that GC pass moved something, so old entries can sit there.
  Its contents are already marked for deletion.
- **`kanban/evidence/<task>`**: nothing cleans it up. Before deleting a redundant copy, such as a `.gz` next to
  the decompressed snapshot, verify that the kept copy's sha256 still matches `manifest.json`. Then comment on
  any open card that references the path.

Most of the remaining space is usually live work: research artifacts, open-card workspaces, models and containers.
When cleanups keep landing above ~80% used, the lasting fix is capacity (a David-only spend ask), not another
deletion pass.

## Growing the hub root volume (once David approves)

This can be done online, with no reboot.

1. The instance role `hermes-hub` cannot call `ec2:ModifyVolume`. Run the resize with the auto-tier scope, pinned
   to the one volume:
   `token-broker exec --scope aws.write.hermes --reason "..." --resource arn:aws:ec2:<region>:<acct>:volume/<vol> -- aws ec2 modify-volume --region <region> --volume-id <vol> --size <GiB>`.
2. Don't poll `describe-volumes-modifications`. The instance role is denied that call too. `lsblk -no SIZE /dev/nvme0n1`
   shows the new size within seconds.
3. `sudo parted -s /dev/nvme0n1 unit s print`: the root partition must be the last one on the disk (on the hub it is
   p1, after p14–p16). Then run `sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1` (ext4; check it
   with `findmnt -no FSTYPE /`).
4. Post the `df -h /` before/after on the capacity card. Then archive that card: unblocking it would dispatch a
   worker to repeat the resize.
