"""The HTTP layer: headers, idempotency keys, retries, the error envelope, the upload path."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
import respx

from faxforagents import (
    APIConnectionError,
    AuthenticationError,
    Client,
    ConflictError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitedError,
    ScopeError,
    ServerError,
)
from tests.conftest import BASE, KEY, document, error, fax_job, quote


def test_bearer_header_and_user_agent(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/me").respond(
        200,
        json={
            "org_id": "org_1",
            "project_id": "proj_1",
            "key_id": "key_1",
            "key_prefix": "ffa_test_aaaa",
            "environment": "test",
            "scopes": ["fax:send"],
            "actor": "key:ffa_test_aaaa",
        },
    )
    me = client.me()
    assert me.environment == "test"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert sent.headers["user-agent"].startswith("faxforagents-python/")
    assert "idempotency-key" not in sent.headers


def test_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAXFORAGENTS_API_KEY", KEY)
    assert Client().settings.api_key == KEY
    monkeypatch.delenv("FAXFORAGENTS_API_KEY")
    monkeypatch.setenv("FFA_API_KEY", KEY)
    assert Client().settings.api_key == KEY
    monkeypatch.delenv("FFA_API_KEY")
    with pytest.raises(ValueError, match="FAXFORAGENTS_API_KEY"):
        Client()


def test_mutating_call_mints_an_idempotency_key(client: Client, api: respx.MockRouter) -> None:
    route = api.post("/v1/faxes").respond(201, json=fax_job())
    client.faxes.create(quote_id="quote_" + "3" * 32)
    key = route.calls.last.request.headers["idempotency-key"]
    assert len(key) == 36
    client.faxes.create(quote_id="quote_" + "3" * 32)
    assert route.calls.last.request.headers["idempotency-key"] != key


def test_explicit_idempotency_key_is_sent_as_is(client: Client, api: respx.MockRouter) -> None:
    route = api.post("/v1/faxes").respond(201, json=fax_job())
    client.faxes.create(quote_id="quote_" + "3" * 32, idempotency_key="order-42")
    assert route.calls.last.request.headers["idempotency-key"] == "order-42"
    body = json.loads(route.calls.last.request.content)
    assert body == {"quote_id": "quote_" + "3" * 32}


def test_request_body_uses_the_api_field_names(client: Client, api: respx.MockRouter) -> None:
    route = api.post("/v1/fax-quotes").respond(201, json=quote())
    client.quotes.create(document_id="doc_" + "2" * 32, to="+15550000001", from_="+12025550100")
    body = json.loads(route.calls.last.request.content)
    assert body == {"document_id": "doc_" + "2" * 32, "to": "+15550000001", "from": "+12025550100"}


def test_none_query_params_are_dropped(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/faxes").respond(200, json={"items": [], "next_cursor": None})
    client.faxes.list(state="failed")
    assert str(route.calls.last.request.url) == f"{BASE}/v1/faxes?state=failed"


def test_retry_on_429_reuses_the_same_idempotency_key(client: Client, api: respx.MockRouter) -> None:
    route = api.post("/v1/faxes")
    route.side_effect = [
        httpx.Response(429, json=error("rate_limited"), headers={"Retry-After": "0"}),
        httpx.Response(201, json=fax_job()),
    ]
    job = client.faxes.create(quote_id="quote_" + "3" * 32)
    assert job.state == "queued"
    keys = {c.request.headers["idempotency-key"] for c in route.calls}
    assert len(route.calls) == 2 and len(keys) == 1


def test_retry_on_503_then_success(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/balance")
    route.side_effect = [
        httpx.Response(503, text="upstream"),
        httpx.Response(502, text="upstream"),
        httpx.Response(
            200,
            json={"environment": "test", "currency": "USD", "available_micro": 100_000_000, "held_micro": 0},
        ),
    ]
    assert client.billing.balance().available_micro == 100_000_000
    assert len(route.calls) == 3


def test_retries_are_bounded(api: respx.MockRouter) -> None:
    route = api.get("/v1/balance").respond(503, json=error("temporarily_unavailable"))
    with Client(KEY, base_url=BASE, max_retries=2, backoff_base=0.0) as c, pytest.raises(ServerError) as exc:
        c.billing.balance()
    assert len(route.calls) == 3
    assert exc.value.status == 503 and exc.value.code == "temporarily_unavailable"


def test_rate_limited_without_retries_carries_retry_after(api: respx.MockRouter) -> None:
    api.get("/v1/balance").respond(429, json=error("rate_limited"), headers={"Retry-After": "7"})
    with Client(KEY, base_url=BASE, max_retries=0) as c, pytest.raises(RateLimitedError) as exc:
        c.billing.balance()
    assert exc.value.retry_after == 7.0
    assert exc.value.correlation_id == "req_1"


def test_connection_errors_are_retried_then_raised(api: respx.MockRouter) -> None:
    route = api.get("/v1/balance").mock(side_effect=httpx.ConnectError("down"))
    with Client(KEY, base_url=BASE, max_retries=1, backoff_base=0.0) as c, pytest.raises(APIConnectionError):
        c.billing.balance()
    assert len(route.calls) == 2


@pytest.mark.parametrize(
    ("status", "code", "cls"),
    [
        (401, "invalid_token", AuthenticationError),
        (403, "scope_missing", ScopeError),
        (404, "not_found", NotFoundError),
        (409, "quote_consumed", ConflictError),
        (402, "insufficient_balance", PaymentRequiredError),
    ],
)
def test_error_envelope_maps_to_typed_exceptions(
    client: Client, api: respx.MockRouter, status: int, code: str, cls: type[Exception]
) -> None:
    api.get("/v1/faxes/fax_x").respond(status, json=error(code, "no", details={"scope": "fax:read"}))
    with pytest.raises(cls) as exc:
        client.faxes.get("fax_x")
    err = exc.value
    assert err.code == code and err.status == status  # type: ignore[attr-defined]
    assert err.details == {"scope": "fax:read"}  # type: ignore[attr-defined]
    assert "req_1" in str(err)


def test_non_json_error_body(client: Client, api: respx.MockRouter) -> None:
    api.get("/v1/faxes/fax_x").respond(404, text="<html>not here</html>")
    with pytest.raises(NotFoundError) as exc:
        client.faxes.get("fax_x")
    assert exc.value.code == "http_404"


def test_upload_runs_the_whole_pdf_path(client: Client, api: respx.MockRouter, tmp_path: object) -> None:
    data = b"%PDF-1.4 fake"
    sha = hashlib.sha256(data).hexdigest()
    session = {
        "id": "upl_" + "4" * 32,
        "status": "open",
        "filename": "letter.pdf",
        "content_type": "application/pdf",
        "declared_size": len(data),
        "received_size": 0,
        "sha256": None,
        "created_at": "2026-10-28T00:00:00Z",
        "expires_at": "2026-10-28T01:00:00Z",
        "completed_at": None,
        "upload_url": f"{BASE}/v1/uploads/upl_4444/content?exp=1&sig=abc",
        "upload_method": "PUT",
        "upload_url_expires_at": "2026-10-28T00:10:00Z",
    }
    create = api.post("/v1/uploads").respond(201, json=session)
    put = api.put("/v1/uploads/upl_4444/content").respond(200, json={**session, "received_size": len(data)})
    complete = api.post(f"/v1/uploads/{session['id']}/complete").respond(
        200, json={**session, "status": "completed", "sha256": sha}
    )
    doc = api.post("/v1/documents").respond(201, json=document(source="upload", upload_id=session["id"]))

    out = client.documents.upload(data, filename="letter.pdf")
    assert out.upload_id == session["id"]
    assert json.loads(create.calls.last.request.content) == {
        "filename": "letter.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(data),
    }
    assert put.calls.last.request.content == data
    assert put.calls.last.request.headers["content-type"] == "application/pdf"
    assert "idempotency-key" not in put.calls.last.request.headers
    assert json.loads(complete.calls.last.request.content) == {"sha256": sha}
    assert json.loads(doc.calls.last.request.content) == {
        "upload_id": session["id"],
        "filename": "letter.pdf",
    }


def test_wait_polls_until_terminal(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/faxes/fax_1")
    route.side_effect = [
        httpx.Response(200, json=fax_job(id="fax_1", state="queued")),
        httpx.Response(200, json=fax_job(id="fax_1", state="active")),
        httpx.Response(200, json=fax_job(id="fax_1", state="delivered", pages_sent=1, evidence={"pages": 1})),
    ]
    job = client.faxes.wait("fax_1", interval=0.0)
    assert job.state == "delivered" and job.evidence == {"pages": 1}
    assert len(route.calls) == 3


def test_download_follows_the_signed_url(client: Client, api: respx.MockRouter) -> None:
    api.get("/v1/documents/doc_1/download").respond(
        200,
        json={
            "url": f"{BASE}/v1/documents/doc_1/content?exp=1&sig=s",
            "expires_at": "x",
            "content_type": "application/pdf",
        },
    )
    api.get("/v1/documents/doc_1/content").respond(
        200, content=b"%PDF", headers={"content-type": "application/pdf"}
    )
    assert client.documents.download("doc_1") == b"%PDF"


def test_raw_request_returns_json(client: Client, api: respx.MockRouter) -> None:
    api.get("/v1/anything").respond(200, json={"ok": True})
    assert client.request("GET", "/v1/anything") == {"ok": True}
