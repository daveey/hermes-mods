"""withheld_notice_watch: bare ask-gate notices delivered to the user file ONE card per host+day; later hits comment."""
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("withheld_notice_watch_test", ROOT / "withheld_notice_watch.py")
ww = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ww)

NOW = 1793300000.0
NOTICE = ("I withheld a request after checking authoritative approval or lifecycle state. "
          "No new permission or execution authorization is granted by this notice.")


def _db(home: Path, rows):
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "state.db")
    db.execute("create table if not exists messages (id integer primary key, session_id text, role text, "
               "content text, timestamp real)")
    db.executemany("insert into messages (session_id, role, content, timestamp) values (?,?,?,?)", rows)
    db.commit()
    db.close()


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, input=None, **kw):
        self.calls.append((argv, input))
        out = json.dumps({"id": "t_card1"}) if argv[:3] == ["hermes", "kanban", "create"] else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")


def test_only_assistant_replies_starting_with_a_notice_count(tmp_path):
    _db(tmp_path, [("s1", "assistant", NOTICE, NOW - 60),
                   ("s2", "assistant", "  The workflow remains stopped. I withheld a request to act on it.", NOW - 50),
                   ("s3", "user", NOTICE + " what is this", NOW - 40),                       # David quoting it
                   ("s4", "assistant", "Earlier ask-gate said: I withheld a request ...", NOW - 30),  # a mention
                   ("s5", "assistant", "I patched it.\n\n(I left a request out of this reply: ...)", NOW - 20),
                   ("s6", "assistant", NOTICE, NOW - 3 * 86400)])                            # too old
    assert [h["session"] for h in ww.scan(tmp_path, NOW - 86400)] == ["s1", "s2"]


def test_files_one_card_per_day_then_comments_only_on_new_hits(tmp_path):
    _db(tmp_path, [("s1", "assistant", NOTICE, NOW - 60)])
    run = Runner()
    assert ww.main(["--home", str(tmp_path), "--host", "hub"], runner=run, now=NOW) == 0
    argv, body = run.calls[0]
    assert argv[:3] == ["hermes", "kanban", "create"] and "withheld-notice:hub:" in " ".join(argv)
    assert "s1" in body and "ask-gate.jsonl" in body
    ww.main(["--home", str(tmp_path), "--host", "hub"], runner=run, now=NOW + 60)
    assert len(run.calls) == 1                     # nothing new: no comment, no second card
    _db(tmp_path, [("s2", "assistant", NOTICE, NOW + 100)])
    ww.main(["--home", str(tmp_path), "--host", "hub"], runner=run, now=NOW + 200)
    assert run.calls[-1][0][:4] == ["hermes", "kanban", "comment", "t_card1"] and "s2" in run.calls[-1][0][-1]


def test_seed_skips_history_and_dry_run_files_nothing(tmp_path, capsys):
    _db(tmp_path, [("s1", "assistant", NOTICE, NOW - 60)])
    run = Runner()
    ww.main(["--home", str(tmp_path), "--seed"], runner=run, now=NOW)
    ww.main(["--home", str(tmp_path)], runner=run, now=NOW + 10)
    assert run.calls == []
    capsys.readouterr()
    ww.main(["--home", str(tmp_path), "--dry-run", "--hours", "1"], runner=run, now=NOW + 10)
    assert run.calls == [] and json.loads(capsys.readouterr().out)["count"] == 1
