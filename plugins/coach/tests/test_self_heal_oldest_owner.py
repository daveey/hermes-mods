"""Competing fingerprint owners must route occurrences to the oldest card."""
import pytest

from test_self_heal_one_card import NOW, defect, env, events, file, sh


@pytest.mark.parametrize("via", ["key", "body"])
@pytest.mark.parametrize("same_created_at", [False, True], ids=["age", "id-tiebreak"])
def test_oldest_matching_owner_receives_occurrence(env, via, same_created_at):
    d = defect()
    fp = d["fingerprint"]
    # For unequal ages, lexical ID order opposes age order. For equal ages,
    # insertion order opposes the ascending-ID tiebreak. Neither can win by luck.
    older = "t_a_owner" if same_created_at else "t_z_owner"
    newer = "t_z_owner" if same_created_at else "t_a_owner"
    for tid, suffix, created_at in (
        (newer, "r2", NOW - 20 if same_created_at else NOW - 10),
        (older, "r1", NOW - 20),
    ):
        env.board.add(
            tid,
            key=f"selfheal:{fp}:{suffix}" if via == "key" else f"manual:{suffix}",
            body=sh.with_fp_line("An existing defect owner.", fp) if via == "body" else "",
            created_at=created_at,
        )

    out = file(env, d)

    assert out["action"] == "skip" and out["dedup"] is True
    assert out["card"] == older
    assert env.board.creates() == []
    ((card, body),) = env.board.comments
    assert card == older and fp in body and d["session_id"] in body
    (event,) = events(env.store, "selfheal_dedup_commented")
    assert event["card"] == older and event["via"] == via and event["ok"] is True
