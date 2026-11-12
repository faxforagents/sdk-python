"""The one-time send: one fax paid by card. Requests and answers against fixtures/one-time.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from faxforagents import AsyncClient, Client, NotFoundError, PaymentRequiredError
from tests.conftest import BASE, KEY, error

CASES: dict[str, Any] = {
    case["name"]: case["body"]
    for case in json.loads((Path(__file__).parent / "fixtures/one-time.json").read_text())
}
ORDER = CASES["draft"]["id"]
TOKEN = CASES["created"]["status_token"]


@pytest.fixture
def keyless(api: respx.MockRouter) -> Any:
    with Client(base_url=BASE, backoff_base=0.0, max_retry_after=0.0, keyless=True) as c:
        yield c


def test_config_needs_no_key(keyless: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/one-time-sends/config").respond(200, json=CASES["config"])
    config = keyless.one_time_sends.config()
    assert config.enabled is True
    assert config.price_version == "launch-v2"
    assert config.routes["us_ca"].flat_micro == 2490000
    assert config.routes["us_ca"].included_pages == 5
    assert "authorization" not in route.calls.last.request.headers


def test_keyless_client_ignores_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAXFORAGENTS_API_KEY", KEY)
    with Client(keyless=True) as c:
        assert c.settings.api_key is None
        assert "Authorization" not in c.settings.headers()


def test_create_from_text_is_json(keyless: Client, api: respx.MockRouter) -> None:
    route = api.post("/v1/one-time-sends").respond(201, json=CASES["created"])
    order = keyless.one_time_sends.create(
        to="+12025550123",
        sender_name="Dana Example",
        accept_aup=True,
        email="sender@example.com",
        text="# Signed form",
        text_format="markdown",
        subject="Signed form",
    )
    assert order.id == ORDER
    assert order.next == "checkout"
    assert order.status_token == TOKEN
    assert order.price.flat_micro == 2490000
    sent = route.calls.last.request
    assert sent.headers["content-type"] == "application/json"
    assert json.loads(sent.content) == {
        "to": "+12025550123",
        "sender_name": "Dana Example",
        "accept_aup": True,
        "email": "sender@example.com",
        "subject": "Signed form",
        "text": "# Signed form",
        "text_format": "markdown",
    }
    assert sent.headers["idempotency-key"]
    assert "authorization" not in sent.headers


def test_create_from_a_file_is_multipart(client: Client, api: respx.MockRouter, tmp_path: Path) -> None:
    route = api.post("/v1/one-time-sends").respond(201, json=CASES["created"])
    path = tmp_path / "form.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    client.one_time_sends.create(
        to="+12025550123",
        sender_name="Dana Example",
        accept_aup=True,
        file=path,
        delete_after_send=True,
        idempotency_key="order-1",
    )
    sent = route.calls.last.request
    assert sent.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert sent.headers["idempotency-key"] == "order-1"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    body = sent.content
    assert b'name="file"; filename="form.pdf"' in body
    assert b"Content-Type: application/pdf" in body
    assert b"%PDF-1.4 test" in body
    assert b'name="accept_aup"\r\n\r\ntrue' in body
    assert b'name="delete_after_send"\r\n\r\ntrue' in body
    assert b'name="email"' not in body


def test_create_file_bytes_and_content_type_by_suffix(client: Client, api: respx.MockRouter) -> None:
    route = api.post("/v1/one-time-sends").respond(201, json=CASES["created"])
    client.one_time_sends.create(
        to="+12025550123", sender_name="Dana Example", accept_aup=True, file=b"II*", filename="scan.tiff"
    )
    body = route.calls.last.request.content
    assert b'filename="scan.tiff"' in body
    assert b"Content-Type: image/tiff" in body


def test_create_needs_exactly_one_of_file_or_text(client: Client) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        client.one_time_sends.create(to="+12025550123", sender_name="Dana", accept_aup=True)
    with pytest.raises(ValueError, match="exactly one"):
        client.one_time_sends.create(
            to="+12025550123", sender_name="Dana", accept_aup=True, file=b"x", text="y"
        )


def test_create_from_quote(client: Client, api: respx.MockRouter) -> None:
    route = api.post("/v1/one-time-sends/from-quote").respond(201, json=CASES["created"])
    order = client.one_time_sends.create_from_quote(
        quote_id="quote_" + "6" * 32, sender_name="Dana Example", accept_aup=True, approval_id="apr_1"
    )
    assert order.status_url and order.status_url.endswith(TOKEN)
    assert json.loads(route.calls.last.request.content) == {
        "quote_id": "quote_" + "6" * 32,
        "sender_name": "Dana Example",
        "accept_aup": True,
        "approval_id": "apr_1",
    }


def test_get_sends_the_order_token(keyless: Client, api: respx.MockRouter) -> None:
    route = api.get(f"/v1/one-time-sends/{ORDER}").respond(200, json=CASES["checkout_open"])
    order = keyless.one_time_sends.get(ORDER, order_token=TOKEN)
    assert order.status == "checkout_open"
    assert order.checkout_url
    sent = route.calls.last.request
    assert sent.headers["x-ffa-order-token"] == TOKEN
    assert "authorization" not in sent.headers


def test_get_with_a_key_sends_no_token_header(client: Client, api: respx.MockRouter) -> None:
    route = api.get(f"/v1/one-time-sends/{ORDER}").respond(200, json=CASES["captured"])
    order = client.one_time_sends.get(ORDER)
    assert order.next == "done"
    assert order.captured_micro == 2490000
    assert order.card is not None and order.card.last4 == "4242"
    assert order.fax is not None and order.fax["state"] == "delivered"
    assert "x-ffa-order-token" not in route.calls.last.request.headers


def test_a_wrong_token_is_not_found(keyless: Client, api: respx.MockRouter) -> None:
    api.get(f"/v1/one-time-sends/{ORDER}").respond(404, json=CASES["not_found"])
    with pytest.raises(NotFoundError) as exc:
        keyless.one_time_sends.get(ORDER, order_token="0.wrong")
    assert exc.value.code == "not_found"


def test_list_and_iter(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/one-time-sends").respond(200, json=CASES["page"])
    page = client.one_time_sends.list(status="captured", limit=10)
    assert [o.id for o in page.items] == [ORDER]
    assert dict(route.calls.last.request.url.params) == {"status": "captured", "limit": "10"}
    assert [o.id for o in client.one_time_sends.iter()] == [ORDER]


def test_checkout(keyless: Client, api: respx.MockRouter) -> None:
    route = api.post(f"/v1/one-time-sends/{ORDER}/checkout").respond(201, json=CASES["checkout"])
    opened = keyless.one_time_sends.checkout(ORDER, order_token=TOKEN)
    assert opened.checkout_url == CASES["checkout"]["checkout_url"]
    assert opened.amount_micro == 2490000
    sent = route.calls.last.request
    assert sent.headers["x-ffa-order-token"] == TOKEN
    assert sent.headers["idempotency-key"]


def test_read_by_token_posts_the_token_in_the_body(keyless: Client, api: respx.MockRouter) -> None:
    route = api.post("/v1/one-time-sends/by-token").respond(200, json=CASES["by_token"])
    summary = keyless.one_time_sends.read_by_token("mail-token")
    assert summary.order_id == ORDER
    sent = route.calls.last.request
    assert json.loads(sent.content) == {"token": "mail-token"}
    assert "mail-token" not in str(sent.url)
    assert "authorization" not in sent.headers


def test_get_by_token_still_works_and_warns(keyless: Client, api: respx.MockRouter) -> None:
    api.get("/v1/one-time-sends/by-token/mail-token").respond(200, json=CASES["by_token"])
    with pytest.warns(DeprecationWarning, match="read_by_token"):
        summary = keyless.one_time_sends.get_by_token("mail-token")
    assert summary.order_id == ORDER


def test_from_quote_sends_accept_aup_only_when_given(client: Client, api: respx.MockRouter) -> None:
    created = {**CASES["created"], "aup_accepted": False}
    route = api.post("/v1/one-time-sends/from-quote").respond(201, json=created)
    order = client.one_time_sends.create_from_quote(
        quote_id="quote_" + "6" * 32, sender_name="Dana Example", approval_id="apr_1"
    )
    assert order.aup_accepted is False
    assert order.status_url
    assert json.loads(route.calls.last.request.content) == {
        "quote_id": "quote_" + "6" * 32,
        "sender_name": "Dana Example",
        "approval_id": "apr_1",
    }


def test_checkout_accept_aup(keyless: Client, api: respx.MockRouter) -> None:
    route = api.post(f"/v1/one-time-sends/{ORDER}/checkout").respond(201, json=CASES["checkout"])
    keyless.one_time_sends.checkout(ORDER, order_token=TOKEN)
    assert json.loads(route.calls.last.request.content) == {}
    keyless.one_time_sends.checkout(ORDER, order_token=TOKEN, accept_aup=True)
    assert json.loads(route.calls.last.request.content) == {"accept_aup": True}
    assert route.calls.last.request.headers["x-ffa-order-token"] == TOKEN


def test_queue_and_new_order_fields(keyless: Client, api: respx.MockRouter) -> None:
    queue = {"queued": True, "jobs_ahead": 7, "estimated_wait_minutes": 14}
    api.get("/v1/one-time-sends/config").respond(200, json={**CASES["config"], "queue": queue})
    config = keyless.one_time_sends.config()
    assert config.queue is not None
    assert config.queue.queued is True
    assert config.queue.estimated_wait_minutes == 14
    body = {
        **CASES["checkout_open"],
        "queue": queue,
        "aup_accepted": False,
        "status_url": "https://faxforagents.com/send/status/x",
    }
    api.get(f"/v1/one-time-sends/{ORDER}").respond(200, json=body)
    order = keyless.one_time_sends.get(ORDER, order_token=TOKEN)
    assert order.queue is not None and order.queue.jobs_ahead == 7
    assert order.aup_accepted is False
    assert order.status_url == "https://faxforagents.com/send/status/x"
    api.get(f"/v1/one-time-sends/{ORDER}").respond(200, json=CASES["checkout_open"])
    older = keyless.one_time_sends.get(ORDER, order_token=TOKEN)
    assert older.queue is None
    assert older.aup_accepted is True


def test_balance_top_up_url(client: Client, api: respx.MockRouter) -> None:
    api.get("/v1/balance").respond(
        200,
        json={
            "available_micro": 0,
            "held_micro": 0,
            "currency": "USD",
            "environment": "live",
            "top_up_url": "https://app.faxforagents.com/app/billing",
        },
    )
    assert client.billing.balance().top_up_url == "https://app.faxforagents.com/app/billing"


def test_get_by_token_and_confirm(keyless: Client, api: respx.MockRouter) -> None:
    api.post("/v1/one-time-sends/by-token").respond(200, json=CASES["by_token"])
    summary = keyless.one_time_sends.read_by_token("mail-token")
    assert summary.state == "ok"
    assert summary.order_id == ORDER
    route = api.post(f"/v1/one-time-sends/{ORDER}/confirm").respond(200, json=CASES["confirmed"])
    done = keyless.one_time_sends.confirm(summary.order_id, token="mail-token")
    assert done.order.status == "released"
    assert done.order.next == "wait"
    assert done.signed_in is False
    assert json.loads(route.calls.last.request.content) == {"token": "mail-token"}


def test_resend_confirmation(keyless: Client, api: respx.MockRouter) -> None:
    route = api.post(f"/v1/one-time-sends/{ORDER}/resend-confirmation").respond(
        200, json=CASES["awaiting_confirmation"]
    )
    order = keyless.one_time_sends.resend_confirmation(ORDER, order_token=TOKEN)
    assert order.next == "confirm"
    assert order.resend_allowed_at == "2026-11-12T09:13:05Z"
    assert route.calls.last.request.headers["x-ffa-order-token"] == TOKEN


def test_receipt_pdf(keyless: Client, api: respx.MockRouter) -> None:
    route = api.get(f"/v1/one-time-sends/{ORDER}/receipt.pdf").respond(
        200, content=b"%PDF-1.7 receipt", headers={"content-type": "application/pdf"}
    )
    assert keyless.one_time_sends.get_receipt_pdf(ORDER, order_token=TOKEN) == b"%PDF-1.7 receipt"
    assert route.calls.last.request.headers["x-ffa-order-token"] == TOKEN


def test_wait_follows_next_until_done(keyless: Client, api: respx.MockRouter) -> None:
    api.get(f"/v1/one-time-sends/{ORDER}").mock(
        side_effect=[
            httpx.Response(200, json=CASES["checkout_open"]),
            httpx.Response(200, json=CASES["awaiting_confirmation"]),
            httpx.Response(200, json=CASES["captured"]),
        ]
    )
    order = keyless.one_time_sends.wait(ORDER, order_token=TOKEN, interval=0.0)
    assert order.status == "captured"
    assert order.next == "done"


def test_wait_until_a_step(keyless: Client, api: respx.MockRouter) -> None:
    route = api.get(f"/v1/one-time-sends/{ORDER}").mock(
        side_effect=[
            httpx.Response(200, json=CASES["checkout_open"]),
            httpx.Response(200, json=CASES["awaiting_confirmation"]),
            httpx.Response(200, json=CASES["captured"]),
        ]
    )
    order = keyless.one_time_sends.wait(ORDER, until="confirm", order_token=TOKEN, interval=0.0)
    assert order.next == "confirm"
    assert route.call_count == 2


def test_wait_returns_the_last_order_on_timeout(keyless: Client, api: respx.MockRouter) -> None:
    api.get(f"/v1/one-time-sends/{ORDER}").respond(200, json=CASES["checkout_open"])
    order = keyless.one_time_sends.wait(ORDER, order_token=TOKEN, timeout=0.0, interval=0.0)
    assert order.next == "checkout"


def test_insufficient_balance_one_time_offer(client: Client, api: respx.MockRouter) -> None:
    plain = error("insufficient_balance", details={"available_micro": 0, "needed_micro": 78000})
    api.post("/v1/faxes").respond(402, json=plain)
    with pytest.raises(PaymentRequiredError) as exc:
        client.faxes.create(quote_id="quote_" + "6" * 32)
    assert exc.value.details["needed_micro"] == 78000
    assert exc.value.one_time_offer is None
    assert exc.value.top_up_url is None
    offer = {
        "amount_micro": 2490000,
        "currency": "USD",
        "document_pages": 2,
        "included_pages": 5,
        "extra_page_micro": 490000,
        "max_document_pages": 20,
        "approval_id": "apr_1",
    }
    with_offer = error(
        "insufficient_balance",
        details={
            "available_micro": 0,
            "needed_micro": 78000,
            "top_up_url": "https://app.faxforagents.com/app/billing",
            "one_time_offer": offer,
        },
    )
    api.post("/v1/faxes").respond(402, json=with_offer)
    with pytest.raises(PaymentRequiredError) as exc:
        client.faxes.create(quote_id="quote_" + "6" * 32)
    assert exc.value.one_time_offer == offer
    found = exc.value.one_time_offer
    assert found is not None
    assert found["amount_micro"] == 2490000
    assert found["approval_id"] == "apr_1"
    assert exc.value.top_up_url == "https://app.faxforagents.com/app/billing"


async def test_async_flow(api: respx.MockRouter) -> None:
    api.post("/v1/one-time-sends").respond(201, json=CASES["created"])
    api.post(f"/v1/one-time-sends/{ORDER}/checkout").respond(201, json=CASES["checkout"])
    read = api.get(f"/v1/one-time-sends/{ORDER}").mock(
        side_effect=[
            httpx.Response(200, json=CASES["awaiting_confirmation"]),
            httpx.Response(200, json=CASES["captured"]),
        ]
    )
    async with AsyncClient(base_url=BASE, backoff_base=0.0, max_retry_after=0.0, keyless=True) as c:
        order = await c.one_time_sends.create(
            to="+12025550123",
            sender_name="Dana Example",
            accept_aup=True,
            email="s@example.com",
            file=b"%PDF",
        )
        opened = await c.one_time_sends.checkout(order.id, order_token=order.status_token)
        assert opened.checkout_url
        done = await c.one_time_sends.wait(order.id, order_token=order.status_token, interval=0.0)
        assert done.next == "done"
    assert read.calls.last.request.headers["x-ffa-order-token"] == TOKEN
    assert "authorization" not in read.calls.last.request.headers
