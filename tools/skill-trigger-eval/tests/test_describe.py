from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import describe  # noqa: E402

DRAFT = {"short": "Use when the Mac disk is full. Find and clean safely.",
         "pdf": 'Freed disk space report. Input: a full Mac disk. Asks: "disk full"; "free space"; "clean up storage". '
                "Not for: CI runner disk (use ci-runner-disk-recovery).",
         "asks": ["disk full", "free space", "clean up storage"],
         "not_for": "CI runner disk (use ci-runner-disk-recovery)"}


def test_parse_accepts_fenced_json():
    d = describe.parse("```json\n" + json.dumps(DRAFT) + "\n```")
    assert d == DRAFT


@pytest.mark.parametrize("key,val,msg", [("short", "x" * 61, "short is 61"), ("pdf", "y" * 321, "pdf is 321"),
                                         ("asks", ["a", "b"], "need 3 asks")])
def test_parse_enforces_limits(key, val, msg):
    with pytest.raises(ValueError, match=msg):
        describe.parse(json.dumps(dict(DRAFT, **{key: val})))


def test_overrides_per_format():
    ov = describe.overrides({"mac-disk-cleanup": DRAFT})
    assert ov["a2"] == {"mac-disk-cleanup": {"description": DRAFT["short"]}}
    assert ov["b"] == {"mac-disk-cleanup": {"description": DRAFT["pdf"]}}
    assert ov["c"]["mac-disk-cleanup"] == {"description": DRAFT["short"],
                                           "triggers": {"asks": DRAFT["asks"], "not_for": DRAFT["not_for"]}}


def test_confused_with_reads_collisions_and_steals():
    m = {"collisions": {"a|b": ["x"], "c|d": ["y"]}, "steals": {"a>e": ["z"], "f>a": ["w"]}}
    assert describe.confused_with(m, "a") == ["b", "e", "f"]
    assert describe.confused_with(m, "q") == []


def test_prompt_never_contains_card_asks():
    p = describe.prompt("s", "desc", "body", ["t"], {"s": "desc", "t": "other"})
    assert "should_load" not in p and "near_miss" not in p and "t (other)" in p
