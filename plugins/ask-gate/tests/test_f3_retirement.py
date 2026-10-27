"""Sanitized regression tests for durable terminal answers and fresh preflight."""
import time

import pytest


@pytest.mark.parametrize("answer", [
    "The account was revoked.",
    "The subscription was cancelled.",
    "That step is completed.",
    "That action is impossible on this platform.",
    "The project is cancelled; stop this workflow.",
])
def test_terminal_answer_survives_recency_window(gate, tmp_path, answer):
    path = tmp_path / "answers.sqlite"
    led = gate.core.Ledger(path)
    led.add("Example service action", answer, session="origin", ts=time.time() - 90 * 86400)
    # Reopen to prove persistence, not an in-memory cache.
    rows = gate.core.Ledger(path).relevant("origin")
    assert len(rows) == 1
    assert rows[0]["answer"] == answer
    assert rows[0]["retired"] is True
    assert gate.core.Ledger(path).relevant("unrelated") == []


def test_old_nonterminal_answer_expires(gate, tmp_path):
    led = gate.core.Ledger(tmp_path / "answers.sqlite")
    led.add("Example deploy", "Perhaps tomorrow", session="origin", ts=time.time() - 90 * 86400)
    assert led.relevant("origin") == []


@pytest.mark.parametrize("answer", [
    "The subscription is not cancelled.",
    "The account has not been revoked.",
    "If the project is cancelled, keep the logs.",
    "Can the step be completed?",
    "I will cancel the subscription tomorrow.",
])
def test_nonterminal_statements_do_not_retire(gate, tmp_path, answer):
    led = gate.core.Ledger(tmp_path / "answers.sqlite")
    led.add("Example service", answer, session="origin")
    assert led.relevant("origin")[0]["retired"] is False


def test_terminal_answers_not_evicted_by_recent_chatter(gate, tmp_path):
    led = gate.core.Ledger(tmp_path / "answers.sqlite")
    led.add("Example account", "The account is revoked.", session="origin", ts=time.time() - 90 * 86400)
    for n in range(25):
        led.add(f"Question {n}", "Maybe later", session="origin")
    assert any(row["retired"] for row in led.relevant("origin", limit=2))


def test_judge_refreshes_capabilities_before_manual_delegation(gate, monkeypatch):
    calls = []
    monkeypatch.setattr(gate.core, "capability_sheet", lambda **kw: calls.append(kw) or "cf-token-minter (auto)")
    monkeypatch.setattr(gate.judge_mod, "judge", lambda *a, **kw: [{"i": 0, "verdict": "ok"}])
    gate.run_judge("origin", ["Please change the Cloudflare setting yourself."], [])
    assert calls == [{"force": True}]
