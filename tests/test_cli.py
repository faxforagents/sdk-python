"""The faxforagents command, driven through Typer's runner against the mocked API."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from faxforagents.cli import app
from tests.conftest import BASE, KEY, document, fax_job, quote

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAXFORAGENTS_API_KEY", KEY)
    monkeypatch.setenv("FAXFORAGENTS_API_URL", BASE)


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0 and result.output.strip() == "0.1.0"


def test_no_key_is_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAXFORAGENTS_API_KEY")
    result = runner.invoke(app, ["balance"])
    assert result.exit_code == 2
    assert "FAXFORAGENTS_API_KEY" in result.output


def test_balance(api: respx.MockRouter) -> None:
    api.get("/v1/balance").respond(
        200,
        json={"environment": "test", "currency": "USD", "available_micro": 100_000_000, "held_micro": 39000},
    )
    result = runner.invoke(app, ["balance"])
    assert result.exit_code == 0
    assert "available $100.000, held $0.039" in result.output


def test_fax_send_wait_and_json(api: respx.MockRouter) -> None:
    post = api.post("/v1/faxes").respond(201, json=fax_job())
    get = api.get(f"/v1/faxes/{fax_job()['id']}")
    get.side_effect = [
        httpx.Response(200, json=fax_job(state="active")),
        httpx.Response(200, json=fax_job(state="delivered", pages_sent=1, evidence={"pages": 1})),
        httpx.Response(200, json=fax_job(state="delivered", pages_sent=1, evidence={"pages": 1})),
    ]
    result = runner.invoke(app, ["fax", "send", "--quote", "quote_x", "--wait"])
    assert result.exit_code == 0, result.output
    assert "delivered" in result.output and "evidence=yes" in result.output
    assert post.calls.last.request.headers["idempotency-key"] == "cli:quote_x"

    result = runner.invoke(app, ["fax", "status", fax_job()["id"], "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["state"] == "delivered"


def test_api_error_is_exit_1(api: respx.MockRouter) -> None:
    api.post("/v1/fax-quotes").respond(
        404, json={"error": {"code": "not_found", "message": "no such document", "correlation_id": "req_9"}}
    )
    result = runner.invoke(app, ["fax", "quote", "--document", "doc_nope", "--to", "+15550000001"])
    assert result.exit_code == 1
    assert "not_found" in result.output and "req_9" in result.output


def test_documents_upload_text(api: respx.MockRouter, tmp_path: object) -> None:
    import pathlib

    p = pathlib.Path(str(tmp_path)) / "hello.txt"
    p.write_text("Hello from the CLI.")
    route = api.post("/v1/documents").respond(201, json=document())
    result = runner.invoke(app, ["documents", "upload", str(p)])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "text": "Hello from the CLI.",
        "filename": "hello",
    }
    assert "pages=1" in result.output


def test_quote_renders_amount(api: respx.MockRouter) -> None:
    api.post("/v1/fax-quotes").respond(201, json=quote(amount_micro=39000))
    result = runner.invoke(app, ["fax", "quote", "--document", "doc_1", "--to", "+15550000001"])
    assert result.exit_code == 0
    assert "39000 micro ($0.039)" in result.output
