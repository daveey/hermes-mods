"""Owner acceptance must use the native live return proof at the gate."""
import pytest
from test_goal_ownership import owner, gd, SID
from goal_consumer_fixture import consumer


def test_gate_rejects_subscription_without_consumer(owner):
    with pytest.raises(ValueError, match='owner_result_consumer_unverified'):
        gd.validate_disposition(owner[1], owner[2], session_id=SID)


def test_gate_accepts_successful_scoped_consumer(owner, consumer):
    assert gd.validate_disposition(owner[1], owner[2], session_id=SID)['disposition'] == 'owned_continuing'


@pytest.mark.parametrize('consumer', ['unreadable'], indirect=True)
def test_failed_native_poll_does_not_certify_consumer(owner, consumer):
    with pytest.raises(ValueError, match='owner_result_consumer_unverified'):
        gd.validate_disposition(owner[1], owner[2], session_id=SID)
