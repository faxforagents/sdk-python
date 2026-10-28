"""Shared fixtures: a client against a respx-mocked API, and sample payloads shaped like the server's."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
import respx

from faxforagents import AsyncClient, Client

BASE = "https://api.test.invalid"
KEY = "ffa_test_" + "a" * 43


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FAXFORAGENTS_API_KEY", "FFA_API_KEY", "FAXFORAGENTS_API_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client(api: respx.MockRouter) -> Iterator[Client]:
    with Client(KEY, base_url=BASE, backoff_base=0.0, max_retry_after=0.0) as c:
        yield c


@pytest.fixture
async def aclient(api: respx.MockRouter) -> Any:
    async with AsyncClient(KEY, base_url=BASE, backoff_base=0.0, max_retry_after=0.0) as c:
        yield c


def fax_job(**over: Any) -> dict[str, Any]:
    base = {
        "id": "fax_" + "1" * 32,
        "environment": "test",
        "direction": "outbound",
        "state": "queued",
        "failure_code": None,
        "to": "+15550000001",
        "from": None,
        "route": "us_ca",
        "document_id": "doc_" + "2" * 32,
        "document_sha256": "ab" * 32,
        "transmitted_sha256": None,
        "quote_id": "quote_" + "3" * 32,
        "billable_pages": 1,
        "cover_pages": 0,
        "pages_sent": 0,
        "amount_micro": 0,
        "captured_micro": 0,
        "currency": "USD",
        "provider": "sandbox",
        "provider_ref": None,
        "attempt_count": 0,
        "client_reference": None,
        "evidence": None,
        "created_at": "2026-10-28T00:00:00Z",
        "updated_at": "2026-10-28T00:00:00Z",
        "completed_at": None,
        "cancel_requested_at": None,
    }
    base.update(over)
    return base


def document(**over: Any) -> dict[str, Any]:
    base = {
        "id": "doc_" + "2" * 32,
        "environment": "test",
        "status": "ready",
        "source": "text",
        "filename": "hello",
        "content_type": "application/pdf",
        "size_bytes": 1234,
        "sha256": "ab" * 32,
        "page_count": 1,
        "render_status": "rendered",
        "render_reason": None,
        "validation": {"warnings": []},
        "upload_id": None,
        "created_at": "2026-10-28T00:00:00Z",
        "expires_at": None,
        "deleted_at": None,
    }
    base.update(over)
    return base


def quote(**over: Any) -> dict[str, Any]:
    base = {
        "id": "quote_" + "3" * 32,
        "environment": "test",
        "document_id": "doc_" + "2" * 32,
        "document_sha256": "ab" * 32,
        "to": "+15550000001",
        "from": None,
        "route": "us_ca",
        "document_pages": 1,
        "cover_pages": 0,
        "billable_pages": 1,
        "amount_micro": 0,
        "currency": "USD",
        "price_version": "launch-v1",
        "cover": None,
        "authorization_required": False,
        "warnings": [],
        "expires_at": "2026-10-28T00:10:00Z",
        "expired": False,
        "consumed_by_job_id": None,
        "created_at": "2026-10-28T00:00:00Z",
    }
    base.update(over)
    return base


def error(code: str, message: str = "refused", **extra: Any) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "retryable": False, "correlation_id": "req_1", **extra}
    }


def live_key() -> str | None:
    return os.environ.get("FAXFORAGENTS_API_KEY") or None
