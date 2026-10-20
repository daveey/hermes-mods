"""Risk classification for one tool call. Pure functions: (tool_name, args) -> [Finding].

Classes (metadata only; nothing here blocks anything):
  dangerous-command  terminal/process command matching Hermes' own approval patterns
  credential-read    a command, code or file path touching ~/.ssh, .env, auth.json, the keychain, broker token...
  real-browser       chrome-devtools MCP calls (it attaches to the user's real, logged-in Chrome)
  message-send       WhatsApp / Slack / Discord / Signal / iMessage / Telegram send tools
  spend-remote-exec  autoresearch run_command / sandbox_exec / buy_credits / set_auto_topup
  broker-vend        token-broker exec / request (scope name kept, never the vended value)
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

SHELL_TOOLS = {"terminal", "process", "process_manage", "run_in_user_terminal", "mcp__terminal",
               "mcp__process_manage"}
CODE_TOOLS = {"execute_code", "mcp__execute_code"}
PATH_TOOLS = {"read_file", "search_files", "mcp__read_file", "mcp__search_files"}
BRIDGE_TOOLS = {"tool_call", "mcp__tool_call"}

CRED_PATH = re.compile(
    r"(?:~|\$HOME|/Users/[^/\s]+|/home/[^/\s]+|/root)?/\.ssh(?:/|\b)"
    r"|(?:^|[\s'\"/=:])\.env(?:\.[\w-]+)?(?=$|[\s'\";|&)])"
    r"|\bauth\.json\b|\bauth-profiles\.json\b"
    r"|\bsecurity\s+(?:find|dump|export)-[\w-]*(?:password|keychain|certificate|identity)"
    r"|/Keychains?/|\.keychain(?:-db)?\b"
    r"|\.config/token-broker/token\b|\.aws/credentials\b|\.netrc\b|\.git-credentials\b"
    r"|\bgit\s+credential\s+fill\b|\bid_(?:rsa|ed25519|ecdsa|dsa)\b|\.pem\b|\.p12\b"
    r"|\bgh\s+auth\s+token\b|\bgcloud\s+auth\s+print-access-token\b",
)
BROKER = re.compile(r"token[-_]broker(?:_client\.py)?\b[^|;&]*?\b(exec|request)\b")
SCOPE = re.compile(r"--scope[=\s]+['\"]?([\w./:$-]+)")
SCOPE_NAME = re.compile(r"^[a-z0-9][\w.-]*(/[a-z0-9][\w-]*)?$")
BROWSER = re.compile(r"chrome[-_]?devtools", re.I)
MESSAGE_NAME = re.compile(r"(whatsapp|slack|discord|signal|imessage|telegram|sms)", re.I)
MESSAGE_VERB = re.compile(r"(send|post|reply|engage|react|upload)", re.I)
SPEND = re.compile(r"autoresearch.*?(run_command|sandbox_exec|buy_credits|set_auto_topup)$", re.I)
SEND_CORE = {"send_message"}
BROWSER_WRITE = re.compile(r"(click|fill|type|press|navigate|evaluate|drag|upload|handle_dialog|new_page|"
                           r"close_page|emulate|hover)", re.I)


class Finding:
    """One risk finding. ``text`` is the command/code/path it came from: the store hashes and redacts it and never
    keeps it raw."""

    __slots__ = ("risk_class", "level", "detail", "text")

    def __init__(self, risk_class: str, level: str, detail: Optional[dict] = None, text: str = ""):
        self.risk_class, self.level, self.detail, self.text = risk_class, level, detail or {}, text


def _shell_text(args: dict) -> str:
    return " ".join(str(args.get(k) or "") for k in ("command", "input", "data", "cmd") if args.get(k))


# Hermes' detector is superlinear on long input (10 KB heredoc ~0.4 s, 50 KB ~2 s). Its patterns key on the
# command head, so the background writer checks only the first DETECT_MAX chars.
DETECT_MAX = 4000


def _dangerous(command: str, detector: Optional[Callable[[str], tuple]]) -> Optional[str]:
    if not detector or not command.strip():
        return None
    try:
        hit, key, _desc = detector(command[:DETECT_MAX])
    except Exception:  # noqa: BLE001 - observability only
        return None
    return str(key) if hit else None


def _broker(text: str) -> list[Finding]:
    out = []
    for match in BROKER.finditer(text):
        tail = text[match.start():match.start() + 400]
        scope = SCOPE.search(tail)
        name = scope.group(1)[:80] if scope else ""
        out.append(Finding("broker-vend", "high", {"verb": match.group(1),
                                                   "scope": name if SCOPE_NAME.match(name) else None}, text))
    return out


def _cred(text: str, source: str) -> list[Finding]:
    match = CRED_PATH.search(text)
    if not match:
        return []
    return [Finding("credential-read", "high", {"via": source, "match": match.group(0).strip(" '\"=:")[:40]}, text)]


def classify(tool_name: str, args: Any, detector: Optional[Callable[[str], tuple]] = None) -> list[Finding]:
    """All findings for one call. ``detector`` is Hermes' detect_dangerous_command (injected for tests)."""
    name = str(tool_name or "")
    args = args if isinstance(args, dict) else {}
    found: list[Finding] = []
    if name in BRIDGE_TOOLS:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                found += classify(str(call.get("name") or ""), call.get("arguments"), detector)
        return found
    if name in SHELL_TOOLS:
        text = _shell_text(args)
        key = _dangerous(text, detector)
        if key:
            found.append(Finding("dangerous-command", "high", {"pattern": key[:80]}, text))
        found += _cred(text, "shell")
        found += _broker(text)
    elif name in CODE_TOOLS:
        text = str(args.get("code") or "")
        found += _cred(text, "code")
        found += _broker(text)
    elif name in PATH_TOOLS:
        text = str(args.get("path") or "")
        found += _cred(text, "path")
    elif BROWSER.search(name):
        action = name.rsplit("__", 1)[-1]
        found.append(Finding("real-browser", "high" if BROWSER_WRITE.search(action) else "medium",
                             {"action": action[:60]}))
    elif SPEND.search(name):
        found.append(Finding("spend-remote-exec", "high", {"action": name.rsplit("__", 1)[-1][:60]}))
    elif name in SEND_CORE or (MESSAGE_NAME.search(name) and MESSAGE_VERB.search(name.rsplit("__", 1)[-1])):
        platform = MESSAGE_NAME.search(name)
        target = args.get("target") or args.get("chat_id") or args.get("channel") or args.get("recipient")
        found.append(Finding("message-send", "high", {
            "platform": (platform.group(1).lower() if platform else str(target or "").split(":", 1)[0][:20]) or None,
            "action": name.rsplit("__", 1)[-1][:60]}))
    return found
