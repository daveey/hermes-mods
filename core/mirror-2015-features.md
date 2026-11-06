# Mirror 2011–2015 features

These five patches extend snapshot 2010. Follow the full [Applying recipe](README.md)
with its integration bridges, not five standalone cherry-picks. Execution results
and limits are in [mirror-2015-verification.md](mirror-2015-verification.md).

## 2011: compression lifecycle test import

### Behavior
The once-mode compression lifecycle tests import the feasibility helper from its
new owning module. This is test maintenance, not a change to runtime behavior.

### How it works / Code
`tests/tui_gateway/test_once_compression_lifecycle.py` imports
`ensure_compression_feasibility_checked` from
`agent.conversation_compression_feasibility`, rather than the old compression facade.

### Tests
That test file passes on both source and applied Linux trees. It exercises the
real lifecycle with synthetic credentials; it does not call a paid model.

### Applying / Gotchas / plugin?
Patch 2011 (`4f59a30236199baf25db02aabe73aa335142673f`) requires the upstream
module move in the bridge before it. No plugin is involved.

## 2012: bootstrap acceptance-test psutil dependency

### Behavior
The bootstrap-installer workflow's isolated test environment installs
`psutil==7.2.2`, retaining the fork's pytest pin and real daemon cleanup.

### How it works / Code
`.github/workflows/bootstrap-installer.yml` adds psutil to the explicit
`scripts.ci.python_packages` dependency command. The session-end reaper uses it
to inspect process environments, including on platforms without `/proc`.

### Tests
`tests/test_session_daemon_reaper.py` passes on both Linux trees. The complete
bootstrap workflow and macOS execution were **not** rerun in this mirror pass;
this is not installer-platform acceptance evidence.

### Applying / Gotchas / plugin?
Patch 2012 (`6c3ee405e68ac621b1bcf7599d6df089c6d7ba72`) is a dependency backport,
not a replacement reaper or relaxed teardown assertion. Workflow setup is core
CI configuration, not a plugin.

## 2013: bounded live owner reads for missing replicas

### Behavior
A remote task wait whose SSH read fails can recover an **absent** local replica
through a bounded live owner read. Stale observations remain fail-closed; they
are not silently replaced with a guessed status. Only owner-produced matching
live evidence is accepted.

### How it works
Task SSH calls have a 15-second subprocess timeout. The fallback receives the
remainder of a 25-second budget and caps the live request at eight seconds.
The transport must explicitly support `timeout_seconds`. The result must match
the task, board, resolved gateway-qualified ref, live source and recognized
status, and include typed owner wait evidence. Status prose alone is not proof.
The owner API publishes `wait_observation`; malformed/unsupported transport is
an error, not a pending or completed task.

### Code
`hermes_cli/kanban_wait_check.py`, `kanban_wait_replica.py`, `kanban_wait_live.py`,
and `plugins/kanban/dashboard/plugin_api.py`.

### Tests
`tests/hermes_cli/test_kanban_wait_live.py`, `test_kanban_wait_replica.py` and
`tests/plugins/test_kanban_foreign_card_api.py` pass on both trees. These cover
bounded transport validation and owner API behavior, not a live remote Desktop
network round trip. Live end-to-end transport is untested in this pass.

### Applying / Gotchas / plugin?
Patch 2013 (`d4ed91bd16e92a144db9826ab95744d06429231f`) needs a compatible
session-mentions transport; old handlers without an explicit timeout parameter
are refused. The existing public example SSH address normalization is retained.
This combines core wait semantics with the existing dashboard/transport plugin,
not a new plugin.

## 2014: preserve scratch used by live owner-tagged jobs

### Behavior
Completion cleanup, deferred parent cleanup and both GC deletion paths retain
scratch while a same-user live process has its cwd beneath that workspace and
a valid local `HERMES_OWNER_CARD=<card>@<hostname>` tag. A child or another card
sharing the workspace can hold it. Cleanup retries naturally after exit; it
never signals the job or makes a marker file into permanent retention authority.

### How it works / Code
`hermes_cli/kanban_workspace_jobs.py` scans with a one-second budget, checks cwd,
user, local owner tag, process status and birth identity. A partial/unavailable
scan defers deletion. `kanban_db_workspace.py` and `kanban_worktree_gc.py` call
the same veto before deletion. Inaccessible process details defer only after a
cwd dependency was observed; arbitrary uninspectable system processes do not
pin every workspace.

### Tests
`tests/hermes_cli/test_kanban_scratch_owned_jobs.py` runs real writer-process
regressions through cleanup entry points; `test_kanban_workspace_jobs.py` checks
authority and conservative scan behavior. Both pass on source and applied Linux
trees. Native macOS cases are skipped here, not claimed as exercised.

### Applying / Gotchas / plugin?
Patch 2014 (`d70c6e1b95694222b79b8976ec2460972ba0155b`) protects **cwd**
dependencies, not arbitrary absolute output paths. Keep long-lived inputs and
outputs in durable storage. A stale marker or PID alone provides no protection.
This is a core deletion invariant, not a plugin.

## 2015: offline escalation cooldown across reparks

### Behavior
The first offline-gateway escalation is allowed. After a wake → claim → repark,
a prior offline escalation for the same task, exact command and gateway suppresses
repeated escalation for 24 hours. The probe retains bounded `check_every`
scheduling instead of immediately dispatching again. A fresh rc0 result and
unexpected faults retain their existing behavior.

### How it works / Code
`hermes_cli/kanban_offline_backoff.py` reads durable `wake_check_broken` events
whose `reason` is `gateway_offline`. `kanban_wake_check.py` consults this receipt
history rather than relying on a disposable probe row. Future receipts and
expired receipts are excluded; malformed payloads cannot establish a cooldown.

### Tests
`tests/hermes_cli/test_kanban_offline_backoff.py` and
`tests/hermes_cli/test_kanban_wait_replica.py` pass on both trees, including the
wake/claim/repark regression, cooldown bounds and successful fresh evidence.
The originating implementation brief records RED (`ready != scheduled`), 77
focused and 700 affected tests, plus required CI 42151258439; those are historical
implementation evidence, not additional tests rerun by this mirror.

### Applying / Gotchas / plugin?
Patch 2015 (`2a6c4ae61b9c13e847cfb320a3e609fcc32ea5f8`) is core scheduler policy.
The cooldown is **not an outage identity**: increasing stale age is not a new
premise, and a distinct outage of the same task/command/gateway inside the
window is not distinguished. Changing command or gateway changes the key;
normal success is not suppressed. No new plugin or configuration knob is added.
