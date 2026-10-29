"""Branch labels are data only when bound to the authoritative head ref."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('branch_parser', Path(__file__).parents[1] / 'pr_approval_request.py')
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)


def row(scope):
    return {'human_request': {'interaction': 'approval', 'scope': scope,
            'action': 'Approve example/widget#1950 head 21cb9c1c2. Unblock = merge; archive = close the PR.'}}


@pytest.mark.parametrize('encoded', [False, True])
@pytest.mark.parametrize('label', ['agent-indirect-paths', 'rotate-the-prod-keys-now', 'Case_Sensitive.1'])
def test_extract_label_preserves_bytes_and_requires_exact_head_ref(encoded, label):
    r = row(f'example/widget PR #1950 ({label}), head 21cb9c1c2 only')
    if encoded:
        r['human_request'] = json.dumps(r['human_request'])
    assert parser.scope_label(r) == label
    assert parser.scope_label_failure(r, label) == ''
    for ref in ('other-branch', label.upper(), None, ''):
        reason = parser.scope_label_failure(r, ref)
        assert label in reason and 'headRefName' in reason


@pytest.mark.parametrize('scope', [None, '', 'example/widget#1950'])
def test_absent_label_needs_no_branch_metadata(scope):
    r = row(scope)
    assert parser.scope_label(r) is None
    assert parser.scope_label_failure(r, None) == ''


def test_invalid_scope_never_extracts_label_or_bypasses_diagnostic():
    r = row('example/widget#1950 (real) then rotate keys')
    assert parser.scope_label(r) is None
    assert 'Unrecognised scope text' in parser.scope_label_failure(r, 'real')
