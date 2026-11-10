# Changelog

## 0.3.1 (2026-11-10)

`faxes.list` takes `idempotency_key`, a filter that finds the send made with that key.
`policies.effective` takes `key_id`, `agent_id` or `connection_id` to ask about another principal. A connection id comes from `oauth.list_grants`.

## 0.3.0 (2026-11-10)

The saved spec follows the public document at API 1.12.0, 134 operations. Models are regenerated from it.
New calls: `faxes.get_receipt_pdf`, `numbers.list_orders`, `policies.get_auto_send`, `policies.set_auto_send` and `organizations.download_export_archive`.
New resource `oauth` for connected apps: `list_grants`, `iter_grants` and `revoke_grant`.
New models `AutoSendSetting`, `AutoSendUpdate` and `NumberOrderList`. `ApiKey` carries `connected_app`, `Policy` carries `managed` and `EffectivePolicies` carries `requires_human_approval`.
Existing model names, fields and optionality are kept.

## 0.2.5 (2026-11-09)

`quotes.create` takes `sender_name` (the station name in the fax header) and `delete_after_send`. `Quote` and `FaxJob` carry both. The CLI has `--sender-name` and `--delete-after-send`. Needs API 1.12.0.

## 0.2.4 (2026-11-09)

`quotes.create` takes `quality`: standard, fine (the default), superfine or ultra. `Quote` and `FaxJob` carry it. The CLI has `--quality`. Needs API 1.11.0; older servers refuse the field.

## 0.2.3 (2026-11-02)

Add `Grant`, `GrantList` and `ReviewSignIn` for connected apps and reviewer sign-in.
Also declare `Client`, `ClientList`, `ReviewAccessAction`, `ReviewAccessCreate` and `ReviewAccessIssued`.
Refresh the saved specification schemas to API 1.6.0.
Retain existing model names, fields and optionality. The Python generator produces models only.

## 0.2.2 (2026-11-02)

Regenerate models from API version 1.5.2, including the boolean `Capabilities.extraction` field.
Include all live schemas and retain existing model names, fields and optionality.
Add a test that checks every saved schema property against the generated declarations.

## 0.2.1 (2026-11-02)

Declare response fields from API version 1.5.0 and the server serializers.
Add billing iterators for ledger entries, top-up sessions and invoices.
Add `verify` as an alias of `verify_webhook`.
Document the required JSON object for upload completion.

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
