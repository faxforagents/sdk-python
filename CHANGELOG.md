# Changelog

## 0.11.2 (2026-11-17)

Follows API 1.21.3. The saved spec has 165 operations and 175 schemas, as in 0.11.1, and the models are regenerated from it.
No method, argument or field changed.

- The generated docstring of `Subscription.ends_at` says it is `None` whenever `entitled` is false, as after the plan ended.
  The server sent that at 1.21.2 already.
- The package metadata names its licence as `License-Expression: MIT`, from `license = "MIT"` and `license-files` (PEP 639).
  The licence classifier is gone, and the build needs hatchling 1.27 or later.
- The Documentation link of the package points at https://faxforagents.com/docs/sdks/python/.

## 0.11.1 (2026-11-17)

Follows API 1.21.2. The saved spec has 165 operations and 175 schemas, as in 0.11.0, and the models are regenerated from it.
No method or argument changed, and nothing is removed.

New fields:

- `Subscription.cancel_at` is when Stripe is set to end the subscription. A cancel in the billing portal sets it and leaves `cancel_at_period_end` false.
- `Subscription.ends_at` is when the plan ends without another charge. It is `None` while the plan renews, and once the plan has ended.
- The public document requires both. The saved spec leaves them optional, so an older payload still parses. Both default to `None`.

`OneTimeConfirmSummary.state` reads `link_canceled` for the confirm link of an order that was canceled.
`one_time_sends.confirm` on that link raises `ConflictError` with the code `link_canceled`, and its docstring says so.

`ConflictError.reason` reads `details["reason"]`, or `None` when the API sent none.
A second `billing.create_subscription_checkout` raises `ConflictError` with the reason `plan_active` while the plan is active.
The reason is `checkout_open` while a payment page of the plan is still open, and `details["url"]` is that page.

The generated `OneTimeOrder` docstring follows the public document, which splits it into shorter sentences.

## 0.11.0 (2026-11-16)

Follows API 1.21.0. The saved spec has 165 operations and 175 schemas, and the models are regenerated from it.
Every operation has a method on both clients. Nothing is removed.

New methods: `organizations.fax_history_csv` and `organizations.audit_csv`, the records export of the Agent workspace plan.
Each returns CSV text for a range of UTC days, `from_` and `to`, both included, as `billing.statement_csv` does.
The fax history needs `fax:read` and `inbox:read`. The audit trail needs `org:admin` and refuses an agent's key.
Without the plan, or more than 30 days after it ended, both raise `ScopeError` with the code `workspace_required`.

New parameters: `faxes.list` and `faxes.iter` take `created_after` and `created_before`, ISO 8601 times.
The start is included and the end is left out. A start after the end raises `InvalidRequestError`.
Left out, no query parameter is sent, so the request is the same as in 0.10.0.

New fields:

- `Quote.live_amount_micro` is what the same fax costs with a live key. A test quote is free, so this is its live price.
  It is `None` when the rate card changed since the quote, and from an older server.
- `SubscriptionList.plan` is the new model `WorkspacePlan`: the request limits, the included number, the keep times and whether the export is open.
  The public document requires it. The saved spec leaves it optional, so an older payload still parses.
- `Number.included_with_workspace` is true for the number whose monthly charge the Agent workspace pays. It defaults to `False`.
- `OneTimeOrder.refund_pending_micro` and `OneTimeOrderCreated.refund_pending_micro` hold a refund the payment provider has not confirmed yet.
  They default to 0.

One-time orders have three new statuses, set by a person at Fax for Agents: `refunding`, `refunded` and `canceled`.
`refunded` and `canceled` read `next` `done`, so `one_time_sends.wait` returns on them. `refunding` reads `next` `wait`.

Descriptions say test where they said sandbox. `Capabilities.limits` names the request limit, 3000 a minute with the plan and 600 without.

## 0.10.0 (2026-11-16)

Follows API 1.20.11, as 0.9.1 did. The saved spec has 163 operations and the models are regenerated from it.
No client method, argument or return type changed. Every model a method takes or returns is kept.

### Removed

70 models are gone from `faxforagents.models`, which now has 174. No method of `Client` or `AsyncClient` takes or returns them, directly or in a field.
The public document of API 1.20.11 does not define them. They came from the server's own document in 0.2.2 and 0.2.3.
Under 0.x a minor release can break imports, and this one does. Code that imports one of them has to drop the import. Nothing replaces them.

