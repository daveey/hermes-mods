"""Normal scan receipts distinguish explicit local evidence from unknown conns."""
import json
from datetime import timedelta

import pytest

from test_backend_timeout_cold_start import Board, END, READY, bt, install, ready, stamp, timeout


@pytest.mark.parametrize("explicit_local,expected", [(True, 2), (False, 0)],
                         ids=["explicit-local-without-conn", "unknown-connection-control"])
def test_normal_scan_retains_explicit_local_without_conn(tmp_path, capsys, explicit_local, expected):
    t = READY + timedelta(seconds=5)
    # Both forms lack conn metadata; only the fleet local marker is local evidence.
    label = "local" if explicit_local else "remote-one"
    fleet = f"{stamp(t)} [hermes] [fleet-roster] {label}: Timed out connecting to Hermes backend after 8000ms"
    install(tmp_path, "\n".join([
        ready(READY), fleet, fleet,
        timeout(t, "/api/x", "?"),
        timeout(t, "/api/y").replace(" conn=local", ""),
    ]) + "\n")
    board = Board()
    assert bt.main(
        ["--home", str(tmp_path), "--host", "fixture", "--threshold", "30", "--window", "30"],
        runner=board, now=END, probe=lambda: {}, power_probe=lambda now: {"intervals": []},
    ) == 0
    output = capsys.readouterr()
    assert output.out == output.err == ""
    rows = (tmp_path / "coach/backend-timeouts-scans.jsonl").read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["schema"] == "coach.backend-timeouts.scan.v1"
    correlation = row["startup_correlation"]
    assert correlation["explicit_local_without_conn_count"] == expected
    assert correlation["count"] == expected
    assert correlation["local_count"] == correlation["primary_count"] == 0
    # Correlation is diagnostic only: it neither subtracts failures nor files a card.
    assert row["raw_count"] == row["awake_count"] == row["residual_count"] == 4
    assert row["owner_actions"] == {}
    assert board.calls == []
