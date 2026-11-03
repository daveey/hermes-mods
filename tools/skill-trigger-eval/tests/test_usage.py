from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import usage  # noqa: E402

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def test_parse_and_merge_across_hosts():
    a = {"x": {"use_count": 2, "last_used_at": "2026-10-30T00:00:00+00:00"},
         "y": {"use_count": 1, "last_used_at": "2026-08-01T00:00:00+00:00"}}
    b = {"y": {"use_count": 4, "last_viewed_at": "2026-10-20T00:00:00+00:00"}, "z": {"use_count": 0}}
    dump_hub = f"### /h/.hermes/skills/.usage.json\n{json.dumps(a)}\n"
    dump_mac = f"### /m/.hermes/skills/.usage.json\n{json.dumps(b)}\n### /m/.hermes/profiles/p/skills/.usage.json\n\n"
    homes = {**usage.parse_dump(dump_hub, "hub"), **usage.parse_dump(dump_mac, "mac")}
    assert set(homes) == {"hub:/h/.hermes/skills/.usage.json", "mac:/m/.hermes/skills/.usage.json"}
    m = usage.merge(homes, NOW)
    assert m["x"]["recent"] and m["x"]["use_count"] == 2
    # y is stale on the hub but recent on the Mac -> not idle; use counts add up.
    assert m["y"]["recent"] and m["y"]["use_count"] == 5 and m["y"]["homes_recent"] == ["mac:/m/.hermes/skills/.usage.json"]
    assert not m["z"]["recent"]
    assert usage.idle(["x", "y", "z", "never"], m) == ["never", "z"]