- The admin console under `/v1/admin`, 43 models: `AbuseReview`, `AbuseReviewCreate`, `AbuseReviewPage`, `AbuseReviewPatch`, `Adjustment`, `AdjustmentApply`, `AdjustmentCreate`, `AdjustmentPage`, `AdminAction`, `AdminBrief`, `AdminDaily`, `AdminDailyPoint`, `AdminJob`, `AdminJobPage`, `AdminOrganization`, `AdminOrganizationDetail`, `AdminOrganizationPage`, `AdminRevenue`, `AdminStats`, `AdminWhoami`, `AnalyticsJourneys`, `AnalyticsOrganization`, `AnalyticsSource`, `AnalyticsSources`, `AnalyticsUsage`, `AnalyticsWebsite`, `AuditEvent`, `AuditEventPage`, `Client`, `ClientList`, `OutboxDiscarded`, `OutboxPage`, `OutboxRow`, `ProviderHealth`, `ProviderList`, `ReconcileResult`, `ReconciliationReport`, `ReleaseResult`, `ReviewAccessAction`, `ReviewAccessCreate`, `ReviewAccessIssued`, `RouteList`, `RouteState`.
- The dashboard sign-in under `/v1/auth`, 17 models: `Accepted`, `MagicLinkRequest`, `Passkey`, `PasskeyList`, `PasskeyLoginVerify`, `PasskeyRegisterVerify`, `Providers`, `Reauth`, `ReviewSignIn`, `Revoked`, `Sent`, `SessionInfo`, `SessionList`, `SessionOut`, `SwitchOrganization`, `UserOut`, `faxforagents__auth__schemas__Me`.
- The OAuth consent calls under `/v1/oauth/authorizations`, 5 models: `OAuthConsent`, `OAuthConsentClient`, `OAuthConsentDecision`, `OAuthConsentOrganization`, `OAuthRedirect`.
- The Stripe and Telnyx receivers under `/webhooks`, 2 models: `TelnyxAck`, `WebhookAck`.
- The server's module-qualified names for `InboxItem`, `InboxItemSummary` and `Me`, 3 models: `faxforagents__api__routes__inbox__InboxItem`, `faxforagents__api__schemas__InboxItem`, `faxforagents__api__schemas__Me`. The plain names stay.

`faxforagents.models.Client` described an OAuth client in the admin console. `faxforagents.Client`, the client of this package, is not affected.

`scripts/gen_models.py` now writes a model only for a schema that an operation reaches. The public document is curated by the same rule.
A refresh of the spec that brings one of these schemas back still leaves it out of `models.py`.
`tests/test_removed_models.py` fails when one of the 70 is back, or when the saved spec holds a schema that no operation reaches.

## 0.9.1 (2026-11-16)

Follows API 1.20.11. The saved spec has 163 operations and the models are regenerated from it.
Only descriptions changed. Nothing is added or removed.

The description of `TopupSession.checkout_status` says it is also `None` for a top-up that is not open.
Stripe is asked about an open top-up only, as the docstring of `billing.get_topup_session` already said.
The saved spec gives `replay_webhook_delivery` a 409 of its own, which names `details.reason` `webhook_deleted`.
`webhooks.replay_delivery` already raises `ConflictError` with `details["reason"]` `webhook_deleted` there, as 0.9.0 documented.

## 0.9.0 (2026-11-15)

Follows API 1.20.10. The saved spec has 163 operations and the models are regenerated from it.
Every operation has a method on both clients.

New parameter: `billing.get_topup_session(topup_session_id, check_checkout=True)` asks Stripe about the hosted Checkout of an open top-up.
The new field `TopupSession.checkout_status` reads `open`, `complete`, `processing` or `expired`, and `None` when not asked.
`complete` means the person paid, and the wallet is credited when Stripe's event arrives. `processing` is a payment that confirms later, such as a bank debit.
The default is `False`, and the request is then the same as in 0.8.0.

New field: `Delivery.webhook_deleted`, which only `DeliveryDetail` had. The API sends it on every delivery.
It is optional on `Delivery`, so a payload without it still parses.
`webhooks.replay_delivery` on a delivery whose webhook was deleted raises `ConflictError` with `details["reason"]` set to `webhook_deleted`.
Deleting a webhook ends the deliveries that were waiting for it. They read `failed`, with no next attempt.

