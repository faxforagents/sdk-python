"""The async client behaves like the sync one: same headers, same retries, same pagination."""

from __future__ import annotations

import httpx
import respx

from faxforagents import AsyncClient
from tests.conftest import fax_job, quote


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
