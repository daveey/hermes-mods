"""self-heal: a post-fix occurrence on a host that hasn't synced the fix yet is `not_deployed`, not "came back".

cron-b2c33e9921: fix dd030e1eb1 landed on the private state repo 2026-11-05 08:25Z; the laptop (sync label Laptop-A)
was asleep ~46 h, fired a catch-up run on the OLD script at 21:37:58Z on 11-06 and only synced the fix in its
`sync: Laptop-A` commit at 22:23Z. The lane judged by the clock alone (6 h grace) and filed a third "fix didn't
hold" investigation. The hub's hermes-state clone shows when each host's sync first contained the fix.
"""
import importlib.util
import json
import os
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


sh = load("coach_self_heal_not_deployed_test", ROOT / "self_heal.py")
core = load("coach_core_not_deployed_test", ROOT / "core.py")

H = 3600.0
FIXED_AT = 1794334000.0          # fix pushed + recorded fixed
T_PRE = FIXED_AT - 30 * H        # host's last sync before the fix
T_STALE = FIXED_AT + 37 * H      # host wakes, commits its sync WITHOUT merging origin (no fix)
T_DEPLOY = FIXED_AT + 38 * H     # host's first sync commit that contains the fix
T_LATER = FIXED_AT + 80 * H      # another host's later sync: the ref is fresh past every occurrence below
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "why": ""}


class Runner:
    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        self.calls.append(list(argv))
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


def _git(cwd, *args, at=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    if at is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{int(at)} +0000"
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _commit(repo, msg, at, path="f.txt"):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text((p.read_text() if p.exists() else "") + msg + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg, at=at)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def state(tmp_path):
    """hermes-state clone: refs/remotes/origin/state = host syncs around a fix commit on the shared branch."""
    repo = tmp_path / "hermes-state"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "state")
    _commit(repo, "sync: Laptop-A 2026-11-04 15:11", T_PRE, "home/Laptop-A")
    base = _git(repo, "rev-parse", "HEAD")
    fix = _commit(repo, "risk_ledger_push: retry connect failures", FIXED_AT - 60, "home/scripts/risk_ledger_push.py")
    # the host's own branch: woke up and committed before merging origin, so this sync lacks the fix
    _git(repo, "checkout", "-qb", "mbp", base)
    _commit(repo, "sync: Laptop-A 2026-11-06 13:41", T_STALE, "home/Laptop-A")
    _git(repo, "checkout", "-q", "state")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge remote-tracking branch 'origin/state' into state", "mbp",
         at=T_STALE + 60)
    _commit(repo, "sync: Laptop-A 2026-11-06 14:23", T_DEPLOY, "home/Laptop-A")
    _commit(repo, "sync: Desk-B 2026-11-08 06:00", T_LATER, "home/Desk-B")
    _git(repo, "update-ref", "refs/remotes/origin/state", "HEAD")
    return SimpleNamespace(repo=repo, fix=fix)


def _cfg(state, **kw):
    return {"state_repo": str(state.repo), **kw}


def cron_defect(at, host="laptop", label="Laptop-A"):
    details = ("job b13b87beffb0 (Risk ledger push, profile default) failure_streak=16 last_status=error\n"
               "script=risk_ledger_push.py\nScript exited with code 1\nstdout:\n"
               f"risk_ledger_push on {label} failed:\n- hermes rows: PushError: ssh to mini exited 255 after 15.1 s:"
               " ssh: connect to host mini.example port 22: Operation timed out")
    d = {"component": "cron", "symptom": f"cron job 'Risk ledger push' failed on {host}: Script exited with code 1",
         "source": "signal", "kind": "cron", "details": details, "host": host, "at": at}
    d["fingerprint"] = sh.fingerprint(d["component"], d["symptom"])
    return d


def _st(state, **kw):
    return {"status": "fixed", "fixed_at": FIXED_AT, "fix_shas": [state.fix[:10]], "card": "t_1", "gen": 2,
            "fix_card": "t_fix", **kw}


