# Fix SDK acceptance F02 F04 F07 and upload examples

Status: active · Mode: fix

## Acceptance

- [ ] Production response fields and regression tests
- [ ] Webhook alias and billing iterators
- [ ] Repository gates and patch commit

Recorded evidence describes a checkpoint. Use status or resume to check current freshness.


## Findings



## Notes and limitations

- Implemented 21 optional response fields from OpenAPI 1.5.0 and serializers, webhook verify alias, sync and async billing iterators, JSON upload docs, version 0.2.1 and changelog. Final tests: 90 passed and 1 live skip. Ruff check and format, mypy (8 files), generator check, truthcheck and diff check passed. Build, twine and clean wheel import/CLI passed. All 132 prior model names and prior field schemas are unchanged. Fixtures are synthetic serializer-shaped responses because credentials and HTTP recordings are unavailable. Staging and the requested heredoc commit both failed because .git/index.lock is on a read-only filesystem. No push or publish.
