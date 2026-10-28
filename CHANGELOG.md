# Changelog

## 0.1.0 (2026-10-28)

First release.

- `Client` and `AsyncClient` over httpx, every `/v1` operation of API version 0.1.0.
- Models generated from the public OpenAPI document (`scripts/gen_models.py`), pydantic v2.
- `Idempotency-Key` on every mutating call, retries for 429 and 5xx with `Retry-After`.
- `iter` helpers on every list, `faxes.wait` and `approvals.wait`.
- `verify_webhook` for the Standard Webhooks signature, with rotation support.
- The `faxforagents` command (extra `cli`): keys, documents, fax, inbox, approvals, balance, sandbox.
