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
    r"|/(login|signin|sign-in|sign_in|log-in|auth/|oauth|sso|mfa|2fa|two-factor|verify|challenge|captcha"
    r"|signup|sign-up|sign_up|join|register|registration)\b",
    re.I)
_LOGIN_TEXT = re.compile(r"\b(captcha|verify you are human|two-factor|2-step verification|enter the code)\b", re.I)
_ASKS_BROWSER = re.compile(r"\b(chrome|browser|tab|window|sign-in page|login page)\b", re.I)
_ASKS_ACTION = re.compile(
    r"\b(sign(ed)? in|log(ged)? in|log on|click|tap|approve|solve|captcha|2fa|verification code|enter (the|your)"
    r"|type (the|your)|select|choose|allow|confirm|accept|complete)\b", re.I)
_URL = re.compile(r"https?://[^\s'\"<>|)\]]+")

# Page kinds a reply can ask about, and how a tab shows it is that page (URL or title). A reply's words pick the
# kinds; the raised tab must be one of them, else the hook says nothing (agent-07dbafdef0).
_REPLY_KINDS = {
    "signup": re.compile(r"\b(sign(ing)?[- ]?up|signup|register|registration|create (an |a |your |the )?account)\b", re.I),
    "captcha": re.compile(r"\b(captcha|verify you are human|human verification)\b", re.I),
    "signin": re.compile(r"\b(sign(ed|ing)? in|sign-in|log(ged|ging)? in|login|log-in|2fa|two-factor|verification code"
                         r"|enter the code)\b", re.I),
}
_SIGNUP_TAB = re.compile(r"/(signup|sign-up|sign_up|join|register|registration|create[-_]?account)\b"
                         r"|\b(sign up|join|create (an |your )?account|register)\b", re.I)
_SIGNIN_TAB = re.compile(r"\b(sign in|log in|login|two-factor|2-step|verification)\b", re.I)
_CAPTCHA_TAB = re.compile(r"captcha|challenge|verify|arkose|are you human", re.I)


def tab_is_kind(tab: dict, kind: str) -> bool:
    url, title = str(tab.get("url", "")), str(tab.get("title", ""))
    both = f"{url} {title}"
    signup = bool(_SIGNUP_TAB.search(both))
    signin = bool(_LOGIN_URL.search(url) or _SIGNIN_TAB.search(title))
    if kind == "signup":
        return signup
    if kind == "signin":
        return signin
    if kind == "captcha":   # captchas live on sign-in/sign-up pages or their own challenge page
        return bool(_CAPTCHA_TAB.search(both)) or signin or signup
    return False


def reply_target(text: str) -> tuple[str | None, tuple[str, ...]]:
    """(host, kinds) of the page a reply asks the user to use: the host of a URL in it, and the page kinds its
    words name. Both empty = the reply names no page, so nothing can be checked and nothing is raised."""
    host = None
    for u in _URL.findall(text or ""):
        host = re.sub(r"^https?://([^/]+).*", r"\1", u).lower()
        break
    kinds = tuple(k for k, rx in _REPLY_KINDS.items() if rx.search(text or ""))
    return host, kinds

_browser_sessions: dict[str, float] = {}   # session_id -> last browser_exec time
_last_focus: dict[str, float] = {}         # session_id|url-key -> time, to avoid focus-stealing loops
_last_raised: dict[str, str] = {}          # session_id -> URL of the tab last raised ("" = nothing matched)
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
        return hit[0] if hit else None   # never another tab: that was the false "in front" of agent-07dbafdef0
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


SHOW_MINUTES = 20


def _chrome_agent_cli() -> str | None:
    """The invisible-chrome-agent supervisor keeps its Chrome on a virtual display and re-homes any window
    that leaves it within ~1s, so raising the app alone just flashes it. Its `show` pauses that."""
    path = os.path.join(_hermes_home(), "chrome-agent-tools", "hermes-chrome-agent")
    return path if os.access(path, os.X_OK) else None


def _chrome_agent(cmd: list[str]) -> bool:
    cli = _chrome_agent_cli()
    if not cli:
        return False
    r = subprocess.run(["/usr/bin/python3", cli, *cmd], capture_output=True, text=True, timeout=20)
    return r.returncode == 0


