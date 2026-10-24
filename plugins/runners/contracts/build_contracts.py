"""Deterministic authoring utility, NOT runtime implementation.
Writes only the three adjacent public contract artifacts. No network or plugin imports.
"""
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
D = {}


def ref(name):
    return {'$ref': '#/$defs/' + name}


def obj(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def enum(*values):
    return {'enum': list(values)}


def array(item, maximum=1000):
    return {'type': 'array', 'items': item, 'maxItems': maximum, 'uniqueItems': True}


def nullable(item):
    return {'anyOf': [item, {'type': 'null'}]}


def text(maximum=256):
    return {'type': 'string', 'minLength': 1, 'maxLength': maximum}


def integer(minimum=0, maximum=2147483647):
    return {'type': 'integer', 'minimum': minimum, 'maximum': maximum}


ID = {'type': 'string', 'pattern': '^[a-z]{2,12}_[0-9a-f]{16,64}$', 'maxLength': 77}
UUID = {'type': 'string', 'format': 'uuid'}
TIME = {'type': 'string', 'format': 'date-time'}
SHA = {'type': 'string', 'pattern': '^[0-9a-f]{40}$'}
DIGEST = {'type': 'string', 'pattern': '^sha256:[0-9a-f]{64}$'}
PROFILE = {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'}
BOOL = {'type': 'boolean'}
OS = enum('macos', 'linux', 'windows', 'embedded', 'unknown')
ARCH = enum('arm64', 'x86_64', 'other', 'unknown')
D['OpaqueID'] = ID
D['Owner'] = obj(gateway_id=UUID, profile=PROFILE, owner_epoch=integer(1), config_revision=integer(1))
# Client routes are resolved by Desktop, never persisted as owner authority.
D['ClientRoute'] = obj(connectionId={'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'}, mode=enum('local', 'remote'), profile=PROFILE, targetProfile=PROFILE)
D['FeatureFlags'] = obj(**{name: dict(BOOL, default=name == 'observe') for name in ('observe', 'repair_sessions', 'management_operations', 'dispatch', 'workspace_jobs')})
D['CapacityPolicy'] = obj(policy=enum('independent_services', 'dedicated_slot', 'shared_admission', 'unknown'), declared_max_jobs=nullable(integer(1, 1024)), observed_active_jobs=nullable(integer()), enforced=BOOL, admission_ref=nullable(ID))
D['Maintenance'] = obj(state=enum('inactive', 'active'), reason=nullable(text(512)), expires_at=nullable(TIME))
D['Maintenance']['allOf'] = [{'if': {'properties': {'state': {'const': 'active'}}}, 'then': {'properties': {'reason': text(512), 'expires_at': TIME}}}]
D['Host'] = obj(id=UUID, resource_id=ID, display_name=text(), os=OS, arch=ARCH, ssh_target_ref=nullable(ID), service_refs=array(ID, 64), capacity=ref('CapacityPolicy'), maintenance=ref('Maintenance'), observation_ids=array(ID), management=enum('reachable', 'unreachable', 'unknown'))
D['PermissionResult'] = obj(action=enum('read_inventory', 'read_jobs', 'dispatch', 'manage'), state=enum('allowed', 'denied', 'unknown', 'not_checked'), observation_id=nullable(ID))
D['Scope'] = obj(id=ID, provider={'const': 'github_actions'}, endpoint={'const': 'https://api.github.com'}, kind=enum('repository', 'organization', 'enterprise'), name={'type': 'string', 'pattern': '^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$', 'maxLength': 201}, credential_ref=ID, permissions=array(ref('PermissionResult'), 4))
D['Scope']['allOf'] = [{'if': {'properties': {'kind': {'const': 'repository'}}}, 'then': {'properties': {'name': {'pattern': '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'}}}, 'else': {'properties': {'name': {'pattern': '^[A-Za-z0-9_.-]+$'}}}}]
D['Runner'] = obj(id=ID, provider={'const': 'github_actions'}, endpoint={'const': 'https://api.github.com'}, scope_id=ID, provider_runner_id={'type': 'string', 'pattern': '^[1-9][0-9]{0,19}$'}, host_id=nullable(UUID), display_name=text(), labels=array(text(64), 100), observed_os=OS, lifecycle=enum('persistent', 'ephemeral', 'jit', 'retired', 'unknown'), replacement_of=nullable(ID), expected_expiry=nullable(TIME), registration=enum('online', 'offline', 'missing', 'unknown'), activity=enum('idle', 'busy', 'unknown'), readiness=enum('pass', 'warn', 'fail', 'not_checked'), freshness=enum('fresh', 'stale', 'unknown'), repair=enum('none', 'creating', 'active', 'verifying', 'failed', 'closed'), observation_ids=array(ID))
D['RunnerBinding'] = obj(runner_id=ID, host_id=UUID, service_ref=ID, verification_observation_id=ID)
D['Capability'] = obj(id=ID, requirement=text(128), declared_version=nullable(text(64)), observed_version=nullable(text(64)), state=enum('available', 'unavailable', 'unknown', 'not_checked'), observed_at=nullable(TIME), expires_at=nullable(TIME), verifier=nullable(ID), observation_ids=array(ID))
D['Observation'] = obj(id=ID, entity_id=ID, dimension=enum('registration', 'activity', 'management', 'readiness', 'capacity', 'capability', 'permission', 'binding', 'job'), value=text(512), reason_code=text(80), source_id=ID, collected_at=TIME, expires_at=TIME, evidence_summary=text(2048), error_class=nullable(enum('auth', 'forbidden', 'rate_limited', 'network', 'timeout', 'partial', 'unsupported', 'unknown')))
# Host observations refer to an opaque host resource ID rather than putting UUIDs into operation URLs.
D['Transition'] = obj(observation_id=ID, at=TIME, from_state=text(64), to_state=text(64))
D['Incident'] = obj(id=ID, entity_id=ID, dimension=text(64), opened_at=TIME, last_seen=TIME, resolved_at=nullable(TIME), transitions=array(ref('Transition'), 100))
CORE_ID = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}
D['SessionAssociation'] = obj(start_request_id=ID, stored_session_id=nullable(CORE_ID), lineage_tip_id=nullable(CORE_ID), runtime_session_id=nullable(CORE_ID), initial_turn_id=nullable(CORE_ID), state=enum('reserved', 'accepted', 'queued', 'running', 'reconciling', 'completed', 'cancelled', 'archived', 'failed', 'unknown'))
D['Requester'] = obj(principal_id=nullable(ID), connection_id=nullable(text(128)), session_id=nullable(ID), request_id=ID)
D['Repair'] = obj(id=ID, entity_id=ID, incident_id=ID, reservation_id=ID, owner=ref('Owner'), requester=ref('Requester'), idempotency_key=ID, mode=enum('diagnose', 'repair'), state=enum('creating', 'active', 'paused', 'verifying', 'restored_unverified_build', 'failed', 'closed', 'unknown_outcome'), session=ref('SessionAssociation'), verification_observation_ids=array(ID))
D['Artifact'] = obj(id=ID, job_id=ID, digest=DIGEST, size_bytes=integer(), state=enum('available', 'expired', 'unknown'), source_sha=nullable(SHA), source_digest=nullable(DIGEST), provider_run_id=nullable(text(64)), attempt=integer(1), toolchain_observation_ids=array(ID))
D['Job'] = obj(id=ID, owner=ref('Owner'), provider_id=ID, request_id=ID, target_id=ID, workflow_id=nullable(ID), provider_run_id=nullable(text(64)), provider_job_id=nullable(text(64)), attempt=integer(1), workflow_ref_sha=nullable(SHA), source_sha=nullable(SHA), source_digest=nullable(DIGEST), assigned_runner_id=nullable(ID), status=enum('queued', 'running', 'concluded', 'cancel_requested', 'cancelled', 'unknown_outcome'), conclusion=nullable(enum('success', 'failure', 'cancelled', 'timed_out', 'unknown')), observation_ids=array(ID), artifacts=array(ref('Artifact'), 100))
D['BBBinding'] = obj(id=ID, host_id=UUID, provider_id=ID, hub_ref=ID, node_ref=ID, catalog_ref=ID, protocol_version=integer(1, 1000), published_capability_ids=array(ID, 100), verification_observation_id=ID, access=enum('authorized', 'denied', 'unknown'), dependency_state=enum('available', 'hub_unavailable', 'target_offline', 'unknown'))
D['Provider'] = obj(id=ID, kind=enum('github_actions', 'bb_pod', 'bb_workspace'), status=enum('available', 'unavailable', 'unauthorized', 'unsupported', 'unknown'), protocol_version=nullable(integer(1)), capability_ids=array(ID, 100), observation_ids=array(ID))
D['ExecutionTarget'] = obj(id=ID, owner=ref('Owner'), host_id=nullable(UUID), provider_id=ID, runner_id=nullable(ID), bb_binding_id=nullable(ID), mode=enum('ci', 'device', 'workspace'), capability_ids=array(ID, 100), policy_ref=ID, availability=enum('eligible', 'ineligible', 'unknown', 'offline', 'dependency_unavailable', 'unsupported'), reason_code=text(80), observation_ids=array(ID))
D['ExecutionTarget']['allOf'] = [
    {'if': {'properties': {'mode': {'const': 'ci'}}}, 'then': {'properties': {'runner_id': ID, 'bb_binding_id': {'type': 'null'}}}},
    {'if': {'properties': {'mode': {'enum': ['device', 'workspace']}}}, 'then': {'properties': {'bb_binding_id': ID, 'runner_id': {'type': 'null'}}}},
]
D['InputValue'] = obj(input_id=ID, value={'oneOf': [text(1024), BOOL, {'type': 'number'}]})
D['WorkflowInput'] = obj(input_id=ID, label=text(80), value_type=enum('string', 'number', 'boolean'), required=BOOL, allowed_values=array({'oneOf': [text(1024), BOOL, {'type': 'number'}]}, 100))
D['Workflow'] = obj(id=ID, scope_id=ID, workflow_ref=ID, trusted_ref_ids=array(ID, 32), required_capability_ids=array(ID, 32), input_schema_ref=ID, input_definitions=array(ref('WorkflowInput'), 32), dispatch_enabled=dict(BOOL, default=False), claim_authority_ref=ID)
D['Config'] = obj(schema_version={'const': 1}, owner_gateway_id=UUID, owner_profile=PROFILE, owner_epoch=integer(1), config_revision=integer(1), features=ref('FeatureFlags'), scopes=array(ref('Scope'), 1000), hosts=array(ref('Host'), 1000), bindings=array(ref('RunnerBinding')), allowed_workflows=array(ref('Workflow'), 100), providers=array(ref('Provider'), 100), bb_bindings=array(ref('BBBinding')), execution_targets=array(ref('ExecutionTarget')), retention= obj(observations_days=integer(1, 365), retired_days=integer(1, 365), audit_days=integer(1, 3650)), thresholds=obj(inventory_stale_seconds=integer(1), host_stale_seconds=integer(1), disk_warn_gib=integer(1), disk_fail_gib=integer(1)))
D['Provenance'] = obj(source_id=ID, collection_id=nullable(ID), observed_at=nullable(TIME), expires_at=nullable(TIME), state=enum('fresh', 'stale', 'unknown', 'unavailable'), observation_ids=array(ID))
D['Coverage'] = obj(scope_id=ID, state=enum('complete', 'partial', 'denied', 'unavailable', 'not_configured', 'unknown'), enumerated_count=nullable(integer()), provider_total_count=nullable(integer()), pagination_complete=BOOL, reason_code=nullable(text(80)))
D['SourceError'] = obj(source_id=ID, code=text(80), retry_after_seconds=nullable(integer()), observed_at=TIME)
D['Envelope'] = obj(contract_version={'const': 1}, request_id=ID, owner=ref('Owner'), generated_at=TIME, observed_at=nullable(TIME), expires_at=nullable(TIME), freshness=enum('fresh', 'stale', 'unknown'), provenance=array(ref('Provenance')), coverage=array(ref('Coverage'), 1000), source_errors=array(ref('SourceError'), 1000))
D['Envelope']['allOf'] = [{'if': {'properties': {'freshness': {'enum': ['fresh', 'stale']}}}, 'then': {'properties': {'observed_at': TIME, 'expires_at': TIME}}}]
D['Error'] = obj(code=enum('invalid_request', 'unauthenticated', 'forbidden', 'not_found', 'owner_conflict', 'owner_disconnected', 'configuration_error', 'revision_conflict', 'idempotency_conflict', 'feature_disabled', 'unsupported_runtime', 'unsafe_target', 'stale_evidence', 'busy', 'unknown_activity', 'dependency_unavailable', 'rate_limited', 'unknown_outcome', 'internal_error'), retryable=BOOL, reason_code=text(80))
D['ErrorResponse'] = obj(meta=ref('Envelope'), error=ref('Error'))
D['EmptyRequest'] = obj()
D['InspectRequest'] = obj(runner_id=ID, history_limit=integer(0, 100))
D['RefreshRequest'] = obj(target_ids=array(ID, 100), idempotency_key=ID)
D['RepairRequest'] = obj(entity_id=ID, incident_id=ID, idempotency_key=ID, mode=enum('diagnose', 'repair'), expected_owner=ref('Owner'))
D['RepairLookupRequest'] = obj(repair_id=ID)
D['VerifyRequest'] = obj(repair_id=ID, expected_owner=ref('Owner'), idempotency_key=ID)
D['MaintenanceRequest'] = obj(entity_id=ID, state=enum('active', 'inactive'), reason=text(512), expires_at=nullable(TIME), expected_config_revision=integer(1), expected_owner=ref('Owner'))
D['MaintenanceRequest']['allOf'] = [{'if': {'properties': {'state': {'const': 'active'}}}, 'then': {'properties': {'expires_at': TIME}}}]
D['JobRequest'] = obj(workflow_id=ID, source_sha=SHA, inputs=array(ref('InputValue'), 32), idempotency_key=ID, expected_owner=ref('Owner'))
D['JobLookupRequest'] = obj(job_id=ID)
D['Source'] = {'oneOf': [obj(kind={'const': 'commit'}, repository_id=ID, sha=SHA), obj(kind={'const': 'snapshot'}, repository_id=ID, base_sha=SHA, manifest_id=ID, digest=DIGEST), obj(kind={'const': 'none'})]}
D['Budget'] = obj(deadline_seconds=integer(1, 86400), cpu_slots=integer(1, 64), memory_mib=integer(64, 262144), output_bytes=integer(1, 1073741824))
D['ArtifactRequirement'] = obj(requirement_id=ID, required=BOOL, maximum_bytes=integer(1, 1073741824))
D['ExecutionResolveRequest'] = obj(recipe_id=ID, mode=enum('ci', 'device', 'workspace'), exact_target_id=nullable(ID), required_capability_ids=array(ID, 32), source=ref('Source'), budget=ref('Budget'), permitted_effects=array(enum('read', 'job_workspace_write', 'device_write'), 3), artifact_requirements=array(ref('ArtifactRequirement'), 32))
D['ExecutionResolveRequest']['allOf'] = [
    {'if': {'properties': {'mode': {'const': 'ci'}}}, 'then': {'properties': {'source': {'properties': {'kind': {'const': 'commit'}}}, 'permitted_effects': {'items': enum('read', 'job_workspace_write')}}}},
    {'if': {'properties': {'mode': {'const': 'workspace'}}}, 'then': {'properties': {'source': {'properties': {'kind': {'enum': ['commit', 'snapshot']}}}, 'permitted_effects': {'items': enum('read', 'job_workspace_write')}}}},
    {'if': {'properties': {'mode': {'const': 'device'}}}, 'then': {'properties': {'source': {'properties': {'kind': {'const': 'none'}}}}}},
]
D['ExecutionRunRequest'] = obj(resolution_id=ID, target_id=ID, idempotency_key=ID, expected_owner=ref('Owner'))
D['ExecutionStatusRequest'] = obj(job_id=ID, expected_owner=ref('Owner'))
D['ExecutionCancelRequest'] = obj(job_id=ID, expected_owner=ref('Owner'), idempotency_key=ID, approval_ref=nullable(ID))
D['ExecutionArtifactsRequest'] = obj(job_id=ID, artifact_ids=array(ID, 100), expected_owner=ref('Owner'))
D['WorkspaceSubmit'] = obj(protocol_version={'const': 1}, request_id=ID, attempt_id=ID, target_id=ID, recipe_id=ID, source=ref('Source'), workspace_lease_id=ID, argv_policy_ref=ID, env_policy_ref=ID, deadline_at=TIME, budget=ref('Budget'), grant_ref=ID)
D['WorkspaceSubmit']['allOf'] = [{'properties': {'source': {'properties': {'kind': {'enum': ['commit', 'snapshot']}}}}}]
D['WorkspaceStatus'] = obj(job_id=ID, request_id=ID, attempt_id=ID, workspace_lease_id=ID, source_digest=DIGEST, state=enum('accepted', 'queued', 'running', 'cancel_requested', 'cancelled', 'succeeded', 'failed', 'unknown_outcome'), exit_code=nullable(integer(0, 255)), descendants_stopped=nullable(BOOL), receipt_ref=ID, artifact_ids=array(ID, 100))
D['WorkspaceLogsRequest'] = obj(job_id=ID, cursor=nullable(ID), limit_bytes=integer(1, 65536))
D['WorkspaceLogs'] = obj(job_id=ID, source_digest=DIGEST, next_cursor=nullable(ID), truncated=BOOL, redacted_text={'type': 'string', 'maxLength': 65536})
D['Collection'] = obj(id=ID, state=enum('pending', 'running', 'complete', 'partial', 'failed'), target_ids=array(ID, 100))
D['Counts'] = obj(registrations=integer(), online=integer(), busy=integer(), verified_ready=integer(), readiness_unverified=integer(), issues=integer(), mapped_hosts=integer(), host_capacity=nullable(integer()))
D['Inventory'] = obj(state=enum('ready', 'not_configured', 'loading', 'partial', 'provider_unavailable', 'owner_disconnected', 'plugin_unavailable', 'configuration_error', 'owner_conflict'), collection=nullable(ref('Collection')), counts=ref('Counts'), hosts=array(ref('Host')), runners=array(ref('Runner')), scopes=array(ref('Scope'), 1000), providers=array(ref('Provider')), targets=array(ref('ExecutionTarget')))
D['RunnerDetail'] = obj(runner=ref('Runner'), observations=array(ref('Observation'), 100), incidents=array(ref('Incident'), 100), repairs=array(ref('Repair'), 100), jobs=array(ref('Job'), 100))
D['RepairResult'] = obj(repair=ref('Repair'), owner_route=nullable(ref('ClientRoute')), route_status=enum('resolved', 'unresolved', 'disconnected', 'unsupported'))
D['Resolution'] = obj(id=ID, expires_at=TIME, request_digest=DIGEST, candidates=array(ref('ExecutionTarget'), 100), selected_target_id=nullable(ID), state=enum('resolved', 'no_match', 'unknown', 'denied', 'unsupported'), reason_code=text(80))
D['ArtifactAccess'] = obj(artifact=ref('Artifact'), retrieval_ref=nullable(ID))
for name, payload in {
    'InventoryResponse': ref('Inventory'), 'InspectResponse': ref('RunnerDetail'),
    'RefreshResponse': ref('Collection'), 'RepairResponse': ref('RepairResult'),
    'VerifyResponse': ref('Collection'), 'MaintenanceResponse': ref('Maintenance'),
    'WorkflowsResponse': array(ref('Workflow'), 100), 'JobResponse': ref('Job'),
    'ExecutionResolveResponse': ref('Resolution'), 'ExecutionRunResponse': ref('Job'),
    'ExecutionStatusResponse': ref('Job'), 'ExecutionCancelResponse': ref('Job'),
    'ExecutionArtifactsResponse': array(ref('ArtifactAccess'), 100),
}.items():
    D[name] = obj(meta=ref('Envelope'), data=payload)

# No live data: deterministic invented identifiers, reserved example names and dates.
def oid(prefix):
    return prefix + '_' + '0' * 15 + '1'


def sample(schema) -> Any:
    if '$ref' in schema:
        return sample(D[schema['$ref'].split('/')[-1]])
    if 'const' in schema:
        return schema['const']
    if 'default' in schema:
        return schema['default']
    if 'enum' in schema:
        return schema['enum'][0]
    if 'anyOf' in schema:
        return None if {'type': 'null'} in schema['anyOf'] else sample(schema['anyOf'][0])
    if 'oneOf' in schema:
        return sample(schema['oneOf'][0])
    kind = schema.get('type')
    if kind == 'object':
        return {k: sample(v) for k, v in schema['properties'].items()}
    if kind == 'array':
        return []
    if kind in ('integer', 'number'):
        return schema.get('minimum', 0)
    if kind == 'boolean':
        return False
    if schema.get('format') == 'uuid':
        return '00000000-0000-4000-8000-000000000001'
    if schema.get('format') == 'date-time':
        return '2030-01-01T00:00:00Z'
    pattern = schema.get('pattern', '')
    if pattern == ID['pattern']:
        return oid('obj')
    if pattern == SHA['pattern']:
        return 'a' * 40
    if pattern == DIGEST['pattern']:
        return 'sha256:' + 'b' * 64
    if pattern.startswith('^[1-9]'):
        return '1'
    return 'example'


def generate():
    schema = {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': 'https://example.com/contracts/runners/v1/schema', 'title': 'PROPOSED Runners v1; no runtime endpoints implied', '$ref': '#/$defs/Config', '$defs': D}
    fixtures = {name: sample(value) for name, value in D.items() if value.get('type') == 'object'}
    fixtures['Scope'].update(id=oid('scope'), name='example/project', credential_ref=oid('cred'))
    fixtures['Host'].update(resource_id=oid('host'), display_name='Fictional build host', service_refs=[oid('service')])
    fixtures['Runner'].update(id=oid('runner'), scope_id=oid('scope'), display_name='Fictional registration', host_id=None)
    fixtures['RunnerBinding'].update(runner_id=oid('runner'), service_ref=oid('service'))
    fixtures['Workflow'].update(id=oid('workflow'), scope_id=oid('scope'))
    fixtures['Provider'].update(id=oid('provider'))
    fixtures['BBBinding'].update(id=oid('binding'), provider_id=oid('bbprovider'))
    fixtures['ExecutionTarget'].update(id=oid('target'), provider_id=oid('provider'), runner_id=oid('runner'))
    fixtures['MaintenanceRequest']['expires_at'] = '2030-01-01T01:00:00Z'
    fixtures['ExecutionResolveRequest']['source'] = dict(kind='commit', repository_id=oid('repo'), sha='a' * 40)
    config = fixtures['Config']
    config.update(scopes=[fixtures['Scope']], hosts=[fixtures['Host']], bindings=[fixtures['RunnerBinding']], allowed_workflows=[fixtures['Workflow']], providers=[fixtures['Provider']], bb_bindings=[fixtures['BBBinding']], execution_targets=[fixtures['ExecutionTarget']])
    config['providers'].append(dict(fixtures['Provider'], id=oid('bbprovider'), kind='bb_pod'))
    config['retention'] = dict(observations_days=30, retired_days=30, audit_days=90)
    config['thresholds'] = dict(inventory_stale_seconds=120, host_stale_seconds=180, disk_warn_gib=20, disk_fail_gib=5)
    # Populate recursively generated response fixtures with the corrected domain examples.
    fixtures['InventoryResponse']['data']['scopes'] = [fixtures['Scope']]
    fixtures['InventoryResponse']['data']['runners'] = [fixtures['Runner']]
    fixtures['Envelope'].update(freshness='unknown')
    fixtures['Provenance'].update(state='unknown')
    fixtures['Coverage'].update(state='unknown')
    fixtures['Envelope']['provenance'] = [fixtures['Provenance']]
    fixtures['Envelope']['coverage'] = [fixtures['Coverage']]
    for name in fixtures:
        if name.endswith('Response'):
            fixtures[name]['meta'] = fixtures['Envelope']
    api_specs = [
        ('GET', '/inventory', 'EmptyRequest', 'InventoryResponse', 'read', 200),
        ('GET', '/runners/{id}', 'InspectRequest', 'InspectResponse', 'read', 200),
        ('POST', '/refresh', 'RefreshRequest', 'RefreshResponse', 'read', 202),
        ('POST', '/repairs', 'RepairRequest', 'RepairResponse', 'manage', 202),
        ('GET', '/repairs/{id}', 'RepairLookupRequest', 'RepairResponse', 'read', 200),
        ('POST', '/repairs/{id}/verify', 'VerifyRequest', 'VerifyResponse', 'manage', 202),
        ('POST', '/maintenance', 'MaintenanceRequest', 'MaintenanceResponse', 'manage', 200),
        ('GET', '/workflows', 'EmptyRequest', 'WorkflowsResponse', 'read', 200),
        ('POST', '/jobs', 'JobRequest', 'JobResponse', 'dispatch', 202),
        ('GET', '/jobs/{id}', 'JobLookupRequest', 'JobResponse', 'read', 200),
    ]
    api = [dict(method=m, path=p, input=i, output=o, policy=policy, success_status=status) for m, p, i, o, policy, status in api_specs]
    tools = {name: {k: api[index][k] for k in ('input', 'output', 'policy')} for name, index in [('runners_list', 0), ('runners_inspect', 1), ('runners_refresh', 2), ('runners_repair', 3), ('runners_verify', 5), ('runners_run', 8), ('runners_job', 9)]}
    for suffix in ('Resolve', 'Run', 'Status', 'Cancel', 'Artifacts'):
        tools['execution_' + suffix.lower()] = dict(input='Execution' + suffix + 'Request', output='Execution' + suffix + 'Response', policy='dispatch' if suffix in ('Run', 'Cancel') else 'read')
    catalog = dict(status='proposed', namespace='/api/plugins/runners/v1', error_schema='ErrorResponse', api=api, tools=tools)
    for filename, value in [('runners.schema.json', schema), ('fixtures.json', fixtures), ('operations.json', catalog)]:
        (ROOT / filename).write_text(json.dumps(value, indent=2) + '\n')
    print('Generated proposed schema, fictional fixtures and operation catalog.')


if __name__ == '__main__':
    generate()
