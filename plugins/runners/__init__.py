"""Runners plugin registration seam.

Phase B deliberately exposes no tools: later service adapters register real,
authenticated operations here. Import/register never creates a store or collector,
reads private inventory, or enables mutations. Models may be used offline.
"""


def register(ctx):
    """Extension point for the collector/API/tool phases; currently inert."""
    return None
