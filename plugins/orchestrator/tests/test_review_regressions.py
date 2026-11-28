from test_snapshot_and_stream import fixture_adapter, run


def test_panel_bundle_top_level_owner_survives_string_session():
    adapter = fixture_adapter()
    adapter.sessions = lambda: {'bundles': {'watched': {'bundle': {
        'session': 'watched', 'gateway': 'remote-one', 'profile': 'work',
        'title': 'Watched chat', 'tasks': [], 'model': {'sections': {}}}}}}
    adapter.requests = lambda _: {}
    result = run(adapter)
    watched = next(row for row in result['sessions'] if row['session_id'] == 'watched')
    assert watched['route'] == {'gatewayId': 'remote-one', 'profile': 'work', 'targetProfile': 'work'}
    assert watched['title'] == 'Watched chat'


def test_two_gate_cards_keep_identity_and_dedupe_same_card_across_producers():
    adapter = fixture_adapter()
    lanes = adapter.lanes()
    cards = [{'id': key, 'title': key, 'governor': 'lane_gate',
              'source': {'kind': 'session', 'id': 'creator'}} for key in ('task-a', 'task-b')]
    lanes['cards'] = cards
    adapter.lanes = lambda: lanes
    adapter.sessions = lambda: {'bundles': {'creator': {'bundle': {
        'session': 'creator', 'tasks': [cards[0]], 'model': {'sections': {}}}}}}
    adapter.requests = lambda _: {}
    result = run(adapter)
    assert {row['key'] for row in result['machinery']} == {'task-a', 'task-b'}
    assert result['totals']['machinery'] == 2
    assert {row['session_id'] for row in result['machinery']} == {'creator'}
