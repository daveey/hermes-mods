"""Explicit service ownership must survive detached process trees."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def c(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "collect_owners", Path(__file__).parents[1] / "scripts/collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HOME", tmp_path)
    return mod


def table(c, tmp_path):
    t = c.parse_ps("901 1 4.0 1024 01:00 /usr/bin/python3 collector.py\n902 901 0.0 1024 00:01 /usr/bin/python3 child.py")
    t[901]["cwd"] = str(tmp_path)
    t[902]["cwd"] = str(tmp_path / "child")
    return t


def test_owner_file_in_cwd(c, tmp_path):
    (tmp_path / "OWNER.md").write_text("card: t_abc123@worker\nCollector\n")
    agent = c.owner(table(c, tmp_path), {}, 901)
    assert c.label(agent) == "card:t_abc123@worker"
    assert agent["owner_source"] == str(tmp_path / "OWNER.md")


def test_explicit_env_outside_hermes_tree(c, tmp_path):
    tags = c.parse_env_tags("902 python HERMES_OWNER_CARD=t_abc123@worker")
    agent = c.owner(table(c, tmp_path), tags, 902)
    assert c.label(agent) == "card:t_abc123@worker"
    assert agent["owner_source"] == "env"


def test_launchd_service_refused_even_for_done_card(c, tmp_path):
    t = table(c, tmp_path)
    t[901]["service"] = "example.collector"
    t[901]["service_manager"] = "launchd"
    ok, why = c.kill_verdict(t, {901: {"HERMES_KANBAN_TASK": "t_abc123"}}, 901, status_fn=lambda _: "done")
    assert not ok
    assert "a launchd service example.collector: ask on its owner card" in why


def test_owner_file_done_local_card_allowed(c, tmp_path):
    (tmp_path / "OWNER.md").write_text("card: t_abc123\n")
    ok, why = c.kill_verdict(table(c, tmp_path), {}, 901, status_fn=lambda _: "done")
    assert ok, why


def test_owner_file_remote_unknown_card_refused(c, tmp_path):
    (tmp_path / "OWNER.md").write_text("card: t_abc123@worker\n")
    ok, why = c.kill_verdict(table(c, tmp_path), {}, 901, status_fn=lambda _: None)
    assert not ok
    assert "t_abc123@worker" in why and "unknown here" in why


def test_owner_from_ancestor_cwd(c, tmp_path):
    (tmp_path / "OWNER.md").write_text("card: t_abc123@worker\n")
    assert c.label(c.owner(table(c, tmp_path), {}, 902)) == "card:t_abc123@worker"


def test_xpc_tag_refuses_kill(c, tmp_path):
    ok, why = c.kill_verdict(table(c, tmp_path), {901: {
        "XPC_SERVICE_NAME": "example.collector", "HERMES_OWNER_CARD": "t_abc123"}},
        902, status_fn=lambda _: "done")
    assert not ok and "example.collector" in why


def test_discover_launchd_pid_and_cwd(c, tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(c, "MAC", True)
    def run(cmd, **kw):
        if cmd[:2] == ["launchctl", "list"]:
            return SimpleNamespace(stdout="PID Status Label\n901 0 example.collector\n", returncode=0)
        if "lsof" in cmd[0]:
            return SimpleNamespace(stdout=f"p901\nn{tmp_path}\n", returncode=0)
        raise AssertionError(cmd)
    monkeypatch.setattr(c.subprocess, "run", run)
    t = table(c, tmp_path)
    t[901].pop("cwd")
    c.annotate_processes(t)
    assert t[901]["cwd"] == str(tmp_path)
    assert t[901]["service"] == "example.collector"
    assert not c.kill_verdict(t, {901: {"HERMES_OWNER_CARD": "t_abc123"}}, 902,
                              status_fn=lambda _: "done")[0]


def test_systemd_cgroup_service(c, tmp_path, monkeypatch):
    monkeypatch.setattr(c, "MAC", False)
    proc = tmp_path / "proc/901"
    proc.mkdir(parents=True)
    (proc / "cgroup").write_text("0::/user.slice/user@1000.service/app.slice/example.service\n")
    t = table(c, tmp_path)
    c.annotate_processes(t, proc_root=tmp_path / "proc")
    assert t[901]["service"] == "example.service"
    ok, why = c.kill_verdict(t, {901: {"HERMES_OWNER_CARD": "t_abc123"}}, 902,
                             status_fn=lambda _: "done")
    assert not ok and "a systemd service example.service" in why


def test_report_rows_expose_owners_and_services(c, tmp_path):
    (tmp_path / "OWNER.md").write_text("card: t_abc123@worker\n")
    t = table(c, tmp_path)
    t[901].update(service="example.collector", service_manager="launchd")
    out = c.processes(t, tags_fn=lambda _: {})
    for row in (out["top"][0], out["young_by_name"][0]):
        assert row["owner"] == "card:t_abc123@worker"
        assert row["service"] == "example.collector"
        assert row["owner_source"] == str(tmp_path / "OWNER.md")
    assert {r["pid"] for r in out["owned_services"]} == {901, 902}


def test_gateway_descendant_is_not_the_gateway_service(c, tmp_path):
    t = table(c, tmp_path)
    t[901].update(name="hermes gateway", service="example.gateway", service_manager="launchd")
    assert c.kill_verdict(t, {902: {"HERMES_OWNER_CARD": "t_abc123"}}, 902,
                          status_fn=lambda _: "done")[0]


def test_remote_done_shadow_is_not_local_authority(c, tmp_path):
    (tmp_path / "OWNER.md").write_text("card: t_abc123@remote-host\n")
    assert not c.kill_verdict(table(c, tmp_path), {}, 901, status_fn=lambda _: "done")[0]


def test_login_scope_is_not_a_systemd_service(c, tmp_path, monkeypatch):
    monkeypatch.setattr(c, "MAC", False)
    proc = tmp_path / "proc/901"
    proc.mkdir(parents=True)
    (proc / "cgroup").write_text("0::/user.slice/user-1000.slice/session-1.scope\n")
    t = table(c, tmp_path)
    c.annotate_processes(t, proc_root=tmp_path / "proc")
    assert not t[901].get("service")


def test_plist_match_tolerates_unquoted_spaces_and_other_bad_argv(c, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import plistlib
    root = tmp_path / "Library/LaunchAgents"
    root.mkdir(parents=True)
    argv = ["/opt/Application Support/python3", "collector.py"]
    with (root / "example.plist").open("wb") as f:
        plistlib.dump({"Label": "example.collector", "ProgramArguments": argv}, f)
    monkeypatch.setattr(c, "MAC", True)
    monkeypatch.setattr(c.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        stdout="- 0 example.collector\n" if cmd[0] == "launchctl" else "", returncode=0))
    t = table(c, tmp_path)
    t = {900: dict(t[901], args="echo don't"), **t}
    t[901]["args"] = " ".join(argv)
    c.annotate_processes(t)
    assert t[901]["service"] == "example.collector"


def test_gui_application_label_is_not_a_background_service(c, tmp_path):
    t = table(c, tmp_path)
    t[901].update(service="application.example.Terminal.123", service_manager="launchd")
    assert not c.service_info(t, {}, 902)["service"]


def test_systemd_gateway_cgroup_does_not_own_workers(c, tmp_path):
    t = table(c, tmp_path)
    t[901].update(name="hermes gateway", service="example.gateway.service", service_manager="systemd")
    t[902].update(service="example.gateway.service", service_manager="systemd")
    assert c.kill_verdict(t, {902: {"HERMES_OWNER_CARD": "t_abc123"}}, 902,
                          status_fn=lambda _: "done")[0]


def test_service_refusal_precedes_generic_gui_guard(c, tmp_path):
    t = table(c, tmp_path)
    t[901].update(args="/opt/Python.app/Contents/MacOS/Python collector.py",
                  service="example.collector", service_manager="launchd")
    ok, why = c.kill_verdict(t, {}, 901)
    assert not ok and "a launchd service example.collector" in why


def test_loaded_launchd_program_arguments_fallback(c, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import plistlib
    root = tmp_path / "Library/LaunchAgents"
    root.mkdir(parents=True)
    with (root / "example.plist").open("wb") as f:
        plistlib.dump({"Label": "example.collector", "ProgramArguments":
                      ["/usr/bin/python3", "collector.py"]}, f)
    monkeypatch.setattr(c, "MAC", True)
    monkeypatch.setattr(c.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        stdout="- 0 example.collector\n" if cmd[0] == "launchctl" else "", returncode=0))
    t = table(c, tmp_path)
    c.annotate_processes(t)
    assert t[901]["service"] == "example.collector"
    assert not c.kill_verdict(t, {901: {"HERMES_OWNER_CARD": "t_abc123"}}, 901,
                              status_fn=lambda _: "archived")[0]
