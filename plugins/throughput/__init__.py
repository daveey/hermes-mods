"""Throughput: per-minute tokens, sessions and cards by gateway for the Desktop throughput chip + panel.

The sampler (sampler.py) runs in one long-lived server process per Hermes root (gateway, Desktop serve or
dashboard; a file lock picks one). The REST API is dashboard/plugin_api.py. No agent tools or hooks.
"""
import logging

log = logging.getLogger("hermes.plugins.throughput")


def _register_throttle(ctx):
    """Every Hermes process on this root holds its model calls while the throttle says so (throttle.py)."""
    register_middleware = getattr(ctx, "register_middleware", None)
    if not callable(register_middleware):
        return  # an older core without middleware: the throttle is display-only there
    from . import sampler, throttle
    root = sampler.hermes_root()
    register_middleware("llm_execution", throttle.Gate(root, sampler.series_path(root)).middleware)


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