`OneTimeCoverCheck` describes the limits of `create`: `to_name` 120 characters, `subject` 200 and `message` 2,000.
`one_time_sends.check_cover` says a longer field raises `InvalidRequestError`, as `create` does.
`billing.cancel_topup_session` says `details["reason"]` is `paid` when the payment came in before the cancel.
New descriptions on the counters of `PolicyUsage` and on `policy_name` and `version` of `PolicyVerdict` and `PolicyEvaluation`.

Nothing is removed. Other model names, fields and optionality are kept.

## 0.8.0 (2026-11-15)

Follows API 1.20.9. The saved spec has 163 operations and the models are regenerated from it.
Every operation has a method on both clients.

New: `billing.cancel_topup_session(topup_session_id)` closes the hosted Checkout of an open top-up and returns it with status `canceled`.
Canceling a setup payment also turns off the auto top-up that waited on it. Scope `billing:write`, never an agent key.
A top-up that was paid, declined or charged off session raises `ConflictError`, with its status in `details["status"]`.
New: `one_time_sends.check_cover(...)` takes the cover fields typed so far and says whether the message fits on the cover page. It needs no key.
It returns the new model `OneTimeCoverRoom` with `fits`, `message_chars`, `lines_left` and `fits_chars`. Nothing is stored.

New parameter: `billing.statement`, `billing.iter_statement` and `billing.statement_csv` take `type`, the type each line shows.
`kind` still works. It is the same filter by its older name, and two different types in both raise `InvalidRequestError`.

Changed: five fields that the saved spec declared itself now follow the public document.
`Quote.policy_evaluation` is a `PolicyVerdict` and `PolicyEvaluation.usage` a `PolicyUsage`. Both were dicts, so read them as attributes, such as `usage.day_jobs`.
`Quote.pricing` is `per_page` or `one_time`, with `per_page` as the default. `PolicyEvaluation.decision` is `allow` or `deny`.
`FaxJob.approval_id` and `FaxJob.policy_version_id` keep their type. `FaxJob.route_paused` is new and false by default.

`identity.rotate_api_key` on a key that expired raises `ConflictError` with `details["reason"]` set to `key_expired`. Create a new key to replace it.
`PaymentReceipt.pdf_url` is `None` for an automatic top-up, which has no invoice. Its `url` still opens the receipt.
A statement line of a hold or a release names what it was for in `ref_type`: `fax_job`, `extraction` or `number_order`.
The card of a one-time order is held, not authorized, in the docstrings and the model descriptions.
`one_time_sends.checkout` states the payment rule of API 1.20.9. A failed fax costs nothing, and a fax that stops part way costs the price of the pages the carrier confirms.
New models `OneTimeCoverCheck`, `OneTimeCoverRoom`, `PolicyVerdict` and `PolicyUsage`. Other model names, fields and optionality are kept.

## 0.7.0 (2026-11-15)

Follows API 1.20.6. The saved spec has 161 operations and the models are regenerated from it.
Every operation has a method on both clients.

New: `billing.statement(kind=..., from_=..., to=...)` reads the wallet statement, one line per event with the balance after it.
`billing.iter_statement` follows `next_cursor`, and `billing.statement_csv` returns the same lines as CSV text. Scope `billing:read`.
New: `documents.rename(document_id, filename=...)`. A name without `.pdf` gets it added. Scope `document:write`.
New: `webhooks.get_delivery(delivery_id)` returns `DeliveryDetail`, with the headers and the JSON body that were sent.
New: `billing.delete_budget(budget_id)`. A budget that never charged a hold is deleted, and one with history is archived.
New: `agents.archive(agent_id)` revokes the agent's keys and takes it out of the list. Its name is free again.
New: `numbers.dismiss_order(order_id)` puts a failed number order away. Any other order raises `ConflictError`.
New: `policies.check_rules(...)` names the numbers of a draft's `allowed_destinations` that a listed country or prefix already allows. It writes nothing.
New: `members.resend_invitation` mails an open invitation again. `members.link_invitation` answers a new link once, for you to hand over. Both stop the old link.
New: `organizations.list_data_requests()` and `organizations.withdraw_data_deletion(request_id)`.
New: `one_time_sends.preview(file=...)` or `preview(text=...)` counts the pages of a document before any order exists, with no key.
New: `one_time_sends.get_page(order_id, page_number)` returns one page of an order as PNG bytes. Page 1 is the cover sheet.

