"""The synchronous client. ``AsyncClient`` in ``async_client.py`` has the same surface with ``await``.

Resources mirror the API's tags. Method names are the operationIds of the OpenAPI document, so the
reference at https://faxforagents.github.io/openapi/reference.html is also the reference of this
client. Every mutating method takes ``idempotency_key``. When you pass none a uuid4 is minted once per
call and reused on every retry.

    from faxforagents import Client

    with Client() as ffa:                        # reads FAXFORAGENTS_API_KEY
        doc = ffa.documents.create(text="Hello from an agent.")
        quote = ffa.quotes.create(document_id=doc.id, to="+15550000001")
        fax = ffa.faxes.create(quote_id=quote.id)
        fax = ffa.faxes.wait(fax.id)             # polls until a terminal state
        print(fax.state, fax.evidence)
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx

from faxforagents import models as m
from faxforagents._http import Settings, SyncHttp, paginate
from faxforagents.errors import ConflictError

TERMINAL_STATES = frozenset({"delivered", "partial", "failed", "unknown"})

#: Content types the upload path accepts, by file suffix. Anything else is sent as a PDF.
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def guess_content_type(filename: str) -> str:
    """The upload content type of a filename: a PDF unless the suffix says PNG, JPEG or TIFF."""
    return CONTENT_TYPES.get(Path(filename).suffix.lower(), "application/pdf")


class _Resource:
    def __init__(self, http: SyncHttp) -> None:
        self._h = http


class Identity(_Resource):
    def me(self) -> m.Me:
        """Who the key is: organization, project, environment, scopes. Scope: any."""
        return self._h.request("GET", "/v1/me", model=m.Me)

    def list_api_keys(self) -> m.ApiKeyList:
        """Keys of the organization, metadata only. Scope: key:admin."""
        return self._h.request("GET", "/v1/api-keys", model=m.ApiKeyList)

    def create_api_key(
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
        return self._h.request(
            "POST", "/v1/api-keys", model=m.ApiKeyWithSecret, json=body, idempotency_key=idempotency_key
        )

    def rotate_api_key(self, key_id: str, *, idempotency_key: str | None = None) -> m.ApiKeyWithSecret:
        """A new secret. The old one works 24 more hours. Scope: key:admin."""
        return self._h.request(
            "POST", f"/v1/api-keys/{key_id}/rotate", model=m.ApiKeyWithSecret, idempotency_key=idempotency_key
        )

    def revoke_api_key(self, key_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Revoke a key at once. Scope: key:admin."""
        return self._h.request(
            "DELETE", f"/v1/api-keys/{key_id}", model=m.Deleted, idempotency_key=idempotency_key
        )


