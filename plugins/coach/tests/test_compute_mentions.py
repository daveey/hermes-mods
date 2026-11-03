"""Mention, not use: text that quotes, describes or proposes a spend-ask rule is not an ask and not an agent-set cap.

Live false holds, 2026-11-03 (coach log.jsonl on hermes-hub, verbatim evidence):
1. An adversarial review's final summary quoted proposed AGENTS.md wording; `compute_envelope` held it and the
   findings report was lost (session 20261103_064913_25f1d6, then its "this is a false positive" explanation).
2. The kanban_create body of the card filed to fix this was refused by `compute_authority`.
3. A reviewer reply reporting "the two GPU sentences the coach plugin quotes are kept word for word" was held by
   `compute_authority` as a multi-GPU declaration.
4. A kanban_block whose human_request scope said "No GPU or multi-node." (JSON-quoted) was held as multi-node.
Genuine asks and caps around the same wording are still held.
"""
import pytest
from test_compute_envelope import _hooks, _log, HERE
import importlib.util


def _ce():
    spec = importlib.util.spec_from_file_location("ce_mentions", HERE / "compute_envelope.py")
    ce = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ce)
    return ce


def call(hooks, surface, text, session='mentions'):
    if surface == 'reply':
        return hooks['pre_final_gate:compute_envelope'](session_id=session, final_response=text)
    return hooks['pre_tool_call'](session_id=session, tool_name=surface, args={'body': text, 'reason': text})


TOPIC = '\nThe GPU rental guidance in AGENTS.md was reviewed.'

REVIEW_SUMMARY = (  # case 1, the held line verbatim plus the reply's other findings shape
    "**17 findings.** Most are wording; three change behavior.\n"
    "- **Fix (line 32):** \"A spend/change ask (card, clarify or reply) \u2026 find the change itself (migration, its "
    "card, what runs the jobs), not just my word\".\n"
    "- **GPU section:** kept as is." )
REVIEW_EXPLANATION = (  # case 1, the agent's explanation after the hold (logged as agent_fault)
    "That coach hold is a false positive. My report doesn't ask anyone to approve compute or spend: the flagged line "
    "is finding #6's suggested wording for the Kanban \"spend/change ask\" rule, which I was quoting.")
CARD_BODY = (  # case 2, the refused kanban_create body (the held line verbatim) and the body that was filed
    "Seen 2026-11-03: the coach compute checks hold text that only quotes the GPU-envelope wording "
    "(\"you may rent ONE single-GPU machine up to $25 total without asking\"; \"Ask only above $25 total or for "
    "multi-GPU / multi-node.\").\n"
    "Acceptance:\n"
    "- Regression tests built from both cases are not held.\n"
    "- A real over-$25 / multi-GPU ask is still held, by the existing tests and one new test.\n"
    "- A genuine agent-set cap above the envelope is still held (existing tests plus one new test).\n"
    "- The plugins/coach suite passes.")
REVIEWER_REPLY = (  # case 3, verbatim held line
    "- **Code that quotes the file:** the two GPU sentences the coach plugin quotes are kept word for word, and all "
    "five headings that code cites are unchanged.")
BLOCKQUOTED_RULE = (
    "Proposed AGENTS.md text:\n\n"
    "> Once I've said go on a job that needs a GPU, you may rent ONE single-GPU machine up to $25 total without "
    "asking. Ask only above $25 total or for multi-GPU / multi-node; reply with an explicit number to approve.\n\n"
    "No other changes.")
CODE_RULE = "The note reads `Approve renting 4 GPUs with a $3000 budget?` and stays unchanged."


@pytest.mark.parametrize('surface', ['reply', 'kanban_create', 'kanban_block'])
@pytest.mark.parametrize('text', [REVIEW_SUMMARY + TOPIC, REVIEW_EXPLANATION + TOPIC, CARD_BODY, REVIEWER_REPLY,
                                  BLOCKQUOTED_RULE, CODE_RULE])
