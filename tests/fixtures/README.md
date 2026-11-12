# Response fixtures

These fixtures reconstruct response shapes from the 2026-11-02 SDK acceptance report and the server serializers.
They are synthetic examples, not captured production traffic. IDs and document text are test values.
Billing pages use those serializer shapes with controlled cursors to exercise pagination.

Server source revision: `b524ba640f34c784ad331ad9c5e9d8a4222d5253`.
Compared serializers include `faxes/service.py`, `billing/quotes.py`, `documents/service.py`, `numbers/service.py`,
`inbox/service.py`, `policies/service.py`, `billing/wallet.py` and `billing/ledger.py` under `src/faxforagents`.
Extraction fields come from `ocr/service.py`. Balance and capabilities come from their API routes.

The schema snapshot was retrieved from https://api.faxforagents.com/openapi.json on 2026-11-02, at version 1.5.0.
Documents, numbers, balance and policy records had no additional fields beyond the snapshot.
Policy evaluations also return usage when a policy applies.
Fax jobs, quotes, extraction pages and inbound summaries needed the route-documented additions.
Capabilities gains `one_time` from the updated snapshot.

## One-time sends (`one-time.json`)

Server source revision: `5bee3b046dbcb5bc0fbf98ae59c1a40a1c8c1434`, API 1.16.1.
`config` and `not_found` were recorded from `https://api.faxforagents.com` on 2026-11-12. Both routes answer without a key.
The other cases follow the serializers `order_json` and `job_block` in `onetime/service.py` and the answers in `api/routes/onetime.py`.
They are synthetic examples, not captured traffic: no order was created and no card was charged to make them.
Ids, addresses, the token and the checkout address are test values.
