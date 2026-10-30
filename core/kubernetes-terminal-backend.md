# Kubernetes terminal backend: one pod per session

Patches: none yet. Core PR (open, not merged): https://github.com/daveey/hermes/pull/242 (two commits on
`wt/k8s-terminal-backend`). The patch numbers get assigned at the next mirror once it lands on the fork's `main`.

A built-in `terminal.backend: kubernetes`. Each Hermes session gets its own pod in a namespace the profile's
config names; commands run through the pod exec API. It is built for a cluster where Hermes holds only a
narrow ServiceAccount: pods (create/get/list/watch/delete), `pods/exec` and `pods/log` in one namespace, and
no PersistentVolumeClaim rights.

## Behavior

- Config lives only under the profile's `terminal.kubernetes`; the namespace never comes from the model or a
  command. Keys: `namespace` and `image` (required), `cpu` (`"1"`), `memory` (`"2Gi"`), `ephemeral_storage`
  (`"10Gi"`), `idle_timeout` (1800 s), `kubeconfig`, `context`, `labels`, `workspace_pvc`, `workspace_subpath`,
  `run_as_user` / `run_as_group` / `fs_group` (1000 each), `node_selector`, `tolerations`.
- Auth: an explicit `kubeconfig` path wins; otherwise in-cluster config, else `KUBECONFIG` / `~/.kube/config`,
  with the optional `context`.
- Pods: one per (profile, session), named `hermes-<profile>-<10 hex of sha256(session)>` (DNS-1123, at most 63
  chars), labelled `app.kubernetes.io/managed-by=hermes` and `hermes/profile=<profile>` plus the configured
  labels. A running pod is reused; a missing or failed one is (re)created (a terminating one too, untested). Hermes waits up to 300 s
  for Ready and fails fast with `EnvironmentConnectionError` on image-pull and container-config errors.
- Pod hardening: `runAsNonRoot` with the configured uid/gid/fsGroup (uid 0 is refused),
  `allowPrivilegeEscalation: false`, all capabilities dropped, `RuntimeDefault` seccomp,
  `automountServiceAccountToken: false`, requests = limits for cpu, memory and ephemeral storage, optional
  `nodeSelector` / `tolerations`. PID 1 is a `sleep` loop that exits on TERM, so deletes are quick.
- Workspace: `/workspace` (the default cwd and `$HOME`) is the existing claim `workspace_pvc` mounted at
  `workspace_subpath`, or an emptyDir when unset. The backend never calls a PVC API.
- Commands: `bash [-l] -c` under the base class's snapshot and cwd wrapping, so cwd and exported env persist like
  the other remote backends. The exit code comes from the exec status channel. Stdin is length-framed
  (`head -c N`), so no stdin-close frame is needed (works on v4 and v5 exec protocols).
- Timeout: a command past its timeout is reported as timed out (`returncode` 124, `hermes_timed_out`); its whole
  remote process tree is killed through a pidfile the wrapper writes (`/proc/<pid>/task/*/children`, falling back
  to `pgrep -P`, each process stopped before its children are walked).
- `fetch_file` is the base transport (base64 over exec, capped at `max_bytes` inside the pod).
- Idle and cleanup: a pod idle past `idle_timeout` (checked by a watcher thread that holds only a weak reference)
  or whose environment is cleaned up is deleted; the next command recreates it and the result carries
  `environment_recreated`. The pod annotation `hermes/last-used` is stamped at most once a minute for an
  external reaper (none is built).
- Host `~/.hermes` state (credential files, skills, cache) syncs into `/workspace/.hermes` with the shared
  `FileSyncManager`, and changed files sync back on cleanup (untested against this backend).

## How it works

`KubernetesEnvironment` subclasses `BaseEnvironment` and returns a `_ThreadedProcessHandle` from `_run_bash`.
The worker opens `kubernetes.stream.stream(connect_get_namespaced_pod_exec, ..., binary=True,
_preload_content=False)`, polls `update()`, collects stdout and stderr, and reads `returncode` once the stream
closes. The handle's cancel runs a second exec that kills the tree named in the pidfile. Pod lifecycle runs
under one lock and the in-flight counter under another, so `init_session` can run while holding the lifecycle
lock. The terminal tool's builder reads `terminal.kubernetes` through `load_config_readonly()` (the served
profile's config) and the profile name through `current_profile_name()`.

## Code

`tools/environments/kubernetes.py` (new); `tools/terminal_tool_backends.py` (builder, requirement check with
the missing-extra message); `tools/terminal_tool.py` (default cwd `/workspace`); backend sets in
`tools/terminal_tool_config.py`, `tools/env_probe.py`, `tools/file_tools_paths.py`, `tools/skills_tool_setup.py`,
`tools/credential_files.py`, `agent/prompt_builder.py`, `agent/terminal_env_registry.py`;
`hermes_cli/config_defaults.py`, `doctor_tools.py`, `setup_terminal.py`, `web_routers/tools.py`,
`web_server_config.py`, `web_server_profiles.py`, `plugins.py`; `pm/extras.py`, `pyproject.toml`
(`kubernetes = ["kubernetes==36.0.0"]`), `uv.lock`, `nix/packages.nix`; desktop backend select; docs and
`cli-config.yaml.example`.

## Tests

`tests/tools/test_kubernetes_environment.py` (15). A fake `kubernetes` package keeps pods in dicts, answers 403
to every PVC call and runs each pod exec as a real local `bash` with `/workspace` and `/tmp` mapped into a temp
dir:

- exec returns stdout and the real exit code (0 and 7);
- cwd persists across two commands;
- a command past its timeout is reported timed out and its process tree is killed (the kill targets that
  command's pidfile);
- an idle pod is deleted and recreated on next use, with no PVC API call at all;
- the profile's config drives namespace, image, resources, kubeconfig/context, labels, node selector and
  tolerations, and the pod carries the security settings (emptyDir when no claim is set);
- `workspace_pvc` + `workspace_subpath` give the claim volume and subPath mount, with no PVC API call;
- `run_as_user` / `run_as_group` / `fs_group` land in the pod and container security contexts; uid 0 is refused;
- a running pod is reused, not recreated; a failed pod is replaced; an image-pull failure raises
  `EnvironmentConnectionError`;
- a missing `kubernetes` package gives an error naming the extra, from the constructor and from the requirements
  check;
- without an explicit kubeconfig the default kubeconfig is used with the configured context;
- `fetch_file` copies over exec and refuses a file over `max_bytes`.

Run with `scripts/run_tests.sh tests/tools/test_kubernetes_environment.py`: 15 passed on Linux and macOS. No test
runs against a real cluster; the exec websocket, the `/proc` children walk on a real node and RBAC behaviour are
untested.

## Applying

Apply the PR's commits, then relock (`hermes pm lock`) if `uv.lock` conflicts. Install the optional extra
(`hermes setup terminal`, Kubernetes) and set `terminal.backend: kubernetes` plus `terminal.kubernetes.namespace`
and `.image`.

## Gotchas

- The image needs `bash` and must run as the configured non-root uid; `HOME` is `/workspace`.
- The terminal tool's own inactive-env reaper (`terminal.lifetime_seconds`, default 300) calls `cleanup()`, so a
  pod's real idle life is the smaller of that and `idle_timeout`.
- Dangerous-command approvals stay on for this backend (`_should_skip_container_guards` does not list it), since a
  mounted workspace claim can be shared and persistent.
- `kubernetes` 37.x needs a newer `certifi` than core pins, hence 36.0.0.
- Profile names are folded to DNS-1123 labels, so two profiles differing only in case or punctuation share a pod
  name prefix (the session hash still separates sessions).
