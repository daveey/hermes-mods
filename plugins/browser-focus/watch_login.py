"""Detached watcher: once the sign-in tab the user was shown leaves its login pages, send the agent Chrome
back off-screen (`hermes-chrome-agent hide`). Stdlib only; started by browser-focus `focus()`.

usage: watch_login.py <port> <target_id> <chrome-agent cli> [max_seconds]
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def is_login(url, pattern):
    return bool(pattern.search(url or ""))


def done(tabs, target_id, pattern):
    """True once the watched tab is gone or no longer on a sign-in page."""
    tab = next((t for t in tabs if t.get("id") == target_id), None)
    return tab is None or not is_login(tab.get("url", ""), pattern)


def main(argv):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bf_watch", os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py"))
    bf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bf)
    port, target, cli = int(argv[1]), argv[2], argv[3]
    deadline = time.time() + (float(argv[4]) if len(argv) > 4 else 20 * 60)
    flag = os.path.join(bf._hermes_home(), "cache", "chrome-agent", "show")
    while time.time() < deadline:
        time.sleep(2)
        if not os.path.exists(flag):
            return 0   # already hidden (by the user, the agent, or the timeout)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=3) as r:
                tabs = json.load(r)
        except Exception:
            continue
        if done(tabs, target, bf._LOGIN_URL):
            time.sleep(3)   # let the post-login redirect settle so the user sees it worked
            subprocess.run(["/usr/bin/python3", cli, "hide"], capture_output=True, timeout=20)
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
