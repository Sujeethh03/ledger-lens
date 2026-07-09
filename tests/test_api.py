"""API tests — network-free: Celery enqueue and the agent pipeline are mocked;
/healthz and /metrics are exercised for real. /readyz and /api/v1/documents need
live Postgres/Redis so they're covered by the manual end-to-end run, not here
(CI has neither service yet — see ci.yml note)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_metrics_exposes_prometheus_format():
    client.get("/healthz")  # ensure at least one observed request
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_ingest_rejects_non_numeric_cik():
    response = client.post("/api/v1/ingest/sec/not-a-cik")
    assert response.status_code == 422


def test_ingest_enqueues_task():
    with patch("ingestion.tasks.ingest_company_task.delay") as mock_delay:
        mock_delay.return_value.id = "fake-task-id"
        response = client.post("/api/v1/ingest/sec/320193?limit=3")
    assert response.status_code == 202
    assert response.json() == {"task_id": "fake-task-id", "status": "queued"}
    mock_delay.assert_called_once_with("320193", limit=3)


def test_drug_ingest_rejects_bad_names():
    assert client.post("/api/v1/ingest/drug/DROP TABLE;--").status_code == 422
    assert client.post("/api/v1/ingest/drug/%20%20").status_code == 422


def test_drug_ingest_enqueues_task():
    with patch("ingestion.tasks.ingest_drug_task.delay") as mock_delay:
        mock_delay.return_value.id = "fake-drug-task"
        response = client.post("/api/v1/ingest/drug/warfarin?limit=2")
    assert response.status_code == 202
    assert response.json() == {"task_id": "fake-drug-task", "status": "queued"}
    mock_delay.assert_called_once_with("warfarin", limit=2)


def test_query_returns_503_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post("/api/v1/query", json={"question": "What are Apple's risks?"})
    assert response.status_code == 503


def test_query_validates_question_length():
    response = client.post("/api/v1/query", json={"question": "hi"})
    assert response.status_code == 422
