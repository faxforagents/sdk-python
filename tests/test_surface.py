"""The sync and async clients expose the same resources, methods and signatures."""

from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from faxforagents import async_client, client

RESOURCES = [
    "identity",
    "documents",
    "quotes",
    "faxes",
    "webhooks",
    "events",
    "billing",
    "platform",
    "sandbox",
    "approvals",
    "policies",
    "agents",
    "inbox",
    "numbers",
    "extractions",
    "organizations",
    "members",
]


def public_methods(cls: type) -> dict[str, inspect.Signature]:
    out = {}
    for name, fn in inspect.getmembers(cls, inspect.isfunction):
        if name.startswith("_"):
            continue
        sig = inspect.signature(fn)
        out[name] = sig.replace(return_annotation=inspect.Signature.empty)
    return out


def test_resources_match() -> None:
    for name in RESOURCES:
        sync_cls = getattr(client, name.capitalize())
        async_cls = getattr(async_client, name.capitalize())
        s, a = public_methods(sync_cls), public_methods(async_cls)
        assert s.keys() == a.keys(), name
        for method in s:
            assert str(s[method]) == str(a[method]), f"{name}.{method}"
            if method.startswith("iter"):
                assert not inspect.iscoroutinefunction(getattr(async_cls, method))
            else:
                assert inspect.iscoroutinefunction(getattr(async_cls, method)), f"{name}.{method}"


