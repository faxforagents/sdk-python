"""The asynchronous client. The same surface as ``client.Client``, every method awaited.

Kept in step with ``client.py`` by ``tests/test_surface.py``, which compares the two method sets and
their signatures. When you change one, change the other.

    from faxforagents import AsyncClient

    async with AsyncClient() as ffa:             # reads FAXFORAGENTS_API_KEY
        doc = await ffa.documents.create(text="Hello from an agent.")
        quote = await ffa.quotes.create(document_id=doc.id, to="+15550000001")
        fax = await ffa.faxes.create(quote_id=quote.id)
        fax = await ffa.faxes.wait(fax.id)
        print(fax.state, fax.evidence)
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import httpx

from faxforagents import models as m
from faxforagents._http import AsyncHttp, Settings, apaginate
from faxforagents.errors import ConflictError

TERMINAL_STATES = frozenset({"delivered", "partial", "failed", "unknown"})


class _Resource:
    def __init__(self, http: AsyncHttp) -> None:
        self._h = http


class Identity(_Resource):
    async def me(self) -> m.Me:
        """Who the key is: organization, project, environment, scopes. Scope: any."""
        return await self._h.request("GET", "/v1/me", model=m.Me)

    async def list_api_keys(self) -> m.ApiKeyList:
        """Keys of the organization, metadata only. Scope: key:admin."""
        return await self._h.request("GET", "/v1/api-keys", model=m.ApiKeyList)

    async def create_api_key(
        self,
        label: str,
        *,
        environment: str = "test",
        scopes: list[str] | None = None,
        expires_in_days: int | None = None,
        idempotency_key: str | None = None,
    ) -> m.ApiKeyWithSecret:
        """Create a key. The secret is in the answer once. Scope: key:admin."""
        body = {
            "label": label,
            "environment": environment,
            "scopes": scopes,
            "expires_in_days": expires_in_days,
        }
        return await self._h.request(
            "POST", "/v1/api-keys", model=m.ApiKeyWithSecret, json=body, idempotency_key=idempotency_key
        )

    async def rotate_api_key(self, key_id: str, *, idempotency_key: str | None = None) -> m.ApiKeyWithSecret:
        """A new secret. The old one works 24 more hours. Scope: key:admin."""
        return await self._h.request(
            "POST", f"/v1/api-keys/{key_id}/rotate", model=m.ApiKeyWithSecret, idempotency_key=idempotency_key
        )

    async def revoke_api_key(self, key_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Revoke a key at once. Scope: key:admin."""
        return await self._h.request(
            "DELETE", f"/v1/api-keys/{key_id}", model=m.Deleted, idempotency_key=idempotency_key
        )


