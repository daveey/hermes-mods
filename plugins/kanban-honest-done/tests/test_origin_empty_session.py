"""A placeholder link with no session id (`@session:default/`, written for cron-run defects) is not an origin chat."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("hd_core_empty", Path(__file__).parents[1] / "core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def test_empty_session_id_is_not_an_origin():
    text = ("Each run printed only `cannot check fork main: ... git fetch origin ... returned non-zero exit status 128`."
            " Session: @session:default/ (a cron run, so there is no chat quote).")
    assert core.origin_sessions(text) == []


def test_real_origin_links_still_parse():
    assert core.origin_sessions("Origin chat: @session:default@macbook/20261025_233046_d48ade (x)") == ["20261025_233046_d48ade"]
    assert core.origin_sessions("origin @session:default/20261025_233046_d48ade") == ["20261025_233046_d48ade"]
    assert core.origin_sessions("origin_session=@session:20261025_233046_d48ade") == ["20261025_233046_d48ade"]
    assert core.origin_sessions("report to origin @session:default/20261025_233046_d48ade.") == ["20261025_233046_d48ade"]
