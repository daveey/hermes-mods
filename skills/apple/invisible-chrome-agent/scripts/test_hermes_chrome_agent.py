"""Unit tests for the passkey-notification click path of hermes-chrome-agent (stdlib only).

Run: /usr/bin/python3 -m unittest -v test_hermes_chrome_agent   (from this directory)
"""
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import time
import unittest
from unittest import mock

_path = str(pathlib.Path(__file__).with_name("hermes-chrome-agent"))
_loader = importlib.machinery.SourceFileLoader("hermes_chrome_agent", _path)
_spec = importlib.util.spec_from_loader("hermes_chrome_agent", _loader)
hca = importlib.util.module_from_spec(_spec)
_loader.exec_module(hca)

VD = [1728, 1117, 1440, 900]
MAIN = [0, 0, 1728, 1117]


class FakeCDP:
    """Two windows on the virtual display: 1 holds tab A (an agent page), 2 holds tab PK (passkey page)."""

    def __init__(self):
        self.calls = []
        self.closed = False
        home = {"left": VD[0], "top": VD[1] + hca.MENU_BAR, "width": VD[2], "height": VD[3] - hca.MENU_BAR,
                "windowState": "normal"}
        self.bounds = {1: dict(home), 2: dict(home)}
        self.tab_window = {"A": 1, "PK": 2}

    def call(self, method, params=None, session=None, timeout=10):
        params = params or {}
        self.calls.append((method, params))
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": t, "type": "page", "url": "https://%s.example/" % t.lower()}
                                    for t in self.tab_window]}
        if method == "Browser.getWindowForTarget":
            wid = self.tab_window[params["targetId"]]
            return {"windowId": wid, "bounds": dict(self.bounds[wid])}
        if method == "Browser.setWindowBounds":
            self.bounds[params["windowId"]].update(params["bounds"])
            return {}
        return {}

    def on_main(self, wid):
        b = self.bounds[wid]
        return hca.inside(b, MAIN) and not hca.inside(b, VD)


class FakeProc:
    def __init__(self):
        self.stdin = io.StringIO()
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


def supervisor():
    s = hca.Supervisor()
    s.cdp = FakeCDP()
    s.vbounds, s.main = VD, MAIN
    s.chrome = mock.Mock(pid=4242)
    s.sess_target = {"sessPK": "PK"}
    return s


def passkey_request(s, op="get", ok=True):
    extra = ', "ok": %s' % ("true" if ok else "false") if op == "done" else ""
    s.on_event({"method": "Runtime.bindingCalled", "sessionId": "sessPK",
                "params": {"name": hca.BINDING,
                           "payload": '{"op": "%s", "origin": "https://pk.example", "rpId": "pk.example"%s}'
                                      % (op, extra)}})


