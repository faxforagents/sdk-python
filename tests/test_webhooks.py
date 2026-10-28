"""The Standard Webhooks check, against vectors produced with the server's own scheme."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from faxforagents import SignatureError, parse_event, verify_webhook
from faxforagents.webhooks import sign, signature_headers

SECRET = "whsec_" + base64.b64encode(b"k" * 24).decode()
OTHER = "whsec_" + base64.b64encode(b"o" * 24).decode()
BODY = json.dumps(
    {
        "id": "evt_1",
        "type": "fax.delivered",
        "created_at": "2026-10-28T14:02:00Z",
        "schema_version": 1,
        "org_id": "org_1",
        "environment": "test",
        "project_id": None,
        "resource": {"type": "fax_job", "id": "fax_1"},
        "fax_job_id": "fax_1",
        "correlation_id": None,
        "data": {"state": "delivered", "pages_sent": 3},
    },
    separators=(",", ":"),
    sort_keys=True,
).encode()
NOW = 1_800_000_000


def test_signature_matches_the_reference_formula() -> None:
    expected = hmac.new(b"k" * 24, f"evt_1.{NOW}.".encode() + BODY, hashlib.sha256).digest()
    assert sign(SECRET, "evt_1", NOW, BODY) == "v1," + base64.b64encode(expected).decode()


def test_verify_roundtrip() -> None:
    headers = signature_headers([SECRET], "evt_1", BODY, timestamp=NOW)
    assert verify_webhook(SECRET, headers, BODY, now=NOW) == "evt_1"
    assert (
        verify_webhook(SECRET, {k.upper(): v for k, v in headers.items()}, BODY.decode(), now=NOW + 100)
        == "evt_1"
    )


def test_tampered_body_fails() -> None:
    headers = signature_headers([SECRET], "evt_1", BODY, timestamp=NOW)
    with pytest.raises(SignatureError, match="no matching signature"):
        verify_webhook(SECRET, headers, BODY.replace(b"delivered", b"failed"), now=NOW)


def test_wrong_secret_fails() -> None:
    headers = signature_headers([SECRET], "evt_1", BODY, timestamp=NOW)
    with pytest.raises(SignatureError):
        verify_webhook(OTHER, headers, BODY, now=NOW)


def test_rotation_sends_two_signatures_and_either_verifies() -> None:
    headers = signature_headers([SECRET, OTHER], "evt_1", BODY, timestamp=NOW)
    assert len(headers["webhook-signature"].split()) == 2
    assert verify_webhook(SECRET, headers, BODY, now=NOW) == "evt_1"
    assert verify_webhook(OTHER, headers, BODY, now=NOW) == "evt_1"


def test_stale_timestamp_fails() -> None:
    headers = signature_headers([SECRET], "evt_1", BODY, timestamp=NOW)
    with pytest.raises(SignatureError, match="tolerance"):
        verify_webhook(SECRET, headers, BODY, now=NOW + 301)
    assert verify_webhook(SECRET, headers, BODY, now=NOW + 301, tolerance_s=600) == "evt_1"


def test_missing_or_bad_headers_fail() -> None:
    with pytest.raises(SignatureError, match="missing"):
        verify_webhook(SECRET, {}, BODY, now=NOW)
    headers = signature_headers([SECRET], "evt_1", BODY, timestamp=NOW)
    with pytest.raises(SignatureError, match="bad timestamp"):
        verify_webhook(SECRET, {**headers, "webhook-timestamp": "soon"}, BODY, now=NOW)


def test_parse_event() -> None:
    event = parse_event(BODY)
    assert event.type == "fax.delivered" and event.resource == {"type": "fax_job", "id": "fax_1"}
    assert event.data["pages_sent"] == 3
