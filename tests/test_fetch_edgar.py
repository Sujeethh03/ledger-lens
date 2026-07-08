"""Retry/backoff and config-error behavior, mocked — no real network calls.

Uses tenacity's `retry_with(...)` to swap in an instant (wait_none) retry
policy for the two tests that exercise retry *counting*, so the suite stays
fast without weakening what production actually does.
"""

import httpx
import pytest
from tenacity import stop_after_attempt, wait_none

from ingestion.fetch_edgar import EDGARConfigError, _get, _user_agent


@pytest.fixture(autouse=True)
def _set_user_agent(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Test Agent test@example.com")


async def test_get_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fast_get = _get.retry_with(wait=wait_none(), stop=stop_after_attempt(5))
        response = await fast_get(client, "https://data.sec.gov/fake")

    assert response.status_code == 200
    assert calls["n"] == 3


async def test_get_raises_after_exhausting_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fast_get = _get.retry_with(wait=wait_none(), stop=stop_after_attempt(3))
        with pytest.raises(httpx.HTTPStatusError):
            await fast_get(client, "https://data.sec.gov/fake")


async def test_get_raises_config_error_on_403_without_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(EDGARConfigError):
            await _get(client, "https://data.sec.gov/fake")

    assert calls["n"] == 1  # 403 means bad config, not transient — must not retry


def test_user_agent_missing_raises_config_error(monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    with pytest.raises(EDGARConfigError):
        _user_agent()
