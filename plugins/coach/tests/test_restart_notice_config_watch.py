"""restart_notice_config_watch: a profile whose home channel (config.yaml or .env) lacks
gateway_restart_notification: false is flagged; an explicit false (top level or extra), an allow-list entry, or no
home channel stays quiet; --dry-run files nothing; a real run files one card per finding with a stable key."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("restart_notice_config_watch_test", ROOT / "restart_notice_config_watch.py")
rw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rw)

HOME_CFG = "platforms:\n  slack:\n    enabled: true\n    home_channel:\n      platform: slack\n      chat_id: C0TEAM\n      name: team\n"


def _profile(home: Path, name: str, cfg: str, env: str = "") -> Path:
    d = home / "profiles" / name
    d.mkdir(parents=True)
    (d / "config.yaml").write_text(cfg)
    if env:
        (d / ".env").write_text(env)
    return d


def _keys(res):
    return sorted(f"{f['profile']}:{f['platform']}" for f in res["findings"])


def test_flags_home_channel_without_opt_out(tmp_path):
    (tmp_path / "config.yaml").write_text("model: {}\n")
    _profile(tmp_path, "teambot", HOME_CFG)
    _profile(tmp_path, "explicit-true", HOME_CFG.replace("    enabled: true\n", "    gateway_restart_notification: true\n"))
    res = rw.scan(str(tmp_path))
    assert _keys(res) == ["explicit-true:slack", "teambot:slack"]
    f = next(x for x in res["findings"] if x["profile"] == "teambot")
    assert f["home_channel"] == "C0TEAM" and f["home_source"] == "config.yaml" and f["gateway_restart_notification"] is None


def test_env_home_channel_counts(tmp_path):
    _profile(tmp_path, "envbot", "platforms:\n  slack:\n    enabled: true\n", env="SLACK_HOME_CHANNEL=C0ENV\nSLACK_HOME_CHANNEL_NAME=general\n")
    res = rw.scan(str(tmp_path))
    assert _keys(res) == ["envbot:slack"]
    assert res["findings"][0]["home_source"] == ".env" and res["findings"][0]["home_channel_name"] == "general"


def test_quiet_cases(tmp_path):
    # top-level false (alpha-askeve's shape), false under extra, string "false", flow-style YAML, no home channel,
    # empty env value
    _profile(tmp_path, "toplevel", HOME_CFG + "    gateway_restart_notification: false\n")
    _profile(tmp_path, "extra", HOME_CFG + "    extra:\n      gateway_restart_notification: false\n")
    _profile(tmp_path, "string", HOME_CFG + "    gateway_restart_notification: 'false'\n")
    _profile(tmp_path, "flow", '{"platforms": {"slack": {"enabled": true, gateway_restart_notification: false}}}\n',
             env="SLACK_HOME_CHANNEL=C0X\n")
    _profile(tmp_path, "nohome", "platforms:\n  slack:\n    enabled: true\n", env="SLACK_HOME_CHANNEL=\n")
    res = rw.scan(str(tmp_path))
    assert res["findings"] == [] and res["errors"] == []
    assert set(res["profiles_checked"]) >= {"toplevel", "extra", "string", "flow", "nohome"}


def test_allow_list(tmp_path):
    _profile(tmp_path, "dmbot", HOME_CFG)
    _profile(tmp_path, "teambot", HOME_CFG)
    (tmp_path / rw.ALLOW_FILE).write_text("# owner DM, notices wanted\ndmbot:slack\n")
    res = rw.scan(str(tmp_path))
    assert _keys(res) == ["teambot:slack"]
    assert [f"{a['profile']}:{a['platform']}" for a in res["allowed"]] == ["dmbot:slack"]
    (tmp_path / rw.ALLOW_FILE).write_text("dmbot\nteambot\n")
    assert rw.scan(str(tmp_path))["findings"] == []


def test_unreadable_config_is_reported(tmp_path):
    _profile(tmp_path, "broken", "platforms: [unclosed\n")
    res = rw.scan(str(tmp_path))
    assert res["findings"] == [] and [e["profile"] for e in res["errors"]] == ["broken"]


def test_dry_run_files_nothing_and_real_run_files_one_card_each(tmp_path, capsys):
    _profile(tmp_path, "teambot", HOME_CFG)
    calls = []

    def runner(argv, **kw):
        calls.append((argv, kw.get("input")))
        return SimpleNamespace(returncode=0, stdout='{"id": "t_x"}', stderr="")

    assert rw.main(["--dry-run", "--home", str(tmp_path)], runner=runner) == 0
    assert calls == []
    assert _keys(json.loads(capsys.readouterr().out)) == ["teambot:slack"]

    assert rw.main(["--home", str(tmp_path)], runner=runner) == 0
    assert len(calls) == 1
    argv, body = calls[0]
    key = argv[argv.index("--idempotency-key") + 1]
    assert key.startswith("restart-notice-config:") and key.endswith(":teambot:slack")
    assert "hermes -p teambot config set platforms.slack.gateway_restart_notification false" in body
    assert "teambot:slack" in capsys.readouterr().out


def test_clean_run_prints_nothing(tmp_path, capsys):
    _profile(tmp_path, "ok", HOME_CFG + "    gateway_restart_notification: false\n")
    assert rw.main(["--home", str(tmp_path)], runner=lambda *a, **k: 1 / 0) == 0
    assert capsys.readouterr().out == ""
