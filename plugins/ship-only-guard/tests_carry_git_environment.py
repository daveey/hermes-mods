"""Exercise carry's Git isolation with real children and hostile scratch config."""
import json
import os
import sys

from tests_carry import GIT_VERIFY, R, REAL_RUN, g, git_history  # noqa: F401


def test_git_verifier_ignores_ambient_config_and_disables_prompts(monkeypatch, tmp_path, git_history):
    repo, heads = git_history
    home = tmp_path / "ambient-home"
    home.mkdir()
    hostile = home / ".gitconfig"
    contents = "[protocol \"file\"]\n\tallow = never\n[carry]\n\tambient = hostile\n"
    hostile.write_text(contents)
    xdg = home / "xdg"
    xdg.mkdir()
    # All fixture writes stay in tmp_path; no user or system config is edited.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    ambient = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    ambient["GIT_CONFIG_NOSYSTEM"] = "1"
    control = REAL_RUN(["git", "config", "--get", "carry.ambient"], cwd=home,
                       env=ambient, capture_output=True, text=True, timeout=5)
    assert control.returncode == 0 and control.stdout.strip() == "hostile"
    denied = REAL_RUN(["git", "ls-remote", str(repo)], cwd=home, env=ambient,
                      capture_output=True, text=True, timeout=5)
    assert denied.returncode != 0 and "transport 'file' not allowed" in denied.stderr

    observed = []

    def run(argv, **kw):
        # Read inside a real child, not merely the dict offered to subprocess.run.
        probe = REAL_RUN([sys.executable, "-c",
                          "import os,json; print(json.dumps({k:os.environ.get(k) for k in "
                          "['GIT_CONFIG_NOSYSTEM','GIT_CONFIG_GLOBAL','GIT_TERMINAL_PROMPT']}))"],
                         cwd=kw["cwd"], env=kw["env"], capture_output=True, text=True,
                         check=True, timeout=5)
        observed.append(json.loads(probe.stdout))
        if "fetch" in argv:
            assert "https://github.com/example/protected.git" in argv
            argv = [str(repo) if a == "https://github.com/example/protected.git" else a for a in argv]
        return REAL_RUN(argv, **kw)

    monkeypatch.setattr(g.subprocess, "run", run)
    why = GIT_VERIFY(*heads["clean"], R, None, {"HERMES_HOME": str(tmp_path / "hermes")})
    assert why == "", "ambient config must not prevent a valid local merge proof: " + why
    assert len(observed) >= 6  # init, fetch, parents, base, merge-tree, tree read
    assert all(e == {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                     "GIT_TERMINAL_PROMPT": "0"} for e in observed)
    assert hostile.read_text() == contents
    assert not list((tmp_path / "hermes" / "cache").iterdir())


def test_git_verifier_child_cannot_prompt_for_credentials(monkeypatch, tmp_path, git_history):
    repo, heads = git_history
    results = []

    def run(argv, **kw):
        if "fetch" in argv:
            # credential fill is offline: it asks for a missing username but never
            # connects to the host. Empty helpers/askpass avoid installed providers.
            env = dict(kw["env"])
            env.pop("GIT_ASKPASS", None)
            env.pop("SSH_ASKPASS", None)
            env["LC_ALL"] = "C"
            results.append(REAL_RUN(
                ["git", "-c", "credential.helper=", "-c", "core.askPass=", "credential", "fill"],
                input="protocol=https\nhost=example.invalid\n\n", cwd=kw["cwd"], env=env,
                capture_output=True, text=True, timeout=5))
            argv = [str(repo) if a == "https://github.com/example/protected.git" else a for a in argv]
        return REAL_RUN(argv, **kw)

    monkeypatch.setattr(g.subprocess, "run", run)
    assert GIT_VERIFY(*heads["clean"], R, None, {"HERMES_HOME": str(tmp_path / "hermes")}) == ""
    assert len(results) == 1
    assert results[0].returncode != 0
    assert "terminal prompts disabled" in results[0].stderr
