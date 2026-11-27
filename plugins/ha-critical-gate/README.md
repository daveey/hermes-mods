# ha-critical-gate

A `pre_tool_call` guard on direct and wrapped `ha_call_service` calls. Every call is sent to a trusted confirmation gate if one is available. `hold`/`deny` blocks; `allow` permits the downstream tool to run. The plugin itself never sends HA service requests.

Without a working gate, unlock/open/disarm/cover motion is blocked. Script, scene, automation, button and switch services/targets always need confirmation: their physical effects cannot be inferred from their names. Entity domains are checked in `entity_id` and `data.entity_id` (including JSON data, lists and comma-separated targets). Generic `homeassistant` calls with implicit, all, group, area, device or label targets fail closed; no network expansion is attempted. Known light/media/climate and explicit closing/locking operations retain the prior no-gate behavior. This is conservative classification, not a promise that every generic service is implemented for every HA entity domain.

## Trusted configuration (breaking security migration)

Only `/etc/hermes/ha-critical-gate.json` is read:

```json
{"command": ["/usr/local/libexec/ha-confirm-gate"], "timeout": 25}
```

It must be a regular, root-owned file; every ancestor directory and the executable must be root-owned, not group/world-writable, not writable by the agent (including effective ACLs), and not a symlink. The file is read once through a no-follow fd with an inode metadata recheck. The timeout must be finite, positive and at most 300 seconds. The command is exactly one absolute executable path; interpreter/script argument chains and relative PATH lookups are refused. An administrator must deploy a standalone, self-contained gate and keep its runtime dependencies and policy outside agent-writable locations. Permissions are a defense-in-depth boundary for an unprivileged agent, not an OS sandbox against an agent with sudo/root authority. A root agent fails the writability check.

`HA_CRITICAL_GATE_COMMAND` and all home/profile `ha-critical-gate.json` files are ignored, even if chmod 0600. Owner-only files belonging to the agent are still agent-writable. Profiles share the system gate; their profile name remains in the JSON payload. No config migration, privileged install or live HA operation is performed by this plugin.

The gate receives one JSON object on stdin: `domain`, `service`, `entity_id`, `data`, `agent`. It prints a final JSON line with `decision` (`allow`, `hold`, `deny`) and optional `message`. Invalid output, nonzero exit, timeout, missing or untrusted configuration fails closed for critical calls. Unexpected hook exceptions block instead of losing protection for a wrapper batch.

## Tests

From the repository root, run `scripts/test-all -k ha-critical-gate`; for an isolated standalone run: `python3 -m pytest -q -p no:cacheprovider plugins/ha-critical-gate/tests_gate.py plugins/ha-critical-gate/tests`.

`tests_gate.py` retains the original ten cases (two mutable-config expectations updated for the intentional migration). `tests/test_review_regressions.py` covers target-domain bypasses, unavailable gates, untrusted configuration/environment, trusted-path ownership/modes/ACL writability, malformed configuration, secure success and wrapper exception fallback. `HA_GATE_SOURCE` is a test-only selector for running behavioral regressions against an extracted original `__init__.py`.

## Upstream verification

Reviewed `NousResearch/hermes-official-plugins@e7a218f2`, `homeassistant/tools.py`: the blocked domains are shell_command, command_line, python_script, pyscript, hassio and rest_command. `homeassistant`, script, scene, automation, button and switch are not blocked. `_handle_call_service` validates selectors and optional entity policy then dispatches; it has no physical-confirmation step. Registration supplies no confirmation hook. Thus the downstream layer must not be relied on to repair this guard's bypass. This is a source/isolated-handler check, not a live HA or universal core-approval exemption claim.

Home Assistant's `components/homeassistant/services.py`, `_async_handle_turn_service`, extracts referenced/indirect entities, groups by entity domain and calls that domain with the same service name when supported. Generic calls are not confined to the literal service domain. Unsupported combinations can be rejected by HA; classification intentionally still fails closed. Source snapshots and the resolved upstream commit are retained with this card's evidence.
