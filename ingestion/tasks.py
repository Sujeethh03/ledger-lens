"""Celery tasks wrapping the (already-proven) synchronous pipeline functions.

The tasks stay thin on purpose: all real logic lives in ingestion/pipeline.py
and retrieval/indexer.py where it's unit-testable without a broker. Retries
here cover transient infrastructure failure (DB down, EDGAR outage past the
client's own retry budget); EDGAR-level retry/backoff already happens inside
fetch_edgar and is not duplicated at this layer.
"""

import asyncio

import structlog

from ingestion.celery_app import app
from ingestion.pipeline import ingest_company, ingest_drug

log = structlog.get_logger(__name__)


@app.task(bind=True, max_retries=2, default_retry_delay=60)
def ingest_company_task(self, cik: str, limit: int = 5) -> dict[str, int]:
    try:
        return asyncio.run(ingest_company(cik, limit=limit))
    except Exception as exc:
        log.error("ingest_task_failed", cik=cik, attempt=self.request.retries, error=str(exc))
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=60)
def ingest_drug_task(self, drug_name: str, limit: int = 3) -> dict[str, int]:
    try:
        return asyncio.run(ingest_drug(drug_name, limit=limit))
    except Exception as exc:
        log.error("ingest_drug_task_failed", drug=drug_name, attempt=self.request.retries, error=str(exc))
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=1, default_retry_delay=120)
def index_filings_task(self) -> dict[str, int]:
    # Imported here so the worker only needs an OpenAI key when this task
    # actually runs, not at import time.
    from retrieval.embeddings import OpenAIEmbedder
    from retrieval.indexer import index_pending_documents

    try:
        return index_pending_documents(OpenAIEmbedder())
    except Exception as exc:
        log.error("index_task_failed", attempt=self.request.retries, error=str(exc))
        raise self.retry(exc=exc)
