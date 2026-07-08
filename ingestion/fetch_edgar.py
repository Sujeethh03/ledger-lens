"""M1: fetch filings from SEC EDGAR with retry/backoff and a rate limiter.

SEC EDGAR requires a descriptive User-Agent (name + contact email) on every
request or it returns 403 regardless of rate — see SEC_EDGAR_USER_AGENT in
.env.example. Fair-access guidance caps clients at ~10 req/s; we throttle to
8 req/s to stay comfortably under that, matching the dossier's stated ceiling.

Retry policy: exponential backoff + jitter on 429/5xx, up to 5 attempts. On
exhaustion we raise EDGARFetchFailed — the caller (ingestion pipeline) is
responsible for marking the Filing row ingestion_status='failed' rather than
silently dropping it. A full dead-letter *queue* is a Celery/Redis concept
that lands with the async pipeline (later milestone); for now "failed" status
+ a structured log line is the M1-scoped version of that same idea.
"""

import asyncio
import os
import time
from dataclasses import dataclass

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

log = structlog.get_logger(__name__)

EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{document}"

RATE_LIMIT_PER_SEC = 8.0
RELEVANT_FORM_TYPES = {"10-K", "10-Q", "8-K"}


class EDGARFetchFailed(Exception):
    """Raised after retries are exhausted. Caller marks the row failed."""


class EDGARConfigError(Exception):
    """Raised on 403 — almost always a missing/malformed User-Agent, not a rate issue."""


@dataclass(frozen=True)
class FilingMeta:
    cik: str
    company_name: str
    form_type: str
    accession_number: str
    filing_date: str
    report_date: str
    primary_document: str


class _RateLimiter:
    """Serializes requests to at most `rate_per_sec`, globally, across callers."""

    def __init__(self, rate_per_sec: float):
        self._interval = 1.0 / rate_per_sec
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last_request_at + self._interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()


_rate_limiter = _RateLimiter(RATE_LIMIT_PER_SEC)


def _user_agent() -> str:
    ua = os.environ.get("SEC_EDGAR_USER_AGENT")
    if not ua:
        raise EDGARConfigError(
            "SEC_EDGAR_USER_AGENT is not set — EDGAR returns 403 without a "
            "descriptive User-Agent (name + contact email). See .env.example."
        )
    return ua


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


@retry(
    retry=retry_if_exception_type(httpx.HTTPStatusError) | retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    await _rate_limiter.acquire()
    response = await client.get(url, headers={"User-Agent": _user_agent()})
    if response.status_code == 403:
        raise EDGARConfigError(f"403 from {url} — check SEC_EDGAR_USER_AGENT formatting")
    if response.status_code == 429 or response.status_code >= 500:
        log.warning("edgar_retryable_error", url=url, status=response.status_code)
        response.raise_for_status()
    response.raise_for_status()
    return response


async def get_company_filings(cik: str, client: httpx.AsyncClient | None = None) -> list[FilingMeta]:
    """Fetch a company's recent filings metadata, filtered to the form types we ingest."""
    padded_cik = cik.zfill(10)
    url = EDGAR_SUBMISSIONS_URL.format(cik=padded_cik)

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        try:
            response = await _get(client, url)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            log.error("edgar_fetch_failed", cik=cik, url=url, error=str(exc))
            raise EDGARFetchFailed(f"failed to fetch filing list for CIK {cik}") from exc

        payload = response.json()
        company_name = payload.get("name", "")
        recent = payload.get("filings", {}).get("recent", {})

        forms = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_documents = recent.get("primaryDocument", [])

        filings = []
        for i, form_type in enumerate(forms):
            if form_type not in RELEVANT_FORM_TYPES:
                continue
            filings.append(
                FilingMeta(
                    cik=padded_cik,
                    company_name=company_name,
                    form_type=form_type,
                    accession_number=accession_numbers[i],
                    filing_date=filing_dates[i],
                    report_date=report_dates[i] if i < len(report_dates) else "",
                    primary_document=primary_documents[i],
                )
            )
        log.info("edgar_filings_fetched", cik=cik, company_name=company_name, count=len(filings))
        return filings
    finally:
        if owns_client:
            await client.aclose()


def filing_source_url(meta: FilingMeta) -> str:
    accession_nodash = meta.accession_number.replace("-", "")
    return EDGAR_ARCHIVES_URL.format(
        cik_int=int(meta.cik), accession_nodash=accession_nodash, document=meta.primary_document
    )


async def fetch_filing_document(meta: FilingMeta, client: httpx.AsyncClient | None = None) -> str:
    """Fetch the raw primary document (HTML/text) for one filing."""
    url = filing_source_url(meta)

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        try:
            response = await _get(client, url)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            log.error("edgar_document_fetch_failed", accession=meta.accession_number, url=url, error=str(exc))
            raise EDGARFetchFailed(f"failed to fetch document for {meta.accession_number}") from exc
        return response.text
    finally:
        if owns_client:
            await client.aclose()
