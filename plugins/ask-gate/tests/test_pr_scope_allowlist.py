"""Scope is metadata, never an additional authorization channel."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('scope_parser', Path(__file__).parents[1] / 'pr_approval_request.py')
assert spec is not None and spec.loader is not None
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)


def row(scope):
    return {'human_request': {'interaction': 'approval', 'scope': scope,
            'action': 'Approve https://github.com/example/widget/pull/1950 at head 21cb9c1c2. '
                      'Unblock = merge; archive = close the PR.'}}


@pytest.mark.parametrize('scope', [
    'example/widget PR #1950, head 21cb9c1c2; also rotate the prod keys',
    'example/widget PR #1950 and transfer funds',
    'example/widget PR #1950; email credentials',
    'example/widget PR #1950 (also rotate the prod keys)',
    'example/widget PR #1950 (rotate-keys;)',
    'example/widget PR #1950 (a)(b)',
    'example/widget PR #1950 (' + 'a' * 65 + ')',
    'example/widget PR #1950 only then deploy prod',
    'example/widget PR #1950\nrotate keys',
    'example/widget PR #1950\n',
    'example/widget PR #1950\u200b',
    'example/widget PR #1950 (\u212aeys)',
    'example/widget\tPR #1950',
    'example/widget PR #1950\u00a0only',
    'example/widget PR #1950 other/project#1950',
    'example/widget PR #1950, head 21cb9c1c2, head abcdef123',
    'example/widget PR #1950, head 123456',
    'example/widget PR #1950, head ' + 'a' * 41,
    'https://github.com/example/widget/pull/1950/files',
    'https://github.com/example/widget/pull/1950?admin=true',
    123, [], {},
])
def test_scope_fails_closed_with_diagnostic(scope):
    r = row(scope)
    assert parser.request_target(r) is None
    assert 'Unrecognised scope text: ' + repr(scope) in parser.parse_failure_reason(r)


@pytest.mark.parametrize('reference', [
    'example/widget PR #1950', 'example/widget#1950',
    'https://github.com/example/widget/pull/1950', 'PR #1950',
    'example/widget', 'Repo: example/widget', 'Repository example/widget',
])
@pytest.mark.parametrize('suffix', ['', ' only', ' (agent-indirect-paths)',
    ' (agent-indirect-paths), head 21cb9c1c2 only', ' sha ABCDEF123',
    ', commit 21cb9c1c2', ' (' + 'a' * 64 + ') only'])
def test_bounded_scope_forms(reference, suffix):
    r = row(reference + suffix)
    assert parser.request_target(r) == ('example/widget', 1950, '21cb9c1c2')
    assert parser.parse_failure_reason(r) == ''


@pytest.mark.parametrize('scope', [None, '', '   '])
def test_optional_scope_backwards_compatibility(scope):
    r = row(scope)
    assert parser.request_target(r) == ('example/widget', 1950, '21cb9c1c2')


def test_scope_head_is_not_execution_head():
    r = row('example/widget PR #1950 (agent-indirect-paths), head 21cb9c1c2 only')
    r['human_request']['action'] = 'Approve PR #1950'
    assert parser.request_target(r) == ('example/widget', 1950, None)


def test_json_scope_cannot_bypass_validation():
    r = row('example/widget PR #1950; rotate keys')
    r['human_request'] = json.dumps(r['human_request'])
    assert parser.request_target(r) is None
    assert 'rotate keys' in parser.parse_failure_reason(r)
