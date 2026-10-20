"""gemini_status: models-list status -> snapshot, fetcher install (no network: fake client)."""
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
PLUGIN = Path(__file__).parents[1]


def load():
    spec = importlib.util.spec_from_file_location("account_usage_gemini_status_test", PLUGIN / "gemini_status.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeResp:
    def __init__(self, code): self.status_code = code


class FakeClient:
    def __init__(self, code): self.code, self.calls = code, []
    def get(self, url, headers): self.calls.append((url, headers)); return FakeResp(self.code)


class GeminiStatus(unittest.TestCase):
    def test_200_is_valid(self):
        snap = load().snapshot_from_response(200)
        self.assertEqual(snap.provider, "gemini")
        self.assertEqual(snap.windows[0].label, "API key")
        self.assertEqual(snap.windows[0].used_percent, 0.0)
        self.assertIn("key valid", snap.windows[0].detail)

    def test_rejected_codes_are_100(self):
        mod = load()
        for code in (400, 401, 403):
            snap = mod.snapshot_from_response(code)
            self.assertEqual(snap.windows[0].used_percent, 100.0)
            self.assertEqual(snap.windows[0].detail, "key rejected")

    def test_inconclusive_codes_give_none(self):
        mod = load()
        for code in (429, 500, 503):
            self.assertIsNone(mod.snapshot_from_response(code))

    def test_fetch_sends_key_in_header_only_and_never_returns_it(self):
        client = FakeClient(200)
        snap = load().fetch_gemini_status(None, "SECRET-KEY", client=client)
        url, headers = client.calls[0]
        self.assertTrue(url.startswith("https://generativelanguage.googleapis.com/v1beta/models"))
        self.assertNotIn("SECRET-KEY", url)
        self.assertEqual(headers["x-goog-api-key"], "SECRET-KEY")
        self.assertNotIn("SECRET-KEY", repr(snap))

    def test_no_key_no_call(self):
        client = FakeClient(200)
        self.assertIsNone(load().fetch_gemini_status(None, "  ", client=client))
        self.assertEqual(client.calls, [])

    def test_install_registers_idempotent_and_has_account_usage(self):
        from agent import account_usage
        from agent.account_usage_cache import has_account_usage
        saved = account_usage._USAGE_FETCHERS.pop("gemini", None)
        try:
            mod = load()
            self.assertTrue(mod.install())
            self.assertIs(account_usage._USAGE_FETCHERS["gemini"], mod.fetch_gemini_status)
            self.assertTrue(has_account_usage("gemini"))
            self.assertTrue(mod.install())
            self.assertTrue(load().install())  # a reloaded module still counts as ours
        finally:
            account_usage._USAGE_FETCHERS.pop("gemini", None)
            if saved is not None:
                account_usage._USAGE_FETCHERS["gemini"] = saved

    def test_install_never_replaces_core_fetcher(self):
        from agent import account_usage
        saved = account_usage._USAGE_FETCHERS.get("gemini")
        def core_fetcher(b, k): return None
        account_usage._USAGE_FETCHERS["gemini"] = core_fetcher
        try:
            self.assertFalse(load().install())
            self.assertIs(account_usage._USAGE_FETCHERS["gemini"], core_fetcher)
        finally:
            account_usage._USAGE_FETCHERS.pop("gemini", None)
            if saved is not None:
                account_usage._USAGE_FETCHERS["gemini"] = saved


if __name__ == "__main__":
    unittest.main()
