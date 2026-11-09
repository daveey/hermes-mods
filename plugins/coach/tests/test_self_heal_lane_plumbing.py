"""self-heal lane plumbing (cron-7b7df72ab4 / t_01a1bfbd17981505): every create declares requires, a refused fix
create is logged, a dirty coach-data clone never stops the pass, and stuck proposals file one lane card."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_plumbing_test", ROOT / "self_heal.py")
core = load("coach_core_test_plumbing", ROOT / "core.py")
signals = load("coach_signals_test_plumbing", ROOT / "signals.py")

NOW = 1794218000.0
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 200.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "available": True, "why": ""}
CLOSED = lambda board=None, now=None: {**OPEN(), "ok_usd": False, "why": "cap"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_SELFHEAL_NO_EXEC", "1")
    return core.Store(tmp_path / "coach")


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if f'"{name}"' in x] if p.exists() else []


class Board:
    """Fake `hermes kanban`: create returns ids (or fails with `refuse`), show reports `status`."""

    def __init__(self, refuse=None, status="ready"):
        self.calls, self.n, self.refuse, self.status = [], 0, refuse, status

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        self.calls.append(list(argv))
        if "create" in argv:
            if self.refuse is not None:
                return SimpleNamespace(returncode=1, stdout="", stderr=self.refuse)
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        if "show" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": self.status}}), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


def requires_of(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--requires"]


# ── requires declaration ──
def test_fix_and_investigation_argv_declare_requires(store):
    inv = sh.card_args({"component": "plugin", "symptom": "x", "fingerprint": "plugin-0123456789"}, "k")
    assert requires_of(sh.kanban_argv(inv)) == ["terminal", "file", "git", "host_repo"]
    sh.LaneState(store.root).update("cron-0123456789", status="proposed", component="cron", symptom="s")
    b = Board()
    r = sh.file_fix("cron-0123456789", store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert r["action"] == "filed"
    assert requires_of(b.creates()[0]) == ["terminal", "file", "git", "host_repo"]


def test_argv_without_requires_key_still_declares_and_empty_list_says_no_requires():
    base = {"title": "t", "body": "b", "assignee": "default", "idempotency_key": "k", "model": "m",
            "max_runtime": 1, "created_by": "c"}
    assert requires_of(sh.kanban_argv(base)) == list(sh.SELFHEAL_REQUIRES)
    assert "--no-requires" in sh.kanban_argv({**base, "requires": []})


def test_core_gate_accepts_the_new_argv_and_refused_the_old():
    """The real core gate (kanban_capabilities.check_declaration), when the agent tree is importable."""
    agent = Path.home() / ".hermes" / "hermes-agent"
    sys.path.insert(0, str(agent))
    try:
        from hermes_cli.kanban_capabilities import RequiresDeclarationMissing, check_declaration
    except Exception:
        pytest.skip("hermes-agent not importable here")
    finally:
        sys.path.remove(str(agent))
    a = sh.card_args({"component": "plugin", "symptom": "x", "fingerprint": "plugin-0123456789"}, "k")
    check_declaration(requires_of(sh.kanban_argv(a)) or None, "scratch")  # passes
    with pytest.raises(RequiresDeclarationMissing):  # the pre-fix argv carried no --requires at all
        check_declaration(None, "scratch")


# ── refused fix create is logged ──
def test_refused_fix_create_logs_file_error_and_stays_proposed(store):
    fp = "cron-98cf060854"
    sh.LaneState(store.root).update(fp, status="proposed", component="cron", symptom="s")
    b = Board(refuse="kanban: requires_declaration_missing: declare what the work needs")
    r = sh.file_fix(fp, store=store, runner=b, now=NOW, budget_fn=OPEN)
    assert r["action"] == "error" and "requires_declaration_missing" in r["why"]
    (rec,) = ev(store, "selfheal_file_error")
    assert rec["fingerprint"] == fp and rec["stage"] == "fix" and rec["rc"] == 1
    assert "requires_declaration_missing" in rec["error"]
    assert sh.LaneState(store.root).get(fp)["status"] == "proposed"


def test_refused_fix_create_with_empty_output_still_logs(store):
    fp = "cron-98cf060854"
    sh.LaneState(store.root).update(fp, status="proposed", component="cron", symptom="s")
    r = sh.file_fix(fp, store=store, runner=Board(refuse=""), now=NOW, budget_fn=OPEN)
    assert r["action"] == "error" and "exited 1" in r["why"]
    assert len(ev(store, "selfheal_file_error")) == 1


# ── coach-data pull ──
def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def data_repo(tmp_path):
    """upstream + a clone whose tracked spend.jsonl has uncommitted local rows (coach_eval on the hub)."""
    up = tmp_path / "up.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(up)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(up), str(seed)], check=True, capture_output=True)
    for d in (seed,):
        git(d, "config", "user.email", "t@example.com")
        git(d, "config", "user.name", "t")
    (seed / "research" / "state").mkdir(parents=True)
    (seed / "research" / "state" / "spend.jsonl").write_text('{"row": "base"}\n')
    (seed / ".gitattributes").write_text("*.jsonl merge=union\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "base")
    git(seed, "push", "-q", "origin", "HEAD:main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(up), str(clone)], check=True, capture_output=True)
    git(clone, "config", "user.email", "t@example.com")
    git(clone, "config", "user.name", "t")
    with (clone / "research" / "state" / "spend.jsonl").open("a") as f:
        f.write('{"row": "local-trial"}\n')
    with (seed / "research" / "state" / "spend.jsonl").open("a") as f:
        f.write('{"row": "upstream-scout"}\n')
    git(seed, "commit", "-qam", "scout")
    git(seed, "push", "-q", "origin", "HEAD:main")
    return SimpleNamespace(up=up, seed=seed, clone=clone)


def test_old_ff_only_pull_refuses_this_clone(data_repo):
    r = subprocess.run(["git", "-C", str(data_repo.clone), "pull", "-q", "--ff-only"], capture_output=True, text=True)
    assert r.returncode != 0 and "overwritten" in r.stderr


def test_pull_data_pulls_and_keeps_local_rows(data_repo):
    assert sh.pull_data(data_repo.clone) is None
    rows = (data_repo.clone / "research" / "state" / "spend.jsonl").read_text().splitlines()
    assert '{"row": "upstream-scout"}' in rows and '{"row": "local-trial"}' in rows
    assert git(data_repo.clone, "rev-parse", "HEAD") == git(data_repo.seed, "rev-parse", "HEAD")
    assert not git(data_repo.clone, "stash", "list")


def test_pull_data_without_union_attribute_heals_the_conflict(data_repo, tmp_path):
    """Upstream lacking .gitattributes: the clone's info/attributes (written by pull_data) still unions."""
    git(data_repo.seed, "rm", "-q", ".gitattributes")
    git(data_repo.seed, "commit", "-qm", "drop attrs")
    git(data_repo.seed, "push", "-q", "origin", "HEAD:main")
    assert sh.pull_data(data_repo.clone) is None
    rows = (data_repo.clone / "research" / "state" / "spend.jsonl").read_text().splitlines()
    assert '{"row": "upstream-scout"}' in rows and '{"row": "local-trial"}' in rows
    assert not git(data_repo.clone, "diff", "--name-only", "--diff-filter=U")


