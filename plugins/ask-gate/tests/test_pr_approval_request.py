"""Generic syntax/ambiguity regressions. These are not authority receipts."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("pr_request_test", Path(__file__).parents[1] / "pr_approval_request.py")
assert spec is not None and spec.loader is not None
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)


def row(action, scope="example/widget PR #1950 (change), head 21cb9c1c2 only", **parts):
    return {"human_request": {"interaction": "approval", "action": action, "scope": scope}, **parts}


@pytest.mark.parametrize("tag", ["head", "sha", "commit"])
@pytest.mark.parametrize("form", [" at {} 21cb9c1c2", " {} 21cb9c1c2", " ({} 21cb9c1c2)"])
@pytest.mark.parametrize("reference", ["PR #1950", "example/widget#1950", "https://github.com/example/widget/pull/1950"])
def test_head_forms(tag, form, reference):
    r = row("Approve " + reference + form.format(tag))
    assert parser.request_target(r) == ("example/widget", 1950, "21cb9c1c2")
    assert parser.parse_failure_reason(r) == ""


@pytest.mark.parametrize("surfaces", ["Discord", "Slack", "BB Inbox", "the BB Inbox", "Inbox", "the Inbox",
    "Slack/Discord/BB Inbox", "Discord, Slack or the BB Inbox", "Discord, Slack, or the BB Inbox",
    "Slack or the Inbox", "Discord / Slack / the BB Inbox", "Discord,Slack or BB Inbox"])
def test_supported_surfaces(surfaces):
    assert parser.request_target(row("OK PR #1950 at sha 21cb9c1c2 on GitHub, or OK the bb approval ask in " + surfaces)) == (
        "example/widget", 1950, "21cb9c1c2")


def test_reported_regression_generic_owner():
    action = ("Approve PR #1950 at head 21cb9c1c2 on GitHub, or OK the bb approval ask in "
              "Slack/Discord/BB Inbox. Unblock = queue merges+deploys and I verify; "
              "archive = close the PR, findings stay open.")
    assert parser.request_target(row(action)) == ("example/widget", 1950, "21cb9c1c2")


@pytest.mark.parametrize("action", [
    "Approve PR #1950 at head 21cb9c1c2 and rotate the deploy key",
    "Approve PR #1950 and authorize deployment", "Approve PR #1950; pay the bill",
    "Approve PR #1950 or PR #1951", "Approve PR #1950 (head 21cb9c1c2) (head abcdef123)",
    "Approve PR #1950 (head 123456)", "Approve PR #1950 (head " + "a" * 41 + ")",
    "Approve PR #1950 or OK the bb approval ask in email", "Approve PR #1950 or OK the bb approval ask in Discord and Slack",
    "Approve PR #1950 or OK the bb approval ask in Discord, admin console",
    "Approve PR #1950. Unblock = rotate the key; archive = close the PR",
    "Approve PR #1950. Unblock = force-push main; archive = close the PR",
    "Approve PR #1950. Unblock = grant access; archive = close the PR",
    "Approve PR #1950. Unblock = authorize spend; archive = close the PR",
    "Approve https://github.com/example/widget/pull/1950/files",
    "Approve https://github.com/example/widget/pull/1950?head=21cb9c1c2",
    "Approve https://github.com/example/widget/pull/1950#review",
])
def test_mixed_or_unknown_action_rejected(action):
    assert parser.request_target(row(action)) is None
    assert parser.parse_failure_reason(row(action))


@pytest.mark.parametrize("scope", [
    "example/widget PR #1950", "https://github.com/example/widget/pull/1950",
    "example/widget#1950", "Repo: example/widget", "Repository example/widget"])
def test_explicit_context_resolves(scope):
    assert parser.request_target(row("Approve PR #1950", scope)) == ("example/widget", 1950, None)


@pytest.mark.parametrize("scope", [
    "example/widget", "Work in example/widget on PR #1950", "example/widget PR #999",
    "example/widget#1950 other/project#1950", "Repo: example/widget Repository: other/project",
    "Repo: other/project example/widget PR #1950", 123])
def test_no_guessing_or_conflicting_labels(scope):
    assert parser.request_target(row("Approve PR #1950", scope)) is None


def test_context_head_not_authority_but_canonical_hint():
    r = row("Approve PR #1950")
    assert parser.request_target(r) == ("example/widget", 1950, None)
    assert "(head 21cb9c1c2)" in parser.canonical_wording(r)


def test_canonical_separate_from_mixed_action_parse():
    r = row("Approve PR #1950 at head 21cb9c1c2 and rotate key")
    assert parser.request_target(r) is None
    assert parser.canonical_wording(r) == (
        "Approve https://github.com/example/widget/pull/1950 (head 21cb9c1c2) on GitHub, "
        "or OK the bb approval ask in Discord, Slack or the BB Inbox. Unblock = …; archive = ….")


def test_ambiguous_heads_not_fabricated():
    r = row("Approve PR #1950 head abcdef123")
    assert "(head SHA)" in parser.canonical_wording(r)


def test_missing_fields_not_fabricated():
    r = row("Approve PR #1950 and deploy", scope="Work on change")
    assert "/OWNER/REPO/pull/1950 (head SHA)" in parser.canonical_wording(r)
    assert "repository" in parser.parse_failure_reason(r) and "head" in parser.parse_failure_reason(r)
    empty = row("OK the bb approval ask", scope="")
    assert "/OWNER/REPO/pull/N (head SHA)" in parser.canonical_wording(empty)
    assert "PR number" in parser.parse_failure_reason(empty)


def test_context_only_metadata_is_a_hint_not_strict_target():
    r = row("OK the bb approval ask")
    assert parser.request_target(r) is None
    assert "/example/widget/pull/1950 (head 21cb9c1c2)" in parser.canonical_wording(r)


def test_owner_policy_is_not_public_parser_policy():
    assert parser.request_target(row("Approve https://github.com/another/project/pull/8")) == ("another/project", 8, None)


@pytest.mark.parametrize("interaction", ["approval", "reply", "external"])
def test_supported_interactions_and_json(interaction):
    r = row("Please Authorize PR #1950 (commit ABCDEF123)")
    r["human_request"]["interaction"] = interaction
    r["human_request"] = json.dumps(r["human_request"])
    assert parser.request_target(r) == ("example/widget", 1950, "ABCDEF123")


@pytest.mark.parametrize("hr", [None, "not JSON", "[]", [], {"interaction": "physical", "action": "Approve PR #1950"},
    {"interaction": "approval", "action": 123}])
def test_bad_request_safe(hr):
    assert parser.request_target({"human_request": hr}) is None
    assert isinstance(parser.parse_failure_reason({"human_request": hr}), str)


@pytest.mark.parametrize("action, expected", [("Approve PR #1950 and deploy", True),
    ("OK https://github.com/example/widget/pull/1950", True), ("OK the bb approval ask", True),
    ("Approve the budget", False), ("Review PR #1950", False), ("The owner approved PR #1950", False)])
def test_detection_is_not_authority(action, expected):
    assert parser.looks_like_pr_approval(row(action)) is expected
