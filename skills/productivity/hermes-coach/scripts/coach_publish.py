"""Best-effort publication of eval artifacts; never changes the evaluation outcome."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sys


def _digest(path):
    return hashlib.sha256(path.read_bytes()).digest() if path.is_file() else None


@contextmanager
def publish_run(data, out=None, *, enabled=True, replies=False):
    """Publish changed run files only, including accounting from interrupted runs.

    Existing unrelated dirt is not staged. Paths resolving outside the data checkout
    (including symlinks) are never published. Git worktrees are supported too.
    """
    repo = Path(data).resolve()
    paths = []
    before = {}
    try:
        if enabled and (repo / ".git").exists():
            candidates = [repo / "research/state/spend.jsonl"]
            if out:
                report = Path(out)
                candidates += [report, report.with_suffix(".json")]
                if replies:
                    candidates.append(report.with_suffix(".replies.jsonl"))
            paths = list(dict.fromkeys(p.resolve() for p in candidates
                                      if repo in p.resolve().parents))
            before = {p: _digest(p) for p in paths}
    except Exception as exc:
        print(f"coach publish: {exc}", file=sys.stderr)
        paths = []
    try:
        yield
    finally:
        try:
            changed = [str(p.relative_to(repo)) for p in paths
                       if p.is_file() and _digest(p) != before[p]]
            if changed:
                from coach_apply import Env, git_commit, git_push
                env = Env(data=repo)
                git_commit(env, repo, changed, "coach: publish eval accounting and reports")
                result = git_push(env, repo, union_jsonl=True)
                print(f"coach publish: {result}", file=sys.stderr)
        except Exception as exc:
            print(f"coach publish failed: {exc}", file=sys.stderr)