def test_every_operation_has_a_method() -> None:
    """Every operationId of spec/openapi.yaml is reachable through a resource, signed URLs through helpers."""
    spec = yaml.safe_load(Path("spec/openapi.yaml").read_text(encoding="utf-8"))
    covered = {
        "get_me": "identity.me",
        "list_api_keys": "identity.list_api_keys",
        "create_api_key": "identity.create_api_key",
        "rotate_api_key": "identity.rotate_api_key",
        "revoke_api_key": "identity.revoke_api_key",
        "create_upload": "documents.create_upload",
        "put_upload_content": "documents.put_upload_content",
        "complete_upload": "documents.complete_upload",
        "cancel_upload": "documents.cancel_upload",
        "create_document": "documents.create",
        "list_documents": "documents.list",
        "get_document": "documents.get",
        "delete_document": "documents.delete",
        "get_document_validation": "documents.get_validation",
        "get_document_download": "documents.get_download",
        "get_document_content": "documents.download",
        "list_document_pages": "documents.list_pages",
        "get_document_page_content": "documents.list_pages",
        "create_fax_quote": "quotes.create",
        "get_fax_quote": "quotes.get",
        "create_fax": "faxes.create",
        "list_faxes": "faxes.list",
        "get_fax": "faxes.get",
        "list_fax_events": "faxes.list_events",
        "list_fax_attempts": "faxes.list_attempts",
        "get_fax_receipt": "faxes.get_receipt",
        "cancel_fax": "faxes.cancel",
        "list_webhooks": "webhooks.list",
        "create_webhook": "webhooks.create",
        "update_webhook": "webhooks.update",
        "delete_webhook": "webhooks.delete",
        "rotate_webhook_secret": "webhooks.rotate_secret",
        "test_webhook": "webhooks.test",
        "list_webhook_deliveries": "webhooks.list_deliveries",
        "replay_webhook_delivery": "webhooks.replay_delivery",
        "list_events": "events.list",
        "get_balance": "billing.balance",
        "list_ledger": "billing.list_ledger",
        "get_billing_settings": "billing.get_settings",
        "patch_billing_settings": "billing.patch_settings",
        "create_topup_session": "billing.create_topup_session",
        "list_topup_sessions": "billing.list_topup_sessions",
        "get_topup_session": "billing.get_topup_session",
        "list_auto_topups": "billing.list_auto_topups",
        "create_auto_topup": "billing.create_auto_topup",
        "delete_auto_topup": "billing.delete_auto_topup",
        "list_budgets": "billing.list_budgets",
        "create_budget": "billing.create_budget",
        "patch_budget": "billing.patch_budget",
        "get_usage": "billing.usage",
        "create_usage_export": "billing.create_usage_export",
        "get_usage_export": "billing.get_usage_export",
        "get_usage_export_content": "billing.get_usage_export",
        "list_invoices": "billing.list_invoices",
        "get_invoice": "billing.get_invoice",
        "create_subscription_checkout": "billing.create_subscription_checkout",
        "list_subscriptions": "billing.list_subscriptions",
        "create_portal_session": "billing.create_portal_session",
        "get_capabilities": "platform.capabilities",
        "get_service_status": "platform.service_status",
        "list_sandbox_scenarios": "sandbox.list_scenarios",
        "create_sandbox_scenario": "sandbox.create_scenario",
        "create_sandbox_inbound_fax": "sandbox.create_inbound_fax",
        "reset_sandbox_data": "sandbox.reset",
        "create_approval_request": "approvals.request",
        "list_approval_requests": "approvals.list",
        "get_approval_request": "approvals.get",
        "approve_request": "approvals.approve",
        "reject_request": "approvals.reject",
        "revoke_approval": "approvals.revoke",
        "list_policies": "policies.list",
        "create_policy": "policies.create",
        "get_effective_policies": "policies.effective",
        "get_policy": "policies.get",
        "update_policy": "policies.update",
        "list_policy_versions": "policies.list_versions",
        "create_policy_evaluation": "policies.evaluate",
        "list_agents": "agents.list",
        "create_agent": "agents.create",
        "get_agent": "agents.get",
        "update_agent": "agents.update",
        "create_agent_key": "agents.create_key",
        "get_agent_activity": "agents.activity",
        "list_inbox": "inbox.list",
        "get_inbox_item": "inbox.get",
        "update_inbox_item": "inbox.update",
        "delete_inbox_item": "inbox.delete",
        "get_inbox_download": "inbox.get_download",
        "get_inbox_content": "inbox.download",
        "get_coverage": "numbers.coverage",
        "get_number_inventory": "numbers.inventory",
        "create_number_order": "numbers.create_order",
        "get_number_order": "numbers.get_order",
        "list_numbers": "numbers.list",
        "get_number": "numbers.get",
        "update_number": "numbers.update",
        "create_release_request": "numbers.release",
        "create_portability_check": "numbers.check_portability",
        "list_port_orders": "numbers.list_port_orders",
        "create_port_order": "numbers.create_port_order",
        "get_port_order": "numbers.get_port_order",
        "create_port_out_request": "numbers.create_port_out_request",
        "create_sender_verification": "numbers.verify_sender",
        "create_extraction": "extractions.create",
        "list_extractions": "extractions.list",
        "get_extraction": "extractions.get",
        "create_correction": "extractions.correct",
        "create_classification": "extractions.classify",
        "list_classifications": "extractions.list_classifications",
        "get_classification": "extractions.get_classification",
        "create_document_summary": "extractions.summarize",
        "list_document_summaries": "extractions.list_summaries",
        "get_document_summary": "extractions.get_summary",
        "list_extraction_schemas": "extractions.list_schemas",
        "create_extraction_schema": "extractions.create_schema",
        "get_extraction_schema": "extractions.get_schema",
        "list_organizations": "organizations.list",
        "get_current_organization": "organizations.current",
        "update_current_organization": "organizations.update_current",
        "request_data_export": "organizations.request_data_export",
        "request_data_deletion": "organizations.request_data_deletion",
        "list_members": "members.list",
        "update_member": "members.update",
        "remove_member": "members.remove",
        "list_invitations": "members.list_invitations",
        "create_invitation": "members.invite",
        "revoke_invitation": "members.revoke_invitation",
    }
    ids = {op["operationId"] for ops in spec["paths"].values() for op in ops.values()}
    assert ids == set(covered), ids ^ set(covered)
    c = client.Client("ffa_test_x")
    for target in covered.values():
        res, meth = target.split(".")
        assert callable(getattr(getattr(c, res), meth)), target
