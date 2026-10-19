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

if __name__ == "__main__":
    unittest.main()
