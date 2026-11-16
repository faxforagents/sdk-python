"""The 70 models 0.10.0 removed stay out, and the generator models only what an operation reaches."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from faxforagents import models

ROOT = Path(__file__).resolve().parents[1]

#: No method of either client took or returned them, and the public document of API 1.20.11 does not
#: define them. They came from the server's own document, most of them from the admin console and the
#: dashboard sign-in.
REMOVED = (
    "AbuseReview",
    "AbuseReviewCreate",
    "AbuseReviewPage",
    "AbuseReviewPatch",
    "Accepted",
    "Adjustment",
    "AdjustmentApply",
    "AdjustmentCreate",
    "AdjustmentPage",
    "AdminAction",
    "AdminBrief",
    "AdminDaily",
    "AdminDailyPoint",
    "AdminJob",
    "AdminJobPage",
    "AdminOrganization",
    "AdminOrganizationDetail",
    "AdminOrganizationPage",
    "AdminRevenue",
    "AdminStats",
    "AdminWhoami",
    "AnalyticsJourneys",
    "AnalyticsOrganization",
    "AnalyticsSource",
    "AnalyticsSources",
    "AnalyticsUsage",
    "AnalyticsWebsite",
    "AuditEvent",
    "AuditEventPage",
    "Client",
    "ClientList",
    "MagicLinkRequest",
    "OAuthConsent",
    "OAuthConsentClient",
    "OAuthConsentDecision",
    "OAuthConsentOrganization",
    "OAuthRedirect",
    "OutboxDiscarded",
    "OutboxPage",
    "OutboxRow",
    "Passkey",
    "PasskeyList",
    "PasskeyLoginVerify",
    "PasskeyRegisterVerify",
    "ProviderHealth",
    "ProviderList",
    "Providers",
    "Reauth",
    "ReconcileResult",
    "ReconciliationReport",
    "ReleaseResult",
    "ReviewAccessAction",
    "ReviewAccessCreate",
    "ReviewAccessIssued",
    "ReviewSignIn",
    "Revoked",
    "RouteList",
    "RouteState",
    "Sent",
    "SessionInfo",
    "SessionList",
    "SessionOut",
    "SwitchOrganization",
    "TelnyxAck",
    "UserOut",
    "WebhookAck",
    "faxforagents__api__routes__inbox__InboxItem",
    "faxforagents__api__schemas__InboxItem",
    "faxforagents__api__schemas__Me",
    "faxforagents__auth__schemas__Me",
)


def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_models", ROOT / "scripts" / "gen_models.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def saved_spec() -> dict[str, Any]:
    spec: dict[str, Any] = yaml.safe_load((ROOT / "spec" / "openapi.yaml").read_text("utf-8"))
    return spec


def test_no_removed_model_is_back_in_the_package() -> None:
    assert len(set(REMOVED)) == len(REMOVED) == 70
    back = [name for name in REMOVED if hasattr(models, name) or name in models.__all__]
    assert not back, "Removed in 0.10.0, back in faxforagents.models: " + ", ".join(back)


def test_every_schema_of_the_saved_spec_is_reached_by_an_operation() -> None:
    spec = saved_spec()
    orphans = sorted(set(spec["components"]["schemas"]) - generator().reached_schemas(spec))
    hint = "No operation reaches these schemas. Drop them from spec/openapi.yaml: "
    assert not orphans, hint + ", ".join(orphans)


def test_a_spec_that_carries_them_again_generates_the_same_module() -> None:
    gen = generator()
    spec = saved_spec()
    expected = gen.render(spec)
    again = copy.deepcopy(spec)
    for name in REMOVED:
        # Each points at a model the clients use, as Accepted did, and nothing points at it.
        again["components"]["schemas"][name] = {
            "type": "object",
            "properties": {"organization": {"$ref": "#/components/schemas/OrganizationSummary"}},
            "required": ["organization"],
        }
    assert gen.render(again) == expected


def test_reached_means_through_paths_shared_responses_and_fields() -> None:
    def ref(name: str, kind: str = "schemas") -> dict[str, str]:
        return {"$ref": f"#/components/{kind}/{name}"}

    def body(schema: dict[str, Any]) -> dict[str, Any]:
        return {"content": {"application/json": {"schema": schema}}}

    spec = {
        "info": {"version": "0.0.0"},
        "paths": {
            "/v1/things": {
                "post": {
                    "requestBody": body(ref("ThingCreate")),
                    "responses": {"201": body(ref("ThingPage")), "404": ref("NotFound", "responses")},
                }
            }
        },
        "components": {
            "responses": {"NotFound": body(ref("ErrorEnvelope"))},
            "schemas": {
                "ThingCreate": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"name": {"type": "string"}},
                },
                "ThingPage": {
                    "type": "object",
                    "properties": {"data": {"type": "array", "items": ref("Thing")}},
                },
                "Thing": {"type": "object", "properties": {"id": {"type": "string"}}},
                "ErrorEnvelope": {"type": "object", "properties": {"error": ref("ErrorBody")}},
                "ErrorBody": {"type": "object", "properties": {"code": {"type": "string"}}},
                "AdminStats": {"type": "object", "properties": {"thing": ref("Thing")}},
                "AdminStatsPage": {
                    "type": "object",
                    "properties": {"data": {"type": "array", "items": ref("AdminStats")}},
                },
            },
        },
    }
    gen = generator()
    assert gen.reached_schemas(spec) == {"ThingCreate", "ThingPage", "Thing", "ErrorEnvelope", "ErrorBody"}
    text = gen.render(spec)
    for name in ("ThingCreate", "ThingPage", "Thing", "ErrorEnvelope", "ErrorBody"):
        assert f"class {name}(" in text
    assert "AdminStats" not in text
