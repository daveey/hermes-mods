"""agent-105768f616: core's restart resume prepends "## Resumed after gateway restart" to the Kanban worker prompt.
The coach must still read it as a worker prompt (automation_prompt) and scope it to the card's header and body."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_resume_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")

BARE = (
    "# Kanban task t_7d17e13d: Upkeep system shepherd · drive graph to completion and ship\n\nAssignee: default\n"
    "Status:   running\nWorkspace: scratch @ /tmp/w\n\n## Body\nORCHESTRATOR card: drive the graph (David: \"unblock "
    "the task and finish it\"). Diagnosis only.\n\n## Recent work by @default\n- t_x — other card summary (just now)\n"
    "\n## Comment thread\nsomeone: just ship it\n")
# core gateway/session_kanban_resume.py RESUME_NOTE, verbatim (6c1154108fd)
NOTE = (
    '\n## Resumed after gateway restart\n'
    'The previous run of this card (run 41) was cut off by a gateway restart and requeued without '
    'counting a failure. Its conversation above (summarized by context compaction when it was too long) is '
    'restored into this run\'s session; continue that work '
    'instead of starting over. Tool calls interrupted by the restart may or may not have taken effect: verify '
    'files, running processes and remote side effects before repeating them. Use only THIS run\'s claim '
    '(run 42); the old run\'s ownership is gone. The card state below is current.\n')
RESUMED = NOTE + BARE
REPLY = "Nothing here is left for me to do. P4a is running; I linked it into SHIP and parked on it."
LOG = ['-> kanban_show({"task_id": "t_01a19026c03f0589"})', '-> kanban_link({"parent_id": "t_01a19026"})',
       '-> kanban_block({"kind": "time"})']


def _head(ask):
    """The 40-char ask_head the backend logs on reply_nudge rows (coach __init__)."""
    return " ".join(rc.strip_preamble(ask).split())[:40]


def test_resumed_prompt_and_its_head_are_automation():
    assert rc.automation_prompt(RESUMED)
    assert _head(RESUMED) == "## Resumed after gateway restart The pre"
    assert rc.automation_prompt(_head(RESUMED))


def test_resumed_scope_is_the_bare_cards_scope():
    assert rc.scope_text(RESUMED) == rc.scope_text(BARE)
    assert "other card summary" not in rc.scope_text(RESUMED) and "Diagnosis only." in rc.scope_text(RESUMED)
    assert rc.scope_limited(RESUMED) == rc.scope_limited(BARE)


def test_status_only_skips_a_resumed_worker_prompt():
    assert rc.PROGRESS_ASK_RE.search(RESUMED)
    assert rc.status_only(RESUMED, REPLY, LOG) is None


def test_a_human_quoting_the_note_mid_message_is_still_a_human_ask():
    assert not rc.automation_prompt("why did it say ## Resumed after gateway restart?")
    assert rc.status_only("so finish it: ## Resumed after gateway restart", REPLY, LOG)
    # the note alone, with no worker header after it, is not a worker prompt to scope
    assert rc.scope_text("## Resumed after gateway restart\nfinish it\n## Body\nx") == \
        "## Resumed after gateway restart\nfinish it\n## Body\nx"


CORE = Path(os.environ.get("HERMES_AGENT_DIR", Path.home() / ".hermes" / "hermes-agent"))


@pytest.mark.skipif(not (CORE / "gateway" / "session_kanban_resume.py").is_file(), reason="no core checkout")
def test_contract_with_core_resume_context():
    """Guard: core's real resume_context() output must stay a worker prompt to the coach. A future core wrapper
    or reordering turns this red instead of silently re-opening agent-105768f616."""
    spec = importlib.util.spec_from_file_location("t_core_resume", CORE / "gateway" / "session_kanban_resume.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(CORE))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(CORE))
    got = mod.resume_context(BARE, {"prior_run_id": 41, "run_id": 42})
    assert got != BARE and got.endswith(BARE)
    assert rc.automation_prompt(got)
    assert rc.automation_prompt(_head(got))
    assert rc.scope_text(got) == rc.scope_text(BARE)
    assert rc.status_only(got, REPLY, LOG) is None
