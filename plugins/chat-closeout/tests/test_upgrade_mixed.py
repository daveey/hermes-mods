"""The migration must not expose an old settled tail as a new close-out."""
from test_closeout import core
from test_upgrade_shepherd import row, K1


def test_upgrade_does_not_wake_old_settled_tail(tmp_path):
    led = core.Ledger(tmp_path / 'ledger.db')
    cards = {K1: row(K1), 'default/t_local': {'id':'t_local','status':'done','completed_at':50},
             'hub/default/build/t_origin': {**row('hub/default/build/t_origin'), 'via':'origin','binding_id':'b'}}
    led.write('chat',batch=list(cards),wake_count=4)
    core.upgrade_shepherd_rows(led, lambda ids: {i:cards[i] for i in ids}, now=100)
    result=led.get('chat')
    result.update(core.reconcile(result,cards))
    assert core.pending_key(result,cards) is None
    assert result['batch']==[]
    assert result['wake_count']==4


def test_upgrade_preserves_post_cutoff_tail(tmp_path):
    led=core.Ledger(tmp_path/'ledger.db')
    cards={K1:row(K1), 'default/t_new':{'id':'t_new','status':'done','completed_at':150}}
    led.write('chat',batch=list(cards))
    core.upgrade_shepherd_rows(led,lambda ids:{i:cards[i] for i in ids},now=100)
    result=led.get('chat')
    assert result['batch']==['default/t_new']
    assert core.pending_key(result,cards)


def test_unlink_clock_progresses_with_alternating_backends():
    row = {'session_id':'chat','batch':['gone']}
    for tick in range(1, 870):
        row.update(core.track_unlinked(row, {}, healthy=lambda:True,
                                      now=tick*100, mono=tick*100, pid=10 + tick % 2))
    assert row['batch'] == set()
    assert core.unlinked_removed(row) == ['gone']


def test_unlinked_is_never_forgotten_after_failed_delivery():
    state={'t_missing':{'removed':1,'since':0,'briefed':10,'tries':10}}
    remaining,retry=core.settle_unlinked({'unlinked':state},False,20)
    assert 't_missing' in remaining
    assert retry
