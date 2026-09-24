from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.observability import current_request_id
from app.worker_metrics import prometheus_snapshot, record


def test_request_id_context_is_empty_by_default():
    assert current_request_id() == ""


def test_worker_metrics_prometheus_snapshot():
    record("unit-test", "completed", 12.0)
    output = prometheus_snapshot()
    assert 'vitae_worker_jobs_total{kind="unit-test",status="completed"}' in output
    assert "vitae_worker_job_latency_p95_ms" in output


def test_metrics_endpoint_requires_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "test-metrics-token")
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 401
        response = client.get(
            "/metrics",
            headers={"Authorization": "Bearer test-metrics-token"},
        )
        assert response.status_code == 200
        assert "vitae_worker_jobs_total" in response.text