New parameters: `faxes.list` and `faxes.iter` take `q`, a fax number or the start of a job id.
`documents.list` and `documents.iter` take `source`, `exclude_source` and `q`. `inbox.list` and `inbox.iter` take `archived`.
`agents.list` and `billing.list_budgets` take `include_archived`. `policies.evaluate` takes `key_id`, `agent_id` or `connection_id`.

Changed: `approvals.reject` no longer needs `reason`. A help request closes without one. A send request still needs it, and the API answers 422 without it.
`approvals.list(status="used")` finds the approvals a send consumed. They read `approved`, and `consumed_by_job_id` names the fax.
Before API 1.20.6 such an approval read `expired` once its time had passed.

New fields, among them `DocumentPage.total`, `InboxPage.unread`, `Balance.held_jobs`, `Quote.route_paused` and `Document.converted_from`.
`Receipt` has `seller`, `provider_ref` and `sender_name`. `ApprovalRequest` has `document`, `requested_by_name` and `decided_by_name`.
18 new models, among them `StatementPage`, `StatementLine`, `PolicyRuleCheck`, `DeliveryDetail` and `OneTimeInspection`.

The API now refuses an organization name over 80 characters with 422. It took 200 before.
Nothing is removed. Other model names, fields and optionality are kept.

## 0.6.1 (2026-11-15)

Follows API 1.20.5. The saved spec has 147 operations and the models are regenerated from it.

New: `billing.get_invoice_receipt(invoice_id)` and `billing.get_topup_receipt(topup_session_id)`, on both clients.
Each returns the new model `PaymentReceipt`. `url` is the hosted receipt and `pdf_url` the same receipt as a PDF.
Both need the scope `billing:read`. A payment that was not made raises `ConflictError`.

Changed: `documents.create_upload` no longer needs `content_type`. Its default was `application/pdf`. It is now `None`, and the field is left out of the request.
The API then reads the type from the filename. `scan.png` opens as `image/png`, and 0.6.0 opened it as a PDF.
A filename of an unsupported format, such as `letter.docx`, raises `InvalidRequestError` with the code `unsupported_format` on this call.
`UploadCreate.content_type` is `str | None` with the default `None`. `documents.upload` names the type itself, as before.

New: `NotFoundError.field` is `details["field"]`. On `quotes.create` it is `document_id` or `from`, the id that was not found.
New: `ConflictError.state` is `details["state"]`. `faxes.get_receipt` on a fax that has not ended carries the fax state there.
Both are `None` when the server sent no such detail.

The API now refuses an auto top-up threshold above 1000 USD and an agent `contact_email` that is not an address. Both raise `InvalidRequestError`.
`Capabilities.scopes` is described as every scope the platform defines. `me()` returns the scopes of the API key.
Other model names, fields and optionality are kept.

## 0.6.0 (2026-11-13)

Follows API 1.20.0. The saved spec has 145 operations and the models are regenerated from it.
No client method was added or removed.

New: `Number.email_pdf`, a boolean. When true, the notice mail of a received fax carries the PDF.
A number read from an older server has `email_pdf` equal to `False`.
`numbers.update(number_id, email_pdf=True)` switches it on, with the scope `number:buy`. An agent key is refused.
`email_pdf` is sent only when given. `False` is sent and switches the attachment off.

Breaking: `Extraction.provider_cost_micro` is removed. The server stopped sending it at API 1.19.0.
Breaking: `ExtractionPage.cost_micro` is removed, for the same reason.
Code that read either field has to stop. `Extraction.price_micro` is the price the wallet paid, as before.
Fixed: 0.5.0 required `provider_cost_micro`, so every extraction call failed to parse on API 1.19.0 and later.
This covers extractions, classifications and summaries. 0.6.0 parses them.

The ledger no longer lists `provider_cost` entries. `billing.list_ledger` and its models did not change.
Other model names, fields and optionality are kept.

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

`identity.create_api_key` takes `connected_app` on both clients. An API key made with `connected_app=True` is bound by the auto-send setting like an OAuth connection.
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

`quotes.create` takes `quality`: standard, fine (the default), superfine or ultra. `Quote` and `FaxJob` carry it. The CLI has `--quality`. Needs API 1.11.0. Older servers refuse the field.

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
