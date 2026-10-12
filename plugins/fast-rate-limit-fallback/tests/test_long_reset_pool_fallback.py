"""Long-reset 429s must rotate or fall back, never burn the capped retry wait."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.credential_pool import CredentialPool, PooledCredential
from run_agent import AIAgent


class LongResetRateLimit(Exception):
    status_code = 429

    def __init__(self):
        message = "This request would exceed your account’s rate limit. Please try again later."
        super().__init__(message)
        self.body = {"error": {"message": message, "reset_at": time.time() + 36 * 3600}}
        self.response = SimpleNamespace(headers={"retry-after": str(36 * 3600)})


def test_long_reset_falls_back_when_rotation_cannot_recover():
    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI", return_value=MagicMock()),
    ):
        agent = AIAgent(
            api_key="test-primary-key", provider="anthropic", model="claude-opus-4-7",
            base_url="https://api.anthropic.com", quiet_mode=True,
            skip_context_files=True, skip_memory=True,
            fallback_model=[{"provider": "zai", "model": "test-fallback"}],
        )
    agent._disable_streaming = True
    pool = CredentialPool("anthropic", [
        PooledCredential(
            provider="anthropic", id="primary", label="primary", auth_type="api_key",
            priority=0, source="manual", access_token="test-primary-key",
            base_url="https://api.anthropic.com",
        ),
        PooledCredential(
            provider="anthropic", id="other-endpoint", label="other", auth_type="api_key",
            priority=1, source="manual", access_token="test-other-key",
            base_url="https://relay.example/anthropic",
        ),
    ])
    # The available entry serves another endpoint: real rotation benches the
    # primary, but the swap veto means it cannot recover this session.
    agent._credential_pool = pool
    agent._credential_pool_entry_id = "primary"
    agent._api_max_retries = 2
    calls = []

    def api_call(kwargs):
        calls.append((agent.provider, agent.model))
        if agent.provider == "anthropic":
            raise LongResetRateLimit()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Fallback worked", tool_calls=None), finish_reason="stop")],
            model="test-fallback", usage=None,
        )

    fallback_client = SimpleNamespace()
    fallback_client.api_key = "test-fallback-key"
    fallback_client.base_url = "https://api.z.ai/api/paas/v4"
    fallback_client._custom_headers = None
    fallback_client.default_headers = None
    with (
        patch.object(agent, "_interruptible_api_call", side_effect=api_call),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch("agent.process_bootstrap.OpenAI", return_value=MagicMock()),
        patch("agent.turn_api_error.interruptible_backoff_sleep", return_value=None) as backoff_sleep,
        patch("agent.auxiliary_client.resolve_provider_client", return_value=(fallback_client, "test-fallback")),
        patch("hermes_cli.model_normalize.normalize_model_for_provider", side_effect=lambda m, p: m),
        patch("agent.model_metadata.get_model_context_length", return_value=200000),
        patch("hermes_cli.config.load_config", return_value={"fallback": {"min_switch_reset_seconds": 0}}),
    ):
        result = agent.run_conversation("hello")

    assert result["completed"] is True
    assert result["final_response"] == "Fallback worked"
    assert calls == [("anthropic", "claude-opus-4-7"), ("zai", "test-fallback")]
    assert pool.entries()[0].model_cooldowns["claude-opus-4-7"] > time.time()
    assert pool.has_available(model="claude-opus-4-7") is True
    backoff_sleep.assert_not_called()
