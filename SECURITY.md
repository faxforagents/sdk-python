# Security policy

## Reporting a vulnerability

Do not open a public issue for a security problem.

Write to hello@faxforagents.com with what you found, how to reproduce it and what an attacker could
do with it. We answer within 72 hours and keep you informed until it is fixed.

## Scope

In scope: this repository, the client libraries and tools published by the `faxforagents`
organization, and the `api.faxforagents.com` and `mcp.faxforagents.com` services.

Out of scope: findings that need a compromised machine or a leaked API key you already hold. Also out
of scope: rate limit exhaustion of your own key, and scanner output without a demonstrated impact.

## Handling your API key

Keys look like `ffa_live_...` and `ffa_test_...`. A live API key spends your prepaid wallet.

- Keep keys in environment variables, never in source control.
- The official clients read `FAXFORAGENTS_API_KEY` and send it to `api.faxforagents.com` only.
- Rotate an exposed key at once with `POST /v1/api-keys/{key_id}/rotate`. The old API key keeps working
  for 24 hours so a deploy can catch up, then stops.
- Give an agent a key of its own with the scopes it needs and nothing more. An agent's API key can never
  carry `approval:decide`, `key:admin`, `policy:admin`, `agent:admin`, `billing:write` or
  `webhook:admin`.
