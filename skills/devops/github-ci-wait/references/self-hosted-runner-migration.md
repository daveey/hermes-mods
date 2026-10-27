# Moving a repo's CI to self-hosted runners (escape an Actions billing lock)

On a personal account runners are registered per repo, so a new repo has none even when a sibling repo
already has self-hosted machines.

## Register the runner (Linux, no sudo)

1. Token: `gh api -X POST repos/OWNER/REPO/actions/runners/registration-token -q .token`.
2. On a host that already runs a sibling runner, copy its install instead of downloading:
   `cp -a bin externals config.sh env.sh run.sh run-helper.sh.template run-helper.cmd.template safe_sleep.sh svc.sh <new dir>/`,
   then `./config.sh --unattended --url https://github.com/OWNER/REPO --token <T> --name <host>-<repo> --labels <repo-label> --work _work --replace`.
3. Copy the sibling's user systemd unit (`~/.config/systemd/user/gh-runner-<x>.service`) with the dir/name
   substituted, `systemctl --user daemon-reload && systemctl --user enable --now <unit>`.
4. Confirm `gh api repos/OWNER/REPO/actions/runners` shows it `online` with the label.

Use repo-specific labels (e.g. `<prefix>-linux`) so two repos' jobs don't take each other's machines.
A macOS LaunchDaemon needs sudo on that host: run it through `run_in_user_terminal` with `ssh -t host 'sudo bash …'`.

## Edit the workflows

- Change every `runs-on` to the new labels, push, and confirm with the run's jobs' `runner_name` before telling
  workers CI counts as a gate again. `gh workflow run` each scheduled workflow once instead of waiting for cron.
- Multi-GB repo: don't full-clone (outlasts the tool timeout):
  `git clone --depth 1 --filter=blob:none --no-checkout <url> d && git -C d sparse-checkout set --no-cone .github && git -C d checkout main`.
- Steps that `sudo apt-get install` fail on a shared runner (no passwordless sudo). Branch on
  `if [ "$RUNNER_ENVIRONMENT" = "self-hosted" ]` and keep the hosted path unchanged. Install tools into the runner
  user's `~/.local/bin` once on the host (e.g. a static ffmpeg build) and `echo "$HOME/.local/bin" >> "$GITHUB_PATH"`;
  for Playwright use the bundled `playwright install chromium` + `--browser chromium` instead of
  `install --with-deps chrome` + `--browser-channel chrome`.

## Disposable Docker-capable capacity

1. Put the job-controlled daemon on an isolated node with bounded storage and an outer CPU/memory cgroup.
   Keep the lifecycle controller outside that daemon; reset only after the `--once` runner exits. Serialize
   each identity so replacement listeners cannot overlap.
2. Preload a clean base-image data-root, stop its daemon, and retain a root-only seed. Clone it with
   `cp --reflink=always -a` on XFS for each job; after completion stop the entire daemon control group and
   remove the active data-root. Recycling only the runner leaves child containers, volumes and build cache.
3. Before adding `docker-ci`, run a real build and child-container networking canary, then a second job
   proving image/container/workspace residue is absent. Exercise denied host-credential bind mounts and
   host rootful-socket access separately; an ordinary build does not prove those boundaries.
4. Verify the INSTALLED lease unit and its actual daemon endpoint before judging shutdown safety.
   Rootful `docker ps` says nothing about rootless jobs. Any runner restart, resize or drain (rootless or
   not) must withdraw its labels on GitHub, prove GitHub `busy=false` sustained over a settle window, then
   act; a drain also unregisters, awaits natural listener exit, and proves its job daemon empty. A frozen
   listener with no `Runner.Worker` child is NOT idle: the listener acquires the job before forking the
   Worker, so local probes (two idle checks, pidfd children) race job assignment and lose jobs. Treat inspection errors as unknown, never empty. A general-node
   checker present in the repository is not evidence that it runs on the Docker node.

## Pitfalls after the move

- Self-hosted checkouts persist between jobs, so anything the build rewrites in a TRACKED file stays dirty and a
  later `git pull --rebase` / commit-back step fails with "cannot pull with rebase: You have unstaged changes".
  The usual culprit is a committed `node_modules/` (platform-specific binaries like esbuild get replaced by
  `npm ci` on Linux). Diagnose on the host with `git status --short` in `_work/<repo>/<repo>`; fix by
  `git rm -r --cached` + `.gitignore`, never by cleaning the host.
- When a migration commit was made from a local checkout, check its `--stat` for accidental bulk adds
  (node_modules, scratch scripts) before calling it done.
- Diagnose saturation by label, not total queued runs: paginate the runners and each queued/in-progress run's
  jobs (`repos/OWNER/REPO/actions/runners`, `actions/runs/ID/jobs`), then group online/busy slots, queued job
  names and age by required custom label. General, Docker and observer lanes can saturate independently;
  extra general slots cannot consume `docker-ci` jobs. Check workflow concurrency and existing owners before
  provisioning. Preserve healthy work and current-head tests; use the superseded gate-only latch sweep in
  SKILL.md rather than cancelling an entire run merely because its commit has a newer descendant.
- PRs refused during the outage: `gh run rerun` replays the PR head's workflow file, which still has the old
  `runs-on`. Rebase onto `origin/main` and `--force-with-lease` push.