def hide() -> dict:
    return {"hidden": _chrome_agent(["hide"])}


def show(minutes: int | None = None) -> dict:
    """Manual show (status-bar toggle): no login watcher, stays until hidden or `minutes` pass."""
    ok = _chrome_agent(["show", "--minutes", str(minutes)] if minutes else ["show"])
    chromes = agent_chromes()
    if ok and chromes:
        activate_app(chromes[0][0])
    return {"shown": ok}


def status() -> dict:
    cli = _chrome_agent_cli()
    flag = os.path.join(_hermes_home(), "cache", "chrome-agent", "show")
    return {"available": bool(cli and agent_chromes()), "shown": os.path.exists(flag)}


def _watch_login(port: int, target_id: str) -> None:
    """Hide again once the user finishes signing in (detached so it outlives one-shot CLI turns)."""
    cli = _chrome_agent_cli()
    if not cli:
        return
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch_login.py")
    subprocess.Popen(["/usr/bin/python3", script, str(port), target_id, cli, str(SHOW_MINUTES * 60)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def _unattended(session_id: str = "") -> bool:
    """Kanban workers and cron jobs have no human watching the session, so an automatic raise there is always a
    focus steal (2026-10-21 audit: a worker's final reply raised Chrome over David's app for 50 s)."""
    return bool(os.environ.get("HERMES_KANBAN_TASK")) or str(session_id or "").startswith("cron_")


def focus(match: str | None = None, require_login: bool = False, kinds: tuple[str, ...] = ()) -> dict:
    """Show the agent Chrome and raise it.
    - match: tab URL/title substring. A match that hits no tab raises NOTHING and returns focused=false with the
      open tabs, so the caller learns the page it wants is not there (never a fallback to another tab).
    - kinds: the tab must be one of these page kinds (signup/signin/captcha); same no-fallback rule.
    - require_login (the automatic tool-result path): the tab must be on a sign-in page."""
    if _quiet():
        return {"focused": False, "error": "quiet mode is on; not raising windows"}
    chromes = agent_chromes()
    if not chromes:
        return {"focused": False, "error": "no agent-driven Chrome is running"}
    strict = bool(match or kinds or require_login)
    pid, port, tab, seen = chromes[0][0], chromes[0][1], None, []
    for c_pid, c_port in chromes:   # several visible agent Chromes: take the one holding the wanted tab
        try:
            tabs = _get(f"http://127.0.0.1:{c_port}/json/list")
        except Exception as e:
            log.debug("browser-focus: CDP list failed on %s: %s", c_port, e)
            continue
        seen += [str(t.get("url", ""))[:200] for t in tabs
                 if t.get("type") == "page" and not str(t.get("url", "")).startswith(("chrome://", "devtools://"))]
        if require_login:
            tabs = [t for t in tabs if _LOGIN_URL.search(str(t.get("url", "")))]
        if kinds:
            tabs = [t for t in tabs if any(tab_is_kind(t, k) for k in kinds)]
        t = pick_tab(tabs, match)
        if t:
            pid, port, tab = c_pid, c_port, t
            break
    if strict and tab is None:
        if require_login and not (match or kinds):
            err = "no tab is on a sign-in page"
        else:
            want = " ".join(x for x in (match or "", f"({'/'.join(kinds)} page)" if kinds else "") if x)
            err = (f"no tab matches {want}; nothing was raised. Open that page first (browser_exec), then call "
                   "browser_focus again before telling the user it is in front")
        return {"focused": False, "error": err, "open_tabs": seen[:10]}
    shown = _chrome_agent(["show", "--minutes", str(SHOW_MINUTES)])
    try:
        if tab:
            _get(f"http://127.0.0.1:{port}/json/activate/{tab['id']}")
    except Exception as e:
        log.debug("browser-focus: CDP activate failed: %s", e)
    app = activate_app(pid)
    if shown and tab and _LOGIN_URL.search(str(tab.get("url", ""))):
        _watch_login(port, tab["id"])
    return {"focused": bool(app or tab), "app_activated": app, "pid": pid, "shown_on_main_display": shown,
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


# Core's browser SSRF guard (tools/browser_tool.py) refuses private/tailnet URLs with this text. A Kanban worker read
# it as "a human must do it" and blocked on David 21 s later, though driving the public site worked once nudged
# (agent-19492bff76). The hint says what to try first; it never blocks and never raises a window.
_PRIVATE_REFUSAL = re.compile(r"Blocked: (?:page )?URL targets a private or internal address")
PRIVATE_URL_HINT = (
    "\n\n[browser-focus] The browser can't open private/tailnet addresses. That does not mean a human is needed: "
    "fetch the page on the host (terminal: curl it, read the form/redirect it builds), then drive the public site "
    "(e.g. submit the same form from a github.com tab with browser_exec) before asking the user.")


def private_url_hint(tool_name: str = "", result: Any = None) -> str | None:
    if (not str(tool_name or "").startswith("browser") or not isinstance(result, str)
            or "[browser-focus] The browser can't open private" in result or not _PRIVATE_REFUSAL.search(result)):
        return None
    return result + PRIVATE_URL_HINT


def transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_):
    hinted = private_url_hint(tool_name, result)
    if hinted:
        return hinted
    if tool_name != "browser_exec" or not isinstance(result, str) or _unattended(session_id):
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
    res = focus(host or None, require_login=True)
    if not res.get("focused"):
        return None
    _remember(session_id, res)
    return result + NOTE.format(title=(res.get("tab") or {}).get("title") or "?")


_HYPOTHETICAL = re.compile(r"\b(unless|if|in case|won't|will not|don't need|no need|nothing will|not need|shouldn't need)\b", re.I)


def asks_user_in_browser(text: str) -> bool:
    """A sentence that asks for a browser action (the browser may be named in the sentence before it), not one that
    only mentions it ("unless Nest asks you to sign in")."""
    sents = [s for s in re.split(r"(?<=[.!?:])\s+|\n+", text) if s.strip()]
    for i, s in enumerate(sents):
        if not _ASKS_ACTION.search(s) or _HYPOTHETICAL.search(s):
            continue
        if _ASKS_BROWSER.search(s) or (i and _ASKS_BROWSER.search(sents[i - 1])):
            return True
    return False


def transform_llm_output(response_text: str = "", session_id: str = "", **_):
    if not isinstance(response_text, str) or _unattended(session_id) or not asks_user_in_browser(response_text):
        return None
    with _lock:
        used = time.time() - _browser_sessions.get(session_id or "", 0) < SESSION_TTL_S
    if not used or _throttled(f"{session_id}|reply"):
        return None
    host, kinds = reply_target(response_text)
    if not (host or kinds):
        return None   # the reply names no page, so a raised tab can't be checked against it: say nothing
    res = focus(host, kinds=kinds)
    _remember(session_id, res)
    tab = res.get("tab") if res.get("focused") else None
    if not tab:
        return None   # the page the reply asks about isn't open: never claim it is in front
    title = str(tab.get("title") or tab.get("url") or "").strip()[:80]
    return response_text.rstrip() + f"\n\n_I've brought the Chrome window to the front for you, on \"{title}\"._"


def _remember(session_id: str, res: dict) -> None:
    res = res or {}
    with _lock:
        _last_raised[session_id or ""] = str((res.get("tab") or {}).get("url") or "") if res.get("focused") else ""


# The user says the page/window the agent pointed at isn't there: the agent must look before it answers.
_CANT_SEE = re.compile(
    r"\b(i\s+)?(don'?t|dont|do not|can'?t|cant|cannot|can not|didn'?t|didnt|not)\s+(see|find|seeing|get)\b"
    r"|\bwhere(?:'s| is)\s+the\s+(window|tab|page|captcha|browser)\b|\bnothing\s+(popped|showed|came)\s+up\b", re.I)
_CANT_SEE_WHAT = re.compile(r"\b(window|tab|page|captcha|browser|chrome|popup|pop-up|sign[- ]?in|sign[- ]?up|login"
                            r"|anything|it|button|code)\b", re.I)


def cant_see(message: str) -> bool:
    for s in re.split(r"(?<=[.!?])\s+|\n+", message or ""):
        if _CANT_SEE.search(s) and _CANT_SEE_WHAT.search(s):
            return True
    return False


def _used_browser(session_id: str, history: Any) -> bool:
    with _lock:
        if time.time() - _browser_sessions.get(session_id or "", 0) < SESSION_TTL_S:
            return True
    for m in reversed(list(history or [])[-80:]):
        if not isinstance(m, dict):
            continue
        for tc in m.get("tool_calls") or []:
            tc = tc if isinstance(tc, dict) else {}
            if ((tc.get("function") or {}).get("name") or tc.get("name")) in ("browser_exec", "browser_focus"):
                return True
        if m.get("role") == "tool" and m.get("name") in ("browser_exec", "browser_focus"):
            return True
    return False


def pre_llm_call(session_id: str = "", user_message: Any = None, conversation_history: Any = None, **_):
    """One-turn note, only when the user says they can't see the page in a session that drove the browser."""
    try:
        msg = user_message if isinstance(user_message, str) else json.dumps(user_message or "")
        if _unattended(session_id) or not cant_see(msg) or not _used_browser(session_id, conversation_history):
            return None
        with _lock:
            last = _last_raised.get(session_id or "")
        where = f"the last tab raised was {last}" if last else "no tab matching your last ask was raised"
        return {"context": ("[browser-focus] The user says they can't see the page/window you asked them to use. "
                            "Before replying, check the browser first (list tabs / screenshot with browser_exec) and "
                            f"confirm the page is really open; {where}. If it isn't there, open it, then call "
                            "browser_focus with a match for it, and say it is in front only if that returns the tab.")}
    except Exception as e:
        log.debug("browser-focus pre_llm_call: %s", e)
        return None


SCHEMA = {
    "name": "browser_focus",
    "description": ("Bring the agent-driven Chrome window (the one browser_exec controls) to the front of the user's "
                    "screen and switch it to the tab that needs them. Call it whenever you are about to ask the user "
                    "to do anything in that browser (sign in, 2FA, captcha, click, approve) so they don't have to "
                    "hunt for the window. `match` picks the tab by URL/title substring; a match that hits no tab "
                    "raises nothing and returns focused=false with `open_tabs` (open the page first). Without "
                    "`match`: a sign-in tab, else the most recent tab. Tell the user it is in front only when the "
                    "returned `tab` is the page you are asking them to use."),
    "parameters": {"type": "object", "properties": {
        "match": {"type": "string", "description": "Substring of the tab URL or title to show (optional)."},
        "hide": {"type": "boolean", "description": ("true = the user is finished: send a hidden (virtual-display) "
                                                    "agent Chrome back out of sight. Shown windows also auto-hide "
                                                    "after 20 minutes.")}}},
}


def handle(args: dict, session_id: str = "", **_) -> str:
    try:
        args = args or {}
        if args.get("hide"):
            return json.dumps(hide())
        res = focus(args.get("match") or None)
        _remember(session_id, res)
        return json.dumps(res)
    except Exception as e:
        return json.dumps({"focused": False, "error": f"{type(e).__name__}: {e}"})


def _hint_only_result(tool_name: str = "", args: Any = None, result: Any = None, **_):
    return private_url_hint(tool_name, result)


def register(ctx) -> None:
    if shutil.which("osascript") is None:
        # No screen to raise a window on (Linux/cloud gateway), but its browser tools still hit the private-URL
        # refusal (the hub drives a Mac Chrome): register the hint alone.
        ctx.register_hook("transform_tool_result", _hint_only_result)
        return
    ctx.register_tool(name="browser_focus", toolset=TOOLSET, schema=SCHEMA, handler=handle, emoji="🔝",
                      description="Bring the agent's Chrome window/tab to the front for the user")
    ctx.register_hook("transform_tool_result", transform_tool_result)
    ctx.register_hook("transform_llm_output", transform_llm_output)
    ctx.register_hook("pre_llm_call", pre_llm_call)