class ClickThrough(unittest.TestCase):
    def setUp(self):
        self.flag = mock.patch.object(hca, "SHOW_FLAG", "/nonexistent/hermes-show-flag")
        self.flag.start()
        self.activate = mock.patch.object(hca, "activate_pid")
        self.activated = self.activate.start()

    def tearDown(self):
        mock.patch.stopall()

    def _click(self, line):
        s = supervisor()
        proc = FakeProc()
        with mock.patch.object(s, "notify_passkey", return_value=proc) as post:
            passkey_request(s)
        post.assert_called_once()
        self.assertEqual(post.call_args[0][0], "PK")   # notification is tied to the asking tab
        s.notices["PK"] = proc
        s.rehome()                                        # hidden: nothing moves before the click
        self.assertFalse(s.cdp.on_main(2))
        s.on_notifier_line(line + "\n", "PK", proc)
        return s, proc

    def _assert_revealed(self, s, proc):
        c = s.cdp
        self.assertTrue(c.on_main(2), "passkey window not on the main display: %s" % c.bounds[2])
        self.assertFalse(c.on_main(1), "an unrelated window was moved")
        self.assertIn(("Target.activateTarget", {"targetId": "PK"}), c.calls)
        self.assertIn("activate 4242\n", proc.stdin.getvalue())   # notifier hands activation to Chrome
        self.activated.assert_called_with(4242)
        for _ in range(3):                                # supervisor must not pull it back while pending
            s.rehome()
        self.assertTrue(c.on_main(2), "re-home loop hid the passkey window before the request settled")

    def test_notification_body_click_reveals_asking_tab(self):
        s, proc = self._click("action default")
        self._assert_revealed(s, proc)

    def test_show_button_reveals_asking_tab(self):
        s, proc = self._click("action show")
        self._assert_revealed(s, proc)

    def test_reveal_reanchors_tab_modal_dialog(self):
        # The passkey dialog is a child window that stays on the virtual display when only moved; the
        # window must be resized after the move so Chrome re-anchors it on the main display.
        s, proc = self._click("action default")
        sets = [p["bounds"] for m, p in s.cdp.calls if m == "Browser.setWindowBounds" and p["windowId"] == 2]
        moved = next(i for i, b in enumerate(sets) if b.get("left") == s.main_rect()[0])
        self.assertEqual([b.get("width") for b in sets[moved + 1:moved + 3]],
                         [sets[moved]["width"] - 1, sets[moved]["width"]])

    def test_settle_hides_again_after_grace(self):
        s, proc = self._click("action show")
        passkey_request(s, op="done")
        self.assertTrue(proc.terminated, "notification not withdrawn when the request settled")
        s.rehome()
        self.assertTrue(s.cdp.on_main(2), "hidden with no grace")
        s.passkey["until"] = time.time() - 1
        s.rehome()
        self.assertTrue(hca.inside(s.cdp.bounds[2], VD), "not re-homed after the request settled")

    def test_failed_request_keeps_notification_and_window(self):
        # A hidden tab's WebAuthn call fails at once (NotAllowedError, page not focused); the human still
        # needs the notification, and once revealed the window must stay so they can retry in front.
        s = supervisor()
        proc = FakeProc()
        with mock.patch.object(s, "notify_passkey", return_value=proc):
            passkey_request(s)
        s.notices["PK"] = proc
        passkey_request(s, op="done", ok=False)
        self.assertFalse(proc.terminated, "failed request withdrew the notification before the human saw it")
        with mock.patch.object(s, "notify_passkey") as again:
            passkey_request(s)                            # site retries: no duplicate notification
        again.assert_not_called()
        s.on_notifier_line("action default", "PK", proc)
        passkey_request(s, op="done", ok=False)
        for _ in range(3):
            s.rehome()
        self.assertTrue(s.cdp.on_main(2))

    def test_dismiss_does_not_reveal(self):
        s, proc = self._click("action dismiss")
        self.assertFalse(s.cdp.on_main(2))
        self.activated.assert_not_called()

    def test_no_osascript_notification_anywhere(self):
        # An osascript "display notification" belongs to Script Editor: clicking it opens Script Editor (the bug).
        src = pathlib.Path(_path).read_text()
        self.assertNotIn("display notification", src)
        self.assertFalse(hasattr(hca, "notify"))

    def test_notifications_off_reveals_window_then_alert_show_activates(self):
        s = supervisor()
        proc = FakeProc()
        s.notices["PK"] = proc
        s.on_notifier_line("error not authorized: denied\n", "PK", proc, "T", "B")
        c = s.cdp
        self.assertTrue(c.on_main(2), "passkey window not revealed when notifications are off")
        self.assertIn(("Target.activateTarget", {"targetId": "PK"}), c.calls)
        self.activated.assert_not_called()                # Chrome stays behind the floating alert for now
        for _ in range(3):
            s.rehome()
        self.assertTrue(c.on_main(2))
        s.on_notifier_line("action show\n", "PK", proc)     # the alert's Show button
        self.assertIn("activate 4242\n", proc.stdin.getvalue())
        self.activated.assert_called_with(4242)

    def test_missing_notifier_reveals_and_activates(self):
        s = supervisor()
        with mock.patch.object(hca, "NOTIFIER_BIN", "/nonexistent/notifier"):
            self.assertIsNone(s.notify_passkey("PK", "T", "B"))
        self.assertTrue(s.cdp.on_main(2))
        self.activated.assert_called_with(4242)

    def test_status_names_settings_link_when_not_allowed(self):
        for st, ok in (("authorized style=banner", True), ("denied style=none", False),
                       ("notDetermined style=none", False), ("not built", False)):
            self.assertEqual(hca.notifier_ok(st), ok, st)
        self.assertEqual(hca.NOTIFIER_SETTINGS_URL, "x-apple.systempreferences:com.apple.Notifications-Settings."
                                                    "extension?id=com.hermes.chrome-agent.notify")

