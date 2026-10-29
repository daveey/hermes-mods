"""Explicit valid human-decision payload for lifecycle/routing fixtures.

These tests predate admission. Their store calls must carry valid asks now; the
real gate still runs. Admission and transport tests never use this helper.
"""
from functools import partial

ASK = {"interaction": "preference", "why_human": "Only the owner can choose the proposed wording", "action": "Choose this wording"}


def decision_inbox(box):
    box.post = partial(box.post, poster="cli", ask=ASK)
    return box
