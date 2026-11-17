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
    InvalidRequestError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitedError,
    ScopeError,
    ServerError,
    models,
)
from tests.conftest import (
    BASE,
    KEY,
    api_key_with_secret,
    delivery,
    document,
    error,
    fax_job,
    quote,
    topup_session,
)


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


def test_validation_and_upload_queue_are_the_envelope(api: respx.MockRouter) -> None:
    """API 1.18.0 declares every error as the envelope. A field that fails validation is no other shape."""
    api.post("/v1/fax-quotes").respond(
        422,
        json={
            "error": {
                "code": "invalid_request",
                "message": "to is not a number",
                "retryable": False,
                "correlation_id": None,
                "details": {"field": "to"},
            }
        },
    )
    api.post("/v1/one-time-sends").respond(
        429,
        json={
            "error": {
                "code": "rate_limited",
                "message": "uploads are queued",
                "retryable": True,
                "correlation_id": "req_2",
                "details": {"cap": "upload_queue"},
            }
        },
        headers={"Retry-After": "5"},
    )
    with Client(KEY, base_url=BASE, max_retries=0) as c:
        with pytest.raises(InvalidRequestError) as invalid:
            c.quotes.create(document_id="doc_1", to="x")
        assert invalid.value.status == 422 and invalid.value.code == "invalid_request"
        assert invalid.value.retryable is False and invalid.value.correlation_id is None
        assert invalid.value.details == {"field": "to"}
        with pytest.raises(RateLimitedError) as queued:
            c.one_time_sends.create(
                to="+12025550100", sender_name="Ada", accept_aup=True, email="a@example.com", text="x"
            )
        assert queued.value.retryable is True and queued.value.retry_after == 5.0
        assert queued.value.correlation_id == "req_2" and queued.value.details == {"cap": "upload_queue"}


def test_error_envelope_model_reads_the_body() -> None:
    from faxforagents.models import ErrorBody, ErrorEnvelope

    env = ErrorEnvelope.model_validate({"error": {"code": "not_found", "message": "no", "retryable": False}})
    assert (
        isinstance(env.error, ErrorBody)
        and env.error.code == "not_found"
        and env.error.correlation_id is None
    )


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


def test_upload_names_the_image_type_from_the_filename(client: Client, api: respx.MockRouter) -> None:
    data = b"\x89PNG fake"
    session = {
        "id": "upl_" + "5" * 32,
        "status": "open",
        "filename": "scan.png",
        "content_type": "image/png",
        "declared_size": len(data),
        "received_size": 0,
        "sha256": None,
        "created_at": "2026-10-28T00:00:00Z",
        "expires_at": "2026-10-28T01:00:00Z",
        "completed_at": None,
        "upload_url": f"{BASE}/v1/uploads/upl_5555/content?exp=1&sig=abc",
        "upload_method": "PUT",
        "upload_url_expires_at": "2026-10-28T00:10:00Z",
    }
    create = api.post("/v1/uploads").respond(201, json=session)
    put = api.put("/v1/uploads/upl_5555/content").respond(200, json={**session, "received_size": len(data)})
    api.post(f"/v1/uploads/{session['id']}/complete").respond(200, json={**session, "status": "completed"})
    api.post("/v1/documents").respond(201, json=document(source="upload", upload_id=session["id"]))

    client.documents.upload(data, filename="scan.png")
    assert json.loads(create.calls.last.request.content)["content_type"] == "image/png"
    assert put.calls.last.request.headers["content-type"] == "image/png"


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


def test_complete_upload_sends_empty_json(client: Client, api: respx.MockRouter) -> None:
    from pathlib import Path

    cases = json.loads((Path(__file__).parent / "fixtures/responses.json").read_text())
    upload = next(case["body"] for case in cases if case["model"] == "UploadSession")
    route = api.post("/v1/uploads/upl_1/complete").respond(200, json=upload)
    client.documents.complete_upload("upl_1")
    assert route.calls.last.request.content == b"{}"
    assert route.calls.last.request.headers["content-type"] == "application/json"


def test_receipt_pdf_returns_the_bytes(client: Client, api: respx.MockRouter) -> None:
    api.get("/v1/faxes/fax_1/receipt.pdf").respond(
        200, content=b"%PDF", headers={"content-type": "application/pdf"}
    )
    assert client.faxes.get_receipt_pdf("fax_1") == b"%PDF"


def test_list_number_orders(client: Client, api: respx.MockRouter) -> None:
    api.get("/v1/number-orders").respond(200, json={"items": []})
    assert client.numbers.list_orders().items == []


def auto_send(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "environment": "test",
        "enabled": False,
        "max_amount_micro": 5000000,
        "daily_max_jobs": 20,
        "policy_id": None,
        "version": None,
    }
    base.update(over)
    return base


def test_auto_send_read_and_set(client: Client, api: respx.MockRouter) -> None:
    api.get("/v1/organizations/current/auto-send").respond(200, json=auto_send())
    assert client.policies.get_auto_send().enabled is False
    route = api.put("/v1/organizations/current/auto-send").respond(
        200, json=auto_send(enabled=True, daily_max_jobs=5, policy_id="pol_1", version=1)
    )
    setting = client.policies.set_auto_send(enabled=True, daily_max_jobs=5)
    assert setting.enabled is True and setting.version == 1
    sent = route.calls.last.request
    assert json.loads(sent.content) == {"enabled": True, "daily_max_jobs": 5}
    assert len(sent.headers["idempotency-key"]) == 36


def test_export_archive_download_sends_the_signed_values(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/organization/export-archives/org_1.arc_1").respond(
        200, content=b"PK", headers={"content-type": "application/zip"}
    )
    assert client.organizations.download_export_archive("org_1.arc_1", exp="1", sig="s") == b"PK"
    assert "exp=1" in str(route.calls.last.request.url) and "sig=s" in str(route.calls.last.request.url)


def test_revoke_oauth_grant(client: Client, api: respx.MockRouter) -> None:
    route = api.delete("/v1/oauth/grants/grant_1").respond(204)
    assert client.oauth.revoke_grant("grant_1", idempotency_key="k1") is None
    assert route.calls.last.request.headers["idempotency-key"] == "k1"


