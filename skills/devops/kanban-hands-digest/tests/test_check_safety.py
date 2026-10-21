from test_hands_digest import hd, cfg


def test_check_args_are_shell_quoted(tmp_path):
    marker = tmp_path / 'executed'
    c = cfg(checks={'echo': {'cmd': 'printf "%s" {0}'}})
    state, text = hd.run_check(f'echo "$(touch {marker})"', c)
    assert state == 'ok'
    assert not marker.exists()
    assert 'touch' in text


def test_bad_check_returns_error():
    c = cfg(checks={'echo': {'cmd': 'printf "%s" {0}'}})
    for value in ['echo', 'echo "', '   ']:
        assert hd.run_check(value, c)[0] == 'error'


def test_initial_card_human_steps_not_shipping_paragraph():
    body = "Shipped code.\n\nSam's steps (daytime only):\n1. Open the app.\n2. Tap Allow.\n\nAgent steps afterwards:\n- Read logs."
    reason = hd.initial_reason(body)
    assert 'Open the app' in reason and 'Tap Allow' in reason
    assert 'Read logs' not in reason and 'Shipped' not in reason


def test_initial_card_reason_line_beats_boilerplate_first_line():
    # Escalation cards open with a generic "worker stopped ... needs a human" line; the question is on "Reason:".
    body = ("The worker stopped on card t_1 (board `bb`) and needs a human.\n\n"
            "Reason: Pick a, b or c for the front-door unlock.\n\nRefs: BB-1.\nResolve: comment, then unblock.")
    reason = hd.initial_reason(body)
    assert reason.startswith('Pick a, b or c')
    assert 'needs a human' not in reason and 'Refs' not in reason
