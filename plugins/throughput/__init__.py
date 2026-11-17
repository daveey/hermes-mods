"""Throughput: per-minute tokens, sessions and cards by gateway for the Desktop throughput chip + panel.

The sampler (sampler.py) runs in one long-lived server process per Hermes root (gateway, Desktop serve or
dashboard; a file lock picks one). The REST API is dashboard/plugin_api.py. No agent tools or hooks.
"""
import logging

log = logging.getLogger("hermes.plugins.throughput")


def _register_throttle(ctx):
    """Every Hermes process on this root holds its model calls while the fleet's throttle says so (throttle.py)."""
    register_middleware = getattr(ctx, "register_middleware", None)
    if not callable(register_middleware):
        return  # an older core without middleware: the throttle is display-only there
    from . import sampler, throttle
    root = sampler.hermes_root()
    gate = throttle.Gate(root, sampler.series_path(root), hub_db=throttle.hub_db_path())
    register_middleware("llm_execution", gate.middleware)
    register_hook = getattr(ctx, "register_hook", None)
    # Not in a long-lived server process: core fires this hook synchronously for async callers too, so a hold
    # there would freeze the server's event loop, and with it the panel route that lowers the level.
    if callable(register_hook) and not sampler.eligible_process():
        try:
            register_hook("pre_auxiliary_call", gate.aux_hold)
        except Exception:  # a core without that hook: auxiliary calls are not held there
            log.info("throughput: this core has no pre_auxiliary_call hook; auxiliary calls are not throttled")


def register(ctx):
    from . import sampler
    try:
        _register_throttle(ctx)
    except Exception:  # never break plugin loading
        log.exception("throughput: throttle failed to register")
    if sampler.eligible_process():
        try:
            sampler.start()
        except Exception:  # never break plugin loading
            log.exception("throughput: sampler failed to start")
        on_unload = getattr(ctx, "on_unload", None)
        if callable(on_unload):
            try:
                on_unload(sampler.stop)
            except Exception:
                pass
    return None