def _fixed_lane(store, d, state):
    sh.LaneState(store.root).update(d["fingerprint"], **_st(state))


# (a) the exact incident: the catch-up run before the host's first post-fix sync is not_deployed (today: "file")
def test_occurrence_before_the_hosts_first_post_fix_sync_is_not_deployed(state):
    d = cron_defect(at=T_DEPLOY - 45 * 60)
    dec = sh.decide(d, _st(state), T_DEPLOY + 60, _cfg(state))
    assert dec["action"] == "not_deployed", dec
    assert dec["label"] == "Laptop-A" and dec["sha"] == state.fix and dec["deployed_at"] == T_DEPLOY
    assert not dec.get("overdue")


def test_not_deployed_holds_and_files_nothing_with_a_log_row(tmp_path, state):
    store = core.Store(tmp_path / "coach")
    r = Runner()
    d = cron_defect(at=T_DEPLOY - 45 * 60)
    _fixed_lane(store, d, state)
    out = sh.file_defect(d, store=store, m="live", runner=r, now=T_DEPLOY + 60, cfg=_cfg(state), budget_fn=OPEN)
    assert out["action"] == "skip" and out.get("not_deployed") is True, out
    assert not r.creates() and not ev(store, "selfheal_recurrence")
    (nd,) = ev(store, "selfheal_not_deployed")
    assert nd["host"] == "laptop" and nd["label"] == "Laptop-A" and nd["sha"] == state.fix
    assert nd["last_sync"] == T_DEPLOY  # the host's own newest sync commit
    st = sh.LaneState(store.root).get(d["fingerprint"])
    assert st["status"] == "fixed" and not st.get("recurrences")


# (b) after the host's sync carried the fix, a new occurrence is a real recurrence
def test_occurrence_after_the_hosts_post_fix_sync_still_files(tmp_path, state):
    dec = sh.decide(cron_defect(at=T_DEPLOY + 3 * H), _st(state), T_DEPLOY + 3 * H + 60, _cfg(state))
    assert dec["action"] == "file" and dec["why"].startswith("recurrence"), dec
    store = core.Store(tmp_path / "coach")
    r = Runner()
    d = cron_defect(at=T_DEPLOY + 3 * H)
    _fixed_lane(store, d, state)
    out = sh.file_defect(d, store=store, m="live", runner=r, now=T_DEPLOY + 3 * H + 60, cfg=_cfg(state),
                         budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == 1 and not ev(store, "selfheal_not_deployed")


def test_label_comes_from_host_sync_labels_when_the_text_names_none(state):
    d = cron_defect(at=T_DEPLOY - H)
    d["details"] = "Script exited with code 1"
    assert sh.decide(d, _st(state), T_DEPLOY, _cfg(state))["action"] == "file"  # no label: time rule
    dec = sh.decide(d, _st(state), T_DEPLOY, _cfg(state, host_sync_labels={"laptop": "Laptop-A"}))
    assert dec["action"] == "not_deployed" and dec["label"] == "Laptop-A"


# (c) it can't tell -> today's time rule, unchanged
@pytest.mark.parametrize("case", ["unknown_label", "unknown_sha", "no_repo", "stale_ref"])
def test_falls_back_to_the_time_rule_when_git_cannot_tell(state, case):
    d = cron_defect(at=T_STALE + 10 * 60)
    st, cfg = _st(state), _cfg(state)
    if case == "unknown_label":
        d = cron_defect(at=T_STALE + 10 * 60, host="elsewhere", label="Nobody-Host")
    elif case == "unknown_sha":
        st["fix_shas"] = ["0123456789abcdef0123"]  # the hermes-mods half of a fix: not a state commit
    elif case == "no_repo":
        cfg = {"state_repo": str(state.repo.parent / "gone")}
    elif case == "stale_ref":
        # the clone hasn't fetched since before the occurrence: its tip (the merge at T_STALE + 60 s) has the fix
        # but no post-fix sync of the host, which proves nothing about an occurrence after that tip
        _git(state.repo, "update-ref", "refs/remotes/origin/state", _git(state.repo, "rev-parse", "state~2"))
    dec = sh.decide(d, st, T_STALE + 20 * 60, cfg)
    assert dec["action"] == "file" and dec["why"].startswith("recurrence"), dec


def test_inside_the_grace_window_an_unknown_host_is_still_held_by_time(state):
    d = cron_defect(at=FIXED_AT + H, host="elsewhere", label="Nobody-Host")
    assert sh.decide(d, _st(state), FIXED_AT + 2 * H, _cfg(state))["action"] == "hold"


# (d) a traceback from a pinned local checkout still goes through stale_checkout first
def test_local_checkout_traceback_is_still_a_stale_runtime(tmp_path, state):
    repo = tmp_path / "core"
    (repo / "tui").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "bug", FIXED_AT - 100, "tui/outcome.py")
    a = _git(repo, "rev-parse", "HEAD")
    b = _commit(repo, "fix", FIXED_AT - 50, "tui/outcome.py")
    _git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "pin-a"), a)
    d = cron_defect(at=T_DEPLOY - H, host=sh.host_name())
    d["details"] += f'\nTraceback (most recent call last):\n  File "{tmp_path / "pin-a"}/tui/outcome.py", line 1\n'
    dec = sh.decide(d, _st(state, fix_shas=[b, state.fix]), T_DEPLOY, _cfg(state))
    assert dec["action"] == "stale_runtime" and dec["sha"] == b, dec


