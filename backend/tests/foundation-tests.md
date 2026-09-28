# T011 foundation test contract

T011 authors tests before T012–T016 implement their behavior. It does not implement
those tasks or expose the student answer endpoint (T030). The tests are ordinary
pytest assertions, not skips or expected-failure markers. Missing modules are
imported inside tests/fixtures so the entire suite still collects.

## Coverage and internal interfaces

These are proposed Python seams for the upcoming implementations, not additional
public HTTP endpoints. Change a seam and its tests together if implementation
requires a different structure, preserving the behavioral assertions.

- `test_source_rules.py`: database revision uniqueness/effective windows and
  terminal lifecycle transitions; owning-office versus Dean permissions; all
  corpus eligibility states; campus/program/course/term mismatches.
- T013 `app.services.source_governance.can_review(owner_office, reviewer_office,
  action) -> bool`: evaluate an already-authenticated office role. This helper is
  not a substitute for OIDC token validation or transaction/audit enforcement.
- T014 `app.services.source_eligibility.is_eligible(...) -> bool`: keyword inputs
  are approval, lifecycle, revision_status, readable, conflicting, effective_from,
  effective_until, now, scope and context. Context uses API names including
  `academicTerm`. Corpus expectations use its fixed clock, not wall time.
- T016 `app.services.decisions.evaluate_gates(...) -> dict | None`: keyword inputs
  are question, context, required_context, evidence_status and contacts. Return
  `None` only when these gates allow processing to continue; otherwise return an
  API-shaped safe outcome. `required_context` identifies material fields, not all
  possible fields. Emergency cases deliberately combine missing context,
  conflicting evidence and personal requests to assert priority. These unit tests
  do not claim to verify the T030 retrieval/generation orchestration.
- T015 `app.api.schemas.chat.StudentQuestion` and the `ChatResponse` union:
  Pydantic validation for request limits/unknown fields and required outcome data.
- T012 `app.api.middleware.configure_middleware(app, settings)`: install shared
  middleware/error handlers. Contract tests attach test-only chat-prefixed probe
  routes to verify public access, JSON errors, no-store for success and 400/429/500,
  and absence of sensitive exception text from responses and captured logs.
  The injected 429 checks error handling, not rate-limit enforcement.

## Validation result

The focused T011 run collected 71 cases: **4 passed, 63 failed, 4 setup errors**.
All 67 failures/errors were verified to be `ModuleNotFoundError` for the missing
`app.services` or `app.api` modules. No application stubs were added to mask them.
The pre-T011 suite still passed all **72 tests in Docker with PostgreSQL**.
Ruff lint and formatting checks passed. No additional dependencies were needed.

Run from `backend/`:

```sh
pytest tests/unit/test_source_rules.py tests/unit/test_decisions.py tests/contract/test_api_rules.py
```

T011's test-authoring work is complete. The foundation checkpoint remains open:
these tests must pass after T012–T016 and before completing T017. The full suite
is intentionally red until that work is implemented; do not treat the regression
subset result as a passing full-suite result.


T012 update: the four middleware contract cases now pass. The full Docker/PostgreSQL
suite has 90 passing tests and 63 failures for the remaining T013–T016 modules.
The initial T011 counts above record the earlier test-authoring checkpoint.

T013 update: office permission tests now pass. Full Docker/PostgreSQL results are
118 passed and 54 remaining failures for T014–T016.

T014 update: all 19 eligibility cases now pass. Full Docker/PostgreSQL results are
141 passed and 35 remaining failures for T015–T016.

T015 update: all schema and middleware contract tests now pass. Full Docker/PostgreSQL
results are 178 passed and 20 remaining failures for T016.

T016 update: all foundation tests now pass. Full Docker/PostgreSQL results are
212 passed, with no failures or skips. T017 frontend work remains incomplete.

T017 update: the full Docker/PostgreSQL foundation suite again passed all 212
tests, with two dependency deprecation warnings and no failures or skips. The
migration runner succeeded against the isolated application database and Alembic
reported `0002_eligibility (head)`. The suite also exercises schema creation,
upgrade, downgrade/re-upgrade, and metadata consistency. Frontend validation
passed 26 unit tests, lint, type checks, production build, and Docker/Caddy
configuration validation. The foundation checkpoint is now complete.
