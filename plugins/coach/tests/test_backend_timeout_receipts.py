"""v1 retained-log-scan receipts: normal cron, never a production trigger."""
import json
from datetime import timedelta

import pytest

from test_backend_timeout_cold_start import bt, Board, END, READY, ready, timeout, install


RECEIPT = "coach/backend-timeouts-scans.jsonl"


def invoke(home, board, now=END, extra=()):
    return bt.main(["--home", str(home), "--host", "fixture", "--threshold", "30",
                    "--window", "30", *extra], runner=board, now=now,
                   probe=lambda: {"secret": "never persist probe payload"},
                   power_probe=lambda now: {"intervals": []})


@pytest.mark.parametrize("last_comment,action", [(0, "commented"), (END.timestamp(), "throttled")])
def test_normal_cron_reused_owner_retains_v1_startup_correlation(tmp_path, capsys, last_comment, action):
    install(tmp_path, "\n".join([ready(READY)] + [timeout(READY + timedelta(seconds=5),
            "/api/private?token=not-for-receipt") for _ in range(31)]) + "\n")
    board = Board()
    board.add("t_fixture", "running", "backend timeouts", "/api/private")
    bt.save_state(tmp_path, {bt.card_key("fixture", END): {
        "task_id": "t_fixture", "created": END.timestamp(), "last_comment": last_comment}})
    assert invoke(tmp_path, board) == 0
    assert "31 in 30 min" in capsys.readouterr().out
    row = json.loads((tmp_path / RECEIPT).read_text())
    assert row["schema"] == "coach.backend-timeouts.scan.v1"
    assert row["scope"] == "retained-log-scan; correlation-only; not API readiness or recovery"
    assert row["window_start"] == (END - timedelta(minutes=30)).isoformat(sep=" ")
    assert row["window_end"] == END.isoformat(sep=" ")
    assert row["raw_count"] == row["awake_count"] == row["residual_count"] == 31
    assert row["startup_correlation"] == {"count": 31, "local_count": 31, "primary_count": 0,
        "explicit_local_without_conn_count": 0, "ready_times": [READY.isoformat(sep=" ")],
        "ready_times_omitted": 0}
    assert row["owner_actions"] == {action: 1}
    assert not board.creates()
    assert "token" not in (tmp_path / RECEIPT).read_text()
    assert "secret" not in (tmp_path / RECEIPT).read_text()


def test_normal_healthy_cron_silent_but_retains_receipt(tmp_path, capsys):
    install(tmp_path, ready(END - timedelta(minutes=31)) + "\n")
    board = Board()
    assert invoke(tmp_path, board) == 0
    assert capsys.readouterr().out == ""
    row = json.loads((tmp_path / RECEIPT).read_text())
    assert row["raw_count"] == row["residual_count"] == 0
    assert row["startup_correlation"]["count"] == 0
    assert row["owner_actions"] == {}
    assert not board.calls


def test_dry_run_does_not_emit_scan_receipt(tmp_path, capsys):
    install(tmp_path, ready(END - timedelta(minutes=31)) + "\n")
    assert invoke(tmp_path, Board(), extra=("--dry-run",)) == 0
    assert not (tmp_path / RECEIPT).exists()


def test_normal_cron_rotation_bounds_and_private_modes(tmp_path, capsys, monkeypatch):
    import os
    import stat
    chmod = os.fchmod
    creation_modes = []

    def inspect_before_chmod(fd, mode):
        creation_modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
        chmod(fd, mode)

    monkeypatch.setattr(os, "fchmod", inspect_before_chmod)
    install(tmp_path, ready(END - timedelta(minutes=31)) + "\n")
    for _ in range(400):
        assert invoke(tmp_path, Board()) == 0
    assert capsys.readouterr().out == ""
    files = sorted((tmp_path / "coach").glob("backend-timeouts-scans.jsonl*"))
    data = [p for p in files if p.suffix != ".lock"]
    assert len(data) == 4
    for path in data:
        assert path.stat().st_size <= 64 * 1024
        assert path.stat().st_mode & 0o777 == 0o600
        for line in path.read_text().splitlines():
            assert json.loads(line)["schema"] == "coach.backend-timeouts.scan.v1"
    assert len(files) == 5  # fixed empty lock file plus bounded data
    assert len(creation_modes) > 400  # includes replacement opens during rotation
    assert set(creation_modes) == {0o600}  # private before chmod, not merely afterward


def test_normal_cron_unknown_conn_not_startup_correlated(tmp_path, capsys):
    install(tmp_path, "\n".join([ready(READY), timeout(READY + timedelta(seconds=5), "/api/x", "?"),
                                timeout(READY + timedelta(seconds=5), "/api/x", "primary")]) + "\n")
    assert invoke(tmp_path, Board()) == 0
    row = json.loads((tmp_path / RECEIPT).read_text())
    assert row["raw_count"] == row["residual_count"] == 2
    assert row["startup_correlation"]["count"] == 1
    assert row["startup_correlation"]["primary_count"] == 1
    assert row["startup_correlation"]["explicit_local_without_conn_count"] == 0


def test_normal_cron_receipt_failure_visible_without_filing_retry(tmp_path, capsys):
    install(tmp_path, ready(END - timedelta(minutes=31)) + "\n")
    (tmp_path / "coach").write_text("not a directory")
    board = Board()
    assert invoke(tmp_path, board) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "backend timeout scan receipt persistence failed\n"
    assert not board.calls


def test_receipt_failure_after_filing_does_not_retry_board(tmp_path, capsys):
    install(tmp_path, "\n".join([ready(READY)] +
        [timeout(READY + timedelta(seconds=5), "/api/x") for _ in range(31)]) + "\n")
    (tmp_path / RECEIPT).mkdir(parents=True)  # actual sink open fails, board state remains writable
    board = Board()
    assert invoke(tmp_path, board) == 1
    output = capsys.readouterr()
    assert "31 in 30 min" in output.out
    assert output.err == "backend timeout scan receipt persistence failed\n"
    assert len(board.creates()) == 1
    assert not board.comments()


def test_busy_receipt_lock_has_bounded_wait(tmp_path, capsys, monkeypatch):
    import fcntl
    install(tmp_path, ready(END - timedelta(minutes=31)) + "\n")
    lock_path = tmp_path / (RECEIPT + ".lock")
    lock_path.parent.mkdir()
    ticks = iter([0, 3])
    with lock_path.open("w") as lock, monkeypatch.context() as patch:
        patch.setattr(bt.time, "monotonic", lambda: next(ticks, 3))
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert invoke(tmp_path, Board()) == 1
    assert capsys.readouterr().err == "backend timeout scan receipt persistence failed\n"
    assert not (tmp_path / RECEIPT).exists()


def test_unresolved_clock_is_not_retained_as_healthy_scan(tmp_path, capsys):
    assert invoke(tmp_path, Board()) == 2
    assert not (tmp_path / RECEIPT).exists()
    assert json.loads(capsys.readouterr().out)["status"] == "clock_unresolved"


def test_ready_timestamp_list_is_bounded_with_explicit_omission(tmp_path, capsys):
    lines = []
    for i in range(150):
        t = READY + timedelta(seconds=i)
        lines.extend([ready(t), timeout(t, "/api/x")])
    install(tmp_path, "\n".join(lines) + "\n")
    assert invoke(tmp_path, Board()) == 0
    row = json.loads((tmp_path / RECEIPT).read_text())
    correlation = row["startup_correlation"]
    assert correlation["count"] == 150
    assert len(correlation["ready_times"]) == 128
    assert correlation["ready_times_omitted"] == 22

