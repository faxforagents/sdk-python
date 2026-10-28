"""Python client for Fax for Agents, https://faxforagents.com.

Fax for Agents is a fax API, a hosted MCP server and SDKs built so AI agents can send and receive
faxes. This package wraps every ``/v1`` operation of the REST API, with typed models, idempotency keys
on every mutating call, retries that honour ``Retry-After``, pagination helpers and the webhook
signature check.

    from faxforagents import Client

    with Client() as ffa:                        # reads FAXFORAGENTS_API_KEY
        doc = ffa.documents.create(text="Hello from an agent.")
        quote = ffa.quotes.create(document_id=doc.id, to="+15550000001")
        fax = ffa.faxes.wait(ffa.faxes.create(quote_id=quote.id).id)
        print(fax.state)                         # delivered, with fax.evidence

Documentation: https://faxforagents.com/docs. The API reference: https://faxforagents.github.io/openapi/.
"""

from __future__ import annotations

__version__ = "0.2.0"

from faxforagents.async_client import AsyncClient
from faxforagents.client import TERMINAL_STATES, Client
from faxforagents.errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    ConflictError,
    FaxForAgentsError,
    InvalidRequestError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitedError,
    ScopeError,
    ServerError,
)
from faxforagents.webhooks import SignatureError, WebhookEvent, parse_event, verify_webhook

__all__ = [
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "AsyncClient",
    "AuthenticationError",
    "Client",
    "ConflictError",
    "FaxForAgentsError",
    "InvalidRequestError",
    "NotFoundError",
    "PaymentRequiredError",
    "RateLimitedError",
    "ScopeError",
    "ServerError",
    "SignatureError",
    "TERMINAL_STATES",
    "WebhookEvent",
    "__version__",
    "parse_event",
    "verify_webhook",
]
