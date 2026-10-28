"""action_request producer: wrongly shaped input must come back as a 422 JSON error naming the field,
never a raw exception (an AttributeError surfaced as a dispatch error the agent could not act on)."""
import copy
import json

import pytest

from test_decisions import OWNER
from test_review_regressions import plugin_at, producer_payload

SID = OWNER['storedSessionId']


def approval_payload():
    payload = producer_payload()
    assert payload['kind'] == 'approval'
    return payload


def publish(plugin, args):
    raw = plugin.publish_action_request(args, session_id=SID)
    assert isinstance(raw, str)
    return json.loads(raw)


def mutate(payload, path, value):
    payload = copy.deepcopy(payload)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return payload


@pytest.mark.parametrize('label, path, value, extra, field', [
    ('string aliases (new)', ('aliases',), ['t_01a1abc'], {}, 'aliases'),
    ('string aliases (replacement)', ('aliases',), ['t_01a1abc'],
     {'requestId': '00000000-0000-4000-8000-000000000000', 'expectedRevision': 1}, 'aliases'),
    ('aliases as a dict', ('aliases',), {'type': 'kanban', 'id': 't_1'}, {}, 'aliases'),
    ('string choices on approval', ('choices',), ['approve', 'revise', 'decline'], {}, 'choices'),
    ('scope as a string', ('scope',), 'Do the thing', {}, 'scope'),
    ('string sourceRefs', ('scope', 'sourceRefs'), ['msg-1'], {}, 'sourceRefs'),
])
def test_producer_rejects_malformed_shapes(tmp_path, monkeypatch, label, path, value, extra, field):
    plugin = plugin_at(tmp_path, monkeypatch)
    out = publish(plugin, dict(extra, request=mutate(approval_payload(), path, value)))
    assert out.get('status') == 422, (label, out)
    assert field in out['error'], (label, out)
    assert plugin.core.ActionStore().requests(OWNER) == []


def test_alias_error_tells_the_agent_the_exact_shape(tmp_path, monkeypatch):
    plugin = plugin_at(tmp_path, monkeypatch)
    out = publish(plugin, {'request': mutate(approval_payload(), ('aliases',), ['t_01a1abc'])})
    assert 'gatewayId' in out['error'] and 'pass []' in out['error']


def test_schema_description_spells_out_alias_and_choice_shapes(tmp_path, monkeypatch):
    plugin = plugin_at(tmp_path, monkeypatch)
    desc = plugin.PRODUCER_SCHEMA['parameters']['properties']['request']['description']
    assert '{gatewayId, profile, type, id, board?}' in desc
    assert 'aliases: []' in desc
    assert '{id, label, effect}' in desc


def test_well_formed_payload_still_publishes(tmp_path, monkeypatch):
    plugin = plugin_at(tmp_path, monkeypatch)
    out = publish(plugin, {'request': approval_payload()})
    assert 'error' not in out, out
    assert out['request']['revision'] == 1


FIELDS = ('schemaVersion', 'kind', 'title', 'scope', 'limits', 'exclusions', 'outcomes', 'choices',
          'aliases', 'state')
BAD = ('x', 7, ['x'], [7], [None], None, {'a': 'b'}, [{'a': 'b'}], True)


@pytest.mark.parametrize('kind', ['approval', 'choice'])
@pytest.mark.parametrize('replacement', [False, True])
@pytest.mark.parametrize('field', FIELDS + ('scope.sourceRefs', 'scope.markdown', 'scope.complete',
                                           'choices.0', 'aliases.0'))
def test_type_fuzz_never_raises(tmp_path, monkeypatch, kind, replacement, field):
    """Detection guard: every field swapped for every wrong type returns JSON, never raises."""
    plugin = plugin_at(tmp_path, monkeypatch)
    base = approval_payload()
    base['kind'] = kind
    base['aliases'] = [dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='message', id='m1')]
    extra = {'requestId': '00000000-0000-4000-8000-000000000000', 'expectedRevision': 1} if replacement else {}
    path = tuple(int(p) if p.isdigit() else p for p in field.split('.'))
    for value in BAD:
        payload = copy.deepcopy(base)
        if path[0] == 'choices' and len(path) > 1:
            payload['choices'][0] = value
        elif path[0] == 'aliases' and len(path) > 1:
            payload['aliases'][0] = value
        else:
            payload = mutate(payload, path, value)
        out = publish(plugin, dict(extra, request=payload))
        assert isinstance(out, dict) and ('error' in out or 'request' in out), (field, value, out)


@pytest.mark.parametrize('request_value', ['x', 7, ['x'], None])
def test_request_itself_wrong_type_never_raises(tmp_path, monkeypatch, request_value):
    plugin = plugin_at(tmp_path, monkeypatch)
    out = publish(plugin, {'request': request_value})
    assert 'error' in out
