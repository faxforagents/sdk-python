# faxforagents

The Python client for Fax for Agents, <https://faxforagents.com>.

Fax for Agents is a fax API, a hosted MCP server and SDKs built so AI agents can send and receive
faxes. It is made by JSB Holdings LLC.

```bash
pip install faxforagents            # the client
pip install "faxforagents[cli]"     # plus the faxforagents command
```

Python 3.10 or later. Two dependencies, httpx and pydantic.

## Quickstart, against the sandbox

A test key (`ffa_test_...`) reaches the sandbox provider only. No carrier, no cost, nothing leaves the
box. The destination number picks the outcome. Keys are created in the dashboard at
<https://app.faxforagents.com> or with `POST /v1/api-keys`.

```python
import os
from faxforagents import Client

os.environ["FAXFORAGENTS_API_KEY"] = "ffa_test_..."      # or export it in the shell

with Client() as ffa:
    doc = ffa.documents.create(text="Hello from an agent.", filename="hello")
    print(doc.id, doc.page_count, doc.sha256[:12])

    quote = ffa.quotes.create(document_id=doc.id, to="+15550000001")
    print(quote.amount_micro, "micro, expires", quote.expires_at)

    fax = ffa.faxes.create(quote_id=quote.id)              # Idempotency-Key minted for you
    print(fax.id, fax.state)                               # queued, never delivered yet

    fax = ffa.faxes.wait(fax.id)                           # polls until a terminal state
    print(fax.state, fax.pages_sent, fax.evidence)         # delivered, 1, {...}

    for job in ffa.faxes.iter(state="failed"):             # follows next_cursor
        print(job.id, job.failure_code)
```

The same in async form, with `AsyncClient` and `await` on every call.

## What the client does for you

- Every mutating call carries an `Idempotency-Key`. When you pass none, a uuid4 is minted once per
  call and reused on every retry, so a retried send never creates a second fax. Pass
  `idempotency_key=` to pick your own.
- 429 and 5xx answers are retried up to three times. `Retry-After` is honoured. Connection errors and
  timeouts are retried the same way.
- Every list has an `iter` twin that follows `next_cursor`.
- `documents.create(text=...)`, `markdown=...` or `html=...` renders on the server. HTML prints with
  scripts off and no network, inline CSS and `data:` images only, at most 2 MiB.
- `documents.upload(path)` runs the whole upload path for a PDF or an image (PNG, JPG, TIFF): the
  upload session, the signed PUT with the content type read from the filename, the sha256 check, the
  document. An image becomes gray letter pages.
- `faxes.wait(fax_id)` and `approvals.wait(approval_id)` poll with a timeout.
- `verify_webhook(secret, headers, body)` checks the Standard Webhooks signature on a delivery.
- Models are pydantic v2, generated from the public OpenAPI document. Money is an integer count of
  USD micro-units (`39000` is $0.039). Timestamps are UTC strings.

## Resources

