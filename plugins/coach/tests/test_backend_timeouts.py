"""backend_timeouts: counts 'Timed out connecting to Hermes backend' lines in desktop.log (live + rotated) over the
last N minutes, grouped by request path; above the threshold it files ONE Kanban card per host per day
(idempotency key backend-timeouts:<host>:<date>) and later firings that day comment on that card instead.
Regression for desktop-64ac180d9e: ~1,200 such lines an hour went unread until an archive failed."""
import importlib.util
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_timeouts_test", ROOT / "backend_timeouts.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)
REAL_PROBE_POWER = getattr(bt, "probe_power", lambda now: {})


@pytest.fixture(autouse=True)
def isolate_power(monkeypatch):
    # Never use this machine's actual power history for fixture log timestamps.
    monkeypatch.setattr(bt, "probe_power", lambda now: {"status": "ok", "intervals": []}, raising=False)


NOW = datetime(2026, 10, 27, 15, 40, 0)
TIMEOUT = "Error: Timed out connecting to Hermes backend after 2000ms"


def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S,") + f"{dt.microsecond // 1000:03d}"


def write_log(home, n, *, now=NOW, path="/api/sessions/20261017_000830_2f28de", name="desktop.log", extra=()):
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    lines = [f"{stamp(now - timedelta(hours=2))} [hermes] [hermes:api GET /api/old] {TIMEOUT}"]  # outside window
    for i in range(n):
        t = now - timedelta(minutes=8) + timedelta(seconds=i * 10)
        p = path if i % 4 else "/api/plugins/kanban/board?board=default"
        lines.append(f"{stamp(t)} [hermes] [hermes:api GET {p}] {TIMEOUT}")
        lines.append("    at ClientRequest.<anonymous> (main.js:5741:20)")  # stack continuation: not counted
    lines.append(f"{stamp(now - timedelta(minutes=1))} [hermes] [renderer console:main] ok line")
    lines.extend(extra)
    (logs / name).write_text("\n".join(lines) + "\n")
    return logs / name


class Runner:
    """Stub for `hermes kanban create|comment`: the idempotency key returns the same id, like the real CLI."""

    def __init__(self):
        self.calls, self.keys = [], {}

    def __call__(self, argv, input=None, **kw):
        self.calls.append((argv, input))
        if argv[:3] == ["hermes", "kanban", "create"]:
            key = argv[argv.index("--idempotency-key") + 1]
            new = key not in self.keys
            tid = self.keys.setdefault(key, f"t_{len(self.keys):08x}")
            d = {"id": tid} if new else {"id": tid, "created_at": NOW.timestamp() - 3600}  # an old card
            return SimpleNamespace(returncode=0, stdout=json.dumps(d), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [c for c in self.calls if c[0][:3] == ["hermes", "kanban", "create"]]

    def comments(self):
        return [c for c in self.calls if c[0][:3] == ["hermes", "kanban", "comment"]]


def run(home, runner, *extra, now=NOW):
    return bt.main(["--home", str(home), "--host", "David-MBP", "--json", *extra], runner=runner, now=now,
                   probe=lambda: {"loadavg": [44.1, 50.2, 70.3], "backend_ps": ["123 281.0 hermes serve"]})


def test_40_timeouts_open_one_card_with_counts_paths_cpu_and_acceptance(tmp_path, capsys):
    write_log(tmp_path, 40)
    r = Runner()
    assert run(tmp_path, r) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 40 and out["over_threshold"] is True
    assert out["by_path"]["/api/sessions/<id>"] == 30
    assert out["by_path"]["/api/plugins/kanban/board"] == 10
    (argv, body), = r.creates()
    assert argv[argv.index("--idempotency-key") + 1] == "backend-timeouts:David-MBP:2026-10-27"
    assert argv[argv.index("--assignee") + 1] == "default"
    assert "40" in argv[-1] and "David-MBP" in argv[-1]
    assert "/api/sessions/<id>" in body and "281.0" in body and "44.1" in body
    assert "zero" in body.lower() and "30-minute" in body  # acceptance: a fresh clean window, not a restart
    assert r.comments() == []


def test_5_timeouts_open_nothing(tmp_path, capsys):
    write_log(tmp_path, 5)
    r = Runner()
    assert run(tmp_path, r) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 5 and out["over_threshold"] is False
    assert r.calls == []


def test_twice_in_one_day_opens_one_card_and_comments_once(tmp_path, capsys):
    r = Runner()
    write_log(tmp_path, 40)
    assert run(tmp_path, r) == 0
    assert run(tmp_path, r, now=NOW + timedelta(minutes=10)) == 0  # next cron tick, still over: throttled
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["cards"][0]["action"] == "throttled"
    assert len(r.creates()) == 1 and r.comments() == []
    later = NOW + timedelta(minutes=61)
    write_log(tmp_path, 40, now=later)
    assert run(tmp_path, r, now=later) == 0  # an hour on, still firing: one comment on the same card
    capsys.readouterr()
    assert len(r.keys) == 1  # one card
    (argv, _), = r.comments()
    assert argv[3] == "t_00000000" and "40 timeouts" in argv[-1]
    assert run(tmp_path, r, now=later + timedelta(minutes=10)) == 0  # throttled again
    assert len(r.comments()) == 1 and len(r.keys) == 1


def test_lost_state_still_comments_on_the_existing_card(tmp_path, capsys):
    r = Runner()
    write_log(tmp_path, 40)
    assert run(tmp_path, r) == 0
    bt.state_path(tmp_path).unlink()
    assert run(tmp_path, r, now=NOW + timedelta(minutes=10)) == 0
    capsys.readouterr()
    assert len(r.keys) == 1 and len(r.comments()) == 1  # the key returned the old card: comment, no duplicate


def test_next_day_opens_a_new_card(tmp_path, capsys):
    r = Runner()
    write_log(tmp_path, 40)
    assert run(tmp_path, r) == 0
    tomorrow = NOW + timedelta(days=1)
    write_log(tmp_path, 40, now=tomorrow)
    assert run(tmp_path, r, now=tomorrow) == 0
    capsys.readouterr()
    assert sorted(r.keys) == ["backend-timeouts:David-MBP:2026-10-27", "backend-timeouts:David-MBP:2026-10-28"]
    assert r.comments() == []


def test_dry_run_creates_nothing(tmp_path, capsys):
    write_log(tmp_path, 40)
    r = Runner()
    assert run(tmp_path, r, "--dry-run") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["over_threshold"] is True and out["cards"] == [] and r.calls == []


def test_rotated_logs_are_read_and_threshold_is_configurable(tmp_path, capsys):
    write_log(tmp_path, 12, name="desktop.log.1")
    write_log(tmp_path, 0)
    r = Runner()
    assert run(tmp_path, r, "--dry-run", "--threshold", "10") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 12 and out["over_threshold"] is True


def test_hours_histogram_reports_bursts_across_rotated_logs(tmp_path, capsys):
    write_log(tmp_path, 40, name="desktop.log.2")
    write_log(tmp_path, 3, now=NOW + timedelta(hours=5))
    assert bt.main(["--home", str(tmp_path), "--hours", "--json"], runner=Runner(), now=NOW + timedelta(hours=5)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hours"]["2026-10-27 15"] == 40
    assert out["hours"]["2026-10-27 20"] == 3


def power_line(dt, event):
    # Use the runner's own local offset so tests also work on UTC Linux hosts.
    stamp = dt.astimezone().strftime('%Y-%m-%d %H:%M:%S %z')
    detail = {'Sleep': 'Entering Sleep state', 'DarkWake': 'DarkWake from Deep Idle',
              'Wake': 'Wake from Deep Idle'}[event]
    return f'{stamp} {event:20s} {detail}'


def test_sleep_darkwake_full_wake_and_no_grace():
    start = NOW - timedelta(minutes=10)
    dark = NOW - timedelta(minutes=7)
    wake = NOW - timedelta(minutes=2)
    text = '\n'.join([power_line(start, 'Sleep'), power_line(dark, 'DarkWake'),
                      power_line(dark + timedelta(seconds=3), 'Sleep'),
                      power_line(wake, 'Wake')])
    assert bt.power_intervals(text, NOW) == [(start, wake)]
    # An open DarkWake is positive evidence too; a lone full wake is not.
    assert bt.power_intervals(power_line(dark, 'DarkWake'), NOW) == [(dark, NOW)]
    assert bt.power_intervals(power_line(wake, 'Wake'), NOW) == []
    entering_dark = power_line(start, 'Sleep').replace('Entering Sleep state', 'Entering DarkWake state')
    assert bt.power_intervals(entering_dark + '\n' + power_line(wake, 'Wake'), NOW) == [(start, wake)]
    assert bt.power_intervals('2026-10-27 15:00:00 +0000 Wake Requests next wake', NOW) == []


def test_darkwake_to_fullwake_does_not_hide_awake_failures(tmp_path):
    start = NOW - timedelta(minutes=9)
    wake = NOW - timedelta(minutes=8)
    wake_stamp = wake.astimezone().strftime('%Y-%m-%d %H:%M:%S %z')
    text = power_line(start, 'DarkWake') + '\n' + (
        f'{wake_stamp} Wake                \tDarkWake to FullWake from Deep Idle [CDNVA] : due to HID Activity')
    intervals = bt.power_intervals(text, NOW)
    assert intervals == [(start, wake)]
    write_log(tmp_path, 40)
    out = bt.scan(tmp_path, NOW, 30, intervals)
    # The entry at the wake second is retained as a sleep timer; all later ones alert.
    assert (out['count'], out['sleep_count'], out['awake_count']) == (40, 1, 39)


def test_sleep_only_burst_keeps_raw_count_but_opens_no_card(tmp_path, capsys, monkeypatch):
    write_log(tmp_path, 40)
    monkeypatch.setattr(bt, 'probe_power', lambda now: {'status': 'ok', 'intervals': [(NOW - timedelta(minutes=9), NOW)]})
    r = Runner()
    run(tmp_path, r)
    out = json.loads(capsys.readouterr().out)
    assert out['count'] == 40 and sum(out['by_path'].values()) == 40
    assert out['sleep_count'] == 40 and out['awake_count'] == 0
    assert not out['over_threshold'] and not r.calls


def test_mixed_burst_still_alerts_for_awake_failures(tmp_path, capsys, monkeypatch):
    write_log(tmp_path, 40)
    # First ten entries are asleep; thirty are awake, including just after wake.
    wake = NOW - timedelta(minutes=8) + timedelta(seconds=90)
    monkeypatch.setattr(bt, 'probe_power', lambda now: {'status': 'ok', 'intervals': [(NOW - timedelta(minutes=9), wake)]})
    r = Runner()
    run(tmp_path, r)
    out = json.loads(capsys.readouterr().out)
    assert (out['count'], out['sleep_count'], out['awake_count']) == (40, 10, 30)
    assert sum(out['awake_by_path'].values()) == 30
    assert out['over_threshold'] and len(r.creates()) == 1
    assert '30 occurred while awake' in r.creates()[0][1]


def test_wake_second_included_but_next_second_is_awake(tmp_path):
    write_log(tmp_path, 0, extra=[
        f'{stamp(NOW - timedelta(seconds=1))} [hermes] /api/status {TIMEOUT}',
        f'{stamp(NOW)} [hermes] /api/status {TIMEOUT}',
    ])
    wake = NOW - timedelta(seconds=1)
    out = bt.scan(tmp_path, NOW, 30, [(NOW - timedelta(minutes=1), wake)])
    assert (out['count'], out['sleep_count'], out['awake_count']) == (2, 1, 1)


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'missing'])
def test_power_probe_failure_never_suppresses_alerts(tmp_path, capsys, monkeypatch, failure):
    monkeypatch.setattr(bt.sys, 'platform', 'darwin')
    def failed(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('pmset', 10)
        if failure == 'missing':
            raise FileNotFoundError('pmset')
        return SimpleNamespace(returncode=1, stdout='', stderr='denied')
    monkeypatch.setattr(bt.subprocess, 'run', failed)
    power = REAL_PROBE_POWER(NOW)
    assert power['status'] == 'failed' and power['intervals'] == []
    write_log(tmp_path, 40)
    r = Runner()
    bt.main(['--home', str(tmp_path), '--host', 'test', '--json'], runner=r, now=NOW,
            probe=lambda: {}, power_probe=lambda now: power)
    out = json.loads(capsys.readouterr().out)
    assert out['awake_count'] == 40 and out['over_threshold'] and len(r.creates()) == 1


def test_real_probe_parses_pmset_contract(monkeypatch):
    monkeypatch.setattr(bt.sys, 'platform', 'darwin')
    start = NOW - timedelta(minutes=5)
    wake = NOW - timedelta(minutes=1)
    def pmset(argv, **kwargs):
        assert argv == ['/usr/bin/pmset', '-g', 'log'] and kwargs['timeout'] == 30
        return SimpleNamespace(returncode=0, stdout=power_line(start, 'Sleep') + '\n' + power_line(wake, 'Wake'))
    monkeypatch.setattr(bt.subprocess, 'run', pmset)
    assert REAL_PROBE_POWER(NOW) == {'status': 'ok', 'intervals': [(start, wake)]}


def test_unsupported_platform_never_calls_pmset(monkeypatch):
    monkeypatch.setattr(bt.sys, 'platform', 'linux')
    monkeypatch.setattr(bt.subprocess, 'run', lambda *a, **kw: pytest.fail('must not call pmset'))
    assert REAL_PROBE_POWER(NOW) == {'status': 'unsupported', 'intervals': []}


def test_awake_timeouts_are_split_by_conn_and_time_since_wake(tmp_path):
    wake = NOW - timedelta(minutes=20)
    write_log(tmp_path, 0, extra=[
        f'{stamp(wake + timedelta(seconds=20))} [hermes] [hermes:api GET /api/plugins/hub-sync/status] conn=hermes-hub {TIMEOUT}',
        f'{stamp(wake + timedelta(minutes=4))} [hermes] [hermes:api GET /api/plugins/hub-sync/status] conn=hermes-hub {TIMEOUT}',
        f'{stamp(wake + timedelta(minutes=5))} [hermes] [hermes:api GET /api/plugins/inbox/items] conn=mac-mini {TIMEOUT}',
        f'{stamp(NOW - timedelta(minutes=1))} [hermes] /api/status {TIMEOUT}',
    ])
    out = bt.scan(tmp_path, NOW, 30, [(wake - timedelta(minutes=5), wake)])
    assert out['awake_count'] == 4 and out['sleep_count'] == 0
    assert out['awake_by_conn'] == {'hermes-hub': 2, 'mac-mini': 1, '?': 1}
    assert out['awake_since_wake'] == {'<1m': 1, '<10m': 2, '>=10m': 1}


def test_awake_split_without_any_wake_is_no_wake_seen(tmp_path):
    write_log(tmp_path, 3)
    out = bt.scan(tmp_path, NOW, 30, [])
    assert out['awake_since_wake'] == {'no_wake_seen': 3}
