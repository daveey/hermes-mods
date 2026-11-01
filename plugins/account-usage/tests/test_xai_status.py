"""xai_status: /v1/me -> status snapshot, fetcher install (no network: fake client)."""
import importlib.util
import sys
import unittest
from pathlib import Path

from core_path import use_core
use_core()
PLUGIN = Path(__file__).parents[1]


def load():
    spec = importlib.util.spec_from_file_location("account_usage_xai_status_test", PLUGIN / "xai_status.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeResp:
    def __init__(self, body): self.body = body
    def raise_for_status(self): pass
    def json(self): return self.body


class FakeClient:
    def __init__(self, body): self.body, self.calls = body, []
    def get(self, url, headers): self.calls.append((url, headers)); return FakeResp(self.body)


class XaiStatus(unittest.TestCase):
    def test_blocked_team_is_100_percent(self):
        mod = load()
        client = FakeClient({"user_id": "u1", "team_blocked": True})
        snap = mod.fetch_xai_status(api_key="tok", client=client)
        self.assertEqual(client.calls[0][0], "https://api.x.ai/v1/me")
        self.assertEqual(client.calls[0][1]["Authorization"], "Bearer tok")
        self.assertEqual(snap.provider, "xai-oauth")
        self.assertEqual(snap.windows[0].used_percent, 100.0)
        self.assertEqual(snap.identity, "u1")

    def test_open_team_is_0_percent(self):
        snap = load().fetch_xai_status(api_key="tok", client=FakeClient({"user_id": "u2", "team_blocked": False}))
        self.assertEqual(snap.windows[0].used_percent, 0.0)
        self.assertEqual(snap.windows[0].label, "API access")

    def test_install_registers_and_has_account_usage(self):
        from agent import account_usage
        from agent.account_usage_cache import has_account_usage
        saved = account_usage._USAGE_FETCHERS.pop("xai-oauth", None)
        try:
            mod = load()
            self.assertTrue(mod.install())
            self.assertIs(account_usage._USAGE_FETCHERS["xai-oauth"], mod.fetch_xai_status)
            self.assertTrue(has_account_usage("xai-oauth"))
            self.assertTrue(mod.install())  # idempotent
        finally:
            account_usage._USAGE_FETCHERS.pop("xai-oauth", None)
            if saved is not None:
                account_usage._USAGE_FETCHERS["xai-oauth"] = saved

    def test_install_never_replaces_core_fetcher(self):
        from agent import account_usage
        saved = account_usage._USAGE_FETCHERS.get("xai-oauth")
        def core_fetcher(b, k): return None
        account_usage._USAGE_FETCHERS["xai-oauth"] = core_fetcher
        try:
            self.assertFalse(load().install())
            self.assertIs(account_usage._USAGE_FETCHERS["xai-oauth"], core_fetcher)
        finally:
            account_usage._USAGE_FETCHERS.pop("xai-oauth", None)
            if saved is not None:
                account_usage._USAGE_FETCHERS["xai-oauth"] = saved


if __name__ == "__main__":
    unittest.main()
