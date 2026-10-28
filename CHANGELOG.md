# Changelog

## 0.2.0 (2026-10-28)

API version 1.1.0: HTML rendering and image uploads.

- `documents.create(html=...)` renders HTML on the server, scripts off and no network.
- `documents.upload` takes a PNG, JPG or TIFF beside a PDF and reads the content type from the
  filename. `create_upload` documents the four content types.
- New resources, every operation of the document: `numbers` (coverage, inventory, orders, porting,
  sender verification), `extractions` (OCR, fields, classification, summaries, saved schemas),
  `organizations` and `members`. The inbox gains `update`, `delete`, `get_download` and `download`.
- `sandbox.create_inbound_fax` returns `InboxItemSummary`. `InboxItem` carries the number, project,
  media status and read state of a received fax. `Quote` carries `per_page_micro` and `route_label`.
- The `faxforagents documents upload` command takes images and HTML files.
- Generated fields that clash with pydantic's own names get a trailing underscore and an alias
  (`schema_`, alias `schema`).

## 0.1.0 (2026-10-28)

First release.

- `Client` and `AsyncClient` over httpx, every `/v1` operation of API version 0.1.0.
- Models generated from the public OpenAPI document (`scripts/gen_models.py`), pydantic v2.
- `Idempotency-Key` on every mutating call, retries for 429 and 5xx with `Retry-After`.
- `iter` helpers on every list, `faxes.wait` and `approvals.wait`.
- `verify_webhook` for the Standard Webhooks signature, with rotation support.
- The `faxforagents` command (extra `cli`): keys, documents, fax, inbox, approvals, balance, sandbox.
