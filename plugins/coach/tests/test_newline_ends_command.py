"""A newline ends a shell command. The live-checkout rules read `git <subcommand>` / `npm <subcommand>` within one
simple command, and the blank before the subcommand used to be allowed to be a newline: the line
`echo "core: $(git -C ~/.hermes/hermes-agent rev-parse HEAD)"` followed by the line `rm -f out.txt` was refused as
`git rm` in the live checkout (2026-11-02). A backslash-newline continues the command, so continued lines still count."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import agent_rules as rules  # noqa: E402


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("COACH_ALLOW_LIVE", raising=False)
    home = tmp_path.resolve()
    monkeypatch.setenv("HOME", str(home))
    root = home / ".hermes" / "hermes-agent"
    (root / "hermes_cli").mkdir(parents=True)
    (root / "web").mkdir()
    (root / "hermes_cli" / "x.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


# The refused command, paths shortened.
REPORTED = ('S=/tmp/s\n'
            'echo "Mac core: $(git -C {live} rev-parse --short=10 HEAD) (origin/main $(git -C {live} rev-parse '
            '--short=10 origin/main))"\n'
            'rm -f "$S/lz31.txt"')


def _check(kind, command, live):
    if kind == "block":
        return rules.blocks_live_checkout("terminal", {"command": command}, live)
    if kind == "hint":
        return rules.touches_live_checkout("terminal", {"command": command}, live)
    return rules.is_file_change("terminal", {"command": command})


@pytest.mark.parametrize("kind,template", [
    ("block", REPORTED),                                                        # git rm (GIT_FILE_WRITE_RE)
    ("block", "git -C {live} log -1\nmv /tmp/a /tmp/b"),                        # git mv
    ("block", "git -C {live} log -1\nstash=$(mktemp); echo $stash"),            # git stash
    ("block", "x=$(git -C {live} rev-parse HEAD)\ncommit=$(date +%s); echo $x $commit"),  # git commit (GIT_HEAD_RE)
    ("block", "cd {live} && git status --short\nreset=1; echo $reset"),        # git reset
    ("block", "cd {live}/web && npm run typecheck\ni=0; echo $i"),              # npm i (PKG_INSTALL_RE)
    ("hint", "git -C {live} status --short\nls checkout/"),                    # GIT_MUTATE_RE crossed lines too
    ("hint", "cd {live} && git log -1\nprintf '%s\\n' x\npull=1"),
    ("file_change", "git log -1\ncommit=$(date +%s)"),                          # GIT_EDIT_RE
])
def test_a_word_on_the_next_line_is_not_a_subcommand(live, kind, template):
    command = template.format(live=live)
    assert not _check(kind, command, live), command


@pytest.mark.parametrize("kind,template,expect", [
    ("block", "git -C {live} rm hermes_cli/x.py", "`git rm`"),
    ("block", "git -C {live} \\\nrm hermes_cli/x.py", "`git rm`"),
    ("block", "git -C {live} \\\n  rm hermes_cli/x.py", "`git rm`"),            # missed before
    ("block", "cd {live} && git \\\n  commit -am wip", "`git commit`"),           # missed before
    ("block", "cd {live} && git -c core.pager=cat \\\n    stash pop", "`git stash`"),
    ("block", "cd {live}/web && npm --no-audit \\\n  i left-pad", "`npm i`"),
    ("hint", "cd {live} && git \\\n  pull --ff-only", None),
    ("file_change", "git \\\n  commit -m x", None),                              # missed before
])
def test_continued_lines_still_count(live, kind, template, expect):
    command = template.format(live=live)
    what = _check(kind, command, live)
    assert what, command
    if expect:
        assert expect in what, (command, what)


def test_reported_command_passes_the_pre_tool_call_hook(live, tmp_path):
    spec = importlib.util.spec_from_file_location("coach_pkg_init_nl", HERE.parent / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    _, hooks = mod.build(store=core.Store(root=tmp_path / "coach"), checkout=live)
    assert hooks["pre_tool_call"](tool_name="terminal", args={"command": REPORTED.format(live=live)}) is None
    out = hooks["pre_tool_call"](tool_name="terminal", args={"command": f"git -C {live} \\\n  rm hermes_cli/x.py"})
    assert out and out["action"] == "block" and "`git rm`" in out["message"]
