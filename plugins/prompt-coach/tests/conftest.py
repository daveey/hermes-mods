import pytest


@pytest.fixture(autouse=True)
def _no_system_one_network(monkeypatch, tmp_path):
    """Never call the real System One API from tests (the client reads the key from ~/.hermes/.env too) and never
    write the live system-one log: mode off unless a test opts in, log to tmp."""
    monkeypatch.setenv("SYSTEM_ONE_MODE", "off")
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    monkeypatch.setenv("SYSTEM_ONE_LOG", str(tmp_path / "system-one-log.jsonl"))
