# M9 Final Report

Status: `M9 REMOTE GREEN — CLOSED`
Date: 2026-07-31

M9 is closed from its exact pushed implementation and successful remote CI
evidence. This closure does not claim that M9 supplies or approves production
legal text.

## Baseline And Scope

- Closed parent: `M8 REMOTE GREEN — CLOSED`.
- M9 authority: `docs/m9_scope_contract.md` and `docs/m9_decisions.md`.
- Delivered capability: versioned multilingual legal offers and authenticated
  REGISTRATION acceptance evidence.
- Added persistence: three offer-domain tables and the approved CR-M9-01
  `audit_log` supporting table; Alembic head `a9b0c1d2e3f4`.
- No activation, PII-document, debt/payment, notification, scheduler, generic
  CMS, or new runtime dependency was added.

## Local Validation

| Check | Result |
|---|---|
| `uv sync --dev --frozen` | GREEN, 47 packages |
| Empty DB `base -> head`, `alembic current`, `alembic heads` | GREEN; one head `a9b0c1d2e3f4` |
| Current PostgreSQL schema inventory | GREEN; 22 application tables, 23 including `alembic_version` |
| `uv run ruff check .` | GREEN |
| `uv run ruff format --check .` | GREEN, 369 files |
| `docker compose config --quiet` | GREEN |
| Full PostgreSQL `pytest -q -ra` | 2540 passed, 0 skip/xfail/xpass, 2 dependency deprecation warnings |
| Full PostgreSQL `pytest -q -ra --durations=10` | 2540 passed, 0 skip/xfail/xpass, same 2 warnings |
| Focused recovery, M9 security, and M1–M8 containment matrices | 106 passed, 0 skip/xfail/xpass |
| M9 leak/scope tests and source scan | GREEN |
| `git diff --check` | GREEN |

## Exact Remote Evidence

| Evidence | Exact result |
|---|---|
| Implementation SHA | `e2cda04920964cf383a749e07504539ccdafa0ab` |
| GitHub Actions run | `30645425078` |
| Workflow / job | `CI` / `dependency-sync` |
| Workflow / job conclusion | `success` / `success` |
| Remote full PostgreSQL pytest | `2540 passed` |
| Remote outcome matrix | `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| Alembic head/current | `a9b0c1d2e3f4` / `a9b0c1d2e3f4` |
| Implementation history | `8/8` exact M9 checkpoints |
| Security, concurrency, M1–M8 containment | GREEN |

Remote CI used frozen dependency sync, applied the real PostgreSQL migration,
verified the exact M9 head, passed Ruff, ran the inherited containment guards,
and completed the full suite against the implementation SHA above.

## External Audit Recovery

- Admin list/create/detail and authenticated registration-offer pages render
  their complete UI shell from an immutable typed UZ-Latn/RU copy contract.
- UI locale remains independent from legal `OfferLanguage`; rendered legal
  articles use `uz-Latn`, `uz-Cyrl`, or `ru` and remain autoescaped.
- `app.main:create_app` includes the offer router directly and exactly once;
  the auth router no longer imports or extends offer routes.
- Admin offer creation and authenticated registration-offer navigation are
  discoverable without adding activation, PII, OTP-registration, or debt runtime.
- Alembic `heads` and `current` both report `a9b0c1d2e3f4` in the valid local
  PostgreSQL test environment.

## Genuine Local Browser Acceptance

Google Chrome headless was driven through the Chrome DevTools Protocol against
the local application and PostgreSQL. All legal text was synthetic.

1. Platform admin logged in, created a REGISTRATION DRAFT, saved all three
   languages, supplied synthetic external-review evidence, approved it, and
   made it current. DB evidence: status CURRENT, 3 text variants, exactly
   1 current for the purpose, and matching lifecycle audits.
2. A separate authenticated account viewed the RU current offer, accepted it,
   and replayed the same form. DB evidence: exactly 1 acceptance and 1 audit;
   version, text, language, hash, aware UTC time, and bounded normalized browser
   evidence matched.
3. With no current offer, the browser received `409/OFFER_UNAVAILABLE`.
4. After a RU form was opened, the admin browser switched current. Submitting
   the old form reached the `offer-changed` fail-closed page and DB evidence
   showed 0 new acceptances and exactly 1 current. Automated HTTP coverage
   separately asserts the response header is `OFFER_CHANGED`.

## Closure And Preserved OUT Scope

The eight implementation checkpoints remain unchanged, ending at
`e2cda04920964cf383a749e07504539ccdafa0ab`. This docs-only closeout records
remote evidence without amending or relabeling that implementation history.

M9 adds no public registration, registration OTP, activation, new PII,
`customer_document`, object-domain attachment, `shop_customer`, owner
application, debt/payment runtime, rating, disclosure, notification,
scheduler, generic CMS, or full admin-management suite. M10 implementation
has not started.

`M9 REMOTE GREEN — CLOSED`
