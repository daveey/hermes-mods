"""Exercise ask's real completion extraction with mocked provider responses."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _load import load  # noqa: E402

pl = load("pool_llm")


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("stop_reason,text,failed", [
    ("refusal", "", True),
    ("refusal", "I cannot answer that.", True),
    ("max_tokens", "", True),
    ("max_tokens", "  \n", True),
    ("max_tokens", "Partial answer", False),
    ("end_turn", "Answer", False),
])
def test_ask_anthropic_stop_reason(monkeypatch, capsys, json_output, stop_reason, text, failed):
    calls = []

    def respond(body, route=None):
        calls.append(body)
        return {"stop_reason": stop_reason, "content": [
            {"type": "thinking", "thinking": "internal reasoning"},
            *([{"type": "text", "text": text}] if text else []),
        ]}

    monkeypatch.setattr(pl, "anthropic_messages", respond)
    monkeypatch.setattr(pl, "resolve_model", lambda *args: "claude-test")
    rc = pl.main(["ask", "hello", "--provider", "anthropic"] + (["--json"] if json_output else []))
    out = capsys.readouterr()
    assert (rc != 0) == failed
    assert len(calls) == 1  # refusals are not retried on another account
    if failed:
        assert stop_reason in out.err
    else:
        assert out.err == ""
    if json_output:
        result = json.loads(out.out)
        assert result["stop_reason"] == stop_reason
        assert result["text"] == text
    else:
        assert out.out == text + "\n"
    assert "internal reasoning" not in out.out + out.err


def test_ask_codex_unchanged(monkeypatch, capsys):
    monkeypatch.setattr(pl, "resolve_model", lambda *args: "gpt-test")
    monkeypatch.setattr(pl, "codex_responses", lambda *args, **kwargs: {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Answer"}]}],
    })
    assert pl.main(["ask", "hello", "--provider", "openai", "--json"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["text"] == "Answer"
    assert "stop_reason" not in json.loads(out.out)
    assert out.err == ""
