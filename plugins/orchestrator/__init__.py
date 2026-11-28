"""Orchestrator is read-only; the core dashboard loader owns its API router."""


def register(ctx):
    """No agent tools, background writers, or global lifecycle changes."""
