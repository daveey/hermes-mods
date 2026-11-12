"""A user_terminal ask whose action names one chat as the sole executor (self-heal kanban-53e6157b2f).

Observed: Ask Eve activation card t_01a1cbfae450bef1 blocked with action "From owner chat 20261111_032644_40d5be, run
the single ssh command ... through run_in_user_terminal" (task_events 1145465). That chat had been reaped; the live
chat that got the notice deferred to it, and David saw a blocked card no one could act on. A second card used "In
owner Desktop chat <id>, ..." (1152154). The action is what David reads: a chat id there is a relay through him and
nothing checks the chat is alive. Chat refs belong in scope; the action is core's canonical owner-terminal template.
"""
import pytest

TASK = "t_01a1cbfae450bef1"
STORED = {
    "event-1145465": ("From owner chat 20261111_032644_40d5be, run the single ssh command `ssh askeve-host "
                      "'cd /srv/ask-eve && ./activate.sh'` through run_in_user_terminal. Unblock = resume "
                      "verification; archive = abandon activation."),
    "event-1152154": ("In owner Desktop chat 20261102_172718_5fb607, start the owner-terminal flow and run the "
                      "activation check. Unblock = resume verification; archive = drop it."),
}


def request(gate, action, **changes):
    chat = gate.chat_delivery
    return {"interaction": "user_terminal", "action": action, "why_human": chat.OWNER_TERMINAL_REASON,
            "evidence": chat.OWNER_TERMINAL_EVIDENCE,
            "scope": "Host askeve-host, cwd /srv/ask-eve: run ./activate.sh; started from @session:default/"
                     "20261111_032644_40d5be.", **changes}


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", TASK)


@pytest.mark.parametrize("action", list(STORED.values()), ids=list(STORED))
def test_stored_actions_naming_a_chat_are_refused_with_the_canonical_action(gate, worker, action):
    r = gate.pre_tool_call(tool_name="kanban_block", session_id="s1",
                           args={"reason": "owner terminal step", "kind": "needs_input",
                                 "human_request": request(gate, action)})
    assert r and r["action"] == "block"
    msg = r["message"].replace("\\'", "'")
    assert gate.chat_delivery.owner_terminal_action(TASK) in msg
    assert "scope" in msg and "never in action" in msg


def test_kanban_create_blocked_with_a_named_chat_is_refused(gate, worker):
    r = gate.pre_tool_call(tool_name="kanban_create", session_id="s1",
                           args={"title": "activate", "initial_status": "blocked",
                                 "human_request": request(gate, STORED["event-1145465"])})
    assert r and r["action"] == "block"


@pytest.mark.parametrize("action", list(STORED.values()), ids=list(STORED))
def test_helper_finds_the_named_chat(gate, action):
    assert gate.chat_delivery.named_chat_in_terminal_action(request(gate, action))


@pytest.mark.parametrize("action", [
    "From the owner chat, run the check. Unblock = resume; archive = drop.",
    "Open @session:default/20261111_032644_40d5be and run it. Unblock = resume; archive = drop.",
    "Run it from session 20261111_032644_40d5be. Unblock = resume; archive = drop.",
])
def test_other_chat_forms_in_action_are_found(gate, action):
    assert gate.chat_delivery.named_chat_in_terminal_action(request(gate, action))


def test_canonical_template_with_chat_in_scope_is_not_flagged(gate):
    hr = request(gate, gate.chat_delivery.owner_terminal_action(TASK))
    assert gate.chat_delivery.named_chat_in_terminal_action(hr) is None


@pytest.mark.parametrize("interaction", ["approval", "reply", "physical", "external"])
def test_only_user_terminal_asks_are_checked(gate, interaction):
    hr = request(gate, STORED["event-1145465"], interaction=interaction)
    assert gate.chat_delivery.named_chat_in_terminal_action(hr) is None


def test_owner_word_without_chat_is_not_flagged(gate):
    hr = request(gate, "Approve the owner rollout from your own Hermes chat. Unblock = ship; archive = drop.")
    assert gate.chat_delivery.named_chat_in_terminal_action(hr) is None