Method names are the operationIds of the [OpenAPI document](https://github.com/faxforagents/openapi),
shortened by their resource. The reference at <https://faxforagents.github.io/openapi/> names the scope
each call needs.

| Resource | Calls |
|---|---|
| `ffa.identity` | `me`, `list_api_keys`, `create_api_key`, `rotate_api_key`, `revoke_api_key` |
| `ffa.documents` | `create`, `upload`, `create_upload`, `put_upload_content`, `complete_upload`, `cancel_upload`, `list`, `iter`, `get`, `delete`, `get_validation`, `get_download`, `download`, `list_pages` |
| `ffa.quotes` | `create`, `get` |
| `ffa.faxes` | `create`, `list`, `iter`, `get`, `wait`, `list_events`, `list_attempts`, `get_receipt`, `cancel` |
| `ffa.webhooks` | `list`, `create`, `update`, `delete`, `rotate_secret`, `test`, `list_deliveries`, `replay_delivery` |
| `ffa.events` | `list`, `iter` |
| `ffa.billing` | `balance`, `list_ledger`, settings, top-ups, auto top-ups, budgets, `usage`, exports, invoices, subscriptions, `create_portal_session` |
| `ffa.platform` | `capabilities`, `service_status` |
| `ffa.sandbox` | `list_scenarios`, `create_scenario`, `create_inbound_fax`, `reset` |
| `ffa.approvals` | `request`, `request_help`, `list`, `iter`, `get`, `wait`, `approve`, `reject`, `revoke` |
| `ffa.policies` | `list`, `create`, `effective`, `get`, `update`, `list_versions`, `evaluate` |
| `ffa.agents` | `list`, `create`, `get`, `update`, `create_key`, `activity` |
| `ffa.inbox` | `list`, `iter`, `get`, `update`, `delete`, `get_download`, `download` |
| `ffa.numbers` | `coverage`, `inventory`, `create_order`, `get_order`, `list`, `get`, `update`, `release`, `check_portability`, `list_port_orders`, `create_port_order`, `get_port_order`, `create_port_out_request`, `verify_sender` |
| `ffa.extractions` | `create`, `list`, `iter`, `get`, `correct`, `classify`, `list_classifications`, `iter_classifications`, `get_classification`, `summarize`, `list_summaries`, `iter_summaries`, `get_summary`, `list_schemas`, `create_schema`, `get_schema` |
| `ffa.organizations` | `list`, `current`, `update_current`, `request_data_export`, `request_data_deletion` |
| `ffa.members` | `list`, `update`, `remove`, `list_invitations`, `invite`, `revoke_invitation` |

`ffa.request("GET", "/v1/anything")` reaches an operation the resources do not cover yet.

## States

A fax job is `queued`, `active`, `partial`, `failed`, `unknown` or `delivered`. `delivered` is written
only when the carrier's evidence is on the attempt, and `fax.evidence` holds it. `partial` and
`unknown` never retransmit on their own. Whether to resend is a human decision.

Sandbox destinations: `+15550000001` delivered, `+15550000002` busy then delivered, `+15550000003`
partial, `+15550000004` no answer, `+15550000005` unknown, `+15550000006` provider timeout,
`+15550000007` invalid number. Any other `+1` number is delivered.

## Errors

```python
from faxforagents import APIError, RateLimitedError

try:
    fax = ffa.faxes.create(quote_id=quote.id)
except RateLimitedError as exc:
    print("retry in", exc.retry_after)
except APIError as exc:
    print(exc.code, exc.message, exc.correlation_id)   # quote_expired, insufficient_balance, approval_required
```

`AuthenticationError` (401), `ScopeError` (403), `NotFoundError` (404), `ConflictError` (409),
`PaymentRequiredError` (402), `InvalidRequestError` (400, 422), `RateLimitedError` (429) and
`ServerError` (5xx) are all `APIError`. `APIConnectionError` and `APITimeoutError` mean the API was
not reached.

## Approvals

A live send needs either a human approval bound to the quote or a delegated policy that allows it.

```python
req = ffa.approvals.request(quote.id)          # a person is mailed
req = ffa.approvals.wait(req.id)               # pending until they decide
if req.status == "approved":
    fax = ffa.faxes.create(quote_id=quote.id, approval_id=req.id)
```

An agent's own API key can never approve. `ffa.policies.evaluate(quote_id=...)` is a dry run that says
whether a policy would allow the send.

## Webhooks

```python
from faxforagents import verify_webhook, parse_event, SignatureError

try:
    event_id = verify_webhook(secret, request.headers, request.body)
except SignatureError:
    return 400
event = parse_event(request.body)              # event.type is fax.delivered, fax.failed, fax.received...
```

Verify on the raw bytes. Store `event_id` to drop a redelivery you already handled. During a secret
rotation two signatures arrive for 24 hours and either one verifies.

## The command

```bash
export FAXFORAGENTS_API_KEY=ffa_test_...
faxforagents documents upload letter.pdf        # or .png, .jpg, .tiff, .html, .md, .txt
faxforagents fax quote --document doc_... --to +15550000001
faxforagents fax send --quote quote_... --wait
faxforagents fax list --state failed
faxforagents inbox list
faxforagents approvals list --status pending
faxforagents balance
faxforagents sandbox scenario partial --to +12025550100
```

Every command takes `--json` for the raw answer.

## Prices

Price card launch-v1 is effective from 2026-10-28. $0.039 per page sent, $0.019 per page received.
$2 per number per month, $19 per month for the Agent workspace. Sandbox pages cost 0.
`ffa.platform.capabilities()` returns the card in force.

## Working on this repository

```bash
pip install -e ".[dev,cli]"
pytest -q                                   # unit tests, respx fixtures, no network
FAXFORAGENTS_API_KEY=ffa_test_... pytest -q -m live    # one real sandbox send
python scripts/gen_models.py --check        # models.py matches spec/openapi.yaml
ruff check src tests && mypy src
sh scripts/truthcheck.sh
```

`spec/openapi.yaml` is a copy of the public document. To take a new one, copy it over and run
`scripts/gen_models.py`.

## Licence

MIT. See [LICENSE](LICENSE). Security reports go to hello@faxforagents.com, see [SECURITY.md](SECURITY.md).
