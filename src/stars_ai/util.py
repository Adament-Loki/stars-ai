from __future__ import annotations
import math
from .models import Position


def distance(a: Position, b: Position) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)
