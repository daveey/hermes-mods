# Large disk consumers seen on this machine (228 GB disk)

Check sizes before acting, since they change.

| Path | Typical size | Safe to delete? |
|---|---|---|
| `~/Library/Application Support/Claude/vm_bundles` | ~10 GB | Ask first. Claude desktop's VM bundle; redownloads when used |
| `~/Library/Containers/com.docker.docker/.../Docker.raw` | ~7 GB on disk | Ask first. Orphaned: Docker Desktop is uninstalled and docker runs through podman |
| `~/.local/share/containers/podman/machine` | ~7 GB | Keep. This is the running podman VM |
| `~/.local/share/babybear-ci-runner{,-2}/_work` | 3–5 GB each | Ask first. Rebuilt on each CI job; check for `Runner.Worker` before deleting |
| `~/Library/Application Support/BabyBear/{shared,rollout-staging,pod/updates}` | 6 / 2 / 1 GB | `shared` is live app data. Only old staging dirs in `pod/updates` can be cleaned |
| `~/Library/Application Support/Google/Chrome` | ~7 GB | User's Chrome profile; leave it |
| `~/.cache/{huggingface,codex-runtimes,puppeteer}` | ~3 GB | Ask first. Can be redownloaded |
| `~/code` | ~29 GB | User's source code. Never delete it; at most suggest pruning build dirs |
| `~/.hermes.empty-bak-*` | <1 GB | Ask first. Hermes backup |