class Documents(_Resource):
    async def create(
        self,
        *,
        upload_id: str | None = None,
        text: str | None = None,
        markdown: str | None = None,
        html: str | None = None,
        filename: str | None = None,
        idempotency_key: str | None = None,
    ) -> m.Document:
        """An immutable document from one of upload_id, text, markdown or html. Scope: document:write."""
        body = m.DocumentCreate(
            upload_id=upload_id, text=text, markdown=markdown, html=html, filename=filename
        )
        return await self._h.request(
            "POST", "/v1/documents", model=m.Document, json=body, idempotency_key=idempotency_key
        )

    async def create_upload(
        self,
        *,
        filename: str,
        size_bytes: int,
        content_type: str = "application/pdf",
        idempotency_key: str | None = None,
    ) -> m.UploadSession:
        """Open an upload session with a signed PUT URL (25 MB, one hour). Scope: document:write."""
        body = m.UploadCreate(filename=filename, size_bytes=size_bytes, content_type=content_type)
        return await self._h.request(
            "POST", "/v1/uploads", model=m.UploadSession, json=body, idempotency_key=idempotency_key
        )

    async def put_upload_content(
        self, upload_url: str, data: bytes, *, content_type: str = "application/pdf"
    ) -> m.UploadSession:
        """Send the bytes to the signed URL from ``create_upload``. No scope, the link is the credential."""
        return await self._h.request(
            "PUT", upload_url, model=m.UploadSession, content=data, headers={"Content-Type": content_type}
        )

    async def complete_upload(
        self, upload_id: str, *, sha256: str | None = None, idempotency_key: str | None = None
    ) -> m.UploadSession:
        """Finalize an upload. The size and, when given, the sha256 are checked. Scope: document:write."""
        body = m.UploadComplete(sha256=sha256)
        return await self._h.request(
            "POST",
            f"/v1/uploads/{upload_id}/complete",
            model=m.UploadSession,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def cancel_upload(self, upload_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Cancel an incomplete upload. Scope: document:write."""
        return await self._h.request(
            "DELETE", f"/v1/uploads/{upload_id}", model=m.Deleted, idempotency_key=idempotency_key
        )

    async def upload(
        self, file: str | Path | bytes, *, filename: str | None = None, content_type: str = "application/pdf"
    ) -> m.Document:
        """The whole PDF path: create_upload, put the bytes, complete with the sha256, create the document."""
        if isinstance(file, bytes):
            data = file
            name = filename or "document.pdf"
        else:
            path = Path(file)
            data = path.read_bytes()
            name = filename or path.name
        session = await self.create_upload(filename=name, size_bytes=len(data), content_type=content_type)
        if not session.upload_url:
            raise ConflictError(
                status=409, code="upload_url_missing", message="the upload session carries no upload_url"
            )
        await self.put_upload_content(session.upload_url, data, content_type=content_type)
        await self.complete_upload(session.id, sha256=hashlib.sha256(data).hexdigest())
        return await self.create(upload_id=session.id, filename=name)

    async def list(self, *, cursor: str | None = None, limit: int | None = None) -> m.DocumentPage:
        """Documents, newest first. Scope: document:read."""
        return await self._h.request(
            "GET", "/v1/documents", model=m.DocumentPage, params={"cursor": cursor, "limit": limit}
        )

    def iter(self, *, limit: int | None = None) -> AsyncIterator[m.Document]:
        """Every document, following next_cursor."""
        return apaginate(self.list, {"limit": limit})

    async def get(self, document_id: str) -> m.Document:
        """Hash, pages and validation state. Scope: document:read."""
        return await self._h.request("GET", f"/v1/documents/{document_id}", model=m.Document)

    async def delete(self, document_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Delete the document, its bytes and its previews. Scope: document:write."""
        return await self._h.request(
            "DELETE", f"/v1/documents/{document_id}", model=m.Deleted, idempotency_key=idempotency_key
        )

    async def get_validation(self, document_id: str) -> m.DocumentValidation:
        """Validation warnings. Scope: document:read."""
        return await self._h.request(
            "GET", f"/v1/documents/{document_id}/validation", model=m.DocumentValidation
        )

    async def get_download(self, document_id: str) -> m.SignedUrl:
        """A signed download URL, valid ten minutes. Scope: document:read."""
        return await self._h.request("GET", f"/v1/documents/{document_id}/download", model=m.SignedUrl)

    async def download(self, document_id: str) -> bytes:
        """The PDF bytes, through a fresh signed URL."""
        link = await self.get_download(document_id)
        data = await self._h.request("GET", link.url)
        return bytes(data)

    async def list_pages(self, document_id: str) -> m.DocumentPages:
        """Rendered pages with signed preview URLs. Scope: document:read."""
        return await self._h.request("GET", f"/v1/documents/{document_id}/pages", model=m.DocumentPages)


class Quotes(_Resource):
    async def create(
        self,
        *,
        document_id: str,
        to: str,
        from_: str | None = None,
        cover: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> m.Quote:
        """The exact price of one document to one E.164 destination, valid ten minutes. Scope: fax:quote."""
        body = {"document_id": document_id, "to": to, "from": from_, "cover": cover}
        return await self._h.request(
            "POST", "/v1/fax-quotes", model=m.Quote, json=body, idempotency_key=idempotency_key
        )

    async def get(self, quote_id: str) -> m.Quote:
        """Read a quote. Scope: fax:quote."""
        return await self._h.request("GET", f"/v1/fax-quotes/{quote_id}", model=m.Quote)


class Faxes(_Resource):
    async def create(
        self,
        *,
        quote_id: str,
        approval_id: str | None = None,
        client_reference: str | None = None,
        idempotency_key: str | None = None,
    ) -> m.FaxJob:
        """Send a quoted fax. The answer is queued, never delivered. Poll ``wait``. Scope: fax:send."""
        body = m.FaxCreate(quote_id=quote_id, approval_id=approval_id, client_reference=client_reference)
        return await self._h.request(
            "POST", "/v1/faxes", model=m.FaxJob, json=body, idempotency_key=idempotency_key
        )

    async def list(
        self, *, state: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> m.FaxJobPage:
        """Fax jobs, newest first. Scope: fax:read."""
        return await self._h.request(
            "GET", "/v1/faxes", model=m.FaxJobPage, params={"state": state, "cursor": cursor, "limit": limit}
        )

    def iter(self, *, state: str | None = None, limit: int | None = None) -> AsyncIterator[m.FaxJob]:
        """Every fax job, following next_cursor."""
        return apaginate(self.list, {"state": state, "limit": limit})

    async def get(self, fax_id: str) -> m.FaxJob:
        """The job with its evidence once delivered or partial. Scope: fax:read."""
        return await self._h.request("GET", f"/v1/faxes/{fax_id}", model=m.FaxJob)

    async def wait(self, fax_id: str, *, timeout: float = 300.0, interval: float = 2.0) -> m.FaxJob:
        """Poll until the state is delivered, partial, failed or unknown. Returns the last job read."""
        deadline = time.monotonic() + timeout
        job = await self.get(fax_id)
        while job.state not in TERMINAL_STATES and time.monotonic() < deadline:
            await asyncio.sleep(interval)
            job = await self.get(fax_id)
        return job

    async def list_events(self, fax_id: str) -> m.EventPage:
        """Ordered events of one fax. Scope: fax:read."""
        return await self._h.request("GET", f"/v1/faxes/{fax_id}/events", model=m.EventPage)

    async def list_attempts(self, fax_id: str) -> m.AttemptPage:
        """Attempt history. Scope: fax:read."""
        return await self._h.request("GET", f"/v1/faxes/{fax_id}/attempts", model=m.AttemptPage)

    async def get_receipt(self, fax_id: str) -> m.Receipt:
        """Delivery evidence as a receipt. Scope: fax:read."""
        return await self._h.request("GET", f"/v1/faxes/{fax_id}/receipt", model=m.Receipt)

    async def cancel(self, fax_id: str, *, idempotency_key: str | None = None) -> m.FaxJob:
        """Cancel while queued, best effort once sending. Scope: fax:cancel."""
        return await self._h.request(
            "POST", f"/v1/faxes/{fax_id}/cancel", model=m.FaxJob, json={}, idempotency_key=idempotency_key
        )


class Webhooks(_Resource):
    async def list(self) -> m.WebhookList:
        """Registered destinations. Scope: webhook:admin."""
        return await self._h.request("GET", "/v1/webhooks", model=m.WebhookList)

    async def create(
        self,
        *,
        url: str,
        description: str | None = None,
        event_types: Sequence[str] | None = None,
        idempotency_key: str | None = None,
    ) -> m.WebhookWithSecret:
        """Register a destination. The secret is in the answer once. Scope: webhook:admin."""
        body = {"url": url, "description": description, "event_types": event_types}
        return await self._h.request(
            "POST", "/v1/webhooks", model=m.WebhookWithSecret, json=body, idempotency_key=idempotency_key
        )

    async def update(
        self, webhook_id: str, *, idempotency_key: str | None = None, **changes: Any
    ) -> m.Webhook:
        """Change url, description, event_types or status. Scope: webhook:admin."""
        body = m.WebhookUpdate(**changes)
        return await self._h.request(
            "PATCH", f"/v1/webhooks/{webhook_id}", model=m.Webhook, json=body, idempotency_key=idempotency_key
        )

    async def delete(self, webhook_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Remove a destination. Scope: webhook:admin."""
        return await self._h.request(
            "DELETE", f"/v1/webhooks/{webhook_id}", model=m.Deleted, idempotency_key=idempotency_key
        )

    async def rotate_secret(
        self, webhook_id: str, *, idempotency_key: str | None = None
    ) -> m.WebhookWithSecret:
        """A new secret. The old one signs too for 24 hours. Scope: webhook:admin."""
        return await self._h.request(
            "POST",
            f"/v1/webhooks/{webhook_id}/rotate-secret",
            model=m.WebhookWithSecret,
            idempotency_key=idempotency_key,
        )

    async def test(self, webhook_id: str, *, idempotency_key: str | None = None) -> m.Event:
        """Send a signed webhook.test event to the destination. Scope: webhook:admin."""
        return await self._h.request(
            "POST", f"/v1/webhooks/{webhook_id}/test", model=m.Event, json={}, idempotency_key=idempotency_key
        )

    async def list_deliveries(
        self,
        *,
        webhook_id: str | None = None,
        event_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> m.DeliveryPage:
        """Delivery attempts. Scope: webhook:admin."""
        params = {"webhook_id": webhook_id, "event_id": event_id, "cursor": cursor, "limit": limit}
        return await self._h.request("GET", "/v1/webhook-deliveries", model=m.DeliveryPage, params=params)

    async def replay_delivery(self, delivery_id: str, *, idempotency_key: str | None = None) -> m.Delivery:
        """Deliver the event again with the same event id. Scope: webhook:admin."""
        return await self._h.request(
            "POST",
            f"/v1/webhook-deliveries/{delivery_id}/replay",
            model=m.Delivery,
            json={},
            idempotency_key=idempotency_key,
        )


class Events(_Resource):
    async def list(
        self, *, type: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> m.EventPage:
        """Events for reconciliation, oldest first from the cursor. Scope: fax:read."""
        return await self._h.request(
            "GET", "/v1/events", model=m.EventPage, params={"type": type, "cursor": cursor, "limit": limit}
        )

    def iter(self, *, type: str | None = None, limit: int | None = None) -> AsyncIterator[m.Event]:
        """Every event, following next_cursor."""
        return apaginate(self.list, {"type": type, "limit": limit})


class Billing(_Resource):
    async def balance(self) -> m.Balance:
        """Available and held balance of this key's environment. Scope: billing:read."""
        return await self._h.request("GET", "/v1/balance", model=m.Balance)

    async def list_ledger(self, *, cursor: str | None = None, limit: int | None = None) -> m.LedgerPage:
        """Wallet transactions, newest first. Scope: billing:read."""
        return await self._h.request(
            "GET", "/v1/ledger", model=m.LedgerPage, params={"cursor": cursor, "limit": limit}
        )

    async def get_settings(self) -> m.BillingSettings:
        """Billing settings of the organization. Scope: billing:read."""
        return await self._h.request("GET", "/v1/billing-settings", model=m.BillingSettings)

    async def patch_settings(
        self, *, idempotency_key: str | None = None, **changes: Any
    ) -> m.BillingSettings:
        """Change the billing email or the low-balance threshold. Scope: billing:write."""
        body = m.BillingSettingsPatch(**changes)
        return await self._h.request(
            "PATCH",
            "/v1/billing-settings",
            model=m.BillingSettings,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def create_topup_session(
        self, *, idempotency_key: str | None = None, **fields: Any
    ) -> m.TopupSession:
        """A hosted Checkout to fund the live wallet. Scope: billing:write."""
        body = m.TopupCreate(**fields)
        return await self._h.request(
            "POST", "/v1/topup-sessions", model=m.TopupSession, json=body, idempotency_key=idempotency_key
        )

    async def list_topup_sessions(
        self, *, cursor: str | None = None, limit: int | None = None
    ) -> m.TopupSessionPage:
        """Top-up sessions, newest first. Scope: billing:read."""
        return await self._h.request(
            "GET", "/v1/topup-sessions", model=m.TopupSessionPage, params={"cursor": cursor, "limit": limit}
        )

    async def get_topup_session(self, topup_session_id: str) -> m.TopupSession:
        """Read a top-up session. Scope: billing:read."""
        return await self._h.request("GET", f"/v1/topup-sessions/{topup_session_id}", model=m.TopupSession)

    async def list_auto_topups(self) -> m.AutoTopupList:
        """Auto top-up settings. Scope: billing:read."""
        return await self._h.request("GET", "/v1/auto-topups", model=m.AutoTopupList)

    async def create_auto_topup(self, *, idempotency_key: str | None = None, **fields: Any) -> m.AutoTopup:
        """Opt in to auto top-up. Scope: billing:write."""
        body = m.AutoTopupCreate(**fields)
        return await self._h.request(
            "POST", "/v1/auto-topups", model=m.AutoTopup, json=body, idempotency_key=idempotency_key
        )

    async def delete_auto_topup(self, auto_topup_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Turn auto top-up off. Scope: billing:write."""
        return await self._h.request(
            "DELETE", f"/v1/auto-topups/{auto_topup_id}", model=m.Deleted, idempotency_key=idempotency_key
        )

    async def list_budgets(self) -> m.BudgetList:
        """Spending caps of this environment. Scope: billing:read."""
        return await self._h.request("GET", "/v1/budgets", model=m.BudgetList)

    async def create_budget(self, *, idempotency_key: str | None = None, **fields: Any) -> m.Budget:
        """A daily or monthly cap for the organization, a project or an agent. Scope: billing:write."""
        body = m.BudgetCreate(**fields)
        return await self._h.request(
            "POST", "/v1/budgets", model=m.Budget, json=body, idempotency_key=idempotency_key
        )

    async def patch_budget(
        self, budget_id: str, *, idempotency_key: str | None = None, **changes: Any
    ) -> m.Budget:
        """Change a cap's limit or status. Scope: billing:write."""
        body = m.BudgetPatch(**changes)
        return await self._h.request(
            "PATCH", f"/v1/budgets/{budget_id}", model=m.Budget, json=body, idempotency_key=idempotency_key
        )

    async def usage(
        self,
        *,
        from_: str | None = None,
        to: str | None = None,
        group_by: str | None = None,
        project_id: str | None = None,
    ) -> m.UsageReport:
        """Usage rollups by day, direction, destination and project. Scope: billing:read."""
        params = {"from": from_, "to": to, "group_by": group_by, "project_id": project_id}
        return await self._h.request("GET", "/v1/usage", model=m.UsageReport, params=params)

    async def create_usage_export(
        self, *, idempotency_key: str | None = None, **fields: Any
    ) -> m.UsageExport:
        """Export usage as CSV behind a signed download. Scope: billing:read."""
        body = m.UsageExportCreate(**fields)
        return await self._h.request(
            "POST", "/v1/usage-exports", model=m.UsageExport, json=body, idempotency_key=idempotency_key
        )

    async def get_usage_export(self, export_id: str) -> m.UsageExport:
        """Read a usage export with a fresh link. Scope: billing:read."""
        return await self._h.request("GET", f"/v1/usage-exports/{export_id}", model=m.UsageExport)

    async def list_invoices(self, *, cursor: str | None = None, limit: int | None = None) -> m.InvoicePage:
        """Stripe invoices and receipts as hosted links. Scope: billing:read."""
        return await self._h.request(
            "GET", "/v1/invoices", model=m.InvoicePage, params={"cursor": cursor, "limit": limit}
        )

    async def get_invoice(self, invoice_id: str) -> m.Invoice:
        """One invoice. Scope: billing:read."""
        return await self._h.request("GET", f"/v1/invoices/{invoice_id}", model=m.Invoice)

    async def create_subscription_checkout(
        self, *, idempotency_key: str | None = None
    ) -> m.SubscriptionCheckout:
        """A hosted Checkout for the Agent workspace subscription. Scope: billing:write."""
        return await self._h.request(
            "POST",
            "/v1/subscriptions/checkout",
            model=m.SubscriptionCheckout,
            json={},
            idempotency_key=idempotency_key,
        )

    async def list_subscriptions(self) -> m.SubscriptionList:
        """Subscriptions and the workspace entitlement. Scope: billing:read."""
        return await self._h.request("GET", "/v1/subscriptions", model=m.SubscriptionList)

    async def create_portal_session(self, *, idempotency_key: str | None = None) -> m.PortalSession:
        """The Stripe customer portal. Scope: billing:write."""
        return await self._h.request(
            "POST",
            "/v1/billing-portal-sessions",
            model=m.PortalSession,
            json={},
            idempotency_key=idempotency_key,
        )


class Platform(_Resource):
    async def capabilities(self) -> m.Capabilities:
        """Limits, routes, formats, prices and scopes in force. Scope: any."""
        return await self._h.request("GET", "/v1/capabilities", model=m.Capabilities)

    async def service_status(self) -> m.ServiceStatus:
        """Customer-safe availability. Scope: any."""
        return await self._h.request("GET", "/v1/service-status", model=m.ServiceStatus)


class Sandbox(_Resource):
    async def list_scenarios(self) -> m.SandboxScenarios:
        """The scenario catalog, the destination table and active fixtures. Test keys only."""
        return await self._h.request("GET", "/v1/sandbox/scenarios", model=m.SandboxScenarios)

    async def create_scenario(
        self, scenario: str, *, destination: str | None = None, idempotency_key: str | None = None
    ) -> m.SandboxFixture:
        """Pin an outcome to a destination (or to every send) for 24 hours. Test keys only."""
        body = m.SandboxScenarioCreate(scenario=scenario, destination=destination)
        return await self._h.request(
            "POST",
            "/v1/sandbox/scenarios",
            model=m.SandboxFixture,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def create_inbound_fax(
        self, *, to: str, from_: str | None = None, pages: int = 1, idempotency_key: str | None = None
    ) -> m.InboxItem:
        """A synthetic received fax and its fax.received event. Test keys only."""
        body = {"to": to, "from": from_, "pages": pages}
        return await self._h.request(
            "POST", "/v1/sandbox/inbound-faxes", model=m.InboxItem, json=body, idempotency_key=idempotency_key
        )

    async def reset(self, *, idempotency_key: str | None = None) -> m.SandboxReset:
        """Delete the organization's test data and reseed the test wallet. Test keys only."""
        return await self._h.request(
            "DELETE", "/v1/sandbox/data", model=m.SandboxReset, idempotency_key=idempotency_key
        )


class Approvals(_Resource):
    async def request(self, quote_id: str, *, idempotency_key: str | None = None) -> m.ApprovalRequest:
        """Ask a person to approve a quoted send. Grants nothing. Scope: approval:request."""
        body = {"kind": "send", "quote_id": quote_id}
        return await self._h.request(
            "POST",
            "/v1/approval-requests",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def request_help(
        self, message: str, *, fax_job_id: str | None = None, idempotency_key: str | None = None
    ) -> m.ApprovalRequest:
        """Ask a person for help. The message is shown as data. Scope: approval:request."""
        body = {"kind": "help", "message": message, "fax_job_id": fax_job_id}
        return await self._h.request(
            "POST",
            "/v1/approval-requests",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def list(
        self, *, status: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> m.ApprovalRequestPage:
        """Requests, pending first. Scope: approval:read."""
        params = {"status": status, "cursor": cursor, "limit": limit}
        return await self._h.request(
            "GET", "/v1/approval-requests", model=m.ApprovalRequestPage, params=params
        )

    def iter(
        self, *, status: str | None = None, limit: int | None = None
    ) -> AsyncIterator[m.ApprovalRequest]:
        """Every request, following next_cursor."""
        return apaginate(self.list, {"status": status, "limit": limit})

    async def get(self, approval_id: str) -> m.ApprovalRequest:
        """One request. Scope: approval:read."""
        return await self._h.request("GET", f"/v1/approval-requests/{approval_id}", model=m.ApprovalRequest)

    async def wait(
        self, approval_id: str, *, timeout: float = 600.0, interval: float = 5.0
    ) -> m.ApprovalRequest:
        """Poll until the request leaves pending, or the timeout passes."""
        deadline = time.monotonic() + timeout
        req = await self.get(approval_id)
        while req.status == "pending" and time.monotonic() < deadline:
            await asyncio.sleep(interval)
            req = await self.get(approval_id)
        return req

    async def approve(
        self, approval_id: str, *, reason: str | None = None, idempotency_key: str | None = None
    ) -> m.ApprovalRequest:
        """Approve the bound send. Scope: approval:decide, never the key that asked, never an agent key."""
        body = m.ApprovalDecision(reason=reason)
        return await self._h.request(
            "POST",
            f"/v1/approval-requests/{approval_id}/approve",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def reject(
        self, approval_id: str, *, reason: str, idempotency_key: str | None = None
    ) -> m.ApprovalRequest:
        """Reject with a reason. Scope: approval:decide."""
        body = m.ApprovalDecision(reason=reason)
        return await self._h.request(
            "POST",
            f"/v1/approval-requests/{approval_id}/reject",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def revoke(
        self, approval_id: str, *, reason: str | None = None, idempotency_key: str | None = None
    ) -> m.ApprovalRequest:
        """Revoke an approval nobody has used yet. Scope: approval:decide."""
        body = m.ApprovalDecision(reason=reason)
        return await self._h.request(
            "POST",
            f"/v1/approvals/{approval_id}/revoke",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )


class Policies(_Resource):
    async def list(self) -> m.PolicyList:
        """The organization's policies. Scope: policy:read."""
        return await self._h.request("GET", "/v1/policies", model=m.PolicyList)

    async def create(self, *, idempotency_key: str | None = None, **fields: Any) -> m.Policy:
        """Create a delegated policy, version 1. Scope: policy:admin, never an agent key."""
        body = m.PolicyCreate(**fields)
        return await self._h.request(
            "POST", "/v1/policies", model=m.Policy, json=body, idempotency_key=idempotency_key
        )

    async def effective(self) -> m.EffectivePolicies:
        """What this key is allowed to do alone, and whether a human is needed. Scope: policy:read."""
        return await self._h.request("GET", "/v1/policies/effective", model=m.EffectivePolicies)

    async def get(self, policy_id: str) -> m.Policy:
        """A policy at its current version. Scope: policy:read."""
        return await self._h.request("GET", f"/v1/policies/{policy_id}", model=m.Policy)

    async def update(self, policy_id: str, *, idempotency_key: str | None = None, **changes: Any) -> m.Policy:
        """Change a policy: a new immutable version. Scope: policy:admin."""
        body = m.PolicyUpdate(**changes)
        return await self._h.request(
            "PATCH", f"/v1/policies/{policy_id}", model=m.Policy, json=body, idempotency_key=idempotency_key
        )

    async def list_versions(self, policy_id: str) -> m.PolicyVersionList:
        """Every version, oldest first. Scope: policy:read."""
        return await self._h.request("GET", f"/v1/policies/{policy_id}/versions", model=m.PolicyVersionList)

    async def evaluate(
        self,
        *,
        quote_id: str | None = None,
        document_id: str | None = None,
        to: str | None = None,
        from_: str | None = None,
    ) -> m.PolicyEvaluation:
        """Dry run: would this key's policies allow the send? Never executes. Scope: policy:read."""
        body = {"quote_id": quote_id, "document_id": document_id, "to": to, "from": from_}
        return await self._h.request("POST", "/v1/policy-evaluations", model=m.PolicyEvaluation, json=body)


class Agents(_Resource):
    async def list(self) -> m.AgentList:
        """Agent identities. Scope: agent:read."""
        return await self._h.request("GET", "/v1/agents", model=m.AgentList)

    async def create(self, *, idempotency_key: str | None = None, **fields: Any) -> m.Agent:
        """Register an agent identity. Scope: agent:admin, never an agent key."""
        body = m.AgentCreate(**fields)
        return await self._h.request(
            "POST", "/v1/agents", model=m.Agent, json=body, idempotency_key=idempotency_key
        )

    async def get(self, agent_id: str) -> m.Agent:
        """An agent with its keys and budgets. Scope: agent:read."""
        return await self._h.request("GET", f"/v1/agents/{agent_id}", model=m.Agent)

    async def update(self, agent_id: str, *, idempotency_key: str | None = None, **changes: Any) -> m.Agent:
        """Change an agent. Disabling revokes its keys. Scope: agent:admin."""
        body = m.AgentUpdate(**changes)
        return await self._h.request(
            "PATCH", f"/v1/agents/{agent_id}", model=m.Agent, json=body, idempotency_key=idempotency_key
        )

    async def create_key(
        self, agent_id: str, *, idempotency_key: str | None = None, **fields: Any
    ) -> m.ApiKeyWithSecret:
        """Mint a key for the agent, bounded scopes, secret shown once. Scope: agent:admin and key:admin."""
        body = m.AgentKeyCreate(**fields)
        return await self._h.request(
            "POST",
            f"/v1/agents/{agent_id}/keys",
            model=m.ApiKeyWithSecret,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def activity(
        self, agent_id: str, *, cursor: str | None = None, limit: int | None = None
    ) -> m.AgentActivityPage:
        """The agent's audit trail, metadata only. Scope: agent:read."""
        params = {"cursor": cursor, "limit": limit}
        return await self._h.request(
            "GET", f"/v1/agents/{agent_id}/activity", model=m.AgentActivityPage, params=params
        )


class Inbox(_Resource):
    async def list(self, *, cursor: str | None = None, limit: int | None = None) -> m.InboxPage:
        """Received faxes, newest first, metadata only. ``from_`` is sender data. Scope: inbox:read."""
        return await self._h.request(
            "GET", "/v1/inbox", model=m.InboxPage, params={"cursor": cursor, "limit": limit}
        )

    def iter(self, *, limit: int | None = None) -> AsyncIterator[m.InboxItem]:
        """Every received fax, following next_cursor."""
        return apaginate(self.list, {"limit": limit})

    async def get(self, inbox_id: str) -> m.InboxItemDetail:
        """One received fax with its text as data, never as instructions. Scope: inbox:read."""
        return await self._h.request("GET", f"/v1/inbox/{inbox_id}", model=m.InboxItemDetail)


class AsyncClient:
    """The asynchronous Fax for Agents client. Use it as an async context manager or await ``close``."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        max_retry_after: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = Settings(api_key, base_url, timeout, max_retries, backoff_base, max_retry_after)
        self._h = AsyncHttp(self.settings, transport)
        self.identity = Identity(self._h)
        self.documents = Documents(self._h)
        self.quotes = Quotes(self._h)
        self.faxes = Faxes(self._h)
        self.webhooks = Webhooks(self._h)
        self.events = Events(self._h)
        self.billing = Billing(self._h)
        self.platform = Platform(self._h)
        self.sandbox = Sandbox(self._h)
        self.approvals = Approvals(self._h)
        self.policies = Policies(self._h)
        self.agents = Agents(self._h)
        self.inbox = Inbox(self._h)

    async def me(self) -> m.Me:
        """Shortcut for ``identity.me()``."""
        return await self.identity.me()

    async def request(self, method: str, path: str, **kw: Any) -> Any:
        """Any operation the resources do not cover yet. Returns the parsed JSON (or bytes)."""
        return await self._h.request(method, path, **kw)

    async def close(self) -> None:
        await self._h.close()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
