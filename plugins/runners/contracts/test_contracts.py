"""Offline proposed-contract tests; never import a plugin or resolve credentials."""
import copy
import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent


class Contracts(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((ROOT / 'runners.schema.json').read_text())
        self.fixtures = json.loads((ROOT / 'fixtures.json').read_text())

    def validate(self, definition, value):
        schema = dict(self.schema, **{'$ref': '#/$defs/' + definition})
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)

    def reject(self, definition, value):
        from jsonschema.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.validate(definition, value)

    def test_large_scope_inventory_is_representable(self):
        config = copy.deepcopy(self.fixtures['Config'])
        config['scopes'] = [dict(config['scopes'][0], id='scope_' + format(i, '016x'), name='example/project-' + str(i)) for i in range(600)]
        self.validate('Config', config)

    def test_schema_is_2020_12(self):
        self.assertEqual(self.schema['$schema'], 'https://json-schema.org/draft/2020-12/schema')
        Draft202012Validator.check_schema(self.schema)

    def test_all_fictional_fixtures(self):
        self.assertGreaterEqual(len(self.fixtures), 25)
        for name, value in self.fixtures.items():
            with self.subTest(definition=name):
                self.validate(name, value)

    def test_no_unknown_fields_in_any_fixture(self):
        for name, value in self.fixtures.items():
            bad = copy.deepcopy(value)
            bad['unexpected_field'] = True
            with self.subTest(definition=name):
                self.reject(name, bad)

    def test_secret_fields_rejected_at_all_config_objects(self):
        def walk(value):
            if isinstance(value, dict):
                yield value
                for child in list(value.values()):
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)
        config = copy.deepcopy(self.fixtures['Config'])
        for obj in walk(config):
            for key in ('token', 'password', 'private_key', 'environment', 'command'):
                obj[key] = 'FICTIONAL-REJECTED-VALUE'
                self.reject('Config', config)
                del obj[key]

    def test_secret_value_cannot_replace_credential_reference(self):
        config = copy.deepcopy(self.fixtures['Config'])
        for value in ('Bearer fictional', '-----BEGIN PRIVATE KEY-----', 'https://example.com/token'):
            config['scopes'][0]['credential_ref'] = value
            self.reject('Config', config)

    def test_invalid_operation_ids(self):
        for value in ('', 'builder-name', 'host.example.com', '../target', 'a/b', 'https://example.com', '$(whoami)', '-oProxyCommand=x'):
            req = dict(self.fixtures['InspectRequest'], runner_id=value)
            self.reject('InspectRequest', req)

    def test_unsafe_endpoint_and_ssh_target(self):
        for value in ('http://api.github.com', 'https://127.0.0.1', 'https://api.github.com@localhost', 'https://api.github.com/evil', 'https://api.github.com?token=x'):
            config = copy.deepcopy(self.fixtures['Config'])
            config['scopes'][0]['endpoint'] = value
            self.reject('Config', config)
        config = copy.deepcopy(self.fixtures['Config'])
        config['hosts'][0]['ssh_target_ref'] = 'user@host; echo unsafe'
        self.reject('Config', config)

    def test_false_is_default_not_implicit_enablement(self):
        flags = self.schema['$defs']['FeatureFlags']['properties']
        for flag in ('repair_sessions', 'management_operations', 'dispatch', 'workspace_jobs'):
            self.assertIs(flags[flag]['default'], False)
            self.assertIs(self.fixtures['Config']['features'][flag], False)
        bad = copy.deepcopy(self.fixtures['Config'])
        del bad['features']['dispatch']
        self.reject('Config', bad)  # defaults are annotations, not insertion

    def test_owner_uuid_is_not_client_connection(self):
        owner = dict(self.fixtures['Owner'], gateway_id='local')
        self.reject('Owner', owner)
        route = dict(self.fixtures['ClientRoute'], connectionId='')
        self.reject('ClientRoute', route)
        owner = dict(self.fixtures['Owner'], connectionId='local')
        self.reject('Owner', owner)

    def test_execution_rejects_shell_paths_and_mutation_shortcuts(self):
        for key in ('hostname', 'shell', 'argv', 'cwd', 'env', 'url', 'healthy'):
            req = dict(self.fixtures['ExecutionRunRequest'], **{key: 'unsafe'})
            self.reject('ExecutionRunRequest', req)
        req = dict(self.fixtures['ExecutionResolveRequest'], exact_target_id='friendly-name')
        self.reject('ExecutionResolveRequest', req)
        req = copy.deepcopy(self.fixtures['ExecutionResolveRequest'])
        req['budget']['deadline_seconds'] = 0
        self.reject('ExecutionResolveRequest', req)
        req = copy.deepcopy(self.fixtures['ExecutionResolveRequest'])
        req['source']['digest'] = '../../workspace'
        self.reject('ExecutionResolveRequest', req)

    def test_no_client_healthy_verification(self):
        self.reject('VerifyRequest', dict(self.fixtures['VerifyRequest'], healthy=True))

    def test_unknown_and_missing_are_explicit(self):
        runner = copy.deepcopy(self.fixtures['Runner'])
        for state in ('missing', 'unknown'):
            runner['registration'] = state
            self.validate('Runner', runner)
        runner['registration'] = None
        self.reject('Runner', runner)
        runner['registration'] = 'online'
        runner['host_id'] = None
        self.validate('Runner', runner)

    def test_freshness_and_provenance_required_for_responses(self):
        envelope = copy.deepcopy(self.fixtures['Envelope'])
        for field in ('owner', 'freshness', 'provenance', 'coverage', 'observed_at', 'expires_at'):
            bad = dict(envelope)
            del bad[field]
            self.reject('Envelope', bad)
        envelope['freshness'] = 'unknown'
        envelope['observed_at'] = None
        envelope['expires_at'] = None
        self.validate('Envelope', envelope)

    def test_bb_host_binding_requires_verification_evidence(self):
        binding = dict(self.fixtures['BBBinding'], verification_observation_id=None)
        self.reject('BBBinding', binding)

    def test_no_mutating_device_effect_on_ci_mode(self):
        req = copy.deepcopy(self.fixtures['ExecutionResolveRequest'])
        req['permitted_effects'] = ['device_write']
        self.reject('ExecutionResolveRequest', req)

    def test_exact_sha_required_for_workflow_submission(self):
        for sha in ('main', 'latest', 'a' * 7, 'g' * 40):
            self.reject('JobRequest', dict(self.fixtures['JobRequest'], source_sha=sha))

    def test_workspace_snapshot_and_device_routes(self):
        req = copy.deepcopy(self.fixtures['ExecutionResolveRequest'])
        req['mode'] = 'workspace'
        req['source'] = {'kind': 'snapshot', 'repository_id': 'repo_' + '1' * 16,
                         'base_sha': 'a' * 40, 'manifest_id': 'manifest_' + '1' * 16,
                         'digest': 'sha256:' + 'b' * 64}
        self.validate('ExecutionResolveRequest', req)
        req['mode'] = 'ci'
        self.reject('ExecutionResolveRequest', req)
        req['mode'] = 'device'
        req['source'] = {'kind': 'none'}
        req['permitted_effects'] = ['read', 'device_write']
        self.validate('ExecutionResolveRequest', req)
        target = copy.deepcopy(self.fixtures['ExecutionTarget'])
        for mode in ('device', 'workspace'):
            target.update(mode=mode, runner_id=None, bb_binding_id='binding_' + '1' * 16)
            self.validate('ExecutionTarget', target)
            target['bb_binding_id'] = None
            self.reject('ExecutionTarget', target)

    def test_freshness_requires_real_timestamps(self):
        envelope = copy.deepcopy(self.fixtures['Envelope'])
        envelope['freshness'] = 'fresh'
        self.reject('Envelope', envelope)
        envelope.update(observed_at='2030-01-01T00:00:00Z', expires_at='2030-01-01T00:02:00Z')
        self.validate('Envelope', envelope)
        envelope['expires_at'] = 'tomorrow'
        self.reject('Envelope', envelope)

    def test_workspace_wire_is_bounded_not_shell(self):
        for key in ('argv', 'env', 'shell', 'cwd', 'ssh_target'):
            self.reject('WorkspaceSubmit', dict(self.fixtures['WorkspaceSubmit'], **{key: 'unsafe'}))
        request = dict(self.fixtures['WorkspaceLogsRequest'], limit_bytes=65537)
        self.reject('WorkspaceLogsRequest', request)

    def test_generated_schema_matches_authoring_definitions(self):
        from build_contracts import D
        self.assertEqual(self.schema['$defs'], D)
        expected = {name for name, value in D.items() if value.get('type') == 'object'}
        self.assertEqual(set(self.fixtures), expected)

    def test_all_contract_operations_have_inputs_outputs(self):
        catalog = json.loads((ROOT / 'operations.json').read_text())
        self.assertEqual(catalog['namespace'], '/api/plugins/runners/v1')
        expected = {'runners_list', 'runners_inspect', 'runners_refresh', 'runners_repair', 'runners_verify', 'runners_run', 'runners_job', 'execution_resolve', 'execution_run', 'execution_status', 'execution_cancel', 'execution_artifacts'}
        self.assertEqual(set(catalog['tools']), expected)
        for operation in list(catalog['tools'].values()) + catalog['api']:
            self.assertIn(operation['input'], self.schema['$defs'])
            self.assertIn(operation['output'], self.schema['$defs'])
            self.assertIn(operation['policy'], ('read', 'manage', 'dispatch'))
        self.assertEqual(len(catalog['api']), 10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
