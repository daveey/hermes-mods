"""Regenerate tests/fixtures/main_verdicts.json from a pristine checkout of main's guard.

    python3 gen_main_verdicts.py /path/to/main/plugins/sqlite-live-guard/guard.py

Classification only: no path under the synthetic home is ever touched.
"""
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import diff_corpus  # noqa: E402


def load(path):
    spec = importlib.util.spec_from_file_location("main_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def corpus_sha(commands):
    return hashlib.sha256("\0".join(commands).encode()).hexdigest()


def main():
    os.environ["HOME"] = diff_corpus.HOME_PARENT
    os.environ["HERMES_HOME"] = diff_corpus.HOME_PARENT + "/.hermes"
    guard = load(sys.argv[1])
    commands = diff_corpus.corpus()
    refused = [c for c in commands if guard.check_shell(c, cwd=diff_corpus.HOME_PARENT)]
    fixture = {
        "main_commit": sys.argv[2] if len(sys.argv) > 2 else "",
        "corpus_size": len(commands),
        "corpus_sha256": corpus_sha(commands),
        "refused_by_main": refused,
        # command -> reason, for each deliberate refuse->allow change.
        "allowed_changes": {},
    }
    out = HERE / "fixtures" / "main_verdicts.json"
    old = json.loads(out.read_text()) if out.exists() else {}
    fixture["allowed_changes"] = old.get("allowed_changes", {})
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(fixture, indent=0, sort_keys=True) + "\n")
    print(len(commands), "commands;", len(refused), "refused by main")


if __name__ == "__main__":
    main()