def test_quoted_described_or_proposed_rules_are_not_held(tmp_path, monkeypatch, surface, text):
    assert call(_hooks(tmp_path, monkeypatch), surface, text) is None
    assert not any(str(r.get('rule', '')).startswith('compute') for r in _log(tmp_path))


def test_json_quoted_human_request_scope_is_not_a_multi_node_declaration(tmp_path, monkeypatch):
    """Case 4: the human_request used to be JSON-encoded into the checked text, quoting every value."""
    hooks = _hooks(tmp_path, monkeypatch)
    args = {'reason': 'Need a second Linux worker for the board.', 'kind': 'needs_input', 'human_request': {
        'action': 'Reply A or B. Unblock = provision; archive = drop.', 'why_human': 'New EC2 spend needs you.',
        'interaction': 'reply', 'scope': 'one t3.large worker. No GPU or multi-node.', 'evidence': 'one worker'}}
    assert hooks['pre_tool_call'](session_id='m', tool_name='kanban_block', args=args) is None


# --- still held -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('surface', ['reply', 'kanban_create', 'kanban_block'])
@pytest.mark.parametrize('text', [
    'I set a $3000 GPU cap for the sweep; the coach rule says to ask, but the deadline is today.',
    'I will rent 4 GPUs now; the vendor page says "H100 x4 available".',
    'Renting 8 H100s on Vast now with a $3000 cap.',
    'GPU rental budget: $500 for stage B, per the plan quoted above.',
    # live hold (coach log, 11-03) that rule talk alone would have excused: the agent set the cap itself
    "I've set the new GPU rules: each idea card can now rent its own GPU, and the whole campaign has a **$75 cap**.",
])
def test_genuine_caps_next_to_rule_talk_or_quotes_are_still_held(tmp_path, monkeypatch, surface, text):
    out = call(_hooks(tmp_path, monkeypatch), surface, text)
    assert out
    assert any(r.get('rule') == 'compute_authority' for r in _log(tmp_path))


def test_cap_inside_human_request_is_still_held(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    args = {'reason': 'Starting stage B.', 'human_request': {
        'action': 'FYI only. Unblock = continue; archive = stop.', 'why_human': 'status',
        'interaction': 'reply', 'scope': 'GPU rental cap $3000 on Vast', 'evidence': 'plan'}}
    out = hooks['pre_tool_call'](session_id='m', tool_name='kanban_block', args=args)
    assert out and out['action'] == 'block'
    assert any(r.get('rule') == 'compute_authority' for r in _log(tmp_path))


@pytest.mark.parametrize('text', [
    'Reply "approve $20" and I\'ll rent the GPU.',
    'Reply **\u201capprove $20\u201d** and I\u2019ll rent one 4090 on Vast.',
    'May I rent one 4090 on Vast for $12? The rule I follow is "ask before renting".',
])
def test_in_envelope_asks_around_quotes_are_still_classified(text):
    assert _ce().classify(text)


def test_in_envelope_ask_only_in_human_request_is_still_held(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    args = {'reason': 'Waiting on compute.', 'human_request': {
        'action': 'Approve renting one L40S GPU on Vast for $20. Unblock = rent; archive = drop.',
        'why_human': 'spend', 'interaction': 'approval', 'scope': 'one L40S', 'evidence': 'Slurm down'}}
    out = hooks['pre_tool_call'](session_id='m', tool_name='kanban_block', args=args)
    assert out and out['action'] == 'block'
    assert any(r.get('rule') == 'compute_envelope' for r in _log(tmp_path))


def test_unquoted_keeps_answer_quotes_and_blanks_mentions():
    ce = _ce()
    assert '"approve $20"' in ce.unquoted('Reply "approve $20" to go.')
    out = ce.unquoted('Rule: "ask before renting a GPU". Code `rent 4 GPUs now` ok `t_1`.\n> quoted line\nend')
    assert 'ask before' not in out and 'rent 4' not in out and 'quoted line' not in out
    assert '`t_1`' in out and 'end' in out
