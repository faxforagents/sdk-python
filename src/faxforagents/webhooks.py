"""Verify the signature on a webhook delivery (Standard Webhooks, https://www.standardwebhooks.com).

Copied from the server's ``webhooks/signing.py`` (the reference implementation, 2026-10-28). The server
sends three headers on every delivery. ``webhook-id`` is the event id, stable across retries and
replays. ``webhook-timestamp`` is unix seconds. ``webhook-signature`` is one or more space-separated
``v1,<base64(HMAC-SHA256(secret, "{id}.{timestamp}.{body}"))>`` values. During a secret rotation two
signatures are sent, one per live secret, for 24 hours. Secrets are ``whsec_`` followed by base64.

Usage, in any web framework::

    from faxforagents import verify_webhook, SignatureError

    try:
        event_id = verify_webhook(secret, request.headers, request.body_bytes)
    except SignatureError:
        return 400
    event = json.loads(request.body_bytes)   # {"id", "type", "data", ...}

Verify on the raw bytes, before any JSON parsing or re-serialization. Store ``event_id`` to drop a
redelivery you already handled.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

SECRET_PREFIX = "whsec_"
TOLERANCE_S = 300


class SignatureError(ValueError):
    """The delivery is not signed by this secret, or its timestamp is outside the tolerance."""


class WebhookEvent(BaseModel):
    """The body of a delivery. ``data`` is the event payload, shaped by ``type``."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    created_at: str
    schema_version: int
    org_id: str
    environment: str
    project_id: str | None = None
    resource: dict[str, Any]
    fax_job_id: str | None = None
    correlation_id: str | None = None
    data: dict[str, Any]


def _key(secret: str) -> bytes:
    raw = secret[len(SECRET_PREFIX) :] if secret.startswith(SECRET_PREFIX) else secret
    return base64.b64decode(raw)


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    """The signature the server computes. Exposed so a test receiver can forge a valid delivery."""
    to_sign = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(_key(secret), to_sign, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def signature_headers(
    secrets: list[str], msg_id: str, body: bytes, *, timestamp: int | None = None
) -> dict[str, str]:
    """The three headers of a delivery, for tests and local tooling."""
    ts = int(time.time()) if timestamp is None else timestamp
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": " ".join(sign(s, msg_id, ts, body) for s in secrets),
        "content-type": "application/json",
    }


def verify_webhook(
    secret: str,
    headers: Mapping[str, str],
    body: bytes | str,
    *,
    now: float | None = None,
    tolerance_s: int = TOLERANCE_S,
) -> str:
    """Return the event id when a signature matches and the timestamp is fresh. Raise ``SignatureError``."""
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    msg_id = lower.get("webhook-id", "")
    ts_raw = lower.get("webhook-timestamp", "")
    sigs = lower.get("webhook-signature", "")
    if not msg_id or not ts_raw or not sigs:
        raise SignatureError("missing webhook headers")
    try:
        ts = int(ts_raw)
    except ValueError as exc:
        raise SignatureError("bad timestamp") from exc
    current = time.time() if now is None else now
    if abs(current - ts) > tolerance_s:
        raise SignatureError("timestamp outside tolerance")
    expected = sign(secret, msg_id, ts, raw)
    for candidate in sigs.split():
        if candidate.startswith("v1,") and hmac.compare_digest(candidate, expected):
            return msg_id
    raise SignatureError("no matching signature")


def parse_event(body: bytes | str) -> WebhookEvent:
    """Parse a verified body. Call ``verify_webhook`` first."""
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    return WebhookEvent.model_validate(json.loads(raw))
