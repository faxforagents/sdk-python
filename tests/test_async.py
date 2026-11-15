"""The async client behaves like the sync one: same headers, same retries, same pagination."""

from __future__ import annotations

import httpx
import respx

from faxforagents import AsyncClient
from tests.conftest import api_key_with_secret, fax_job, quote


async def test_async_send_and_wait(aclient: AsyncClient, api: respx.MockRouter) -> None:
    api.post("/v1/fax-quotes").respond(201, json=quote())
    post = api.post("/v1/faxes").respond(201, json=fax_job())
    get = api.get(f"/v1/faxes/{fax_job()['id']}")
    get.side_effect = [
        httpx.Response(200, json=fax_job(state="active")),
        httpx.Response(200, json=fax_job(state="delivered", pages_sent=1, evidence={"pages": 1})),
    ]
    q = await aclient.quotes.create(document_id="doc_" + "2" * 32, to="+15550000001")
    fax = await aclient.faxes.create(quote_id=q.id)
    assert fax.state == "queued"
    assert len(post.calls.last.request.headers["idempotency-key"]) == 36
    done = await aclient.faxes.wait(fax.id, interval=0.0)
    assert done.state == "delivered" and done.evidence


async def test_async_retry_on_429(aclient: AsyncClient, api: respx.MockRouter) -> None:
    route = api.get("/v1/balance")
    route.side_effect = [
        httpx.Response(
            429, json={"error": {"code": "rate_limited", "message": "slow"}}, headers={"Retry-After": "0"}
        ),
        httpx.Response(
            200, json={"environment": "test", "currency": "USD", "available_micro": 5, "held_micro": 0}
        ),
    ]
    b = await aclient.billing.balance()
    assert b.available_micro == 5 and len(route.calls) == 2


async def test_async_iter_follows_cursor(aclient: AsyncClient, api: respx.MockRouter) -> None:
    route = api.get("/v1/faxes")
    route.side_effect = [
        httpx.Response(200, json={"items": [fax_job(id="fax_a")], "next_cursor": "c2"}),
        httpx.Response(200, json={"items": [fax_job(id="fax_b")], "next_cursor": None}),
    ]
    ids = [job.id async for job in aclient.faxes.iter(state="failed", limit=1)]
    assert ids == ["fax_a", "fax_b"]
    assert "cursor=c2" in str(route.calls.last.request.url)
    assert "state=failed" in str(route.calls.last.request.url)


async def test_complete_upload_sends_empty_json(aclient: AsyncClient, api: respx.MockRouter) -> None:
    import json
    from pathlib import Path

    cases = json.loads((Path(__file__).parent / "fixtures/responses.json").read_text())
    upload = next(case["body"] for case in cases if case["model"] == "UploadSession")
    route = api.post("/v1/uploads/upl_1/complete").respond(200, json=upload)
    await aclient.documents.complete_upload("upl_1")
    assert route.calls.last.request.content == b"{}"
    assert route.calls.last.request.headers["content-type"] == "application/json"


async def test_async_receipt_pdf_and_auto_send(aclient: AsyncClient, api: respx.MockRouter) -> None:
    api.get("/v1/faxes/fax_1/receipt.pdf").respond(
        200, content=b"%PDF", headers={"content-type": "application/pdf"}
    )
    assert await aclient.faxes.get_receipt_pdf("fax_1") == b"%PDF"
    setting = {
        "environment": "test",
        "enabled": True,
        "max_amount_micro": 5000000,
        "daily_max_jobs": 20,
        "policy_id": "pol_1",
        "version": 1,
    }
    route = api.put("/v1/organizations/current/auto-send").respond(200, json=setting)
    out = await aclient.policies.set_auto_send(enabled=True)
    assert out.policy_id == "pol_1"
    assert route.calls.last.request.content == b'{"enabled":true}'


async def test_async_oauth_grants_iter(aclient: AsyncClient, api: respx.MockRouter) -> None:
    api.get("/v1/oauth/grants").respond(200, json={"items": [], "next_cursor": None})
    assert [g.id async for g in aclient.oauth.iter_grants()] == []


async def test_async_create_api_key_sends_connected_app_only_when_given(
    aclient: AsyncClient, api: respx.MockRouter
) -> None:
    import json

    route = api.post("/v1/api-keys")
    route.side_effect = [
        httpx.Response(201, json=api_key_with_secret(connected_app=True)),
        httpx.Response(201, json=api_key_with_secret()),
    ]
    made = await aclient.identity.create_api_key("gemini", connected_app=True, idempotency_key="k1")
    assert made.connected_app is True
    assert json.loads(route.calls.last.request.content) == {
        "label": "gemini",
        "environment": "test",
        "connected_app": True,
    }
    assert route.calls.last.request.headers["idempotency-key"] == "k1"
    assert (await aclient.identity.create_api_key("plain")).connected_app is False
    assert "connected_app" not in json.loads(route.calls.last.request.content)


