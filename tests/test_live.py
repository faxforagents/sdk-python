"""One real pass against https://api.faxforagents.com on the sandbox. Opt in with FAXFORAGENTS_API_KEY.

    FAXFORAGENTS_API_KEY=ffa_test_... pytest -q -m live

It uses a test key only. Nothing leaves the sandbox and nothing is charged. The test never prints the
key, and it asserts the states the product promises: queued on send, delivered with evidence on
+15550000001, the same job back under the same Idempotency-Key.
"""

from __future__ import annotations

import os
import uuid

import pytest

from faxforagents import Client

pytestmark = pytest.mark.live

KEY = os.environ.get("FAXFORAGENTS_API_KEY", "")
skip_without_key = pytest.mark.skipif(
    not KEY.startswith("ffa_test_"), reason="FAXFORAGENTS_API_KEY (a test key) is not set"
)


@skip_without_key
def test_sandbox_send_is_delivered_with_evidence() -> None:
    with Client(KEY, base_url="https://api.faxforagents.com") as ffa:
        me = ffa.me()
        assert me.environment == "test"
        caps = ffa.platform.capabilities()
        assert caps.provider == "sandbox" and "us_ca" in caps.routes

        doc = ffa.documents.create(text="Live test from the Python SDK.", filename="sdk-live")
        assert doc.page_count >= 1 and len(doc.sha256) == 64

        q = ffa.quotes.create(document_id=doc.id, to="+15550000001")
        assert q.amount_micro == 0 and q.authorization_required is False

        key = f"sdk-live-{uuid.uuid4()}"
        fax = ffa.faxes.create(quote_id=q.id, idempotency_key=key)
        assert fax.state == "queued"
        again = ffa.faxes.create(quote_id=q.id, idempotency_key=key)
        assert again.id == fax.id

        done = ffa.faxes.wait(fax.id, timeout=120, interval=2)
        assert done.state == "delivered", done.failure_code
        assert done.evidence, "delivered without evidence"
        assert done.pages_sent == done.billable_pages

        receipt = ffa.faxes.get_receipt(fax.id)
        assert receipt.state == "delivered" and receipt.evidence

        page = ffa.faxes.list(limit=1)
        assert page.items and page.items[0].id

        balance = ffa.billing.balance()
        assert balance.environment == "test"
