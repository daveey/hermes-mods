"""Lane and pane attention must be the same set on a single card snapshot."""
import json

import pytest

from test_model import NOW, bundle, human, model, task


def test_lane_and_panel_card_attention_sets_match():
    asks = pytest.importorskip('hermes_cli.kanban_open_asks')
    if not hasattr(asks, 'needs_you'):
        pytest.skip('requires companion shared attention core')
    from hermes_cli.kanban_lanes_flow import classify_reason, classify_step

    cards = [
        task(id='valid', status='blocked', block_kind='needs_input', human_request=human()),
        task(id='triage', status='triage', block_kind='capability', human_request=json.dumps(human())),
        task(id='stale-kind', status='scheduled', block_kind='capability'),
        task(id='bare-kind', status='blocked', block_kind='needs_input'),
        task(id='running', status='running', human_request=human()),
        task(id='invalid', status='blocked', human_request={'action': 'partial'}),
        task(id='owner-step', status='scheduled', wait={'wait_state': 'human', 'owner_step': 'Connect device'}),
        task(id='external', status='scheduled', wait={'wait_state': 'waiting'}),
        task(id='stale', status='blocked', stale=True, human_request=human()),
    ]
    lanes = {c['id'] for c in cards if classify_reason(c, classify_step(c)) == 'you'}
    pane = model.reduce_panel(bundle(*cards), now=NOW)
    red = {c['id'] for c in pane['sections']['needs_you']}
    assert red == lanes == {'valid', 'triage', 'owner-step'}
    assert pane['header']['counts']['needs_you'] == len(lanes)
