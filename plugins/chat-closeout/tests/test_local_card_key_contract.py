"""Local card keys are ``<board slug>/<task id>``: the identity core's sidebar counts (``kanban_activity_identity
.local_keys``) and ``parked_waits`` give the same local card, so a pending close-out of a card core already counts is
deduplicated, not counted twice (core ``tests/hermes_cli/test_kanban_local_waiting_overlap.py`` pins the other side)."""
from test_closeout import core


def test_local_card_key_is_board_slug_and_id():
    assert core.card_key({"id": "t_1", "board": "ops", "remote": False, "gateway": None}) == "ops/t_1"
    assert core.card_key({"id": "t_1", "board": None}) == "default/t_1"
    assert core.card_key({"id": "t_1"}) == "default/t_1"


def test_remote_card_key_never_has_the_local_shape():
    key = core.card_key({"id": "t_1", "board": "ops", "remote": True, "gateway": "hub", "profile": "default"})
    assert key == "hub/default/ops/t_1" and key.count("/") > 1
    assert core.card_key({"id": "t_1", "remote": True}).count("/") > 1
