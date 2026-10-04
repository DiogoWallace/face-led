import os
import time
from contextlib import contextmanager

import psutil

_PROC = psutil.Process(os.getpid())


@contextmanager
def measured(out: dict, prefix: str = ""):
    """Tempo de parede (ms), CPU do processo (% de 1 núcleo) e RSS (MB) ao final."""
    cpu0 = _PROC.cpu_times()
    t0 = time.perf_counter()
    yield
    wall = time.perf_counter() - t0
    cpu1 = _PROC.cpu_times()
    cpu = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    out[f"{prefix}ms"] = round(wall * 1000, 2)
    out[f"{prefix}cpu_pct"] = round(100 * cpu / wall, 1) if wall > 0 else None
    out[f"{prefix}rss_mb"] = round(_PROC.memory_info().rss / 2**20, 1)


def rss_mb() -> float:
    return round(_PROC.memory_info().rss / 2**20, 1)
