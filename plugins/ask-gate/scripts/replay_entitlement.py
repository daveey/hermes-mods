"""Replay structured human_request cases through the REAL judge: vendor entitlement missing vs credential missing.

Run under the Hermes runtime interpreter (see replay.py for the command), with an optional repeat count:
  $RT -I -c "import sys,runpy; sys.argv=['x','3']; sys.path.insert(0,'$HOME/.hermes/hermes-agent'); \
import hermes_bootstrap; runpy.run_path('scripts/replay_entitlement.py')"
Uses the live capability sheet of this machine (never written to disk). Tool logs are synthetic and hold no secrets.
Expected: entitlement cases pass (the owner must change the App), credential cases are refused (the agent can mint).
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ask_gate_replay_ent", HERE / "__init__.py",
                                              submodule_search_locations=[str(HERE)])
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
tmp = Path(tempfile.mkdtemp())
os.environ["ASK_GATE_LEDGER"] = str(tmp / "l.sqlite")
gate._LEDGER.update(path=str(tmp / "l.sqlite"), obj=gate.core.Ledger(tmp / "l.sqlite"))
gate.core.card_status = lambda ids, db_paths=None: {}

INSTALL_NO_ADMIN = json.dumps({"id": 1001, "app_slug": "example-app", "account": {"login": "owner"},
                               "repository_selection": "all", "permissions": {
                                   "checks": "read", "issues": "write", "actions": "write", "contents": "write",
                                   "metadata": "read", "workflows": "write", "pull_requests": "write"}})
INSTALL_WITH_ADMIN = json.dumps({"id": 1001, "app_slug": "example-app", "account": {"login": "owner"},
                                 "repository_selection": "all", "permissions": {
                                     "administration": "read", "actions": "write", "metadata": "read"}})
GH = "token-broker exec --scope user/github-app-key -- python3 check_app.py"

ENTITLEMENT_TOOLS = [
    ("terminal", {"command": GH + " installation"}, "GET /app/installations/1001 -> 200 " + INSTALL_NO_ADMIN),
    ("terminal", {"command": GH + " mint --permissions administration:read --repo example-repo"},
     'POST /app/installations/1001/access_tokens {"permissions":{"administration":"read"},'
     '"repositories":["example-repo"]} -> HTTP 422 {"message":"The permissions requested are not granted to this '
     'installation."}'),
    ("terminal", {"command": GH + " mint --permissions actions:read --repo example-repo && gh api "
                  "/repos/owner/example-repo/actions/runners/25"},
     "mint actions:read -> 201 (token not printed); GET /repos/owner/example-repo/actions/runners/25 -> HTTP 403 "
     '{"message":"Resource not accessible by integration"}'),
    ("web_extract", {"urls": ["https://docs.github.com/en/rest/actions/self-hosted-runners"]},
     "Get a self-hosted runner for a repository ... Fine-grained access tokens / GitHub App: \"Administration\" "
     "repository permissions (read)"),
]
ENTITLEMENT_REQ = {
    "action": "Choose: (a) add Repository permissions -> Administration: Read-only to the shared GitHub App at "
              "https://github.com/organizations/example-org/settings/apps/example-app/permissions and accept the "
              "update at https://github.com/settings/installations/1001, or (b) a dedicated broker-only App.",
    "why_human": "Expanding a shared org App's maximum permissions (it is installed on all repositories) or creating "
                 "a new App is an owner decision; GitHub has no API to change an App's permissions, and no token "
                 "minted from this App can exceed what the installation was granted.",
    "scope": "App example-app installation 1001; broker tokens limited to administration:read on example-repo. "
             "The task forbids falling back to the classic user/github token that this card replaces.",
    "evidence": "GET installation 1001 permissions omit administration; mint administration:read -> HTTP 422 "
                "'The permissions requested are not granted to this installation'; actions:read token -> HTTP 403 "
                "on GET runners/25; docs require Administration read.",
    "interaction": "reply",
}

CRED_TOOLS = [
    ("terminal", {"command": GH + " installation"}, "GET /app/installations/1001 -> 200 " + INSTALL_WITH_ADMIN),
    ("terminal", {"command": "GITHUB_TOKEN=$(token-broker get user/github-bb-stall-runner-read) gh api "
                  "/repos/owner/example-repo/actions/runners/25"},
     'HTTP 403 {"message":"Resource not accessible by personal access token"}'),
]
CRED_REQ = {
    "action": "Add Administration: Read to the runner-read token so the broker can read runner 25.",
    "why_human": "The token lacks the permission; only David can edit it.",
    "scope": "example-repo runner reads only.",
    "evidence": "GET runners/25 with the runner-read token -> HTTP 403 'Resource not accessible by personal access "
                "token'.",
    "interaction": "reply",
}

CF_TOOLS = [
    ("terminal", {"command": "token-broker exec --scope cf-infra -- curl -s "
                  "https://api.cloudflare.com/client/v4/zones/Z/email/routing"},
     'HTTP 403 {"success":false,"errors":[{"code":10000,"message":"Authentication error"}]}'),
]
CF_REQ = {
    "action": "Add Email Routing: Edit to the cf-infra Cloudflare API token in the dashboard.",
    "why_human": "The token lacks Email Routing permission and only David can edit tokens in the dashboard.",
    "scope": "One zone's email routing rules.",
    "evidence": "GET email/routing with cf-infra -> HTTP 403 Authentication error.",
    "interaction": "reply",
}

CASES = [("entitlement: App lacks administration (expect ok)", ENTITLEMENT_TOOLS, ENTITLEMENT_REQ, True),
         ("credential: installation has admin, agent never minted (expect refused)", CRED_TOOLS, CRED_REQ, False),
         ("credential: Cloudflare token, minter on sheet (expect refused)", CF_TOOLS, CF_REQ, False)]


def run_case(name, tools, req, n):
    sid = "replay-" + name.split(":")[0]
    passed = 0
    for k in range(n):
        gate.LOG.reset(sid)
        for tool, args, result in tools:
            gate.LOG.add(sid, tool, json.dumps(args), result)
        r = gate.pre_tool_call(tool_name="kanban_create", session_id=sid,
                               args={"title": "decision", "initial_status": "blocked", "human_request": req})
        ok = r is None
        passed += ok
        print(f"   run {k + 1}: {'PASSED' if ok else 'REFUSED'}" + ("" if ok else f" -- {r['message'][:260]}"))
    return passed


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    bad = 0
    for name, tools, req, want_pass in CASES:
        print(f"== {name}")
        p = run_case(name, tools, req, n)
        good = p if want_pass else n - p
        bad += n - good
        print(f"   expected outcome {good}/{n}")
    print(f"TOTAL mismatches: {bad}")
    sys.exit(1 if bad else 0)