class Evict(unittest.TestCase):
    """Supervisor scheduling of the hermes-window-evict helper (helper mocked)."""

    def setUp(self):
        self.sup = hca.Supervisor()
        self.sup.vbounds = VD
        self.sup.chrome = mock.Mock(pid=111)
        self.sup.vd = mock.Mock(pid=222)
        self.logs = []
        patches = [mock.patch.object(hca, "log", self.logs.append),
                   mock.patch.object(hca.os.path, "exists", lambda p: True if p == hca.EVICT else os.path.lexists(p))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_helper(self, out, now):
        with mock.patch.object(hca.subprocess, "run", return_value=mock.Mock(stdout=out)) as r:
            self.sup.evict(now)
        return r

    def test_runs_every_two_seconds_with_bounds_and_skip_pids(self):
        r = self.run_helper("", 100.0)
        self.assertEqual(r.call_args[0][0], [hca.EVICT, "1728", "1117", "1440", "900",
                                             "--skip-pid", "111", "--skip-pid", "222"])
        self.assertEqual(self.run_helper("", 101.0).call_count, 0)   # too soon
        self.assertEqual(self.run_helper("", 102.1).call_count, 1)

    def test_moves_are_logged(self):
        self.run_helper("evict com.apple.TextEdit Untitled from 1800,-800 to 200,80\n", 10.0)
        self.assertEqual(self.logs, ["evict com.apple.TextEdit Untitled from 1800,-800 to 200,80"])

    def test_untrusted_logged_once_per_run(self):
        self.run_helper("untrusted\n", 10.0)
        self.run_helper("untrusted\n", 20.0)
        self.run_helper("untrusted\n", 30.0)
        self.assertEqual(len([l for l in self.logs if l.startswith("evict: untrusted")]), 1)

    def test_no_run_without_virtual_display_or_helper(self):
        self.sup.vbounds = None
        self.assertEqual(self.run_helper("", 10.0).call_count, 0)
        self.sup.vbounds = VD
        with mock.patch.object(hca.os.path, "exists", return_value=False):
            with mock.patch.object(hca.subprocess, "run") as r:
                self.sup.evict(50.0)
        self.assertEqual(r.call_count, 0)

    def test_helper_timeout_does_not_raise(self):
        with mock.patch.object(hca.subprocess, "run", side_effect=hca.subprocess.TimeoutExpired("x", 5)):
            self.sup.evict(10.0)
        self.assertTrue(self.logs[0].startswith("evict: helper failed"))



class FocusGuardTest(unittest.TestCase):
    """Unrequested focus grabs by the agent Chrome are handed back; asked-for ones are left alone."""
    CHROME, HERMES = 500, 42
    IDLE = 30.0   # seconds since the last click / modifier press

    def setUp(self):
        p = mock.patch.object(hca, "pid_alive", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.g = hca.FocusGuard()
        self.g.step(self.HERMES, self.CHROME, 100.0, self.IDLE, float("inf"))   # user is in Hermes

    def test_unrequested_grab_returns_focus_to_previous_app(self):
        self.assertEqual(self.g.step(self.CHROME, self.CHROME, 101.0, self.IDLE, float("inf")), self.HERMES)

    def test_hand_back_is_not_resent_while_landing(self):
        self.g.step(self.CHROME, self.CHROME, 101.0, self.IDLE, float("inf"))
        self.assertIsNone(self.g.step(self.CHROME, self.CHROME, 101.25, self.IDLE, float("inf")))
        self.assertEqual(self.g.step(self.CHROME, self.CHROME, 102.5, self.IDLE, float("inf")), self.HERMES)

    def test_recent_show_allows_focus_and_grant_persists(self):
        self.assertIsNone(self.g.step(self.CHROME, self.CHROME, 101.0, self.IDLE, 2.0))   # browser_focus ran show
        # still frontmost 5 min later, show long ago: the user is working there, leave it
        self.assertIsNone(self.g.step(self.CHROME, self.CHROME, 400.0, self.IDLE, 300.0))

    def test_user_click_allows_focus(self):
        self.assertIsNone(self.g.step(self.CHROME, self.CHROME, 101.0, 0.2, float("inf")))

    def test_passkey_reveal_allows_focus(self):
        self.g.allow(now=100.5)
        self.assertIsNone(self.g.step(self.CHROME, self.CHROME, 101.0, self.IDLE, float("inf")))

    def test_grant_ends_when_chrome_loses_focus(self):
        self.g.step(self.CHROME, self.CHROME, 101.0, 0.2, float("inf"))
        self.g.step(self.HERMES, self.CHROME, 102.0, self.IDLE, float("inf"))
        self.assertEqual(self.g.step(self.CHROME, self.CHROME, 200.0, self.IDLE, float("inf")), self.HERMES)

    def test_no_previous_app_means_no_action(self):
        g = hca.FocusGuard()
        self.assertIsNone(g.step(self.CHROME, self.CHROME, 1.0, self.IDLE, float("inf")))

    def test_dead_previous_app_is_not_retried(self):
        with mock.patch.object(hca, "pid_alive", return_value=False):
            self.assertIsNone(self.g.step(self.CHROME, self.CHROME, 101.0, self.IDLE, float("inf")))

    def test_gives_up_after_max_tries(self):
        with mock.patch.object(hca, "pid_alive", return_value=True):
            got = [self.g.step(self.CHROME, self.CHROME, 101.0 + 1.5 * i, self.IDLE, float("inf"))
                   for i in range(hca.FOCUS_MAX_TRIES + 2)]
        self.assertEqual(got, [self.HERMES] * hca.FOCUS_MAX_TRIES + [None, None])

    def test_reveal_grants_focus(self):
        sup = hca.Supervisor()
        sup.reveal(None, activate=False)
        self.assertGreater(sup.focus.allow_until, time.time() + hca.FOCUS_ALLOW_S - 2)

    def test_show_age_follows_show_flag_mtime(self):
        import tempfile
        d = tempfile.mkdtemp()
        with mock.patch.object(hca, "SHOW_FLAG", os.path.join(d, "show")):
            self.assertEqual(hca.show_age(), float("inf"))
            open(hca.SHOW_FLAG, "w").close()
            self.assertLess(hca.show_age(), 5)


if __name__ == "__main__":
    unittest.main()
