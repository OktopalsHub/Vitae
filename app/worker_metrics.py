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
        return {
            "counts": dict(_counts),
            "latency_ms": {"count": len(values), "p95": round(p95, 2)},
        }


def prometheus_snapshot() -> str:
    """Return a small dependency-free Prometheus text exposition."""
    snap = snapshot()
    lines = [
        "# HELP vitae_worker_jobs_total Worker jobs completed or failed in this process.",
        "# TYPE vitae_worker_jobs_total counter",
    ]
    for key, value in sorted(snap["counts"].items()):
        if ":" not in key:
            continue
        kind, status = key.rsplit(":", 1)
        lines.append(
            f'vitae_worker_jobs_total{{kind="{_label(kind)}",status="{_label(status)}"}} {value}'
        )

    lines.extend(
        [
            "# HELP vitae_worker_job_latency_p95_ms Approximate p95 worker job latency.",
            "# TYPE vitae_worker_job_latency_p95_ms gauge",
            f'vitae_worker_job_latency_p95_ms {snap["latency_ms"]["p95"]}',
        ]
    )
    return "\n".join(lines) + "\n"


def _label(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
