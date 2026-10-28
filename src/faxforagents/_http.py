"""The HTTP layer under both clients: one key, one base URL, idempotency keys, retries, the error envelope.

- Every mutating call (POST, PATCH, DELETE) carries an ``Idempotency-Key``. When the caller passes none,
  a uuid4 is minted once per call and reused on every retry of that call, so a retried send can never
  create a second fax.
- 429 and 5xx answers are retried up to ``max_retries`` times. ``Retry-After`` is honoured when present
  (seconds or an HTTP date, capped at ``max_retry_after``). Otherwise the wait is exponential with full
  jitter from ``backoff_base``. Connection errors and timeouts are retried the same way.
- An error status raises ``APIError`` or the subclass for that status (``errors.py``).
"""

from __future__ import annotations

import email.utils
import os
import random
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any, TypeVar, overload

import httpx
from pydantic import BaseModel

from faxforagents import __version__
from faxforagents.errors import APIConnectionError, APIError, APITimeoutError, error_for

DEFAULT_BASE_URL = "https://api.faxforagents.com"
ENV_KEY = "FAXFORAGENTS_API_KEY"
ENV_KEY_FALLBACK = "FFA_API_KEY"
ENV_URL = "FAXFORAGENTS_API_URL"
MUTATING = frozenset({"POST", "PATCH", "DELETE"})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

M = TypeVar("M", bound=BaseModel)


def resolve_key(key: str | None) -> str:
    value = (key or os.environ.get(ENV_KEY) or os.environ.get(ENV_KEY_FALLBACK) or "").strip()
    if not value:
        raise ValueError(
            f"no API key: pass api_key= or set {ENV_KEY}. Keys are created in the Fax for Agents dashboard "
            "or with POST /v1/api-keys."
        )
    return value


def resolve_url(base_url: str | None) -> str:
    return (base_url or os.environ.get(ENV_URL) or DEFAULT_BASE_URL).rstrip("/")


def retry_after_seconds(value: str | None, cap: float) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return min(float(value), cap)
    except ValueError:
        pass
    parsed = email.utils.parsedate_to_datetime(value) if value else None
    if parsed is None:
        return None
    return max(0.0, min(parsed.timestamp() - time.time(), cap))


def dump(body: Any) -> Any:
    if isinstance(body, BaseModel):
        return body.model_dump(by_alias=True, exclude_none=True, mode="json")
    if isinstance(body, dict):
        return {k: v for k, v in body.items() if v is not None}
    return body


def clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    out = {k: v for k, v in params.items() if v is not None}
    return out or None


class Settings:
    def __init__(
        self,
        api_key: str | None,
        base_url: str | None,
        timeout: float,
        max_retries: int,
        backoff_base: float,
        max_retry_after: float,
    ) -> None:
        self.api_key = resolve_key(api_key)
        self.base_url = resolve_url(base_url)
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff_base = backoff_base
        self.max_retry_after = max_retry_after

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": f"faxforagents-python/{__version__}",
        }

    def wait_for(self, attempt: int, retry_after: str | None) -> float:
        explicit = retry_after_seconds(retry_after, self.max_retry_after)
        if explicit is not None:
            return explicit
        return random.uniform(0, self.backoff_base * (2**attempt))  # noqa: S311 (jitter, not security)


