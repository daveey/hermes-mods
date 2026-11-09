"""pre_verify_latency_watch: p90 of core's `pre_verify done ... seconds=` lines over a day > limit files ONE card per
host+day (self-heal agent-7aa110cfd7); quoted lines never count; dry-run files nothing."""
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pre_verify_latency_watch_test", ROOT / "pre_verify_latency_watch.py")
pw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pw)

NOW = 1793300000.0


def _line(ts: float, seconds: float, sid="s1", continued=False) -> str:
    stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    return (f"{stamp},123 INFO [{sid}] agent.conversation_loop: pre_verify done session={sid} "
            f"seconds={seconds:.2f} continued={continued} attempt=0\n")


def _logs(home: Path, lines, name="agent.log"):
    (home / "logs").mkdir(parents=True, exist_ok=True)
    with (home / "logs" / name).open("a") as f:
        f.writelines(lines)


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, input=None, **kw):
        self.calls.append((argv, input))
        out = json.dumps({"id": "t_card1"}) if argv[:3] == ["hermes", "kanban", "create"] else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")


def test_parse_anchored_at_line_start():
    assert pw.parse_line(_line(NOW, 23.4))["seconds"] == 23.4
    assert pw.parse_line(_line(NOW, 1.0).replace("[s1] ", ""))["seconds"] == 1.0  # no session stamp
    quoted = "2026-11-09 15:00:00,001 INFO agent.tools: tool call grep 'pre_verify done session=x seconds=99.00 continued=True'"
    assert pw.parse_line(quoted) is None
    assert pw.parse_line("  " + _line(NOW, 50)) is None
    assert pw.p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 30]) == 9 and pw.p90([]) is None


def test_files_one_card_after_a_day_of_slow_turns_then_nothing_more(tmp_path):
    home = tmp_path
    r = Runner()
    # day 0: first run seeds the window (span 0 -> nothing filed even though slow)
    _logs(home, [_line(NOW - 3600 + i * 60, 25.0) for i in range(30)])
    assert pw.main(["--home", str(home), "--host", "hub"], runner=r, now=NOW) == 0
    assert r.calls == []
    # a day later, still slow: the window spans >= 23 h and has >= 20 samples -> one card
    later = NOW + 23.5 * 3600
    _logs(home, [_line(later - 1800 + i * 30, 24.0, sid="s2") for i in range(25)])
    pw.main(["--home", str(home), "--host", "hub"], runner=r, now=later)
    creates = [c for c in r.calls if c[0][:3] == ["hermes", "kanban", "create"]]
    assert len(creates) == 1
    argv, body = creates[0]
    assert "pre-verify-latency:hub:" in " ".join(argv) and "--assignee" in argv
    assert "p90" in body and "elapsed_s" in body and "Inbox" not in body
    # another slow hour the same day: no second card, no comment
    _logs(home, [_line(later + 600 + i, 40.0, sid="s3") for i in range(5)])
    pw.main(["--home", str(home), "--host", "hub"], runner=r, now=later + 1200)
    assert len([c for c in r.calls if c[0][:3] == ["hermes", "kanban", "create"]]) == 1
    assert not any(c[0][:3] == ["hermes", "kanban", "comment"] for c in r.calls)


def test_fast_day_files_nothing(tmp_path):
    r = Runner()
    _logs(tmp_path, [_line(NOW - 3600 + i * 60, 3.0) for i in range(40)] + [_line(NOW - 30, 45.0)])
    pw.main(["--home", str(tmp_path), "--host", "hub"], runner=r, now=NOW)
    pw.main(["--home", str(tmp_path), "--host", "hub"], runner=r, now=NOW + 600)
    assert r.calls == []
    st = json.loads(pw.state_path(tmp_path).read_text())
    assert st["last_stats"]["count"] == 41  # a rescan of the same log does not double-count
    pw.main(["--home", str(tmp_path), "--host", "hub"], runner=r, now=NOW + 24 * 3600 - 10)
    assert r.calls == []  # a whole day, p90 3 s: nothing filed
    assert json.loads(pw.state_path(tmp_path).read_text())["last_stats"]["count"] == 0  # all aged out of 24 h


def test_samples_survive_rotation_and_are_not_double_counted(tmp_path):
    r = Runner()
    _logs(tmp_path, [_line(NOW - 100, 5.0), _line(NOW - 50, 6.0)])
    pw.main(["--home", str(tmp_path)], runner=r, now=NOW)
    (tmp_path / "logs" / "agent.log").rename(tmp_path / "logs" / "agent.log.1")
    _logs(tmp_path, [_line(NOW + 10, 7.0)])
    pw.main(["--home", str(tmp_path)], runner=r, now=NOW + 20)
    st = json.loads(pw.state_path(tmp_path).read_text())
    assert st["last_stats"]["count"] == 3


def test_dry_run_files_and_saves_nothing(tmp_path, capsys):
    r = Runner()
    _logs(tmp_path, [_line(NOW - 60 * i, 30.0) for i in range(30)])
    pw.main(["--home", str(tmp_path), "--dry-run"], runner=r, now=NOW)
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 30 and out["p90_s"] == 30.0
    assert r.calls == [] and not pw.state_path(tmp_path).exists()