async def test_async_receipts_and_an_upload_without_a_type(
    aclient: AsyncClient, api: respx.MockRouter
) -> None:
    receipt = {
        "url": "https://pay.example.test/receipts/1",
        "amount_micro": 25_000_000,
        "amount_refunded_micro": 0,
        "currency": "USD",
        "payment_intent_id": "pi_1",
        "paid_at": None,
    }
    api.get("/v1/invoices/in_1/receipt").respond(200, json={**receipt, "invoice_id": "in_1"})
    api.get("/v1/topup-sessions/tus_1/receipt").respond(200, json={**receipt, "topup_session_id": "tus_1"})
    assert (await aclient.billing.get_invoice_receipt("in_1")).invoice_id == "in_1"
    assert (await aclient.billing.get_topup_receipt("tus_1")).topup_session_id == "tus_1"
    create = api.post("/v1/uploads").respond(
        201,
        json={
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
        },
    )
    session = await aclient.documents.create_upload(filename="scan.png", size_bytes=9)
    assert session.content_type == "image/png"
    assert b"content_type" not in create.calls.last.request.content


async def test_async_statement_pages_and_csv(aclient: AsyncClient, api: respx.MockRouter) -> None:
    line = {
        "id": "led_1",
        "created_at": "2026-11-15T09:00:00Z",
        "type": "fax",
        "description": "Fax to +15550000001",
        "amount_micro": -39_000,
        "balance_after_micro": 99_961_000,
        "ref_type": "fax_job",
        "ref_id": "fax_1",
    }
    route = api.get("/v1/statement")
    route.side_effect = [
        httpx.Response(200, json={"items": [line], "next_cursor": "led_1"}),
        httpx.Response(200, json={"items": [{**line, "id": "led_0"}], "next_cursor": None}),
    ]
    ids = [item.id async for item in aclient.billing.iter_statement(from_="2026-11-01")]
    assert ids == ["led_1", "led_0"]
    assert dict(route.calls[0].request.url.params) == {"from": "2026-11-01"}
    assert dict(route.calls[1].request.url.params) == {"from": "2026-11-01", "cursor": "led_1"}
    api.get("/v1/statement.csv").respond(200, content=b"date,amount\n", headers={"content-type": "text/csv"})
    assert await aclient.billing.statement_csv(kind="fax") == "date,amount\n"


async def test_async_new_writes(aclient: AsyncClient, api: respx.MockRouter) -> None:
    import json

    from tests.conftest import document

    doc_id = document()["id"]
    rename = api.patch(f"/v1/documents/{doc_id}").respond(200, json=document(filename="lease.pdf"))
    assert (await aclient.documents.rename(doc_id, filename="lease")).filename == "lease.pdf"
    assert json.loads(rename.calls.last.request.content) == {"filename": "lease"}
    api.delete("/v1/agents/agent_1").respond(
        200, json={"id": "agent_1", "deleted": False, "archived": True, "keys_revoked": 1}
    )
    assert (await aclient.agents.archive("agent_1")).keys_revoked == 1
    api.delete("/v1/budgets/bud_1").respond(200, json={"id": "bud_1", "deleted": True, "archived": False})
    assert (await aclient.billing.delete_budget("bud_1")).deleted is True
    check = api.post("/v1/policy-rule-checks").respond(200, json={"already_allowed": []})
    assert (await aclient.policies.check_rules(allowed_country_prefixes=["+1"])).already_allowed == []
    assert json.loads(check.calls.last.request.content) == {"allowed_country_prefixes": ["+1"]}
    link = {"id": "inv_1", "url": "https://app.example.test/join/x", "expires_at": "2026-11-22T00:00:00Z"}
    api.post("/v1/invitations/inv_1/link").respond(200, json=link)
    assert (await aclient.members.link_invitation("inv_1")).url.endswith("/join/x")
    withdrawn = {
        "id": "orq_1",
        "kind": "deletion",
        "status": "withdrawn",
        "created_at": "2026-11-15T09:00:00Z",
        "due_at": "2026-12-15T09:00:00Z",
        "closed_at": "2026-11-15T10:00:00Z",
    }
    api.delete("/v1/organizations/current/deletion-requests/orq_1").respond(200, json=withdrawn)
    assert (await aclient.organizations.withdraw_data_deletion("orq_1")).status == "withdrawn"
    api.get("/v1/organizations/current/data-requests").respond(200, json={"items": [withdrawn]})
    assert (await aclient.organizations.list_data_requests()).items[0].id == "orq_1"


async def test_async_filters_and_one_time_reads(aclient: AsyncClient, api: respx.MockRouter) -> None:
    faxes = api.get("/v1/faxes").respond(200, json={"items": [fax_job()], "next_cursor": None})
    assert len([job async for job in aclient.faxes.iter(q="+15550000001")]) == 1
    assert dict(faxes.calls.last.request.url.params) == {"q": "+15550000001"}
    inbox = api.get("/v1/inbox").respond(200, json={"items": [], "next_cursor": None, "unread": 0})
    assert (await aclient.inbox.list(archived=True)).unread == 0
    assert dict(inbox.calls.last.request.url.params) == {"archived": "true"}
    preview = api.post("/v1/one-time-sends/preview").respond(
        200, json={"document_pages": 1, "thumbnail_png_base64": None}
    )
    assert (await aclient.one_time_sends.preview(text="Hello")).document_pages == 1
    assert preview.calls.last.request.headers["authorization"].startswith("Bearer ffa_test_")
    page = api.get("/v1/one-time-sends/oto_1/pages/2").respond(
        200, content=b"\x89PNG", headers={"content-type": "image/png"}
    )
    assert await aclient.one_time_sends.get_page("oto_1", 2) == b"\x89PNG"
    assert "x-ffa-order-token" not in page.calls.last.request.headers
