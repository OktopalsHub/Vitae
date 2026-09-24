from __future__ import annotations

from collections import Counter
from threading import Lock

_lock = Lock()
_counts: Counter[str] = Counter()
_latencies: list[float] = []

def record(kind: str, status: str, duration_ms: float) -> None:
    with _lock:
        _counts[f"{kind}:{status}"] += 1
        _latencies.append(duration_ms)
        if len(_latencies) > 5000:
            del _latencies[:-5000]

def snapshot() -> dict:
    with _lock:
        values = sorted(_latencies)
        p95 = values[int(len(values) * 0.95) - 1] if values else 0
        return {"counts": dict(_counts), "latency_ms": {"count": len(values), "p95": round(p95, 2)}}
