# Changelog

## 0.5.0 (2026-11-12)

Follows API 1.18.0. The saved spec has 145 operations and the models are regenerated from it.
No client method, argument or return type changed. An API key is sent as before.

Breaking: the models `HTTPValidationError` and `ValidationError` are removed from `faxforagents.models`.
The server never sent that shape. A 422 is the error envelope and the client raises `InvalidRequestError`, as it did in 0.4.1.
Code that imported either model has to delete the import. Nothing replaces them. pydantic's own `ValidationError` is not affected.

Breaking: `ErrorEnvelope.error` is the new model `ErrorBody`, where 0.4.1 declared a plain dict. Read `env.error.code`, not `env.error["code"]`.
`ErrorBody` has `code`, `message`, `retryable`, `correlation_id` and `details`. `correlation_id` may be `None`.
Raised errors are the same classes with the same attributes.

`one_time_sends.create` can raise `RateLimitedError` with `details["cap"]` equal to `upload_queue`. Too many files are being read at once, so wait `retry_after` seconds.
Other model names, fields and optionality are kept.

## 0.4.1 (2026-11-12)

Follows API 1.17.0. The saved spec has 145 operations and the models are regenerated from it.

`one_time_sends.create_from_quote` no longer requires `accept_aup`. It is sent only when given. `approval_id` still carries the approval of a card order.
`one_time_sends.checkout` takes `accept_aup`. An order whose `aup_accepted` is false needs `accept_aup=True` once, with `order_token`. The payer accepts the terms on the order page, never a key.
New call `one_time_sends.read_by_token(token)`: the read of a mailed confirmation link, with the token in the body. `get_by_token` still works and raises a `DeprecationWarning`.

`OneTimeOrder` and `OneTimeOrderCreated` carry `aup_accepted`, `status_url` and `queue`. `OneTimeConfig` carries `queue`.
New model `OneTimeQueue` with `queued`, `jobs_ahead` and `estimated_wait_minutes`.
`Balance` carries `top_up_url`. `Capabilities` carries `extraction_pricing`.

`PaymentRequiredError.top_up_url` reads `details["top_up_url"]`, the page where a person adds funds.
`PaymentRequiredError.one_time_offer` is typed as `OneTimeOffer`, a `TypedDict` exported by the package. It is still a plain dict at run time.
Other model names, fields and optionality are kept.

## 0.4.0 (2026-11-12)

New resource `one_time_sends` on both clients: one fax paid by card, with no wallet.
Its calls are `config`, `create`, `create_from_quote`, `list`, `iter`, `get`, `wait`, `checkout`, `get_by_token`, `confirm`, `resend_confirmation` and `get_receipt_pdf`.
`create` sends a file as multipart or text as JSON. `wait` polls until the order's `next` is `done`, or the step passed as `until`.
`get`, `wait`, `checkout`, `resend_confirmation` and `get_receipt_pdf` take `order_token`, sent as `X-FFA-Order-Token`. The header name is exported as `ORDER_TOKEN_HEADER`.
`Client(keyless=True)` and `AsyncClient(keyless=True)` build a client with no API key. It sends no `Authorization` header and can call `one_time_sends` only.

The saved spec follows the public document at API 1.16.1, 144 operations. Models are regenerated from it.
New models: `OneTimeOrder`, `OneTimeOrderCreated`, `OneTimeOrderPage`, `OneTimeFromQuote`, `OneTimeCheckout`, `OneTimeConfirm`, `OneTimeConfirmSummary`, `OneTimeConfirmed`, `OneTimeCard`, `OneTimePrice`, `OneTimeJob` and `InsufficientBalance`.
Changed: `FaxJob.one_time` is a `OneTimeJob` model, where 0.3.2 declared a plain dict. Read `fax.one_time.order_id`, not `fax.one_time["order_id"]`. `Receipt` carries `one_time` too.
Other model names, fields and optionality are kept.

`PaymentRequiredError.one_time_offer` reads `details["one_time_offer"]` when a server sends it, and is `None` otherwise. API 1.16.1 does not send it.
The package metadata links to the pricing page and the dashboard. The README names price card launch-v2, effective 2026-10-31.

## 0.3.2 (2026-11-10)

`identity.create_api_key` takes `connected_app` on both clients. A key made with `connected_app=True` is bound by the auto-send setting like an OAuth connection.
The field is sent only when given, so existing calls send the same body. Needs API 1.12.0.

The saved spec follows the public document at API 1.13.2. No operation or schema changed.

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
