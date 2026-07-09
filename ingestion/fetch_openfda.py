"""Fetch FDA drug labels from openFDA — the platform's second document source.

Same skeleton as fetch_edgar (async client, global rate limiter, tenacity
retry with backoff, typed failure the pipeline turns into status='failed'),
different wire details:

- openFDA is a JSON search API, not a document archive: one response carries
  full label content, so there's no separate metadata/document fetch pair.
- Rate limits: 240 req/min with a free API key, 40 req/min without. We
  throttle to 0.5 req/s (30/min) so the keyless default stays comfortably
  under the ceiling; set OPENFDA_API_KEY to raise the ceiling (the limiter
  stays put — ingestion is bursty enough that 30/min has not been the
  bottleneck).
- A search with no matches returns HTTP 404 with a NOT_FOUND body — that is
  "no such drug", not an outage, and must map to an empty list, never a retry
  or a dead-letter.

Identity: a label lineage is identified by set_id and revised over time
(version, effective_time). We ingest the most recent version per set_id and
key documents on set_id — re-ingesting a revised label is an update, not a
duplicate.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

log = structlog.get_logger(__name__)

OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
# DailyMed is the canonical human-readable home of a label set_id.
DAILYMED_URL = "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}"

RATE_LIMIT_PER_SEC = 0.5


class OpenFDAFetchFailed(Exception):
    """Raised after retries are exhausted. Caller marks the row failed."""


@dataclass(frozen=True)
class DrugLabelRecord:
    set_id: str
    version: str
    effective_time: str  # YYYYMMDD as openFDA ships it
    brand_name: str
    generic_name: str
    manufacturer: str
    product_type: str
    raw: dict = field(repr=False)  # full label JSON — the normalizer's input

    @property
    def source_url(self) -> str:
        return DAILYMED_URL.format(set_id=self.set_id)

    @property
    def year(self) -> int | None:
        return int(self.effective_time[:4]) if len(self.effective_time) >= 4 else None


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


def _is_no_results(response: httpx.Response) -> bool:
    """openFDA signals 'query matched nothing' as a 404 with a NOT_FOUND error body."""
    if response.status_code != 404:
        return False
    try:
        return response.json().get("error", {}).get("code") == "NOT_FOUND"
    except Exception:
        return False


@retry(
    retry=retry_if_exception_type(httpx.HTTPStatusError) | retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def _get(client: httpx.AsyncClient, params: dict) -> httpx.Response:
    await _rate_limiter.acquire()
    api_key = os.environ.get("OPENFDA_API_KEY")
    if api_key:
        params = {**params, "api_key": api_key}
    response = await client.get(OPENFDA_LABEL_URL, params=params)
    if _is_no_results(response):
        return response  # legitimate empty result — never retried
    if response.status_code == 429 or response.status_code >= 500:
        log.warning("openfda_retryable_error", status=response.status_code)
    response.raise_for_status()
    return response


def _first(record: dict, *path: str) -> str:
    """openFDA wraps nearly everything in single-element lists; unwrap safely."""
    value: object = record
    for key in path:
        value = value.get(key, {}) if isinstance(value, dict) else {}
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value) if isinstance(value, str) else ""


def _to_record(result: dict) -> DrugLabelRecord:
    return DrugLabelRecord(
        set_id=result.get("set_id", ""),
        version=str(result.get("version", "")),
        effective_time=str(result.get("effective_time", "")),
        brand_name=_first(result, "openfda", "brand_name"),
        generic_name=_first(result, "openfda", "generic_name"),
        manufacturer=_first(result, "openfda", "manufacturer_name"),
        product_type=_first(result, "openfda", "product_type"),
        raw=result,
    )


async def fetch_drug_labels(
    drug_name: str, limit: int = 3, client: httpx.AsyncClient | None = None
) -> list[DrugLabelRecord]:
    """Fetch the most recent labels matching a brand or generic drug name.

    Returns at most `limit` records, newest effective_time first, one per
    set_id. An unknown drug name returns [] (openFDA's 404-NOT_FOUND), which
    the caller reports as "nothing found" rather than a pipeline failure.
    """
    name = drug_name.strip()
    search = f'openfda.generic_name:"{name}" openfda.brand_name:"{name}"'  # openFDA ORs terms
    params = {"search": search, "sort": "effective_time:desc", "limit": max(limit * 3, 10)}

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        try:
            response = await _get(client, params)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            log.error("openfda_fetch_failed", drug=name, error=str(exc))
            raise OpenFDAFetchFailed(f"failed to fetch labels for {name!r}") from exc

        if _is_no_results(response):
            log.info("openfda_no_labels", drug=name)
            return []

        results = response.json().get("results", [])
        records: list[DrugLabelRecord] = []
        seen_set_ids: set[str] = set()
        for result in results:
            record = _to_record(result)
            if not record.set_id or record.set_id in seen_set_ids:
                continue  # results are newest-first, so the first hit per set_id wins
            seen_set_ids.add(record.set_id)
            records.append(record)
            if len(records) >= limit:
                break

        log.info("openfda_labels_fetched", drug=name, count=len(records))
        return records
    finally:
        if owns_client:
            await client.aclose()
