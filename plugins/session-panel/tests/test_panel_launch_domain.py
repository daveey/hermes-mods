"""The production lifecycle must use the already-supported stable launch domain."""
from pathlib import Path

from test_panel_provider_manifest import load_api


def test_lifecycle_default_admission_matches_supported_launch_domain_and_source(tmp_path, monkeypatch):
    import hermes_constants
    from hermes_cli import serve_producer_admission as admission
    from hermes_cli import serve_producer_lifecycle as lifecycle
    api = load_api()
    monkeypatch.setattr(api, '_serve_home', lambda: tmp_path)
    monkeypatch.setattr(hermes_constants, 'get_hermes_home', lambda: tmp_path)
    monkeypatch.setattr(hermes_constants, 'get_default_hermes_root', lambda home=None: tmp_path)
    monkeypatch.setattr(lifecycle.PanelLifecycle, 'start_watching', lambda self: None)
    enrolled = []
    real_enroll = admission.enroll_launch
    def enroll(runtime, role, *, installation, timeout):
        enrolled.append((Path(runtime), Path(installation)))
        return real_enroll(runtime, role, installation=installation, timeout=timeout)
    monkeypatch.setattr(admission, 'enroll_launch', enroll)
    admission.supported_launch('serve')
    called = []
    sentinel = object()
    def admit(runtime, installation, *, root):
        called.append((Path(runtime), Path(installation), Path(root)))
        return sentinel
    monkeypatch.setattr(lifecycle, 'default_admit', admit)
    seam = api.install_panel_lifecycle({'HERMES_SERVE_PANEL_EXTERNAL': '1'})
    assert seam._admit() is sentinel
    assert called == [(enrolled[0][0], enrolled[0][1], tmp_path)]
