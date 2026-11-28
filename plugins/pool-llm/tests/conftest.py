"""Never read credentials from the real home in plugin tests."""
import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes'))
    directory = tmp_path / 'hermes' / 'pool-llm'
    directory.mkdir(parents=True, mode=0o700)
    path = directory / 'serve.token'
    path.write_text('http-fixture-token')
    path.chmod(0o600)
