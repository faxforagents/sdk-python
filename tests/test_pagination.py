"""iter helpers follow next_cursor and pass the filters through on every page."""

from __future__ import annotations

import httpx
import respx

from faxforagents import Client
from tests.conftest import fax_job


def test_iter_follows_next_cursor(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/faxes")
    route.side_effect = [
        httpx.Response(200, json={"items": [fax_job(id="fax_a"), fax_job(id="fax_b")], "next_cursor": "c2"}),
        httpx.Response(200, json={"items": [fax_job(id="fax_c")], "next_cursor": "c3"}),
        httpx.Response(200, json={"items": [], "next_cursor": None}),
    ]
    ids = [job.id for job in client.faxes.iter(state="failed", limit=2)]
    assert ids == ["fax_a", "fax_b", "fax_c"]
    urls = [str(c.request.url) for c in route.calls]
    assert "cursor" not in urls[0] and "cursor=c2" in urls[1] and "cursor=c3" in urls[2]
    assert all("state=failed" in u and "limit=2" in u for u in urls)


def test_iter_stops_on_empty_first_page(client: Client, api: respx.MockRouter) -> None:
    route = api.get("/v1/inbox").respond(200, json={"items": [], "next_cursor": None})
    assert list(client.inbox.iter()) == []
    assert len(route.calls) == 1


def test_events_iter(client: Client, api: respx.MockRouter) -> None:
    event = {
        "id": "evt_1",
        "type": "fax.delivered",
        "created_at": "2026-10-28T00:00:00Z",
        "org_id": "org_1",
        "environment": "test",
        "project_id": None,
        "resource_type": "fax_job",
        "resource_id": "fax_1",
        "fax_job_id": "fax_1",
        "schema_version": 1,
        "correlation_id": None,
        "data": {"state": "delivered"},
    }
    route = api.get("/v1/events")
    route.side_effect = [
        httpx.Response(200, json={"items": [event], "next_cursor": "n"}),
        httpx.Response(200, json={"items": [], "next_cursor": None}),
    ]
    assert [e.type for e in client.events.iter(type="fax.delivered")] == ["fax.delivered"]
    assert "type=fax.delivered" in str(route.calls.last.request.url)
