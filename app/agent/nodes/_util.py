"""节点公共工具。"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps


def timed(name: str) -> Callable:
    """统一记录节点耗时，落进 state.timings，用于定位链路瓶颈。"""

    def deco(fn):
        @wraps(fn)
        def wrapper(state, *a, **kw):
            t0 = time.perf_counter()
            out = fn(state, *a, **kw) or {}
            out.setdefault("timings", {})[name] = (time.perf_counter() - t0) * 1000
            return out

        return wrapper

    return deco
