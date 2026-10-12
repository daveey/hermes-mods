---
name: macos-disk-space-cleanup
description: Use when a Mac is low on disk. Find, clean, ask first.
---

# macOS disk space: investigate and free space

## Procedure

1. **Measure the right volume.** Run `df -h / /System/Volumes/Data`. User data lives on the Data volume (`disk3s5`), so ignore the sealed `/` capacity figure. `diskutil apfs list` shows usage per volume, including Preboot and VM.
2. **Find the big consumers** in a single call: `du -sh ~/* ~/.[!.]* | sort -rh | head -25`. Then drill into `~/Library/{Application Support,Containers,Caches,Group Containers}`, `~/.local/share`, `~/.cache`, `~/code`, `/Applications`, `/opt`, `/usr/local` and `/private/tmp`.
   - `du` usually accounts for 30–40 GB less than the volume reports. That gap is system data, purgeable space or snapshots that can't be read without sudo. Mention it, but don't chase it.
   - For VM disk images, check `du -sh`, not `ls -l`. Files like Docker.raw and podman `*.raw` are sparse: `ls` shows the maximum size (30–64 GB), while `du` shows what is actually on disk.
3. **Rule out a runaway writer before cleaning anything.** Sample free space over 60–90 s, with no cleanup running at the same time, because concurrent deletes hide the trend:
   `a=$(df -k /System/Volumes/Data|tail -1|awk '{print $4}'); sleep 90; b=...; echo $(( (b-a)/1024 ))MB`
   If space is shrinking, find recent large writes: `find ~ /private/tmp -xdev -type f -mmin -10 -size +20M -exec ls -la {} \;`. Swings of a few hundred MB from VM disks, sqlite files and app update staging are normal. Report it as a runaway only if it keeps growing.
4. **Clear safe, rebuildable items without asking.** Run each command separately so one hang can't block the rest:
   - `brew cleanup -s --prune=all`, `npm cache clean --force`, `uv cache clean`, `pip cache purge`
   - `rm -rf ~/Library/Caches/<app>` for browser, Codex, SwiftPM and Homebrew caches
   - Empty the Trash: `find ~/.Trash -mindepth 1 -maxdepth 1 -exec rm -rf {} +`
   - Old large logs and tarballs in `/private/tmp` owned by the user: `find /private/tmp -maxdepth 1 -type f \( -name '*.log' -o -name '*.tar.gz' \) -mtime +1 -size +5M -user $USER -delete`
   - `podman image prune -f` (dangling images only)
   - App log dirs that grow huge, e.g. `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/Logs` (5.7 GB seen). Delete files older than a day: `find <dir> -type f -mtime +1 -delete`.
   - `~/.cache/huggingface/hub/tmp*`: leftovers from aborted downloads. Check `pgrep -f huggingface` first.
   - Leftovers from retired services: LaunchAgent plists (including `LaunchAgents/disabled/`), their `~/Library/Logs/<label>` dirs and helper scripts. Confirm with `launchctl list` that nothing is loaded.
5a. **Check for an external drive** (`diskutil list external`, `df -h | grep Volumes`). If one has free space, offer to *move* stale repos and archives there rather than delete them. Find stale repos by last commit date with `git log -1 --format=%cs`.
5. **Ask once before deleting app state, VMs or work dirs.** Use a single multi-select `clarify` question. Each option gives the size and what happens if it's deleted, e.g. 'redownloads when used', 'rebuilt on next CI job', 'old images and volumes lost'. Never touch the user's own project data, source trees or running VM disks without asking.
6. **Re-run `df`** and report the before and after free space, what was deleted, what failed and the fix, what is still large, and what will fill back up.

## Daily automation
A Hermes cron job (`ea9100b0961a`, 04:00 daily, no_agent, deliver=all) runs `~/.hermes/scripts/disk_cleanup.sh` and logs to `~/.hermes/logs/disk_cleanup.log`. It prints output only when free space is under 25 GB, has dropped by 15 GB or more since the last run, or the cleanup freed 2 GB or more. When adding a new obvious cleanup target, extend that script rather than doing a one-off cleanup.

## Pitfalls

- **Never chain cleanup commands with `;` in one long call.** One hang (brew waiting on a lock, uv waiting on its lock) eats the whole tool timeout and nothing after it runs. macOS has no `timeout` binary, so give each risky step its own call or run it in the background.
- **uv lock timeout:** another uv process holds `~/.cache/uv/.lock`. Skip it and tell the user to run it later. Don't kill the other process.
- **npm EACCES on `~/.npm/_cacache`:** some cache files are owned by root. The fix is `sudo chown -R $(id -u):$(id -g) ~/.npm`. Hand that command to the user, since sudo is usually non-interactive here.
- **Orphaned Docker Desktop disk:** if `/Applications/Docker.app` is gone and `docker context ls` shows podman as the active context, then `~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw` is leftover. It still needs the user's OK, because old volumes may hold data.
- **CI runner `_work` dirs:** check `pgrep -f Runner.Worker` first. Deleting a workspace while a job is running breaks that job.
- **App update or staging dirs:** delete only entries older than a day (`find ... -mindepth 1 -maxdepth 1 -type d -mtime +1`). The newest one may be in use.
- **Re-run `df` at the start of every turn.** Free space on this machine moved by 20 GB between turns because of new babybear worktrees and builds, an iPhone IPSW download, and system data. Don't report freed space against an old baseline.
- **iPhone IPSW** (`~/Library/iTunes/iPhone Software Updates/*.ipsw`, ~6 GB) can be deleted and re-downloads, but skip it while `MobileDeviceUpdater` is running.
- **Moving repos to the external drive:** run a script in the background that does `rsync -a`, checks file count plus `rsync -anc` for zero diffs, and only then runs `rm -rf`. It also skips any dir that has a process cwd inside it.
- **Unanswered clarify (timed out) means no consent.** Don't act on any of its options. List them in the report so the user can pick later.
- **Don't delete a running podman machine's disk.** Check `podman machine list`.

For big consumers seen on this user's machine, see `references/known-consumers.md`.
