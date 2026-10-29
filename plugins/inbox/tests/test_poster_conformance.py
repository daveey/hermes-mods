"""CI conformance: every literal Inbox poster in hermes-mods, daveey/hermes (state) and hermes-hub is registered.

Roots beyond hermes-mods come from ``HERMES_POSTER_ROOTS`` (os.pathsep list) or, when present on this host,
~/code/hermes-hub, ~/code/hermes, ~/code/pr and ~/.hermes/scripts. A root that does not exist is skipped, so the
public CI (mods checkout only) still runs the mods scan and the planted-poster regression.

REFUSED_BY_DESIGN lists posters that stay unregistered on purpose: they post status/failure notices that belong on
Kanban, so the post-time gate refuses them and files a Kanban card. Each entry is proven refused below; adding a
new unregistered poster elsewhere fails ``test_every_literal_poster_is_registered_or_refused_by_design``.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).parent))
import ask_contract  # noqa: E402
import poster_scan  # noqa: E402

REPO = HERE.parents[1]
REFUSED_BY_DESIGN = {"hermes-hub", "hermes-mail", "bsky-cron-error"}


def roots():
    env = os.environ.get("HERMES_POSTER_ROOTS")
    cands = ([Path(p) for p in env.split(os.pathsep) if p] if env else
             [Path.home() / "code" / "hermes-hub", Path.home() / "code" / "hermes", Path.home() / "code" / "pr",
              Path.home() / ".hermes" / "scripts"])
    return [REPO] + [c for c in cands if c.is_dir()]


def unregistered(hits, home):
    reg = ask_contract.registry(home)
    return [h for h in hits if ask_contract.family(h["poster"]) not in reg
            and ask_contract.family(h["poster"]) not in REFUSED_BY_DESIGN]


def test_every_literal_poster_is_registered_or_refused_by_design(tmp_path):
    hits = [dict(h, root=str(r)) for r in roots() for h in poster_scan.scan(r)]
    assert hits, "scanner found no poster call sites: the conformance guard would be vacuous"
    bad = unregistered(hits, tmp_path)
    assert not bad, "unregistered Inbox posters (register in plugins/inbox/posters.yaml or post to Kanban):\n" + \
        "\n".join(f"{h['root']}/{h['site']}: {h['poster']!r}" for h in bad)


def test_planted_unregistered_poster_fails_conformance(tmp_path):
    src = tmp_path / "other-repo" / "client"
    src.mkdir(parents=True)
    (src / "notify.py").write_text(
        "def go(inbox):\n    inbox.post('Disk is 91% full', 'body', key='k', poster='planted-new-poster')\n")
    hits = poster_scan.scan(tmp_path / "other-repo")
    assert [h["poster"] for h in hits] == ["planted-new-poster"]
    assert [h["poster"] for h in unregistered(hits, tmp_path)] == ["planted-new-poster"]


def test_planted_argv_poster_is_found(tmp_path):
    (tmp_path / "job.py").write_text("ARGV = ['hermes', 'inbox', 'post', 't', '--poster', 'argv-planted']  # inbox\n")
    assert [h["poster"] for h in poster_scan.scan(tmp_path)] == ["argv-planted"]


@pytest.mark.parametrize("poster", sorted(REFUSED_BY_DESIGN))
def test_refused_by_design_posters_are_really_refused(tmp_path, monkeypatch, poster):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("_conf_store", HERE / "store.py")
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    cards = []
    monkeypatch.setattr(st, "kanban_task_creator", lambda **kw: cards.append(kw) or "t_conf")
    from ask_fixtures import ASK
    with pytest.raises(st.AgentOwnedPost):
        st.Inbox().post("Needs a look", "body", "do it", key=f"conf:{poster}", poster=poster, ask=ASK)
    assert cards, "refusal must file a Kanban card"
