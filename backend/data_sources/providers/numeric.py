from __future__ import annotations

import math


def is_finite_public_number(value: object) -> bool:
    """Accept exact integers and finite floats, never bool or float-coerced integers."""
    return type(value) is int or (type(value) is float and math.isfinite(value))
