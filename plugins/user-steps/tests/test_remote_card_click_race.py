import pytest
from test_remote_card_courier import remote_row
from test_kanban_mirror import fixture, decide
from test_withdrawal import OWNER


@pytest.mark.parametrize('click_during_withdrawal', [False, True])
def test_new_generation_does_not_publish_over_concurrent_pending_click(tmp_path, click_during_withdrawal):
    """A click that commits at the withdrawal boundary holds the target: the replacement is absent."""
    mirror, store, _, hr = fixture(tmp_path)
    row = remote_row(hr)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    old = store.requests(OWNER)[0]
    row['card_answer_generation'] = 'b' * 64
    withdraw = mirror._withdraw

    def click_then_withdraw(owner, request):
        if click_during_withdrawal:
            decide(store, request)
        return withdraw(owner, request)

    mirror._withdraw = click_then_withdraw
    mirror.remote.reconcile(OWNER)
    opens = [r for r in store.requests(OWNER) if r['state'] == 'open']
    if click_during_withdrawal:
        # Replacement absent: no second decision-capable tile, so no second click is possible.
        assert [r['requestId'] for r in opens] == [old['requestId']]
        assert opens[0].get('resolution')
        assert len(store.undelivered(OWNER['gatewayId'], OWNER['profile'])) == 1
    else:
        assert len(opens) == 1 and opens[0]['requestId'] != old['requestId']
