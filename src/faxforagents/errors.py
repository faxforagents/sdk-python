"""Exceptions of the Fax for Agents client.

Every API refusal is one envelope: ``{"error": {"code", "message", "retryable", "correlation_id",
"details"}}``. The client raises ``APIError`` (or a subclass picked by status) with those fields. The
``code`` is the stable thing to branch on: ``quote_expired``, ``insufficient_balance``,
``approval_required`` and so on. Quote the ``correlation_id`` when you write to hello@faxforagents.com.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast


class FaxForAgentsError(Exception):
    """Base of every error this package raises."""


class APIConnectionError(FaxForAgentsError):
    """The API could not be reached, or the connection dropped."""


class APITimeoutError(APIConnectionError):
    """The request ran past its timeout."""


class APIError(FaxForAgentsError):
    """The API answered with an error envelope."""

    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        retryable: bool = False,
        correlation_id: str | None = None,
        details: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(
            f"{code}: {message}" + (f" (correlation {correlation_id})" if correlation_id else "")
        )
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.correlation_id = correlation_id
        self.details = details
        self.retry_after = retry_after


class InvalidRequestError(APIError):
    """400 or 422: the request did not pass validation."""


class AuthenticationError(APIError):
    """401 invalid_token: no key, or a revoked or expired one."""


class ScopeError(APIError):
    """403: the key lacks the scope (``details["scope"]``), or an agent key tried to administer."""


class NotFoundError(APIError):
    """404 not_found: not an id of this organization, or deleted."""


class ConflictError(APIError):
    """409: the resource is in a state that refuses this change (``quote_consumed``, ``not_cancelable``)."""


class OneTimeOffer(TypedDict, total=False):
    """``details["one_time_offer"]`` of 402 insufficient_balance: the card path for this exact send.

    A pointer, never a promise: ``one_time_sends.create_from_quote`` runs every check again."""

    amount_micro: int
    currency: str
    document_pages: int
    included_pages: int
    extra_page_micro: int
    max_document_pages: int
    approval_id: str | None


class PaymentRequiredError(APIError):
    """402 insufficient_balance: the wallet does not cover the fax. A person tops up."""

    @property
    def top_up_url(self) -> str | None:
        """``details["top_up_url"]``: the dashboard page where a person adds funds, else ``None``.

        A key cannot top up. Servers send it since API 1.17.0."""
        url = self.details.get("top_up_url") if isinstance(self.details, dict) else None
        return url if isinstance(url, str) else None

    @property
    def one_time_offer(self) -> OneTimeOffer | None:
        """``details["one_time_offer"]`` when the server sent one, else ``None``.

        Present when this exact send can be paid by card: it names the one-time price in
        ``amount_micro``, the page counts and the ``approval_id`` that authorizes the order. Pass the
        quote to ``one_time_sends.create_from_quote``. Servers send it since API 1.17.0."""
        offer = self.details.get("one_time_offer") if isinstance(self.details, dict) else None
        return cast(OneTimeOffer, offer) if isinstance(offer, dict) else None


class RateLimitedError(APIError):
    """429 rate_limited. ``retry_after`` is the seconds the API asked for."""


class ServerError(APIError):
    """5xx: the API failed. The client already retried."""


_BY_STATUS: dict[int, type[APIError]] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    402: PaymentRequiredError,
    403: ScopeError,
    404: NotFoundError,
    409: ConflictError,
    422: InvalidRequestError,
    429: RateLimitedError,
}


def error_for(status: int, payload: Any, *, retry_after: float | None = None, text: str = "") -> APIError:
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        detail = payload.get("detail") if isinstance(payload, dict) else None
        err = {"code": f"http_{status}", "message": text[:200] or f"HTTP {status}", "details": detail}
    cls = ServerError if status >= 500 else _BY_STATUS.get(status, APIError)
    return cls(
        status=status,
        code=str(err.get("code") or f"http_{status}"),
        message=str(err.get("message") or f"HTTP {status}"),
        retryable=bool(err.get("retryable", status in (429, 502, 503, 504))),
        correlation_id=err.get("correlation_id"),
        details=err.get("details"),
        retry_after=retry_after,
    )