def test_pull_data_failure_returns_git_error_without_raising(tmp_path):
    repo = tmp_path / "norepo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "remote", "add", "origin", str(tmp_path / "missing.git"))
    err = sh.pull_data(repo)
    assert err and "missing.git" in err


def test_pull_data_skips_a_plain_directory(tmp_path):
    assert sh.pull_data(tmp_path) is None


def test_cron_carries_on_after_a_failed_pull_and_files_one_card_at_the_streak(store, monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_SELFHEAL", "live")
    synced = []
    monkeypatch.setattr(sh, "sync", lambda store=None, now=None, **kw: synced.append(now) or [])
    monkeypatch.setattr(sh, "drain", lambda store=None, **kw: [])
    monkeypatch.setattr(sh, "retry_deferred", lambda store=None, now=None, **kw: [])
    b = Board()
    monkeypatch.setattr(sh, "_run", lambda argv, runner=None, stdin=None: (lambda r: (r.returncode, r.stdout, r.stderr))(b(argv)))
    fail = lambda data: "error: Your local changes to the following files would be overwritten by merge"
    scans = []
    kw = dict(backstop_fn=lambda *a: scans.append(1) or [], liveness_fn=lambda *a, **k: {"action": "ok"},
              pull_fn=fail)
    for i in range(sh.PULL_FAIL_CARD_AFTER + 2):
        out = sh.cron(tmp_path, store, now=NOW + i * 3000, **kw)
        assert out[0].startswith("pull: failed (digest backstop skipped): error: Your local changes")
    assert len(synced) == sh.PULL_FAIL_CARD_AFTER + 2  # sync (fix filing) ran every pass
    assert not scans  # stale digests are never judged (d499a5a)
    creates = b.creates()
    assert len(creates) == 1 and "coach-data pull failing" in creates[0][creates[0].index("create") + 1]
    assert requires_of(creates[0]) == list(sh.SELFHEAL_REQUIRES)
    assert len(ev(store, "selfheal_data_pull_failed")) == sh.PULL_FAIL_CARD_AFTER + 2
    sh.cron(tmp_path, store, now=NOW + 20000, **{**kw, "pull_fn": lambda data: None})
    assert len(ev(store, "selfheal_data_pull_recovered")) == 1 and scans == [1]
    b.status = "done"  # the incident's card ended: a new streak files a fresh card
    for i in range(sh.PULL_FAIL_CARD_AFTER):
        sh.cron(tmp_path, store, now=NOW + 86400 + i * 300, **kw)
    assert len(b.creates()) == 2


# ── stuck proposals ──
def stuck(store, fp="cron-98cf060854", age=3 * 3600):
    sh.LaneState(store.root).update(fp, status="proposed", component="cron", symptom="s", proposed_at=NOW - age)


def test_stuck_proposal_files_one_lane_card_per_incident(store):
    stuck(store)
    stuck(store, "plugin-8a7e4507f0", age=5 * 3600)
    b = Board()
    # first open-capacity sighting only starts the watch: a lane pass may not have run since the slot freed
    first = sh.check_stuck(store, now=NOW - 1800, budget_fn=OPEN, runner=b, auto=True)
    assert first["action"] == "watch" and not b.creates()
    r = sh.check_stuck(store, now=NOW, budget_fn=OPEN, runner=b, auto=True, lane_status="353210256a77 error")
    assert r["action"] == "filed" and r["n"] == 2 and r["oldest"] == "plugin-8a7e4507f0"
    (argv,) = b.creates()
    body = argv[argv.index("--body") + 1]
    assert "cron-98cf060854" in body and "353210256a77 error" in body
    assert requires_of(argv) == list(sh.SELFHEAL_REQUIRES)
    assert sh.check_stuck(store, now=NOW + 1800, budget_fn=OPEN, runner=b, auto=True)["action"] == "skip"
    assert len(b.creates()) == 1
    b.status = "done"
    sh.check_stuck(store, now=NOW + 86400, budget_fn=OPEN, runner=b, auto=True)
    assert len(b.creates()) == 2


def test_no_stuck_card_when_the_lane_approved_a_fix_since_the_first_sighting(store):
    """2026-11-09 11:23 (t_01a1c00ca6bc0945): the sweep saw a slot that had freed minutes before the next lane pass,
    which then approved fixes. Capacity open at one instant is not a dead lane; open across a pass with no approval is."""
    stuck(store)
    b = Board()
    assert sh.check_stuck(store, now=NOW - 1800, budget_fn=OPEN, runner=b, auto=True)["action"] == "watch"
    store.log({"event": "selfheal_approved", "fingerprint": "agent-0000000000", "card": "t_x", "ts": NOW - 900})
    r = sh.check_stuck(store, now=NOW, budget_fn=OPEN, runner=b, auto=True)
    assert r["action"] == "watch" and not b.creates()


@pytest.mark.parametrize("case", ["young", "budget_closed", "manual", "has_fix_card"])
def test_no_stuck_card_when_waiting_is_legitimate(store, case):
    stuck(store, age=600 if case == "young" else 3 * 3600)
    if case == "has_fix_card":
        sh.LaneState(store.root).update("cron-98cf060854", fix_card="t_00000001")
    b = Board()
    r = sh.check_stuck(store, now=NOW, budget_fn=CLOSED if case == "budget_closed" else OPEN, runner=b,
                       auto=case != "manual")
    assert r["action"] == "ok" and not b.creates()


def test_signals_lane_check_runs_check_stuck_only_live(tmp_path):
    seen = []
    fake = SimpleNamespace(mode=lambda: "live", check_stuck=lambda **kw: seen.append(kw) or {"action": "filed"})
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        {"id": "353210256a77", "script": "selfheal_lane.py", "last_status": "error", "failure_streak": 881,
         "last_error": "Script exited with code 1\nCalledProcessError: git pull"}]}))
    assert signals.lane_check(tmp_path, None, sh=fake) == {"action": "filed"}
    assert "failure_streak=881" in seen[0]["lane_status"] and "CalledProcessError" in seen[0]["lane_status"]
    assert signals.lane_check(tmp_path, "shadow", sh=fake) is None and len(seen) == 1
