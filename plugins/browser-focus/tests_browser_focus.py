import importlib.util
import pathlib
import unittest
from unittest import mock

_spec = importlib.util.spec_from_file_location("browser_focus", pathlib.Path(__file__).with_name("__init__.py"))
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

PS = """\
  101 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/h/.hermes/chrome-agent --remote-debugging-port=9333 --no-first-run
  102 /Applications/Google Chrome.app/Contents/Frameworks/x --type=renderer --user-data-dir=/h/.hermes/chrome-agent --remote-debugging-port=9333
  103 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/other --remote-debugging-port=9222
  104 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  105 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --headless=new --remote-debugging-port=9336 --user-data-dir=/h/.hermes/cache/scratch/x
"""
TABS = [
    {"id": "a", "type": "page", "title": "Example", "url": "https://example.net/"},
    {"id": "b", "type": "page", "title": "Sign in - Google Accounts", "url": "https://accounts.google.com/v3/signin/identifier?x=1"},
    {"id": "c", "type": "browser_ui", "title": "Omnibox", "url": "chrome://omnibox"},
]


class T(unittest.TestCase):
    def setUp(self):
        bf._browser_sessions.clear()
        bf._last_focus.clear()

    def test_finds_only_main_process_under_home(self):
        with mock.patch("os.path.realpath", side_effect=lambda p: p):
            self.assertEqual(bf.agent_chromes("/h/.hermes", PS), [(101, 9333)])

    def test_pick_tab_prefers_match_then_login(self):
        self.assertEqual(bf.pick_tab(TABS)["id"], "b")
        self.assertEqual(bf.pick_tab(TABS, "example")["id"], "a")
        self.assertIsNone(bf.pick_tab([TABS[2]]))

    def test_tool_result_on_login_page_focuses_and_notes_once(self):
        out = '{"output": "https://accounts.google.com/v3/signin/identifier?q | Sign in"}'
        with mock.patch.object(bf, "focus", return_value={"focused": True, "tab": {"title": "Sign in"}}) as f:
            r = bf.transform_tool_result("browser_exec", {}, out, session_id="s")
            self.assertIn("[browser-focus]", r)
            f.assert_called_once_with("accounts.google.com")
            self.assertIsNone(bf.transform_tool_result("browser_exec", {}, out, session_id="s"))  # throttled
            self.assertIsNone(bf.transform_tool_result("browser_exec", {}, r, session_id="t"))    # idempotent

    def test_tool_result_ignores_normal_pages_and_other_tools(self):
        with mock.patch.object(bf, "focus") as f:
            self.assertIsNone(bf.transform_tool_result("browser_exec", {}, "https://example.net/ ok", session_id="s"))
            self.assertIsNone(bf.transform_tool_result("terminal", {}, "https://x.com/login", session_id="s"))
            f.assert_not_called()

    def test_reply_asking_user_in_chrome_focuses_only_after_browser_use(self):
        text = "Please sign in to Google in the Chrome window, then say done."
        with mock.patch.object(bf, "focus", return_value={"focused": True, "tab": None}) as f:
            self.assertIsNone(bf.transform_llm_output(text, session_id="s"))  # session never used the browser
            bf.transform_tool_result("browser_exec", {}, "ok", session_id="s")
            r = bf.transform_llm_output(text, session_id="s")
            self.assertIn("front", r)
            f.assert_called_once()
            self.assertIsNone(bf.transform_llm_output("The AC is set to 75.", session_id="s"))

    def test_focus_unhides_virtual_display_chrome_before_raising(self):
        calls = []
        with mock.patch.object(bf, "_quiet", return_value=False), \
             mock.patch.object(bf, "agent_chromes", return_value=[(101, 9333)]), \
             mock.patch.object(bf, "_chrome_agent", side_effect=lambda c: calls.append(c) or True), \
             mock.patch.object(bf, "_get", side_effect=lambda u: TABS if u.endswith("/json/list") else "ok"), \
             mock.patch.object(bf, "activate_app", side_effect=lambda pid: calls.append("activate") or True), \
             mock.patch.object(bf, "_watch_login", side_effect=lambda port, tid: calls.append(("watch", port, tid))):
            r = bf.focus("accounts.google.com")
        self.assertEqual(calls, [["show", "--minutes", str(bf.SHOW_MINUTES)], "activate", ("watch", 9333, "b")])
        self.assertEqual(r["tab"]["title"], "Sign in - Google Accounts")
        self.assertTrue(r["shown_on_main_display"])

    def test_login_watcher_done_when_tab_leaves_sign_in(self):
        _s = importlib.util.spec_from_file_location("wl", pathlib.Path(__file__).with_name("watch_login.py"))
        wl = importlib.util.module_from_spec(_s)
        _s.loader.exec_module(wl)
        on = [{"id": "x", "url": "https://accounts.google.com/v3/signin/challenge/pwd?a"}]
        nest_login = [{"id": "x", "url": "https://home.nest.com/login/"}]
        home = [{"id": "x", "url": "https://home.nest.com/home/9a7e"}]
        self.assertFalse(wl.done(on, "x", bf._LOGIN_URL))
        self.assertFalse(wl.done(nest_login, "x", bf._LOGIN_URL))
        self.assertTrue(wl.done(home, "x", bf._LOGIN_URL))
        self.assertTrue(wl.done([], "x", bf._LOGIN_URL))   # tab closed

    def test_asks_user_in_browser(self):
        self.assertTrue(bf.asks_user_in_browser("Click Allow on the tab I opened"))
        self.assertFalse(bf.asks_user_in_browser("I checked the thermostat history."))
        # mentions without an ask (the false positive that stole focus)
        self.assertFalse(bf.asks_user_in_browser(
            "It works in the hidden Chrome, so nothing will pop up unless Nest asks you to sign in again."))
        self.assertFalse(bf.asks_user_in_browser("The Chrome tab stays hidden. I read the schedule.\nYou can sign in later from your phone."))
        self.assertTrue(bf.asks_user_in_browser("The Nest page is open in Chrome. Please sign in, then say done."))


if __name__ == "__main__":
    unittest.main()