def test_fax_list_filters_by_idempotency_key_in_the_query(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/faxes").respond(200, json={"items": [], "next_cursor": None})
    client.faxes.list(idempotency_key="order-42")
    request = route.calls.last.request
    assert str(request.url) == f"{BASE}/v1/faxes?idempotency_key=order-42"
    assert "idempotency-key" not in request.headers


def test_effective_policies_can_ask_about_another_principal(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/policies/effective").respond(500, json=error("internal_error"))
    with pytest.raises(Exception):  # noqa: B017, PT011  only the request matters here
        Client(api_key=KEY, base_url=BASE, max_retries=0).policies.effective(connection_id="ogr_1")
    assert str(route.calls.last.request.url) == f"{BASE}/v1/policies/effective?connection_id=ogr_1"
    with pytest.raises(Exception):  # noqa: B017, PT011
        Client(api_key=KEY, base_url=BASE, max_retries=0).policies.effective()
    assert str(route.calls.last.request.url) == f"{BASE}/v1/policies/effective"


def test_create_api_key_sends_connected_app_only_when_given(client: Client, api: respx.MockRouter) -> None:
    route = api.post("/v1/api-keys")
    route.side_effect = [
        httpx.Response(201, json=api_key_with_secret(connected_app=True)),
        httpx.Response(201, json=api_key_with_secret(connected_app=False)),
        httpx.Response(201, json=api_key_with_secret()),
    ]
    made = client.identity.create_api_key("gemini", scopes=["fax:send"], connected_app=True)
    assert made.connected_app is True
    assert json.loads(route.calls.last.request.content) == {
        "label": "gemini",
        "environment": "test",
        "scopes": ["fax:send"],
        "connected_app": True,
    }
    assert client.identity.create_api_key("plain", connected_app=False).connected_app is False
    assert json.loads(route.calls.last.request.content)["connected_app"] is False
    # Left out, the field is not sent and an answer without it reads as a plain key.
    assert client.identity.create_api_key("older").connected_app is False
    assert json.loads(route.calls.last.request.content) == {"label": "older", "environment": "test"}


def test_list_api_keys_reads_connected_app(client: Client, api: respx.MockRouter) -> None:
    flagged = {k: v for k, v in api_key_with_secret(connected_app=True).items() if k != "secret"}
    plain = {k: v for k, v in api_key_with_secret(id="key_plain").items() if k != "secret"}
    api.get("/v1/api-keys").respond(200, json={"items": [flagged, plain]})
    assert [k.connected_app for k in client.identity.list_api_keys().items] == [True, False]


def number(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "num_1",
        "environment": "live",
        "e164": "+15550001001",
        "country": "US",
        "number_type": "local",
        "locality": None,
        "status": "active",
        "label": None,
        "project_id": None,
        "inbound_webhook_id": None,
        "inbound_status": "enabled",
        "email_pdf": False,
        "provider": "carrier",
        "monthly_micro": 2000000,
        "currency": "USD",
        "order_id": "nord_1",
        "created_at": "2026-11-13T00:00:00Z",
        "activated_at": "2026-11-13T00:00:00Z",
        "renews_at": None,
        "released_at": None,
    }
    base.update(over)
    return base


def test_update_number_sends_email_pdf_only_when_given(client: Client, api: respx.MockRouter) -> None:
    route = api.patch("/v1/numbers/num_1")
    route.side_effect = [
        httpx.Response(200, json=number(email_pdf=True)),
        httpx.Response(200, json=number(email_pdf=False)),
        httpx.Response(200, json=number(label="front desk")),
    ]
    assert client.numbers.update("num_1", email_pdf=True).email_pdf is True
    assert json.loads(route.calls.last.request.content) == {"email_pdf": True}
    # False is a value, not an omission: it switches the attachment off.
    assert client.numbers.update("num_1", email_pdf=False).email_pdf is False
    assert json.loads(route.calls.last.request.content) == {"email_pdf": False}
    client.numbers.update("num_1", label="front desk")
    assert json.loads(route.calls.last.request.content) == {"label": "front desk"}


def test_number_from_an_older_server_reads_email_pdf_as_false(client: Client, api: respx.MockRouter) -> None:
    older = {k: v for k, v in number().items() if k != "email_pdf"}
    api.get("/v1/numbers/num_1").respond(200, json=older)
    assert client.numbers.get("num_1").email_pdf is False


def test_extraction_parses_without_the_provider_cost(client: Client, api: respx.MockRouter) -> None:
    # API 1.19.0 stopped sending provider_cost_micro and the cost_micro of each page.
    body = {
        "id": "ext_1",
        "kind": "extract",
        "status": "completed",
        "environment": "test",
        "document_id": "doc_1",
        "inbox_item_id": None,
        "schema_id": None,
        "schema": None,
        "options": {},
        "pages_requested": 1,
        "pages_processed": 1,
        "pages_escalated": 0,
        "document_pages": 1,
        "confidence": 0.95,
        "pages": [
            {
                "page": 1,
                "text": "Fixture text",
                "confidence": 0.95,
                "status": "ok",
                "model": "sandbox",
                "warnings": [],
            }
        ],
        "output": None,
        "models": {},
        "price_micro": 0,
        "currency": "USD",
        "error_code": None,
        "created_by": "key:ffa_test_aaaa",
        "created_at": "2026-11-13T00:00:00Z",
        "started_at": "2026-11-13T00:00:00Z",
        "completed_at": "2026-11-13T00:00:01Z",
    }
    api.get("/v1/extractions/ext_1").respond(200, json=body)
    extraction = client.extractions.get("ext_1")
    assert extraction.price_micro == 0
    assert "provider_cost_micro" not in type(extraction).model_fields
    assert "cost_micro" not in type(extraction.pages[0]).model_fields


def upload_session(**over: object) -> dict[str, object]:
    return {
        "id": "upl_" + "6" * 32,
        "status": "open",
        "filename": "scan.png",
        "content_type": "image/png",
        "declared_size": 9,
        "received_size": 0,
        "sha256": None,
        "created_at": "2026-11-14T00:00:00Z",
        "expires_at": "2026-11-14T01:00:00Z",
        "completed_at": None,
        "upload_url": f"{BASE}/v1/uploads/upl_6666/content?exp=1&sig=abc",
        "upload_method": "PUT",
        "upload_url_expires_at": "2026-11-14T00:10:00Z",
        **over,
    }


def test_create_upload_leaves_the_content_type_to_the_api(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.5 reads the type from the filename. 0.6.0 sent application/pdf for every file."""
    create = api.post("/v1/uploads").respond(201, json=upload_session())
    session = client.documents.create_upload(filename="scan.png", size_bytes=9)
    assert json.loads(create.calls.last.request.content) == {"filename": "scan.png", "size_bytes": 9}
    assert session.content_type == "image/png"
    client.documents.create_upload(filename="scan.png", size_bytes=9, content_type="image/png")
    assert json.loads(create.calls.last.request.content) == {
        "filename": "scan.png",
        "size_bytes": 9,
        "content_type": "image/png",
    }


def test_create_upload_refuses_an_office_filename_at_once(api: respx.MockRouter) -> None:
    api.post("/v1/uploads").respond(
        422, json=error("unsupported_format", "letter.docx is not a PDF or an image", details=None)
    )
    with Client(KEY, base_url=BASE, max_retries=0) as c, pytest.raises(InvalidRequestError) as exc:
        c.documents.create_upload(filename="letter.docx", size_bytes=9)
    assert exc.value.code == "unsupported_format" and exc.value.status == 422


def receipt(**over: object) -> dict[str, object]:
    return {
        "url": "https://pay.example.test/receipts/1",
        "pdf_url": "https://pay.example.test/receipts/1/pdf",
        "amount_micro": 25_000_000,
        "amount_refunded_micro": 0,
        "currency": "USD",
        "payment_intent_id": "pi_1",
        "paid_at": "2026-11-14T19:15:26Z",
        **over,
    }


def test_payment_receipts_of_an_invoice_and_a_topup(client: Client, api: respx.MockRouter) -> None:
    """The two read routes of API 1.20.5. Either id may be absent from the answer."""
    invoice = api.get("/v1/invoices/in_1/receipt").respond(200, json=receipt(invoice_id="in_1"))
    topup = api.get("/v1/topup-sessions/tus_1/receipt").respond(200, json=receipt(topup_session_id="tus_1"))
    paid = client.billing.get_invoice_receipt("in_1")
    assert paid.url.endswith("/receipts/1") and paid.amount_micro == 25_000_000
    assert paid.invoice_id == "in_1" and paid.topup_session_id is None
    added = client.billing.get_topup_receipt("tus_1")
    assert added.topup_session_id == "tus_1" and added.invoice_id is None and added.pdf_url
    for route in (invoice, topup):
        assert route.calls.last.request.method == "GET"
        assert "idempotency-key" not in route.calls.last.request.headers


def test_an_unpaid_topup_has_no_receipt(client: Client, api: respx.MockRouter) -> None:
    api.get("/v1/topup-sessions/tus_2/receipt").respond(
        409, json=error("conflict", "this top-up was not paid", details={"status": "open"})
    )
    with pytest.raises(ConflictError) as exc:
        client.billing.get_topup_receipt("tus_2")
    assert exc.value.details == {"status": "open"} and exc.value.state is None


def test_quote_not_found_names_the_field(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.5: the 404 of a quote says which of its two ids was not found."""
    route = api.post("/v1/fax-quotes")
    route.side_effect = [
        httpx.Response(404, json=error("not_found", "no such document", details={"field": "document_id"})),
        httpx.Response(404, json=error("not_found", "no such sender number", details={"field": "from"})),
    ]
    with pytest.raises(NotFoundError) as doc:
        client.quotes.create(document_id="doc_" + "0" * 32, to="+15550000001")
    assert doc.value.field == "document_id"
    with pytest.raises(NotFoundError) as sender:
        client.quotes.create(document_id="doc_" + "2" * 32, to="+15550000001", from_="+14155550199")
    assert sender.value.field == "from"
    # an older server sends no details, and a field that is not a name is not returned
    api.get("/v1/faxes/fax_x").respond(404, json=error("not_found", "no", details=None))
    with pytest.raises(NotFoundError) as plain:
        client.faxes.get("fax_x")
    assert plain.value.field is None
    assert NotFoundError(status=404, code="not_found", message="no", details={"field": 7}).field is None


def test_an_early_receipt_names_the_fax_state(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.5: 409 conflict on a receipt asked for too early carries details.state."""
    api.get("/v1/faxes/fax_y/receipt").respond(
        409, json=error("conflict", "no receipt yet: the fax is active", details={"state": "active"})
    )
    with pytest.raises(ConflictError) as exc:
        client.faxes.get_receipt("fax_y")
    assert exc.value.state == "active" and exc.value.code == "conflict" and exc.value.retryable is False
    assert ConflictError(status=409, code="quote_consumed", message="used").state is None


def statement_line(**over: object) -> dict[str, object]:
    return {
        "id": "led_" + "7" * 32,
        "created_at": "2026-11-15T09:00:00Z",
        "type": "topup",
        "description": "Top-up",
        "amount_micro": 25_000_000,
        "balance_after_micro": 125_000_000,
        "ref_type": "topup_session",
        "ref_id": "tus_1",
        **over,
    }


def test_statement_pages_filters_and_csv(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.6: one line per wallet event with the balance after it, as JSON pages or as CSV."""
    route = api.get("/v1/statement")
    route.side_effect = [
        httpx.Response(200, json={"items": [statement_line()], "next_cursor": "led_9"}),
        httpx.Response(200, json={"items": [statement_line(id="led_2", type="fax")], "next_cursor": "led_2"}),
        httpx.Response(200, json={"items": [statement_line(id="led_1", type="fax")], "next_cursor": None}),
    ]
    page = client.billing.statement(kind="topup", from_="2026-11-01", to="2026-11-15", limit=10)
    assert page.items[0].balance_after_micro == 125_000_000 and page.next_cursor == "led_9"
    sent = route.calls[0].request.url.params
    assert dict(sent) == {"kind": "topup", "from": "2026-11-01", "to": "2026-11-15", "limit": "10"}
    assert "idempotency-key" not in route.calls[0].request.headers
    assert [line.id for line in client.billing.iter_statement(kind="fax")] == ["led_2", "led_1"]
    assert dict(route.calls[1].request.url.params) == {"kind": "fax"}
    assert dict(route.calls[2].request.url.params) == {"kind": "fax", "cursor": "led_2"}
    csv = api.get("/v1/statement.csv").respond(
        200, content=b"date,type,amount\n2026-11-15,topup,25.00\n", headers={"content-type": "text/csv"}
    )
    text = client.billing.statement_csv(to="2026-11-15")
    assert text.splitlines() == ["date,type,amount", "2026-11-15,topup,25.00"]
    assert dict(csv.calls.last.request.url.params) == {"to": "2026-11-15"}


def test_document_rename_and_the_list_filters(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.6: PATCH renames a document. The list filters by source and text and answers total."""
    doc_id = document()["id"]
    patch = api.patch(f"/v1/documents/{doc_id}").respond(200, json=document(filename="contract.pdf"))
    renamed = client.documents.rename(doc_id, filename="contract", idempotency_key="k-rename")
    assert renamed.filename == "contract.pdf"
    assert json.loads(patch.calls.last.request.content) == {"filename": "contract"}
    assert patch.calls.last.request.headers["idempotency-key"] == "k-rename"
    listed = api.get("/v1/documents")
    listed.side_effect = [
        httpx.Response(200, json={"items": [document(source="inbound")], "next_cursor": None, "total": 1}),
        httpx.Response(200, json={"items": [], "next_cursor": None, "total": 0}),
    ]
    page = client.documents.list(source="inbound", q="+1415")
    assert page.total == 1 and page.items[0].source == "inbound"
    assert dict(listed.calls[0].request.url.params) == {"source": "inbound", "q": "+1415"}
    assert list(client.documents.iter(exclude_source="inbound", limit=5)) == []
    assert dict(listed.calls[1].request.url.params) == {"exclude_source": "inbound", "limit": "5"}


def test_fax_search_by_number_or_job_id(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/faxes")
    route.side_effect = [
        httpx.Response(200, json={"items": [fax_job(state="failed")], "next_cursor": None}),
        httpx.Response(200, json={"items": [fax_job()], "next_cursor": None}),
    ]
    page = client.faxes.list(q="fax_1111", state="failed")
    assert page.items[0].state == "failed"
    assert dict(route.calls[0].request.url.params) == {"state": "failed", "q": "fax_1111"}
    assert len(list(client.faxes.iter(q="(555) 000-0001"))) == 1
    assert route.calls[1].request.url.params["q"] == "(555) 000-0001"


def test_one_webhook_delivery_with_what_was_sent(client: Client, api: respx.MockRouter) -> None:
    detail = {
        "id": "dlv_1",
        "webhook_id": "wh_1",
        "webhook_url": "https://example.com/hooks/fax",
        "event_id": "evt_1",
        "event_type": "fax.delivered",
        "attempt": 1,
        "status": "delivered",
        "response_status": 200,
        "error": None,
        "duration_ms": 84,
        "replay_of": None,
        "schedule_s": [0, 60, 300],
        "next_attempt_at": None,
        "created_at": "2026-11-15T09:00:00Z",
        "delivered_at": "2026-11-15T09:00:01Z",
        "webhook_deleted": False,
        "request_headers": {"webhook-id": "evt_1", "content-type": "application/json"},
        "payload": {"type": "fax.delivered", "data": {"id": "fax_1"}},
    }
    api.get("/v1/webhook-deliveries/dlv_1").respond(200, json=detail)
    got = client.webhooks.get_delivery("dlv_1")
    assert got.request_headers["webhook-id"] == "evt_1" and got.payload["type"] == "fax.delivered"
    assert got.webhook_url == "https://example.com/hooks/fax" and got.webhook_deleted is False


def budget(**over: object) -> dict[str, object]:
    return {
        "id": "bud_1",
        "environment": "test",
        "scope": "organization",
        "project_id": None,
        "agent_id": None,
        "period": "monthly",
        "limit_micro": 10_000_000,
        "spent_micro": 0,
        "remaining_micro": 10_000_000,
        "status": "active",
        "resets_at": "2026-12-01T00:00:00Z",
        "created_at": "2026-11-01T00:00:00Z",
        "updated_at": "2026-11-01T00:00:00Z",
        **over,
    }


def test_budget_removal_and_the_archived_list(client: Client, api: respx.MockRouter) -> None:
    removed = api.delete("/v1/budgets/bud_1").respond(
        200, json={"id": "bud_1", "deleted": False, "archived": True}
    )
    gone = client.billing.delete_budget("bud_1")
    assert gone.archived is True and gone.deleted is False
    assert len(removed.calls.last.request.headers["idempotency-key"]) == 36
    listed = api.get("/v1/budgets")
    listed.side_effect = [
        httpx.Response(200, json={"items": [budget(archived_at="2026-11-15T09:00:00Z")]}),
        httpx.Response(200, json={"items": []}),
    ]
    assert client.billing.list_budgets(include_archived=True).items[0].archived_at
    assert dict(listed.calls[0].request.url.params) == {"include_archived": "true"}
    client.billing.list_budgets()
    assert dict(listed.calls[1].request.url.params) == {}


def test_agent_archive_and_the_archived_list(client: Client, api: respx.MockRouter) -> None:
    route = api.delete("/v1/agents/agent_1").respond(
        200, json={"id": "agent_1", "deleted": False, "archived": True, "keys_revoked": 2}
    )
    archived = client.agents.archive("agent_1", idempotency_key="k-archive")
    assert archived.keys_revoked == 2 and archived.archived is True
    assert route.calls.last.request.headers["idempotency-key"] == "k-archive"
    listed = api.get("/v1/agents").respond(200, json={"items": []})
    assert client.agents.list(include_archived=True).items == []
    assert dict(listed.calls.last.request.url.params) == {"include_archived": "true"}


def number_order(**over: object) -> dict[str, object]:
    return {
        "id": "nor_1",
        "environment": "test",
        "status": "failed",
        "e164": "+15550001000",
        "country": "US",
        "number_type": "local",
        "number_id": None,
        "number": None,
        "provider": "sandbox",
        "recurring_charge_micro": 2_000_000,
        "recurring_period": "month",
        "first_month_hold_micro": 2_000_000,
        "currency": "USD",
        "error": "Sandbox carrier refused this number order.",
        "created_at": "2026-11-15T09:00:00Z",
        "completed_at": "2026-11-15T09:00:05Z",
        **over,
    }


def test_number_order_dismissal(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.6: a failed order is put away. Any other order answers 409 conflict."""
    route = api.post("/v1/number-orders/nor_1/dismissals")
    route.side_effect = [
        httpx.Response(200, json=number_order(dismissed_at="2026-11-15T10:00:00Z")),
        httpx.Response(409, json=error("conflict", "this order did not fail")),
    ]
    order = client.numbers.dismiss_order("nor_1")
    assert order.dismissed_at == "2026-11-15T10:00:00Z" and order.status == "failed"
    assert route.calls[0].request.content == b""
    assert len(route.calls[0].request.headers["idempotency-key"]) == 36
    with pytest.raises(ConflictError):
        client.numbers.dismiss_order("nor_1")


def evaluation(**over: object) -> dict[str, object]:
    return {
        "decision": "allow",
        "rule": None,
        "reason": "allowed by policy",
        "policy_id": "pol_1",
        "policy_version_id": "polv_1",
        "policy_name": "US only",
        "version": 1,
        "denials": [],
        "plan": {"to": "+15550000001"},
        **over,
    }


def test_policy_rule_check_and_an_evaluation_for_an_agent(client: Client, api: respx.MockRouter) -> None:
    check = api.post("/v1/policy-rule-checks").respond(
        200, json={"already_allowed": [{"number": "+14155550100", "countries": ["US"], "prefixes": []}]}
    )
    found = client.policies.check_rules(
        allowed_destinations=("+14155550100", "+447700900123"), allowed_countries=["US"]
    )
    assert found.already_allowed[0].number == "+14155550100" and found.already_allowed[0].countries == ["US"]
    assert json.loads(check.calls.last.request.content) == {
        "allowed_destinations": ["+14155550100", "+447700900123"],
        "allowed_countries": ["US"],
    }
    evaluate = api.post("/v1/policy-evaluations").respond(
        200, json=evaluation(subject="agent_1", under_policy=True, approval_required=False)
    )
    result = client.policies.evaluate(to="+15550000001", document_id="doc_" + "2" * 32, agent_id="agent_1")
    assert result.subject == "agent_1" and result.under_policy is True and result.approval_required is False
    assert json.loads(evaluate.calls.last.request.content) == {
        "to": "+15550000001",
        "document_id": "doc_" + "2" * 32,
        "agent_id": "agent_1",
    }


def invitation(**over: object) -> dict[str, object]:
    return {
        "id": "inv_1",
        "email": "sam@example.com",
        "role": "member",
        "invited_by": "user_1",
        "created_at": "2026-11-15T09:00:00Z",
        "expires_at": "2026-11-22T09:00:00Z",
        "accepted_at": None,
        **over,
    }


def test_invitation_resend_and_a_link_to_hand_over(client: Client, api: respx.MockRouter) -> None:
    resend = api.post("/v1/invitations/inv_1/resend").respond(200, json=invitation())
    assert client.members.resend_invitation("inv_1").expires_at == "2026-11-22T09:00:00Z"
    assert len(resend.calls.last.request.headers["idempotency-key"]) == 36
    link = api.post("/v1/invitations/inv_1/link").respond(
        200,
        json={
            "id": "inv_1",
            "url": "https://app.example.test/join/abc",
            "expires_at": "2026-11-22T09:00:00Z",
        },
    )
    made = client.members.link_invitation("inv_1", idempotency_key="k-link")
    assert made.url == "https://app.example.test/join/abc"
    assert link.calls.last.request.headers["idempotency-key"] == "k-link"


def data_request(**over: object) -> dict[str, object]:
    return {
        "id": "orq_1",
        "kind": "deletion",
        "status": "open",
        "created_at": "2026-11-15T09:00:00Z",
        "due_at": "2026-12-15T09:00:00Z",
        "closed_at": None,
        **over,
    }


def test_data_requests_and_withdrawing_a_deletion(client: Client, api: respx.MockRouter) -> None:
    api.get("/v1/organizations/current/data-requests").respond(
        200, json={"items": [data_request(), data_request(id="orq_2", kind="export", status="closed")]}
    )
    listed = client.organizations.list_data_requests()
    assert [(r.kind, r.status) for r in listed.items] == [("deletion", "open"), ("export", "closed")]
    route = api.delete("/v1/organizations/current/deletion-requests/orq_1")
    route.side_effect = [
        httpx.Response(200, json=data_request(status="withdrawn", closed_at="2026-11-15T10:00:00Z")),
        httpx.Response(
            409, json=error("conflict", "not open any more", details={"reason": "request_closed"})
        ),
    ]
    assert client.organizations.withdraw_data_deletion("orq_1").status == "withdrawn"
    with pytest.raises(ConflictError) as exc:
        client.organizations.withdraw_data_deletion("orq_1")
    assert exc.value.details == {"reason": "request_closed"}


def test_inbox_archived_filter_and_the_unread_count(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/inbox").respond(200, json={"items": [], "next_cursor": None, "unread": 3})
    page = client.inbox.list(archived=False)
    assert page.unread == 3
    assert dict(route.calls.last.request.url.params) == {"archived": "false"}
    assert list(client.inbox.iter(archived=True)) == []
    assert dict(route.calls.last.request.url.params) == {"archived": "true"}


def approval(**over: object) -> dict[str, object]:
    return {
        "id": "apr_1",
        "kind": "help",
        "status": "rejected",
        "quote_id": None,
        "bound": {},
        "message": "Which number should I use?",
        "requested_by": "key_1",
        "decided_by": "user_1",
        "reason": None,
        "consumed_by_job_id": None,
        "created_at": "2026-11-15T09:00:00Z",
        "expires_at": "2026-11-16T09:00:00Z",
        "decided_at": "2026-11-15T09:05:00Z",
        **over,
    }


def test_used_approvals_and_closing_a_help_request(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.6: a used approval reads approved, and a help request closes without a reason."""
    used = approval(
        kind="send", status="approved", quote_id="quote_1", consumed_by_job_id="fax_1", requested_by_name="ci"
    )
    listed = api.get("/v1/approval-requests").respond(200, json={"items": [used], "next_cursor": None})
    page = client.approvals.list(status="used")
    assert page.items[0].status == "approved" and page.items[0].consumed_by_job_id == "fax_1"
    assert page.items[0].requested_by_name == "ci"
    assert dict(listed.calls.last.request.url.params) == {"status": "used"}
    reject = api.post("/v1/approval-requests/apr_1/reject").respond(200, json=approval())
    assert client.approvals.reject("apr_1").status == "rejected"
    assert json.loads(reject.calls.last.request.content) == {}
    client.approvals.reject("apr_1", reason="wrong number")
    assert json.loads(reject.calls.last.request.content) == {"reason": "wrong number"}


def test_cancel_an_open_topup(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.8: the hosted Checkout of an open top-up closes, and the session reads canceled."""
    tid = topup_session()["id"]
    route = api.post(f"/v1/topup-sessions/{tid}/cancel")
    route.side_effect = [
        httpx.Response(200, json=topup_session(status="canceled")),
        httpx.Response(
            409,
            json=error(
                "conflict", "this top-up has no open Checkout to cancel", details={"status": "completed"}
            ),
        ),
    ]
    canceled = client.billing.cancel_topup_session(tid, idempotency_key="k-cancel")
    assert canceled.status == "canceled" and canceled.id == tid
    sent = route.calls.last.request
    assert sent.headers["idempotency-key"] == "k-cancel" and sent.content == b""
    with pytest.raises(ConflictError) as exc:
        client.billing.cancel_topup_session(tid)
    assert exc.value.code == "conflict" and exc.value.details == {"status": "completed"}
    assert len(route.calls) == 2


def test_the_statement_filters_by_type_and_kind_still_works(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.8: type is the filter, kind is its older name, and two different types answer 422."""
    hold = statement_line(id="led_3", type="release", ref_type="extraction", ref_id="ext_1")
    route = api.get("/v1/statement")
    route.side_effect = [
        httpx.Response(200, json={"items": [statement_line(type="refund")], "next_cursor": None}),
        httpx.Response(200, json={"items": [hold], "next_cursor": None}),
        httpx.Response(200, json={"items": [], "next_cursor": None}),
        httpx.Response(
            422,
            json=error(
                "invalid_request",
                "type and kind are one filter: send one of them, or the same type in both",
                details={"fields": ["type", "kind"]},
            ),
        ),
    ]
    assert client.billing.statement(type="refund").items[0].type == "refund"
    assert dict(route.calls[0].request.url.params) == {"type": "refund"}
    released = list(client.billing.iter_statement(type="release", limit=5))
    assert released[0].ref_type == "extraction" and released[0].ref_id == "ext_1"
    assert dict(route.calls[1].request.url.params) == {"type": "release", "limit": "5"}
    assert client.billing.statement(kind="fax").items == []
    assert dict(route.calls[2].request.url.params) == {"kind": "fax"}
    with pytest.raises(InvalidRequestError) as exc:
        client.billing.statement(type="fax", kind="topup")
    assert exc.value.details == {"fields": ["type", "kind"]}
    assert dict(route.calls[3].request.url.params) == {"type": "fax", "kind": "topup"}
    csv = api.get("/v1/statement.csv").respond(
        200, content=b"date,type\n", headers={"content-type": "text/csv"}
    )
    assert client.billing.statement_csv(type="refund", from_="2026-11-01") == "date,type\n"
    assert dict(csv.calls.last.request.url.params) == {"type": "refund", "from": "2026-11-01"}


def test_an_expired_key_is_not_rotated(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.8: the replacement would keep the past expiry, so the rotation answers 409."""
    details = {"reason": "key_expired", "expires_at": "2026-11-01T00:00:00Z"}
    route = api.post("/v1/api-keys/key_1/rotate").respond(
        409, json=error("conflict", "this key expired, so it cannot be rotated", details=details)
    )
    with pytest.raises(ConflictError) as exc:
        client.identity.rotate_api_key("key_1")
    assert exc.value.status == 409 and exc.value.details == details
    assert len(route.calls) == 1


def test_the_quote_and_the_job_name_their_policy(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.8: a quote carries its pricing and the policy dry run, a job what authorized it."""
    usage = {"day_jobs": 3, "day_amount_micro": 117_000, "month_jobs": 40, "month_amount_micro": 1_560_000}
    verdict = {**evaluation(version=2), "managed": None, "usage": usage}
    verdict.pop("plan")
    route = api.post("/v1/fax-quotes")
    route.side_effect = [
        httpx.Response(201, json=quote(pricing="one_time", policy_evaluation=verdict)),
        httpx.Response(201, json=quote()),
    ]
    priced = client.quotes.create(document_id="doc_" + "2" * 32, to="+15550000001")
    assert priced.pricing == "one_time" and priced.policy_evaluation is not None
    assert priced.policy_evaluation.decision == "allow" and priced.policy_evaluation.version == 2
    assert priced.policy_evaluation.usage is not None and priced.policy_evaluation.usage.day_jobs == 3
    older = client.quotes.create(document_id="doc_" + "2" * 32, to="+15550000001")
    assert older.pricing == "per_page" and older.policy_evaluation is None
    job_id = fax_job()["id"]
    get = api.get(f"/v1/faxes/{job_id}")
    get.side_effect = [
        httpx.Response(200, json=fax_job(approval_id="apr_1", policy_version_id=None, route_paused=True)),
        httpx.Response(200, json=fax_job()),
    ]
    job = client.faxes.get(job_id)
    assert job.approval_id == "apr_1" and job.policy_version_id is None and job.route_paused is True
    plain = client.faxes.get(job_id)
    assert plain.approval_id is None and plain.route_paused is False
    api.post("/v1/policy-evaluations").respond(200, json=evaluation(managed="connected_apps", usage=usage))
    result = client.policies.evaluate(to="+15550000001", document_id="doc_" + "2" * 32)
    assert result.managed == "connected_apps" and result.usage is not None
    assert result.usage.month_amount_micro == 1_560_000


def test_an_automatic_topup_receipt_has_no_pdf(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.8: a charge without an invoice has a receipt page and no PDF."""
    tid = topup_session(kind="auto")["id"]
    api.get(f"/v1/topup-sessions/{tid}/receipt").respond(
        200,
        json=receipt(pdf_url=None, topup_session_id=tid, url="https://pay.example.test/receipts/payment/1"),
    )
    found = client.billing.get_topup_receipt(tid)
    assert found.pdf_url is None and found.url.endswith("/receipts/payment/1")


def test_a_topup_read_asks_stripe_only_when_told(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.10: check_checkout reads the hosted Checkout at Stripe. The default sends nothing."""
    tid = topup_session()["id"]
    route = api.get(f"/v1/topup-sessions/{tid}")
    route.side_effect = [
        httpx.Response(200, json=topup_session(checkout_status=None)),
        httpx.Response(200, json=topup_session(checkout_status="complete")),
        httpx.Response(200, json=topup_session()),
    ]
    plain = client.billing.get_topup_session(tid)
    assert plain.status == "open" and plain.checkout_status is None
    assert route.calls[0].request.url.query == b""
    paid = client.billing.get_topup_session(tid, check_checkout=True)
    assert paid.status == "open" and paid.checkout_status == "complete"
    assert dict(route.calls[1].request.url.params) == {"check_checkout": "true"}
    older = client.billing.get_topup_session(tid, check_checkout=False)
    assert older.checkout_status is None
    assert route.calls[2].request.url.query == b""


def test_a_delivery_to_a_deleted_webhook_is_not_replayed(client: Client, api: respx.MockRouter) -> None:
    """API 1.20.10: every delivery says whether its webhook was deleted, and a replay of one answers 409."""
    ended = delivery(status="failed", error="the webhook was deleted", webhook_deleted=True)
    older = delivery(id="dlv_2")
    older.pop("webhook_deleted")
    listed = api.get("/v1/webhook-deliveries").respond(
        200, json={"items": [ended, older], "next_cursor": None}
    )
    items = client.webhooks.list_deliveries(webhook_id="wh_1").items
    assert items[0].webhook_deleted is True and items[0].next_attempt_at is None
    assert items[1].webhook_deleted is None
    assert dict(listed.calls.last.request.url.params) == {"webhook_id": "wh_1"}
    details = {"reason": "webhook_deleted"}
    replay = api.post("/v1/webhook-deliveries/dlv_1/replay").respond(
        409, json=error("conflict", "the webhook of this delivery was deleted", details=details)
    )
    with pytest.raises(ConflictError) as exc:
        client.webhooks.replay_delivery("dlv_1")
    assert exc.value.code == "conflict" and exc.value.details == details
    assert len(replay.calls) == 1


def test_the_fax_list_takes_a_created_range(client: Client, api: respx.MockRouter) -> None:
    """API 1.21.0: created_after is included and created_before left out. A reversed range answers 422."""
    later = fax_job(id="fax_" + "9" * 32)
    route = api.get("/v1/faxes")
    route.side_effect = [
        httpx.Response(200, json={"items": [fax_job()], "next_cursor": "c2"}),
        httpx.Response(200, json={"items": [fax_job()], "next_cursor": "c2"}),
        httpx.Response(200, json={"items": [later], "next_cursor": None}),
        httpx.Response(422, json=error("invalid_request", "created_after is after created_before")),
    ]
    page = client.faxes.list(
        created_after="2026-11-16T00:00:00Z", created_before="2026-11-17T00:00:00Z", state="delivered"
    )
    assert page.next_cursor == "c2"
    assert dict(route.calls[0].request.url.params) == {
        "state": "delivered",
        "created_after": "2026-11-16T00:00:00Z",
        "created_before": "2026-11-17T00:00:00Z",
    }
    ids = [job.id for job in client.faxes.iter(created_after="2026-11-16T00:00:00Z")]
    assert ids == [fax_job()["id"], later["id"]]
    assert dict(route.calls[1].request.url.params) == {"created_after": "2026-11-16T00:00:00Z"}
    assert dict(route.calls[2].request.url.params) == {
        "created_after": "2026-11-16T00:00:00Z",
        "cursor": "c2",
    }
    with pytest.raises(InvalidRequestError) as exc:
        client.faxes.list(created_after="2026-11-17T00:00:00Z", created_before="2026-11-16T00:00:00Z")
    assert exc.value.status == 422 and exc.value.code == "invalid_request"


def test_a_test_quote_names_its_live_price(client: Client, api: respx.MockRouter) -> None:
    """API 1.21.0: a test quote is free and live_amount_micro is what the same fax costs live."""
    route = api.post("/v1/fax-quotes")
    older = quote()
    route.side_effect = [
        httpx.Response(201, json=quote(price_version="launch-v3", billable_pages=2, live_amount_micro=78000)),
        httpx.Response(201, json=quote(live_amount_micro=None)),
        httpx.Response(201, json=older),
    ]
    priced = client.quotes.create(document_id="doc_" + "2" * 32, to="+15550000001")
    assert priced.amount_micro == 0 and priced.live_amount_micro == 78000
    unknown = client.quotes.create(document_id="doc_" + "2" * 32, to="+15550000001")
    assert unknown.live_amount_micro is None
    assert "live_amount_micro" not in older
    assert client.quotes.create(document_id="doc_" + "2" * 32, to="+15550000001").live_amount_micro is None


def test_the_records_export_is_csv_text(client: Client, api: respx.MockRouter) -> None:
    """API 1.21.0: the fax history and the audit trail of the Agent workspace plan, as CSV."""
    faxes = api.get("/v1/records/faxes.csv").respond(
        200,
        content=b"direction,id,created_at\noutbound,fax_1,2026-11-16T09:00:00Z\n",
        headers={"content-type": "text/csv; charset=utf-8"},
    )
    text = client.organizations.fax_history_csv(from_="2026-11-01", to="2026-11-16")
    assert text.splitlines() == ["direction,id,created_at", "outbound,fax_1,2026-11-16T09:00:00Z"]
    sent = faxes.calls.last.request
    assert dict(sent.url.params) == {"from": "2026-11-01", "to": "2026-11-16"}
    assert sent.method == "GET" and "idempotency-key" not in sent.headers
    audit = api.get("/v1/records/audit.csv").respond(
        200,
        content="created_at,actor_label\n2026-11-16,Dana Ö\n".encode(),
        headers={"content-type": "text/csv"},
    )
    assert client.organizations.audit_csv() == "created_at,actor_label\n2026-11-16,Dana Ö\n"
    assert audit.calls.last.request.url.query == b""


def test_the_records_export_needs_the_workspace_plan(client: Client, api: respx.MockRouter) -> None:
    """API 1.21.0: without the plan, or 30 days after it ended, the export answers 403 workspace_required."""
    details = {"ended_at": "2026-10-01T00:00:00Z", "closed_at": "2026-10-31T00:00:00Z"}
    api.get("/v1/records/faxes.csv").respond(403, json=error("workspace_required", details=details))
    with pytest.raises(ScopeError) as exc:
        client.organizations.fax_history_csv(from_="2026-09-01")
    assert exc.value.code == "workspace_required" and exc.value.details == details
    api.get("/v1/records/audit.csv").respond(403, json=error("scope_missing", details={"scope": "org:admin"}))
    with pytest.raises(ScopeError) as missing:
        client.organizations.audit_csv(to="2026-11-16")
    assert missing.value.code == "scope_missing" and missing.value.details == {"scope": "org:admin"}


def workspace_plan(**over: object) -> dict[str, object]:
    return {
        "active": True,
        "credit_micro": 10_000_000,
        "records_keep_days": 365,
        "document_keep_days": 30,
        "inbox_keep_days": 90,
        "requests_per_minute": 3000,
        "plan_requests_per_minute": 3000,
        "standard_requests_per_minute": 600,
        "included_numbers": 1,
        "included_number_id": "num_1",
        "included_number": "+15550001001",
        "ended_at": None,
        "cleanup_at": None,
        "export_open": True,
        **over,
    }


def test_the_subscription_list_carries_the_workspace_plan(client: Client, api: respx.MockRouter) -> None:
    """API 1.21.0: plan says what the Agent workspace gives. An older server sends no plan."""
    route = api.get("/v1/subscriptions")
    route.side_effect = [
        httpx.Response(200, json={"items": [], "workspace": True, "plan": workspace_plan()}),
        httpx.Response(200, json={"items": [], "workspace": False}),
    ]
    listed = client.billing.list_subscriptions()
    assert listed.plan is not None and listed.plan.export_open is True
    assert listed.plan.requests_per_minute == 3000 and listed.plan.standard_requests_per_minute == 600
    assert listed.plan.included_number_id == "num_1" and listed.plan.ended_at is None
    assert client.billing.list_subscriptions().plan is None
    with pytest.raises(ValueError):
        models.WorkspacePlan.model_validate({k: v for k, v in workspace_plan().items() if k != "export_open"})


def subscription(**over: object) -> dict[str, object]:
    """A subscription as ``sub_json`` of billing/subscriptions.py writes it at API 1.21.2."""
    return {
        "id": "sub_1",
        "product": "workspace_monthly",
        "status": "active",
        "stripe_subscription_id": "sub_fixture",
        "stripe_price_id": "price_workspace_monthly_test",
        "cancel_at_period_end": False,
        "cancel_at": None,
        "ends_at": None,
        "current_period_end": "2026-12-16T23:23:27.000Z",
        "entitled": True,
        "created_at": "2026-11-16T23:23:27.000Z",
        "updated_at": "2026-11-16T23:23:27.000Z",
        "canceled_at": None,
        **over,
    }


def test_a_subscription_says_when_it_ends(client: Client, api: respx.MockRouter) -> None:
    """API 1.21.2: cancel_at and ends_at. A cancel in the billing portal sets cancel_at, not the flag."""
    end = "2026-12-16T23:23:27.000Z"
    renewing = {k: v for k, v in subscription().items() if k not in ("cancel_at", "ends_at")}
    route = api.get("/v1/subscriptions")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "items": [subscription(cancel_at=end, ends_at=end), subscription(id="sub_2")],
                "workspace": True,
                "plan": workspace_plan(),
            },
        ),
        httpx.Response(200, json={"items": [renewing], "workspace": True}),
    ]
    ending, renews = client.billing.list_subscriptions().items
    assert ending.cancel_at_period_end is False
    assert ending.cancel_at == end and ending.ends_at == end
    assert renews.cancel_at is None and renews.ends_at is None
    older = client.billing.list_subscriptions().items[0]
    assert older.cancel_at is None and older.ends_at is None and older.entitled is True


def test_the_plan_is_never_bought_twice(client: Client, api: respx.MockRouter) -> None:
    """API 1.21.2: a second checkout of the plan answers 409 conflict, and details.reason says why."""
    page = "https://checkout.stripe.com/c/pay/cs_test_fixture"
    route = api.post("/v1/subscriptions/checkout")
    route.side_effect = [
        httpx.Response(
            409,
            json=error(
                "conflict",
                "The Agent workspace is already active for this organization.",
                details={"reason": "plan_active"},
            ),
        ),
        httpx.Response(
            409,
            json=error(
                "conflict",
                "A payment page for the Agent workspace is already open.",
                details={"reason": "checkout_open", "url": page, "expires_at": "2026-11-17T23:23:27.000Z"},
            ),
        ),
        httpx.Response(
            201,
            json={"url": page, "stripe_session_id": "cs_test_fixture", "product": "workspace_monthly"},
        ),
    ]
    with pytest.raises(ConflictError) as active:
        client.billing.create_subscription_checkout()
    assert active.value.code == "conflict" and active.value.reason == "plan_active"
    assert active.value.state is None and active.value.retryable is False
    with pytest.raises(ConflictError) as open_page:
        client.billing.create_subscription_checkout()
    assert open_page.value.reason == "checkout_open"
    assert open_page.value.details["url"] == page
    assert open_page.value.details["expires_at"] == "2026-11-17T23:23:27.000Z"
    assert client.billing.create_subscription_checkout().url == page
    assert len(route.calls) == 3
    # a refusal with no details, or a reason that is not a string, has no reason
    assert ConflictError(status=409, code="quote_consumed", message="used").reason is None
    assert ConflictError(status=409, code="conflict", message="x", details={"reason": 7}).reason is None
    assert ConflictError(status=409, code="conflict", message="x", details=["plan_active"]).reason is None


def test_the_number_the_workspace_pays_for(client: Client, api: respx.MockRouter) -> None:
    """API 1.21.0: included_with_workspace marks the number whose monthly charge the plan pays."""
    route = api.get("/v1/numbers/num_1")
    route.side_effect = [
        httpx.Response(200, json=number(included_with_workspace=True)),
        httpx.Response(200, json=number()),
    ]
    assert client.numbers.get("num_1").included_with_workspace is True
    assert client.numbers.get("num_1").included_with_workspace is False
