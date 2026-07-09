"""FastAPI application — the GroundedAI API surface.

Endpoints:
    GET  /healthz                       liveness (process is up, nothing else)
    GET  /readyz                        readiness — real DB + Redis checks
    GET  /metrics                       Prometheus scrape
    POST /api/v1/ingest/sec/{cik}       enqueue async SEC ingestion -> 202 + task id
    POST /api/v1/ingest/drug/{name}     enqueue async openFDA label ingestion -> 202 + task id
    GET  /api/v1/tasks/{task_id}        Celery task status/result
    GET  /api/v1/documents              documents + ingestion_status (drift/OCR visible here)
    POST /api/v1/query                  full agent pipeline -> cited answer
"""

import os
import time

import structlog
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from db.models import Document  # noqa: E402
from db.session import get_session  # noqa: E402

structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])
log = structlog.get_logger(__name__)

app = FastAPI(title="GroundedAI", version="0.2.0")

REQUEST_COUNT = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "Request latency", ["method", "path"])


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    path = request.scope.get("route").path if request.scope.get("route") else request.url.path
    REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(elapsed)
    log.info("request", method=request.method, path=path, status=response.status_code, duration_ms=round(elapsed * 1000, 1))
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    problems: dict[str, str] = {}
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        problems["postgres"] = str(exc)
    try:
        import redis

        redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0")).ping()
    except Exception as exc:
        problems["redis"] = str(exc)

    if problems:
        raise HTTPException(status_code=503, detail=problems)
    return {"status": "ready", "postgres": "ok", "redis": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class IngestResponse(BaseModel):
    task_id: str
    status: str = "queued"


@app.post("/api/v1/ingest/sec/{cik}", status_code=202, response_model=IngestResponse)
def trigger_sec_ingest(cik: str, limit: int = 5):
    # Read-only deployments (no worker running) disable ingestion honestly
    # instead of enqueueing jobs nothing will ever pick up.
    if os.environ.get("INGEST_ENABLED", "1") != "1":
        raise HTTPException(status_code=503, detail="Ingestion is disabled in this deployment (read-only demo)")
    if not cik.isdigit():
        raise HTTPException(status_code=422, detail="CIK must be numeric")
    from ingestion.tasks import ingest_company_task

    result = ingest_company_task.delay(cik, limit=limit)
    log.info("ingest_enqueued", cik=cik, task_id=result.id)
    return IngestResponse(task_id=result.id)


@app.post("/api/v1/ingest/drug/{drug_name}", status_code=202, response_model=IngestResponse)
def trigger_drug_ingest(drug_name: str, limit: int = 3):
    if os.environ.get("INGEST_ENABLED", "1") != "1":
        raise HTTPException(status_code=503, detail="Ingestion is disabled in this deployment (read-only demo)")
    name = drug_name.strip()
    if not name or len(name) > 100 or not all(c.isalnum() or c in " -" for c in name):
        raise HTTPException(status_code=422, detail="Drug name must be alphanumeric (spaces/hyphens allowed)")
    from ingestion.tasks import ingest_drug_task

    result = ingest_drug_task.delay(name, limit=limit)
    log.info("drug_ingest_enqueued", drug=name, task_id=result.id)
    return IngestResponse(task_id=result.id)


@app.get("/api/v1/tasks/{task_id}")
def task_status(task_id: str):
    from ingestion.celery_app import app as celery_app

    async_result = celery_app.AsyncResult(task_id)
    payload = {"task_id": task_id, "state": async_result.state}
    if async_result.successful():
        payload["result"] = async_result.result
    elif async_result.failed():
        payload["error"] = str(async_result.result)
    return payload


@app.get("/api/v1/documents")
def list_documents(source_type: str | None = None):
    with get_session() as session:
        query = select(Document).order_by(Document.published_at.desc().nulls_last())
        if source_type:
            query = query.where(Document.source_type == source_type)
        documents = session.scalars(query).all()
        return [
            {
                "id": str(d.id),
                "source_type": d.source_type,
                "entity": d.entity_name,
                "entity_id": d.entity_id,
                "doc_type": d.doc_type,
                "year": d.year,
                "source_key": d.source_key,
                "ingestion_status": d.ingestion_status,
                "ocr_confidence": float(d.ocr_confidence) if d.ocr_confidence is not None else None,
            }
            for d in documents
        ]


class QueryRequest(BaseModel):
    question: str = Field(min_length=5, max_length=1000)


class QueryResponse(BaseModel):
    answer: str
    refused: bool
    sub_queries: list[str]
    sources: list[dict]


@app.post("/api/v1/query", response_model=QueryResponse)
def query(body: QueryRequest):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured on the server")
    from agents.graph import ask
    from agents.llm import OpenAIChat
    from retrieval.embeddings import OpenAIEmbedder

    result = ask(body.question, OpenAIChat(), OpenAIEmbedder())
    return QueryResponse(
        answer=result.answer,
        refused=result.refused,
        sub_queries=result.sub_queries,
        sources=[
            {
                "label": c.label,
                "entity": c.hit.entity_name,
                "doc_type": c.hit.doc_type,
                "year": c.hit.year,
                "section": c.hit.section,
                "source_key": c.hit.source_key,
            }
            for c in result.citations
        ],
    )
