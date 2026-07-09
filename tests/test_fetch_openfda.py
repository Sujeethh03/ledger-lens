"""openFDA fetcher behavior, mocked — no real network calls.

The two behaviors that differ from the EDGAR client get explicit coverage:
404-with-NOT_FOUND means "no such drug" (empty list, zero retries), and
records dedupe to one per set_id, newest effective_time first.
"""

import httpx
import pytest
from tenacity import stop_after_attempt, wait_none

import ingestion.fetch_openfda as fetch_openfda_module
from ingestion.fetch_openfda import OpenFDAFetchFailed, _get, fetch_drug_labels


@pytest.fixture(autouse=True)
def _no_rate_limit_wait(monkeypatch):
    # The production limiter spaces requests 2s apart; pointless against MockTransport.
    monkeypatch.setattr(fetch_openfda_module._rate_limiter, "_interval", 0.0)


def _label(set_id: str, effective_time: str, generic: str = "WARFARIN SODIUM") -> dict:
    return {
        "set_id": set_id,
        "version": "20",
        "effective_time": effective_time,
        "openfda": {
            "brand_name": ["Brandex"],
            "generic_name": [generic],
            "manufacturer_name": ["Acme Pharma"],
            "product_type": ["HUMAN PRESCRIPTION DRUG"],
        },
        "indications_and_usage": ["..."],
    }


def _client_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_get_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"results": []})

    async with _client_with(handler) as client:
        fast_get = _get.retry_with(wait=wait_none(), stop=stop_after_attempt(5))
        response = await fast_get(client, {"search": "x"})

    assert response.status_code == 200
    assert calls["n"] == 3


async def test_unknown_drug_returns_empty_list_without_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "No matches found!"}})

    async with _client_with(handler) as client:
        records = await fetch_drug_labels("notarealdrugxyz", client=client)

    assert records == []
    assert calls["n"] == 1  # "no such drug" is an answer, not an outage


async def test_dedupes_by_set_id_and_respects_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    _label("set-a", "20250617"),
                    _label("set-a", "20240101"),  # older revision of the same lineage — dropped
                    _label("set-b", "20230505"),
                    _label("set-c", "20220303"),
                ]
            },
        )

    async with _client_with(handler) as client:
        records = await fetch_drug_labels("warfarin", limit=2, client=client)

    assert [r.set_id for r in records] == ["set-a", "set-b"]
    assert records[0].effective_time == "20250617"
    assert records[0].year == 2025
    assert records[0].generic_name == "WARFARIN SODIUM"


async def test_persistent_5xx_raises_fetch_failed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    # Swap in an instant retry policy so the test doesn't sleep through backoff.
    import ingestion.fetch_openfda as mod

    monkeypatch.setattr(mod, "_get", mod._get.retry_with(wait=wait_none(), stop=stop_after_attempt(2)))

    async with _client_with(handler) as client:
        with pytest.raises(OpenFDAFetchFailed):
            await fetch_drug_labels("warfarin", client=client)


async def test_api_key_is_sent_when_configured(monkeypatch):
    monkeypatch.setenv("OPENFDA_API_KEY", "test-key-123")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": [_label("set-a", "20250617")]})

    async with _client_with(handler) as client:
        await fetch_drug_labels("warfarin", client=client)

    assert seen["params"]["api_key"] == "test-key-123"