# (e) detection guard: a host that still hasn't synced the fix 72 h after it files ONE card, never holds silently
def test_host_still_without_the_fix_after_72h_files_one_card(tmp_path, state):
    # the host's newest sync is T_STALE (no fix); the ref is fresh (another host synced at T_LATER)
    _git(state.repo, "update-ref", "refs/remotes/origin/state",
         _git(state.repo, "rev-parse", "HEAD~2"))  # drop T_DEPLOY..: tip = merge of mbp at T_STALE+60
    _git(state.repo, "checkout", "-q", "refs/remotes/origin/state")
    tip = _commit(state.repo, "sync: Desk-B 2026-11-08 06:00", T_LATER, "home/Desk-B")
    _git(state.repo, "update-ref", "refs/remotes/origin/state", tip)
    store = core.Store(tmp_path / "coach")
    r = Runner()
    early = cron_defect(at=FIXED_AT + 40 * H)
    _fixed_lane(store, early, state)
    out = sh.file_defect(early, store=store, m="live", runner=r, now=FIXED_AT + 40 * H + 60, cfg=_cfg(state),
                         budget_fn=OPEN)
    assert out["action"] == "skip" and out.get("not_deployed") and not r.creates(), out
    late = cron_defect(at=FIXED_AT + 73 * H)
    out = sh.file_defect(late, store=store, m="live", runner=r, now=FIXED_AT + 73 * H + 60, cfg=_cfg(state),
                         budget_fn=OPEN)
    assert out["action"] == "not_deployed" and out["card"], out
    (argv,) = r.creates()
    title = argv[argv.index("create") + 1]
    assert title == f"host Laptop-A hasn't synced fix {state.fix[:11]}"
    assert "(came back)" not in title and not ev(store, "selfheal_recurrence")
    again = sh.file_defect(cron_defect(at=FIXED_AT + 75 * H), store=store, m="live", runner=r,
                           now=FIXED_AT + 75 * H + 60, cfg=_cfg(state), budget_fn=OPEN)
    assert again["action"] == "skip" and len(r.creates()) == 1, again
    st = sh.LaneState(store.root).get(late["fingerprint"])
    assert st["status"] == "fixed" and not st.get("recurrences")
