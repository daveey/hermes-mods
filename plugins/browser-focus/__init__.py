"""browser-focus: when the agent needs the user to act in the agent-driven Chrome (sign in, 2FA,
captcha, click something), bring that Chrome window and the right tab to the front automatically.

Three entry points, one `focus()`:
- `transform_tool_result` on browser_exec: the result shows a sign-in / 2FA / captcha page → focus
  that tab and append a note so the agent tells the user it is in front.
- `transform_llm_output`: the final reply asks the user to do something in Chrome/the browser in a
  session that drove the browser → focus it and append one line saying so.
- `browser_focus` tool for anything else (agent calls it before asking).

The agent's Chrome is found as a Chrome process whose --user-data-dir lives under the Hermes home
(the real-profile copy browser) with a --remote-debugging-port; the tab is raised over CDP
(/json/activate) and the app over NSRunningApplication (JXA), which needs no Accessibility grant.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from typing import Any

log = logging.getLogger("browser-focus")
TOOLSET = "browser_focus"

_LOGIN_URL = re.compile(
    r"accounts\.google\.com/[^\s'\"]*(signin|challenge|ServiceLogin)|appleid\.apple\.com|login\.microsoftonline\.com"
    r"|/(login|signin|sign-in|sign_in|log-in|auth/|oauth|sso|mfa|2fa|two-factor|verify|challenge|captcha)\b",
    re.I)
_LOGIN_TEXT = re.compile(r"\b(captcha|verify you are human|two-factor|2-step verification|enter the code)\b", re.I)
_ASKS_BROWSER = re.compile(r"\b(chrome|browser|tab|window|sign-in page|login page)\b", re.I)
_ASKS_ACTION = re.compile(
    r"\b(sign(ed)? in|log(ged)? in|log on|click|tap|approve|solve|captcha|2fa|verification code|enter (the|your)"
    r"|type (the|your)|select|choose|allow|confirm|accept|complete)\b", re.I)
_URL = re.compile(r"https?://[^\s'\"<>|)\]]+")

_browser_sessions: dict[str, float] = {}   # session_id -> last browser_exec time
_last_focus: dict[str, float] = {}         # session_id|url-key -> time, to avoid focus-stealing loops
_lock = threading.Lock()
SESSION_TTL_S = 3600
REFOCUS_S = 90


def _hermes_home() -> str:
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:
        return os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))


def agent_chromes(home: str | None = None, ps_output: str | None = None) -> list[tuple[int, int]]:
    """(pid, devtools port) of Chrome/Chromium main processes whose profile dir is under the Hermes home."""
    home = os.path.realpath(home or _hermes_home())
    if ps_output is None:
        ps_output = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
    found = []
    for line in ps_output.splitlines():
        if "--type=" in line or "--headless" in line or "--remote-debugging-port=" not in line:
            continue
        m_dir = re.search(r"--user-data-dir=(\S+)", line)
        m_port = re.search(r"--remote-debugging-port=(\d+)", line)
        if not (m_dir and m_port):
            continue
        if not os.path.realpath(m_dir.group(1)).startswith(home):
            continue
        found.append((int(line.split()[0]), int(m_port.group(1))))
    return found


def _get(url: str, timeout: float = 3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = r.read().decode()
    try:
        return json.loads(body)
    except ValueError:
        return body


def pick_tab(tabs: list[dict], match: str | None = None) -> dict | None:
    pages = [t for t in tabs if t.get("type") == "page" and not str(t.get("url", "")).startswith(("chrome://", "devtools://"))]
    if not pages:
        return None
    if match:
        m = match.lower()
        hit = [t for t in pages if m in str(t.get("url", "")).lower() or m in str(t.get("title", "")).lower()]
        if hit:
            return hit[0]
    login = [t for t in pages if _LOGIN_URL.search(str(t.get("url", ""))) or "sign in" in str(t.get("title", "")).lower()]
    return (login or pages)[0]   # /json/list is most-recently-active first


def activate_app(pid: int) -> bool:
    if not shutil.which("osascript"):
        return False
    js = ('ObjC.import("AppKit"); var a=$.NSRunningApplication.runningApplicationWithProcessIdentifier(%d);'
          ' a ? a.activateWithOptions(3) : false' % pid)
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True, timeout=5)
    return r.stdout.strip() == "true"


def _quiet() -> bool:
    """Respect quiet/movie mode (host-wide ~/.hermes/quiet-mode.json): never steal focus while it is on."""
    try:
        path = os.environ.get("HERMES_QUIET_MODE_FILE") or os.path.expanduser("~/.hermes/quiet-mode.json")
        data = json.load(open(path))
        until = data.get("until")
        return bool(data.get("on")) and (not until or float(until) > time.time())
    except Exception:
        return False


def focus(match: str | None = None) -> dict:
    if _quiet():
        return {"focused": False, "error": "quiet mode is on; not raising windows"}
    chromes = agent_chromes()
    if not chromes:
        return {"focused": False, "error": "no agent-driven Chrome is running"}
    pid, port, tab = chromes[0][0], chromes[0][1], None
    for c_pid, c_port in chromes:   # several visible agent Chromes: take the one holding the wanted tab
        try:
            t = pick_tab(_get(f"http://127.0.0.1:{c_port}/json/list"), match)
        except Exception as e:
            log.debug("browser-focus: CDP list failed on %s: %s", c_port, e)
            continue
        hit = t and (not match or match.lower() in (str(t.get("url", "")) + str(t.get("title", ""))).lower())
        if t and (tab is None or hit):
            pid, port, tab = c_pid, c_port, t
            if hit:
                break
    try:
        if tab:
            _get(f"http://127.0.0.1:{port}/json/activate/{tab['id']}")
    except Exception as e:
        log.debug("browser-focus: CDP activate failed: %s", e)
    app = activate_app(pid)
    return {"focused": bool(app or tab), "app_activated": app, "pid": pid,
            "tab": {"title": tab.get("title"), "url": str(tab.get("url", ""))[:200]} if tab else None}


def _throttled(key: str) -> bool:
    now = time.time()
    with _lock:
        if now - _last_focus.get(key, 0) < REFOCUS_S:
            return True
        _last_focus[key] = now
    return False


def _login_url_in(text: str) -> str | None:
    for u in _URL.findall(text):
        if _LOGIN_URL.search(u):
            return u
    return None


NOTE = ("\n\n[browser-focus] The Chrome window is now in front of the user, on the tab \"{title}\". If they need "
        "to act there, tell them it is already in front (no need to describe which window to find).")


def transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_):
    if tool_name != "browser_exec" or not isinstance(result, str):
        return None
    with _lock:
        _browser_sessions[session_id or ""] = time.time()
    if "[browser-focus]" in result:
        return None
    url = _login_url_in(result)
    if not url and not _LOGIN_TEXT.search(result):
        return None
    host = re.sub(r"^https?://([^/]+).*", r"\1", url or "")
    if _throttled(f"{session_id}|{host}"):
        return None
    res = focus(host or None)
    if not res.get("focused"):
        return None
    return result + NOTE.format(title=(res.get("tab") or {}).get("title") or "?")


def asks_user_in_browser(text: str) -> bool:
    return bool(_ASKS_BROWSER.search(text) and _ASKS_ACTION.search(text))


def transform_llm_output(response_text: str = "", session_id: str = "", **_):
    if not isinstance(response_text, str) or not asks_user_in_browser(response_text):
        return None
    with _lock:
        used = time.time() - _browser_sessions.get(session_id or "", 0) < SESSION_TTL_S
    if not used or _throttled(f"{session_id}|reply"):
        return None
    res = focus(_login_url_in(response_text))
    if not res.get("focused"):
        return None
    return response_text.rstrip() + "\n\n_I've brought the Chrome window to the front for you._"


SCHEMA = {
    "name": "browser_focus",
    "description": ("Bring the agent-driven Chrome window (the one browser_exec controls) to the front of the user's "
                    "screen and switch it to the tab that needs them. Call it whenever you are about to ask the user "
                    "to do anything in that browser (sign in, 2FA, captcha, click, approve) so they don't have to "
                    "hunt for the window. `match` picks the tab by URL/title substring; default is a sign-in tab, "
                    "else the most recent tab."),
    "parameters": {"type": "object", "properties": {
        "match": {"type": "string", "description": "Substring of the tab URL or title to show (optional)."}}},
}


def handle(args: dict, **_) -> str:
    try:
        return json.dumps(focus((args or {}).get("match") or None))
    except Exception as e:
        return json.dumps({"focused": False, "error": f"{type(e).__name__}: {e}"})


def register(ctx) -> None:
    if shutil.which("osascript") is None:
        return  # no screen to raise a window on (Linux/cloud gateway)
    ctx.register_tool(name="browser_focus", toolset=TOOLSET, schema=SCHEMA, handler=handle, emoji="🔝",
                      description="Bring the agent's Chrome window/tab to the front for the user")
    ctx.register_hook("transform_tool_result", transform_tool_result)
    ctx.register_hook("transform_llm_output", transform_llm_output)