class Call:
    """One request as the caller meant it, with the idempotency key fixed before the first attempt."""

    def __init__(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.params = clean_params(params)
        self.json = dump(json) if json is not None else None
        self.content = content
        self.headers = dict(headers or {})
        if self.method in MUTATING and "Idempotency-Key" not in self.headers:
            self.headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        if content is not None and "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/octet-stream"

    @property
    def idempotency_key(self) -> str | None:
        return self.headers.get("Idempotency-Key")


def parse(resp: httpx.Response, model: type[M] | None) -> Any:
    if not resp.content:
        return {} if model is None else model.model_validate({})
    ctype = resp.headers.get("content-type", "")
    if not ctype.startswith("application/json"):
        return resp.content
    data = resp.json()
    return data if model is None else model.model_validate(data)


def raise_for(resp: httpx.Response, settings: Settings) -> APIError:
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return error_for(
        resp.status_code,
        payload,
        retry_after=retry_after_seconds(resp.headers.get("retry-after"), settings.max_retry_after),
        text=resp.text,
    )


def paginate(fetch: Any, params: dict[str, Any], key: str = "items") -> Iterator[Any]:
    """Follow ``next_cursor`` until the API returns none. ``fetch(params)`` returns a page model or dict."""
    cursor: str | None = None
    while True:
        page = fetch({**params, "cursor": cursor})
        data = page.model_dump(by_alias=True) if isinstance(page, BaseModel) else page
        items = getattr(page, key, None) if isinstance(page, BaseModel) else data.get(key)
        yield from items or []
        cursor = data.get("next_cursor")
        if not cursor:
            return


class SyncHttp:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.base_url,
            headers=settings.headers(),
            timeout=settings.timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def send(self, call: Call) -> httpx.Response:
        attempt = 0
        while True:
            try:
                resp = self._client.request(
                    call.method,
                    call.path,
                    params=call.params,
                    json=call.json,
                    content=call.content,
                    headers=call.headers,
                )
            except httpx.TimeoutException as exc:
                if attempt >= self.settings.max_retries:
                    raise APITimeoutError(f"{call.method} {call.path} timed out") from exc
                time.sleep(self.settings.wait_for(attempt, None))
                attempt += 1
                continue
            except httpx.HTTPError as exc:
                if attempt >= self.settings.max_retries:
                    raise APIConnectionError(
                        f"{self.settings.base_url} not reachable ({type(exc).__name__})"
                    ) from exc
                time.sleep(self.settings.wait_for(attempt, None))
                attempt += 1
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self.settings.max_retries:
                time.sleep(self.settings.wait_for(attempt, resp.headers.get("retry-after")))
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise raise_for(resp, self.settings)
            return resp

    @overload
    def request(self, method: str, path: str, *, model: type[M], **kw: Any) -> M: ...

    @overload
    def request(self, method: str, path: str, *, model: None = None, **kw: Any) -> Any: ...

    def request(self, method: str, path: str, *, model: type[M] | None = None, **kw: Any) -> Any:
        return parse(self.send(Call(method, path, **kw)), model)


class AsyncHttp:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers=settings.headers(),
            timeout=settings.timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def send(self, call: Call) -> httpx.Response:
        import asyncio

        attempt = 0
        while True:
            try:
                resp = await self._client.request(
                    call.method,
                    call.path,
                    params=call.params,
                    json=call.json,
                    content=call.content,
                    headers=call.headers,
                )
            except httpx.TimeoutException as exc:
                if attempt >= self.settings.max_retries:
                    raise APITimeoutError(f"{call.method} {call.path} timed out") from exc
                await asyncio.sleep(self.settings.wait_for(attempt, None))
                attempt += 1
                continue
            except httpx.HTTPError as exc:
                if attempt >= self.settings.max_retries:
                    raise APIConnectionError(
                        f"{self.settings.base_url} not reachable ({type(exc).__name__})"
                    ) from exc
                await asyncio.sleep(self.settings.wait_for(attempt, None))
                attempt += 1
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self.settings.max_retries:
                await asyncio.sleep(self.settings.wait_for(attempt, resp.headers.get("retry-after")))
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise raise_for(resp, self.settings)
            return resp

    @overload
    async def request(self, method: str, path: str, *, model: type[M], **kw: Any) -> M: ...

    @overload
    async def request(self, method: str, path: str, *, model: None = None, **kw: Any) -> Any: ...

    async def request(self, method: str, path: str, *, model: type[M] | None = None, **kw: Any) -> Any:
        return parse(await self.send(Call(method, path, **kw)), model)


async def apaginate(fetch: Any, params: dict[str, Any], key: str = "items") -> AsyncIterator[Any]:
    """The async twin of ``paginate``: ``await fetch(**params)`` returns a page model."""
    cursor: str | None = None
    while True:
        page = await fetch(**{**params, "cursor": cursor})
        items = getattr(page, key, None) or []
        for item in items:
            yield item
        cursor = getattr(page, "next_cursor", None)
        if not cursor:
            return
