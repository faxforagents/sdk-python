"""Billing iterators replay saved response shapes with controlled cursors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from faxforagents import AsyncClient, Client, ScopeError

CASES = json.loads((Path(__file__).parent / "fixtures/billing-pages.json").read_text())


def assert_requests(route: respx.Route, case: dict[str, Any]) -> None:
    assert len(route.calls) == 2
    first, second = [call.request.url.params for call in route.calls]
    assert "cursor" not in first
    assert second["cursor"] == case["pages"][0]["next_cursor"]
    assert first["limit"] == second["limit"] == "1"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_billing_pages(client: Client, api: respx.MockRouter, case: dict[str, Any]) -> None:
    route = api.get(case["route"])
    route.side_effect = [httpx.Response(200, json=page) for page in case["pages"]]
    iterator = getattr(client.billing, "iter_" + case["name"])(limit=1)
    assert not route.called
    items = list(iterator)
    assert [item.model_dump() for item in items] == [page["items"][0] for page in case["pages"]]
    assert_requests(route, case)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_async_billing_pages(aclient: AsyncClient, api: respx.MockRouter, case: dict[str, Any]) -> None:
    route = api.get(case["route"])
    route.side_effect = [httpx.Response(200, json=page) for page in case["pages"]]
    iterator = getattr(aclient.billing, "iter_" + case["name"])(limit=1)
    assert not route.called
    items = [item async for item in iterator]
    assert [item.model_dump() for item in items] == [page["items"][0] for page in case["pages"]]
    assert_requests(route, case)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_empty_billing_page(client: Client, api: respx.MockRouter, case: dict[str, Any]) -> None:
    route = api.get(case["route"]).respond(200, json={"items": [], "next_cursor": None})
    assert list(getattr(client.billing, "iter_" + case["name"])()) == []
    assert len(route.calls) == 1


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_async_empty_billing_page(
    aclient: AsyncClient, api: respx.MockRouter, case: dict[str, Any]
) -> None:
    route = api.get(case["route"]).respond(200, json={"items": [], "next_cursor": None})
    assert [item async for item in getattr(aclient.billing, "iter_" + case["name"])()] == []
    assert len(route.calls) == 1


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_billing_errors_propagate(client: Client, api: respx.MockRouter, case: dict[str, Any]) -> None:
    api.get(case["route"]).respond(403, json={"error": {"code": "scope_missing", "message": "refused"}})
    with pytest.raises(ScopeError):
        list(getattr(client.billing, "iter_" + case["name"])())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_async_billing_errors_propagate(
    aclient: AsyncClient, api: respx.MockRouter, case: dict[str, Any]
) -> None:
    api.get(case["route"]).respond(403, json={"error": {"code": "scope_missing", "message": "refused"}})
    with pytest.raises(ScopeError):
        _ = [item async for item in getattr(aclient.billing, "iter_" + case["name"])()]