class Documents(_Resource):
    def create(
        self,
        *,
        upload_id: str | None = None,
        text: str | None = None,
        markdown: str | None = None,
        html: str | None = None,
        filename: str | None = None,
        idempotency_key: str | None = None,
    ) -> m.Document:
        """An immutable document from one of upload_id, text, markdown or html. HTML is rendered with
        scripts off and no network, at most 2 MiB. Scope: document:write."""
        body = m.DocumentCreate(
            upload_id=upload_id, text=text, markdown=markdown, html=html, filename=filename
        )
        return self._h.request(
            "POST", "/v1/documents", model=m.Document, json=body, idempotency_key=idempotency_key
        )

    def create_upload(
        self,
        *,
        filename: str,
        size_bytes: int,
        content_type: str = "application/pdf",
        idempotency_key: str | None = None,
    ) -> m.UploadSession:
        """Open an upload session with a signed PUT URL (25 MB, one hour). ``content_type`` is
        application/pdf, image/png, image/jpeg or image/tiff. Scope: document:write."""
        body = m.UploadCreate(filename=filename, size_bytes=size_bytes, content_type=content_type)
        return self._h.request(
            "POST", "/v1/uploads", model=m.UploadSession, json=body, idempotency_key=idempotency_key
        )

    def put_upload_content(
        self, upload_url: str, data: bytes, *, content_type: str = "application/pdf"
    ) -> m.UploadSession:
        """Send the bytes to the signed URL from ``create_upload``. No scope, the link is the credential."""
        return self._h.request(
            "PUT", upload_url, model=m.UploadSession, content=data, headers={"Content-Type": content_type}
        )

    def complete_upload(
        self, upload_id: str, *, sha256: str | None = None, idempotency_key: str | None = None
    ) -> m.UploadSession:
        """Finalize an upload. The size and, when given, the sha256 are checked. Scope: document:write."""
        body = m.UploadComplete(sha256=sha256)
        return self._h.request(
            "POST",
            f"/v1/uploads/{upload_id}/complete",
            model=m.UploadSession,
            json=body,
            idempotency_key=idempotency_key,
        )

    def cancel_upload(self, upload_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Cancel an incomplete upload. Scope: document:write."""
        return self._h.request(
            "DELETE", f"/v1/uploads/{upload_id}", model=m.Deleted, idempotency_key=idempotency_key
        )

    def upload(
        self, file: str | Path | bytes, *, filename: str | None = None, content_type: str | None = None
    ) -> m.Document:
        """The whole upload path for a PDF or an image (PNG, JPG or TIFF): create_upload, put the bytes,
        complete with the sha256, create the document. ``content_type`` is read from the filename when
        not given. An image becomes gray letter pages."""
        if isinstance(file, bytes):
            data = file
            name = filename or "document.pdf"
        else:
            path = Path(file)
            data = path.read_bytes()
            name = filename or path.name
        ctype = content_type or guess_content_type(name)
        session = self.create_upload(filename=name, size_bytes=len(data), content_type=ctype)
        if not session.upload_url:
            raise ConflictError(
                status=409, code="upload_url_missing", message="the upload session carries no upload_url"
            )
        self.put_upload_content(session.upload_url, data, content_type=ctype)
        self.complete_upload(session.id, sha256=hashlib.sha256(data).hexdigest())
        return self.create(upload_id=session.id, filename=name)

    def list(self, *, cursor: str | None = None, limit: int | None = None) -> m.DocumentPage:
        """Documents, newest first. Scope: document:read."""
        return self._h.request(
            "GET", "/v1/documents", model=m.DocumentPage, params={"cursor": cursor, "limit": limit}
        )

    def iter(self, *, limit: int | None = None) -> Iterator[m.Document]:
        """Every document, following next_cursor."""
        return paginate(lambda p: self.list(**p), {"limit": limit})

    def get(self, document_id: str) -> m.Document:
        """Hash, pages and validation state. Scope: document:read."""
        return self._h.request("GET", f"/v1/documents/{document_id}", model=m.Document)

    def delete(self, document_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Delete the document, its bytes and its previews. Scope: document:write."""
        return self._h.request(
            "DELETE", f"/v1/documents/{document_id}", model=m.Deleted, idempotency_key=idempotency_key
        )

    def get_validation(self, document_id: str) -> m.DocumentValidation:
        """Validation warnings. Scope: document:read."""
        return self._h.request("GET", f"/v1/documents/{document_id}/validation", model=m.DocumentValidation)

    def get_download(self, document_id: str) -> m.SignedUrl:
        """A signed download URL, valid ten minutes. Scope: document:read."""
        return self._h.request("GET", f"/v1/documents/{document_id}/download", model=m.SignedUrl)

    def download(self, document_id: str) -> bytes:
        """The document bytes (always a PDF), through a fresh signed URL."""
        link = self.get_download(document_id)
        data = self._h.request("GET", link.url)
        return bytes(data)

    def list_pages(self, document_id: str) -> m.DocumentPages:
        """Rendered pages with signed preview URLs. Scope: document:read."""
        return self._h.request("GET", f"/v1/documents/{document_id}/pages", model=m.DocumentPages)


class Quotes(_Resource):
    def create(
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
        return self._h.request(
            "POST", "/v1/fax-quotes", model=m.Quote, json=body, idempotency_key=idempotency_key
        )

    def get(self, quote_id: str) -> m.Quote:
        """Read a quote. Scope: fax:quote."""
        return self._h.request("GET", f"/v1/fax-quotes/{quote_id}", model=m.Quote)


class Faxes(_Resource):
    def create(
        self,
        *,
        quote_id: str,
        approval_id: str | None = None,
        client_reference: str | None = None,
        idempotency_key: str | None = None,
    ) -> m.FaxJob:
        """Send a quoted fax. The answer is queued, never delivered. Poll ``wait``. Scope: fax:send."""
        body = m.FaxCreate(quote_id=quote_id, approval_id=approval_id, client_reference=client_reference)
        return self._h.request(
            "POST", "/v1/faxes", model=m.FaxJob, json=body, idempotency_key=idempotency_key
        )

    def list(
        self, *, state: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> m.FaxJobPage:
        """Fax jobs, newest first. Scope: fax:read."""
        return self._h.request(
            "GET", "/v1/faxes", model=m.FaxJobPage, params={"state": state, "cursor": cursor, "limit": limit}
        )

    def iter(self, *, state: str | None = None, limit: int | None = None) -> Iterator[m.FaxJob]:
        """Every fax job, following next_cursor."""
        return paginate(lambda p: self.list(**p), {"state": state, "limit": limit})

    def get(self, fax_id: str) -> m.FaxJob:
        """The job with its evidence once delivered or partial. Scope: fax:read."""
        return self._h.request("GET", f"/v1/faxes/{fax_id}", model=m.FaxJob)

    def wait(self, fax_id: str, *, timeout: float = 300.0, interval: float = 2.0) -> m.FaxJob:
        """Poll until the state is delivered, partial, failed or unknown. Returns the last job read."""
        deadline = time.monotonic() + timeout
        job = self.get(fax_id)
        while job.state not in TERMINAL_STATES and time.monotonic() < deadline:
            time.sleep(interval)
            job = self.get(fax_id)
        return job

    def list_events(self, fax_id: str) -> m.EventPage:
        """Ordered events of one fax. Scope: fax:read."""
        return self._h.request("GET", f"/v1/faxes/{fax_id}/events", model=m.EventPage)

    def list_attempts(self, fax_id: str) -> m.AttemptPage:
        """Attempt history. Scope: fax:read."""
        return self._h.request("GET", f"/v1/faxes/{fax_id}/attempts", model=m.AttemptPage)

    def get_receipt(self, fax_id: str) -> m.Receipt:
        """Delivery evidence as a receipt. Scope: fax:read."""
        return self._h.request("GET", f"/v1/faxes/{fax_id}/receipt", model=m.Receipt)

    def cancel(self, fax_id: str, *, idempotency_key: str | None = None) -> m.FaxJob:
        """Cancel while queued, best effort once sending. Scope: fax:cancel."""
        return self._h.request(
            "POST", f"/v1/faxes/{fax_id}/cancel", model=m.FaxJob, json={}, idempotency_key=idempotency_key
        )


class Webhooks(_Resource):
    def list(self) -> m.WebhookList:
        """Registered destinations. Scope: webhook:admin."""
        return self._h.request("GET", "/v1/webhooks", model=m.WebhookList)

    def create(
        self,
        *,
        url: str,
        description: str | None = None,
        event_types: Sequence[str] | None = None,
        idempotency_key: str | None = None,
    ) -> m.WebhookWithSecret:
        """Register a destination. The secret is in the answer once. Scope: webhook:admin."""
        body = {"url": url, "description": description, "event_types": event_types}
        return self._h.request(
            "POST", "/v1/webhooks", model=m.WebhookWithSecret, json=body, idempotency_key=idempotency_key
        )

    def update(self, webhook_id: str, *, idempotency_key: str | None = None, **changes: Any) -> m.Webhook:
        """Change url, description, event_types or status. Scope: webhook:admin."""
        body = m.WebhookUpdate(**changes)
        return self._h.request(
            "PATCH", f"/v1/webhooks/{webhook_id}", model=m.Webhook, json=body, idempotency_key=idempotency_key
        )

    def delete(self, webhook_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Remove a destination. Scope: webhook:admin."""
        return self._h.request(
            "DELETE", f"/v1/webhooks/{webhook_id}", model=m.Deleted, idempotency_key=idempotency_key
        )

    def rotate_secret(self, webhook_id: str, *, idempotency_key: str | None = None) -> m.WebhookWithSecret:
        """A new secret. The old one signs too for 24 hours. Scope: webhook:admin."""
        return self._h.request(
            "POST",
            f"/v1/webhooks/{webhook_id}/rotate-secret",
            model=m.WebhookWithSecret,
            idempotency_key=idempotency_key,
        )

    def test(self, webhook_id: str, *, idempotency_key: str | None = None) -> m.Event:
        """Send a signed webhook.test event to the destination. Scope: webhook:admin."""
        return self._h.request(
            "POST", f"/v1/webhooks/{webhook_id}/test", model=m.Event, json={}, idempotency_key=idempotency_key
        )

    def list_deliveries(
        self,
        *,
        webhook_id: str | None = None,
        event_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> m.DeliveryPage:
        """Delivery attempts. Scope: webhook:admin."""
        params = {"webhook_id": webhook_id, "event_id": event_id, "cursor": cursor, "limit": limit}
        return self._h.request("GET", "/v1/webhook-deliveries", model=m.DeliveryPage, params=params)

    def replay_delivery(self, delivery_id: str, *, idempotency_key: str | None = None) -> m.Delivery:
        """Deliver the event again with the same event id. Scope: webhook:admin."""
        return self._h.request(
            "POST",
            f"/v1/webhook-deliveries/{delivery_id}/replay",
            model=m.Delivery,
            json={},
            idempotency_key=idempotency_key,
        )


class Events(_Resource):
    def list(
        self, *, type: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> m.EventPage:
        """Events for reconciliation, oldest first from the cursor. Scope: fax:read."""
        return self._h.request(
            "GET", "/v1/events", model=m.EventPage, params={"type": type, "cursor": cursor, "limit": limit}
        )

    def iter(self, *, type: str | None = None, limit: int | None = None) -> Iterator[m.Event]:
        """Every event, following next_cursor."""
        return paginate(lambda p: self.list(**p), {"type": type, "limit": limit})


class Billing(_Resource):
    def balance(self) -> m.Balance:
        """Available and held balance of this key's environment. Scope: billing:read."""
        return self._h.request("GET", "/v1/balance", model=m.Balance)

    def list_ledger(self, *, cursor: str | None = None, limit: int | None = None) -> m.LedgerPage:
        """Wallet transactions, newest first. Scope: billing:read."""
        return self._h.request(
            "GET", "/v1/ledger", model=m.LedgerPage, params={"cursor": cursor, "limit": limit}
        )

    def get_settings(self) -> m.BillingSettings:
        """Billing settings of the organization. Scope: billing:read."""
        return self._h.request("GET", "/v1/billing-settings", model=m.BillingSettings)

    def patch_settings(self, *, idempotency_key: str | None = None, **changes: Any) -> m.BillingSettings:
        """Change the billing email or the low-balance threshold. Scope: billing:write."""
        body = m.BillingSettingsPatch(**changes)
        return self._h.request(
            "PATCH",
            "/v1/billing-settings",
            model=m.BillingSettings,
            json=body,
            idempotency_key=idempotency_key,
        )

    def create_topup_session(self, *, idempotency_key: str | None = None, **fields: Any) -> m.TopupSession:
        """A hosted Checkout to fund the live wallet. Scope: billing:write."""
        body = m.TopupCreate(**fields)
        return self._h.request(
            "POST", "/v1/topup-sessions", model=m.TopupSession, json=body, idempotency_key=idempotency_key
        )

    def list_topup_sessions(
        self, *, cursor: str | None = None, limit: int | None = None
    ) -> m.TopupSessionPage:
        """Top-up sessions, newest first. Scope: billing:read."""
        return self._h.request(
            "GET", "/v1/topup-sessions", model=m.TopupSessionPage, params={"cursor": cursor, "limit": limit}
        )

    def get_topup_session(self, topup_session_id: str) -> m.TopupSession:
        """Read a top-up session. Scope: billing:read."""
        return self._h.request("GET", f"/v1/topup-sessions/{topup_session_id}", model=m.TopupSession)

    def list_auto_topups(self) -> m.AutoTopupList:
        """Auto top-up settings. Scope: billing:read."""
        return self._h.request("GET", "/v1/auto-topups", model=m.AutoTopupList)

    def create_auto_topup(self, *, idempotency_key: str | None = None, **fields: Any) -> m.AutoTopup:
        """Opt in to auto top-up. Scope: billing:write."""
        body = m.AutoTopupCreate(**fields)
        return self._h.request(
            "POST", "/v1/auto-topups", model=m.AutoTopup, json=body, idempotency_key=idempotency_key
        )

    def delete_auto_topup(self, auto_topup_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Turn auto top-up off. Scope: billing:write."""
        return self._h.request(
            "DELETE", f"/v1/auto-topups/{auto_topup_id}", model=m.Deleted, idempotency_key=idempotency_key
        )

    def list_budgets(self) -> m.BudgetList:
        """Spending caps of this environment. Scope: billing:read."""
        return self._h.request("GET", "/v1/budgets", model=m.BudgetList)

    def create_budget(self, *, idempotency_key: str | None = None, **fields: Any) -> m.Budget:
        """A daily or monthly cap for the organization, a project or an agent. Scope: billing:write."""
        body = m.BudgetCreate(**fields)
        return self._h.request(
            "POST", "/v1/budgets", model=m.Budget, json=body, idempotency_key=idempotency_key
        )

    def patch_budget(self, budget_id: str, *, idempotency_key: str | None = None, **changes: Any) -> m.Budget:
        """Change a cap's limit or status. Scope: billing:write."""
        body = m.BudgetPatch(**changes)
        return self._h.request(
            "PATCH", f"/v1/budgets/{budget_id}", model=m.Budget, json=body, idempotency_key=idempotency_key
        )

    def usage(
        self,
        *,
        from_: str | None = None,
        to: str | None = None,
        group_by: str | None = None,
        project_id: str | None = None,
    ) -> m.UsageReport:
        """Usage rollups by day, direction, destination and project. Scope: billing:read."""
        params = {"from": from_, "to": to, "group_by": group_by, "project_id": project_id}
        return self._h.request("GET", "/v1/usage", model=m.UsageReport, params=params)

    def create_usage_export(self, *, idempotency_key: str | None = None, **fields: Any) -> m.UsageExport:
        """Export usage as CSV behind a signed download. Scope: billing:read."""
        body = m.UsageExportCreate(**fields)
        return self._h.request(
            "POST", "/v1/usage-exports", model=m.UsageExport, json=body, idempotency_key=idempotency_key
        )

    def get_usage_export(self, export_id: str) -> m.UsageExport:
        """Read a usage export with a fresh link. Scope: billing:read."""
        return self._h.request("GET", f"/v1/usage-exports/{export_id}", model=m.UsageExport)

    def list_invoices(self, *, cursor: str | None = None, limit: int | None = None) -> m.InvoicePage:
        """Stripe invoices and receipts as hosted links. Scope: billing:read."""
        return self._h.request(
            "GET", "/v1/invoices", model=m.InvoicePage, params={"cursor": cursor, "limit": limit}
        )

    def get_invoice(self, invoice_id: str) -> m.Invoice:
        """One invoice. Scope: billing:read."""
        return self._h.request("GET", f"/v1/invoices/{invoice_id}", model=m.Invoice)

    def create_subscription_checkout(self, *, idempotency_key: str | None = None) -> m.SubscriptionCheckout:
        """A hosted Checkout for the Agent workspace subscription. Scope: billing:write."""
        return self._h.request(
            "POST",
            "/v1/subscriptions/checkout",
            model=m.SubscriptionCheckout,
            json={},
            idempotency_key=idempotency_key,
        )

    def list_subscriptions(self) -> m.SubscriptionList:
        """Subscriptions and the workspace entitlement. Scope: billing:read."""
        return self._h.request("GET", "/v1/subscriptions", model=m.SubscriptionList)

    def create_portal_session(self, *, idempotency_key: str | None = None) -> m.PortalSession:
        """The Stripe customer portal. Scope: billing:write."""
        return self._h.request(
            "POST",
            "/v1/billing-portal-sessions",
            model=m.PortalSession,
            json={},
            idempotency_key=idempotency_key,
        )


class Platform(_Resource):
    def capabilities(self) -> m.Capabilities:
        """Limits, routes, formats, prices and scopes in force. Scope: any."""
        return self._h.request("GET", "/v1/capabilities", model=m.Capabilities)

    def service_status(self) -> m.ServiceStatus:
        """Customer-safe availability. Scope: any."""
        return self._h.request("GET", "/v1/service-status", model=m.ServiceStatus)


class Sandbox(_Resource):
    def list_scenarios(self) -> m.SandboxScenarios:
        """The scenario catalog, the destination table and active fixtures. Test keys only."""
        return self._h.request("GET", "/v1/sandbox/scenarios", model=m.SandboxScenarios)

    def create_scenario(
        self, scenario: str, *, destination: str | None = None, idempotency_key: str | None = None
    ) -> m.SandboxFixture:
        """Pin an outcome to a destination (or to every send) for 24 hours. Test keys only."""
        body = m.SandboxScenarioCreate(scenario=scenario, destination=destination)
        return self._h.request(
            "POST",
            "/v1/sandbox/scenarios",
            model=m.SandboxFixture,
            json=body,
            idempotency_key=idempotency_key,
        )

    def create_inbound_fax(
        self, *, to: str, from_: str | None = None, pages: int = 1, idempotency_key: str | None = None
    ) -> m.InboxItemSummary:
        """A synthetic received fax and its fax.received event. Test keys only."""
        body = {"to": to, "from": from_, "pages": pages}
        return self._h.request(
            "POST",
            "/v1/sandbox/inbound-faxes",
            model=m.InboxItemSummary,
            json=body,
            idempotency_key=idempotency_key,
        )

    def reset(self, *, idempotency_key: str | None = None) -> m.SandboxReset:
        """Delete the organization's test data and reseed the test wallet. Test keys only."""
        return self._h.request(
            "DELETE", "/v1/sandbox/data", model=m.SandboxReset, idempotency_key=idempotency_key
        )


class Approvals(_Resource):
    def request(self, quote_id: str, *, idempotency_key: str | None = None) -> m.ApprovalRequest:
        """Ask a person to approve a quoted send. Grants nothing. Scope: approval:request."""
        body = {"kind": "send", "quote_id": quote_id}
        return self._h.request(
            "POST",
            "/v1/approval-requests",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )

    def request_help(
        self, message: str, *, fax_job_id: str | None = None, idempotency_key: str | None = None
    ) -> m.ApprovalRequest:
        """Ask a person for help. The message is shown as data. Scope: approval:request."""
        body = {"kind": "help", "message": message, "fax_job_id": fax_job_id}
        return self._h.request(
            "POST",
            "/v1/approval-requests",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )

    def list(
        self, *, status: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> m.ApprovalRequestPage:
        """Requests, pending first. Scope: approval:read."""
        params = {"status": status, "cursor": cursor, "limit": limit}
        return self._h.request("GET", "/v1/approval-requests", model=m.ApprovalRequestPage, params=params)

    def iter(self, *, status: str | None = None, limit: int | None = None) -> Iterator[m.ApprovalRequest]:
        """Every request, following next_cursor."""
        return paginate(lambda p: self.list(**p), {"status": status, "limit": limit})

    def get(self, approval_id: str) -> m.ApprovalRequest:
        """One request. Scope: approval:read."""
        return self._h.request("GET", f"/v1/approval-requests/{approval_id}", model=m.ApprovalRequest)

    def wait(self, approval_id: str, *, timeout: float = 600.0, interval: float = 5.0) -> m.ApprovalRequest:
        """Poll until the request leaves pending, or the timeout passes."""
        deadline = time.monotonic() + timeout
        req = self.get(approval_id)
        while req.status == "pending" and time.monotonic() < deadline:
            time.sleep(interval)
            req = self.get(approval_id)
        return req

    def approve(
        self, approval_id: str, *, reason: str | None = None, idempotency_key: str | None = None
    ) -> m.ApprovalRequest:
        """Approve the bound send. Scope: approval:decide, never the key that asked, never an agent key."""
        body = m.ApprovalDecision(reason=reason)
        return self._h.request(
            "POST",
            f"/v1/approval-requests/{approval_id}/approve",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )

    def reject(
        self, approval_id: str, *, reason: str, idempotency_key: str | None = None
    ) -> m.ApprovalRequest:
        """Reject with a reason. Scope: approval:decide."""
        body = m.ApprovalDecision(reason=reason)
        return self._h.request(
            "POST",
            f"/v1/approval-requests/{approval_id}/reject",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )

    def revoke(
        self, approval_id: str, *, reason: str | None = None, idempotency_key: str | None = None
    ) -> m.ApprovalRequest:
        """Revoke an approval nobody has used yet. Scope: approval:decide."""
        body = m.ApprovalDecision(reason=reason)
        return self._h.request(
            "POST",
            f"/v1/approvals/{approval_id}/revoke",
            model=m.ApprovalRequest,
            json=body,
            idempotency_key=idempotency_key,
        )


class Policies(_Resource):
    def list(self) -> m.PolicyList:
        """The organization's policies. Scope: policy:read."""
        return self._h.request("GET", "/v1/policies", model=m.PolicyList)

    def create(self, *, idempotency_key: str | None = None, **fields: Any) -> m.Policy:
        """Create a delegated policy, version 1. Scope: policy:admin, never an agent key."""
        body = m.PolicyCreate(**fields)
        return self._h.request(
            "POST", "/v1/policies", model=m.Policy, json=body, idempotency_key=idempotency_key
        )

    def effective(self) -> m.EffectivePolicies:
        """What this key is allowed to do alone, and whether a human is needed. Scope: policy:read."""
        return self._h.request("GET", "/v1/policies/effective", model=m.EffectivePolicies)

    def get(self, policy_id: str) -> m.Policy:
        """A policy at its current version. Scope: policy:read."""
        return self._h.request("GET", f"/v1/policies/{policy_id}", model=m.Policy)

    def update(self, policy_id: str, *, idempotency_key: str | None = None, **changes: Any) -> m.Policy:
        """Change a policy: a new immutable version. Scope: policy:admin."""
        body = m.PolicyUpdate(**changes)
        return self._h.request(
            "PATCH", f"/v1/policies/{policy_id}", model=m.Policy, json=body, idempotency_key=idempotency_key
        )

    def list_versions(self, policy_id: str) -> m.PolicyVersionList:
        """Every version, oldest first. Scope: policy:read."""
        return self._h.request("GET", f"/v1/policies/{policy_id}/versions", model=m.PolicyVersionList)

    def evaluate(
        self,
        *,
        quote_id: str | None = None,
        document_id: str | None = None,
        to: str | None = None,
        from_: str | None = None,
    ) -> m.PolicyEvaluation:
        """Dry run: would this key's policies allow the send? Never executes. Scope: policy:read."""
        body = {"quote_id": quote_id, "document_id": document_id, "to": to, "from": from_}
        return self._h.request("POST", "/v1/policy-evaluations", model=m.PolicyEvaluation, json=body)


class Agents(_Resource):
    def list(self) -> m.AgentList:
        """Agent identities. Scope: agent:read."""
        return self._h.request("GET", "/v1/agents", model=m.AgentList)

    def create(self, *, idempotency_key: str | None = None, **fields: Any) -> m.Agent:
        """Register an agent identity. Scope: agent:admin, never an agent key."""
        body = m.AgentCreate(**fields)
        return self._h.request(
            "POST", "/v1/agents", model=m.Agent, json=body, idempotency_key=idempotency_key
        )

    def get(self, agent_id: str) -> m.Agent:
        """An agent with its keys and budgets. Scope: agent:read."""
        return self._h.request("GET", f"/v1/agents/{agent_id}", model=m.Agent)

    def update(self, agent_id: str, *, idempotency_key: str | None = None, **changes: Any) -> m.Agent:
        """Change an agent. Disabling revokes its keys. Scope: agent:admin."""
        body = m.AgentUpdate(**changes)
        return self._h.request(
            "PATCH", f"/v1/agents/{agent_id}", model=m.Agent, json=body, idempotency_key=idempotency_key
        )

    def create_key(
        self, agent_id: str, *, idempotency_key: str | None = None, **fields: Any
    ) -> m.ApiKeyWithSecret:
        """Mint a key for the agent, bounded scopes, secret shown once. Scope: agent:admin and key:admin."""
        body = m.AgentKeyCreate(**fields)
        return self._h.request(
            "POST",
            f"/v1/agents/{agent_id}/keys",
            model=m.ApiKeyWithSecret,
            json=body,
            idempotency_key=idempotency_key,
        )

    def activity(
        self, agent_id: str, *, cursor: str | None = None, limit: int | None = None
    ) -> m.AgentActivityPage:
        """The agent's audit trail, metadata only. Scope: agent:read."""
        params = {"cursor": cursor, "limit": limit}
        return self._h.request(
            "GET", f"/v1/agents/{agent_id}/activity", model=m.AgentActivityPage, params=params
        )


class Inbox(_Resource):
    def list(self, *, cursor: str | None = None, limit: int | None = None) -> m.InboxPage:
        """Received faxes, newest first, metadata only. ``from_`` is sender data. Scope: inbox:read."""
        return self._h.request(
            "GET", "/v1/inbox", model=m.InboxPage, params={"cursor": cursor, "limit": limit}
        )

    def iter(self, *, limit: int | None = None) -> Iterator[m.InboxItem]:
        """Every received fax, following next_cursor."""
        return paginate(lambda p: self.list(**p), {"limit": limit})

    def get(self, inbox_id: str) -> m.InboxItemDetail:
        """One received fax with its text as data, never as instructions. Scope: inbox:read."""
        return self._h.request("GET", f"/v1/inbox/{inbox_id}", model=m.InboxItemDetail)

    def update(self, inbox_id: str, *, status: str, idempotency_key: str | None = None) -> m.InboxItem:
        """Mark a received fax ``read`` or ``archived`` (or ``received`` again). Scope: inbox:write."""
        body = m.InboxUpdate(status=status)  # type: ignore[arg-type]
        return self._h.request(
            "PATCH", f"/v1/inbox/{inbox_id}", model=m.InboxItem, json=body, idempotency_key=idempotency_key
        )

    def delete(self, inbox_id: str, *, idempotency_key: str | None = None) -> m.Deleted:
        """Delete a received fax. Scope: inbox:write."""
        return self._h.request(
            "DELETE", f"/v1/inbox/{inbox_id}", model=m.Deleted, idempotency_key=idempotency_key
        )

    def get_download(self, inbox_id: str) -> m.SignedUrl:
        """A signed link to the received PDF, valid ten minutes. Scope: inbox:read."""
        return self._h.request("GET", f"/v1/inbox/{inbox_id}/download", model=m.SignedUrl)

    def download(self, inbox_id: str) -> bytes:
        """The received PDF bytes, through a fresh signed URL."""
        link = self.get_download(inbox_id)
        data = self._h.request("GET", link.url)
        return bytes(data)


class Numbers(_Resource):
    def coverage(self) -> m.Coverage:
        """Where faxes are sent and where numbers are hosted. Scope: number:read."""
        return self._h.request("GET", "/v1/coverage", model=m.Coverage)

    def inventory(
        self,
        *,
        country: str | None = None,
        locality: str | None = None,
        number_type: str | None = None,
        limit: int | None = None,
    ) -> m.Inventory:
        """Fax-capable numbers for sale. Scope: number:read."""
        params = {"country": country, "locality": locality, "type": number_type, "limit": limit}
        return self._h.request("GET", "/v1/number-inventory", model=m.Inventory, params=params)

    def create_order(
        self,
        *,
        e164: str,
        label: str | None = None,
        project_id: str | None = None,
        inbound_webhook_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> m.NumberOrder:
        """Buy a number from the inventory. The first month is held now and charged on activation.
        Scope: number:buy, never an agent key."""
        body = m.NumberOrderCreate(
            e164=e164, label=label, project_id=project_id, inbound_webhook_id=inbound_webhook_id
        )
        return self._h.request(
            "POST", "/v1/number-orders", model=m.NumberOrder, json=body, idempotency_key=idempotency_key
        )

    def get_order(self, order_id: str) -> m.NumberOrder:
        """Read a number order. Scope: number:read."""
        return self._h.request("GET", f"/v1/number-orders/{order_id}", model=m.NumberOrder)

    def list(self, *, include_released: bool | None = None) -> m.NumberList:
        """Your numbers. Scope: number:read."""
        return self._h.request(
            "GET", "/v1/numbers", model=m.NumberList, params={"include_released": include_released}
        )

    def get(self, number_id: str) -> m.Number:
        """One number. Scope: number:read."""
        return self._h.request("GET", f"/v1/numbers/{number_id}", model=m.Number)

    def update(self, number_id: str, *, idempotency_key: str | None = None, **changes: Any) -> m.Number:
        """Change the label, project or inbound webhook of a number. Scope: number:buy."""
        body = m.NumberUpdate(**changes)
        return self._h.request(
            "PATCH", f"/v1/numbers/{number_id}", model=m.Number, json=body, idempotency_key=idempotency_key
        )

    def release(self, number_id: str, *, confirm: str, idempotency_key: str | None = None) -> m.Number:
        """Release a number. ``confirm`` is the number itself, typed out. A 30 day quarantine follows.
        Scope: number:buy."""
        body = m.ReleaseRequest(confirm=confirm)
        return self._h.request(
            "POST",
            f"/v1/numbers/{number_id}/release-requests",
            model=m.Number,
            json=body,
            idempotency_key=idempotency_key,
        )

    def check_portability(
        self, numbers: Sequence[str], *, idempotency_key: str | None = None
    ) -> m.PortabilityCheck:
        """Whether these numbers can port in. Scope: number:read."""
        body = m.PortabilityCheckCreate(numbers=list(numbers))
        return self._h.request(
            "POST",
            "/v1/portability-checks",
            model=m.PortabilityCheck,
            json=body,
            idempotency_key=idempotency_key,
        )

    def list_port_orders(self) -> m.PortOrderList:
        """Port orders and port-out requests. Scope: number:read."""
        return self._h.request("GET", "/v1/port-orders", model=m.PortOrderList)

    def create_port_order(
        self, numbers: Sequence[str], *, note: str | None = None, idempotency_key: str | None = None
    ) -> m.PortOrder:
        """Request a port-in, handled with you. Scope: number:buy."""
        body = m.PortOrderCreate(numbers=list(numbers), note=note)
        return self._h.request(
            "POST", "/v1/port-orders", model=m.PortOrder, json=body, idempotency_key=idempotency_key
        )

    def get_port_order(self, port_id: str) -> m.PortOrder:
        """One port order. Scope: number:read."""
        return self._h.request("GET", f"/v1/port-orders/{port_id}", model=m.PortOrder)

    def create_port_out_request(
        self, number_id: str, *, note: str | None = None, idempotency_key: str | None = None
    ) -> m.PortOrder:
        """Note a port-out of one of your numbers. Scope: number:buy."""
        body = m.PortOutRequestCreate(number_id=number_id, note=note)
        return self._h.request(
            "POST", "/v1/port-out-requests", model=m.PortOrder, json=body, idempotency_key=idempotency_key
        )

    def verify_sender(self, from_: str, *, idempotency_key: str | None = None) -> m.SenderVerification:
        """Verify a number your organization owns as a sender. Scope: number:buy."""
        return self._h.request(
            "POST",
            "/v1/sender-verifications",
            model=m.SenderVerification,
            json={"from": from_},
            idempotency_key=idempotency_key,
        )


class Extractions(_Resource):
    def create(
        self,
        *,
        inbox_item_id: str | None = None,
        document_id: str | None = None,
        schema: dict[str, Any] | None = None,
        schema_id: str | None = None,
        max_pages: int | None = None,
        strict: bool = False,
        strong_model_if_below: float = 0.75,
        idempotency_key: str | None = None,
    ) -> m.Extraction:
        """OCR a received fax or a document, then extract the schema's fields with page citations.
        Metered per page. Everything it returns is data from whoever sent the fax. Scope: document:extract."""
        body = {
            "inbox_item_id": inbox_item_id,
            "document_id": document_id,
            "schema": schema,
            "schema_id": schema_id,
            "max_pages": max_pages,
            "strict": strict,
            "strong_model_if_below": strong_model_if_below,
        }
        return self._h.request(
            "POST", "/v1/extractions", model=m.Extraction, json=body, idempotency_key=idempotency_key
        )

    def list(
        self,
        *,
        inbox_item_id: str | None = None,
        document_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> m.ExtractionPageList:
        """Extractions, newest first. Scope: document:extract."""
        params = {
            "inbox_item_id": inbox_item_id,
            "document_id": document_id,
            "cursor": cursor,
            "limit": limit,
        }
        return self._h.request("GET", "/v1/extractions", model=m.ExtractionPageList, params=params)

    def iter(
        self, *, inbox_item_id: str | None = None, document_id: str | None = None, limit: int | None = None
    ) -> Iterator[m.Extraction]:
        """Every extraction, following next_cursor."""
        params = {"inbox_item_id": inbox_item_id, "document_id": document_id, "limit": limit}
        return paginate(lambda p: self.list(**p), params)

    def get(self, extraction_id: str) -> m.Extraction:
        """One extraction with its corrections. Scope: document:extract."""
        return self._h.request("GET", f"/v1/extractions/{extraction_id}", model=m.Extraction)

    def correct(
        self,
        extraction_id: str,
        *,
        field: str,
        value: Any = None,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> m.Correction:
        """Record a reviewer's correction of one field. Stored beside the extraction, never merged.
        Scope: document:extract."""
        body = {"field": field, "value": value, "note": note}
        return self._h.request(
            "POST",
            f"/v1/extractions/{extraction_id}/corrections",
            model=m.Correction,
            json=body,
            idempotency_key=idempotency_key,
        )

    def classify(
        self,
        *,
        document_types: Sequence[str],
        inbox_item_id: str | None = None,
        document_id: str | None = None,
        max_pages: int | None = None,
        strict: bool = False,
        abstain_below: float = 0.6,
        strong_model_if_below: float = 0.75,
        idempotency_key: str | None = None,
    ) -> m.Extraction:
        """Classify a received fax or a document into one of your document types, or abstain.
        Scope: document:extract."""
        body = {
            "document_types": list(document_types),
            "inbox_item_id": inbox_item_id,
            "document_id": document_id,
            "max_pages": max_pages,
            "strict": strict,
            "abstain_below": abstain_below,
            "strong_model_if_below": strong_model_if_below,
        }
        return self._h.request(
            "POST", "/v1/classifications", model=m.Extraction, json=body, idempotency_key=idempotency_key
        )

    def list_classifications(
        self,
        *,
        inbox_item_id: str | None = None,
        document_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> m.ExtractionPageList:
        """Classifications, newest first. Scope: document:extract."""
        params = {
            "inbox_item_id": inbox_item_id,
            "document_id": document_id,
            "cursor": cursor,
            "limit": limit,
        }
        return self._h.request("GET", "/v1/classifications", model=m.ExtractionPageList, params=params)

    def iter_classifications(
        self, *, inbox_item_id: str | None = None, document_id: str | None = None, limit: int | None = None
    ) -> Iterator[m.Extraction]:
        """Every classification, following next_cursor."""
        params = {"inbox_item_id": inbox_item_id, "document_id": document_id, "limit": limit}
        return paginate(lambda p: self.list_classifications(**p), params)

    def get_classification(self, classification_id: str) -> m.Extraction:
        """One classification. Scope: document:extract."""
        return self._h.request("GET", f"/v1/classifications/{classification_id}", model=m.Extraction)

    def summarize(
        self,
        *,
        inbox_item_id: str | None = None,
        document_id: str | None = None,
        max_pages: int | None = None,
        strict: bool = False,
        strong_model_if_below: float = 0.75,
        idempotency_key: str | None = None,
    ) -> m.Extraction:
        """Summarise a received fax or a document. Every sentence cites its pages. Scope: document:extract."""
        body = {
            "inbox_item_id": inbox_item_id,
            "document_id": document_id,
            "max_pages": max_pages,
            "strict": strict,
            "strong_model_if_below": strong_model_if_below,
        }
        return self._h.request(
            "POST", "/v1/document-summaries", model=m.Extraction, json=body, idempotency_key=idempotency_key
        )

    def list_summaries(
        self,
        *,
        inbox_item_id: str | None = None,
        document_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> m.ExtractionPageList:
        """Summaries, newest first. Scope: document:extract."""
        params = {
            "inbox_item_id": inbox_item_id,
            "document_id": document_id,
            "cursor": cursor,
            "limit": limit,
        }
        return self._h.request("GET", "/v1/document-summaries", model=m.ExtractionPageList, params=params)

    def iter_summaries(
        self, *, inbox_item_id: str | None = None, document_id: str | None = None, limit: int | None = None
    ) -> Iterator[m.Extraction]:
        """Every summary, following next_cursor."""
        params = {"inbox_item_id": inbox_item_id, "document_id": document_id, "limit": limit}
        return paginate(lambda p: self.list_summaries(**p), params)

    def get_summary(self, summary_id: str) -> m.Extraction:
        """One summary. Scope: document:extract."""
        return self._h.request("GET", f"/v1/document-summaries/{summary_id}", model=m.Extraction)

    def list_schemas(self) -> m.ExtractionSchemaList:
        """Your saved extraction schemas. Scope: document:extract."""
        return self._h.request("GET", "/v1/extraction-schemas", model=m.ExtractionSchemaList)

    def create_schema(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> m.ExtractionSchema:
        """Save a versioned extraction schema. The same name again is the next version.
        Scope: document:extract."""
        body = {"name": name, "schema": schema, "description": description}
        return self._h.request(
            "POST",
            "/v1/extraction-schemas",
            model=m.ExtractionSchema,
            json=body,
            idempotency_key=idempotency_key,
        )

    def get_schema(self, schema_id: str) -> m.ExtractionSchema:
        """One schema version. Scope: document:extract."""
        return self._h.request("GET", f"/v1/extraction-schemas/{schema_id}", model=m.ExtractionSchema)


class Organizations(_Resource):
    def list(self) -> m.OrganizationList:
        """The organizations the caller belongs to. Scope: any."""
        return self._h.request("GET", "/v1/organizations", model=m.OrganizationList)

    def current(self) -> m.Organization:
        """The organization of this key. Scope: account:read."""
        return self._h.request("GET", "/v1/organizations/current", model=m.Organization)

    def update_current(self, *, name: str, idempotency_key: str | None = None) -> m.Organization:
        """Rename the organization. Scope: member:admin, never an agent key."""
        body = m.OrganizationPatch(name=name)
        return self._h.request(
            "PATCH",
            "/v1/organizations/current",
            model=m.Organization,
            json=body,
            idempotency_key=idempotency_key,
        )

    def request_data_export(self, *, idempotency_key: str | None = None) -> m.Requested:
        """Ask for an export of the organization's data. Scope: org:admin, never an agent key."""
        return self._h.request(
            "POST",
            "/v1/organizations/current/export-requests",
            model=m.Requested,
            json={},
            idempotency_key=idempotency_key,
        )

    def request_data_deletion(self, *, idempotency_key: str | None = None) -> m.Requested:
        """Ask for the organization and its data to be deleted. Scope: org:admin, never an agent key."""
        return self._h.request(
            "POST",
            "/v1/organizations/current/deletion-requests",
            model=m.Requested,
            json={},
            idempotency_key=idempotency_key,
        )


class Members(_Resource):
    def list(self) -> m.MemberList:
        """The organization's members. Scope: account:read."""
        return self._h.request("GET", "/v1/members", model=m.MemberList)

    def update(self, member_id: str, *, role: str, idempotency_key: str | None = None) -> m.Member:
        """Change a member's role: owner, admin, member or viewer. Scope: member:admin, never an agent key."""
        body = m.MemberPatch(role=role)  # type: ignore[arg-type]
        return self._h.request(
            "PATCH", f"/v1/members/{member_id}", model=m.Member, json=body, idempotency_key=idempotency_key
        )

    def remove(self, member_id: str, *, idempotency_key: str | None = None) -> None:
        """Remove a member. Scope: org:admin, never an agent key."""
        self._h.request("DELETE", f"/v1/members/{member_id}", idempotency_key=idempotency_key)

    def list_invitations(self) -> m.InvitationList:
        """Open invitations. Scope: member:admin."""
        return self._h.request("GET", "/v1/invitations", model=m.InvitationList)

    def invite(self, *, email: str, role: str = "member", idempotency_key: str | None = None) -> m.Invitation:
        """Invite someone by email, a link that works for 7 days. Scope: member:admin, never an agent key."""
        body = m.InvitationCreate(email=email, role=role)  # type: ignore[arg-type]
        return self._h.request(
            "POST", "/v1/invitations", model=m.Invitation, json=body, idempotency_key=idempotency_key
        )

    def revoke_invitation(self, invitation_id: str, *, idempotency_key: str | None = None) -> None:
        """Withdraw an invitation. Scope: member:admin, never an agent key."""
        self._h.request("DELETE", f"/v1/invitations/{invitation_id}", idempotency_key=idempotency_key)


class Client:
    """The synchronous Fax for Agents client. Use it as a context manager or call ``close``."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        max_retry_after: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = Settings(api_key, base_url, timeout, max_retries, backoff_base, max_retry_after)
        self._h = SyncHttp(self.settings, transport)
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
        self.numbers = Numbers(self._h)
        self.extractions = Extractions(self._h)
        self.organizations = Organizations(self._h)
        self.members = Members(self._h)

    def me(self) -> m.Me:
        """Shortcut for ``identity.me()``."""
        return self.identity.me()

    def request(self, method: str, path: str, **kw: Any) -> Any:
        """Any operation the resources do not cover yet. Returns the parsed JSON (or bytes)."""
        return self._h.request(method, path, **kw)

    def close(self) -> None:
        self._h.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
