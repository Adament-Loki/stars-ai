"""Compatibility facade over the unified v8.8 StarsAPI hull model."""
from __future__ import annotations
from .starsapi_items import ACTUAL_SLOT_COUNTS as STANDARD_SLOT_COUNTS, stock_hulls

def load_standard_hull_rows():
    return tuple((h.hull_id,h.name,h.is_starbase,tuple((s.capacity,s.allowed_categories) for s in h.slots)) for h in stock_hulls().values())

STANDARD_HULL_SLOTS=load_standard_hull_rows()
