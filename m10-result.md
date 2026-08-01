# M10 Result

Status: `M10 REMOTE GREEN — CLOSED`

Date: 2026-08-01

## Milestone

M10 — Customer Identity And Document Attachment Foundation

## Exact Implementation And Remote Evidence

| Evidence | Exact result |
|---|---|
| Implementation SHA | `b79250858a3f6a63908a288f891d5dad1126dd48` |
| GitHub Actions run | `30705134413` |
| Workflow / job | `CI` / `dependency-sync` |
| Job identifier | `91382838013` |
| Run and job conclusion | `success` / `success` |
| Full remote pytest | `2735 passed` |
| Outcome matrix | `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| Alembic head/current | `b0c1d2e3f4a5` / `b0c1d2e3f4a5` |
| Implementation checkpoints | `8/8` exact commits |
| Local repeated full validation | `2735 passed` in both normal runs and the separate durations run |
| Real Chrome/PostgreSQL/MinIO acceptance | M10.81, M10.82, and M10.83 GREEN |
| Implementation baseline sync after push | `HEAD == origin/main`, divergence `0 0`, clean worktree |

The exact pushed implementation SHA was verified by GitHub Actions run
`30705134413`. Frozen dependency sync, the approved crypto dependency, Ruff,
real PostgreSQL migration/head verification, private MinIO integration,
synthetic backup/restore, security, concurrency, and inherited containment all
succeeded.

## Eight Implementation Checkpoints

1. `621ebe9c6155713c6755a2109b3525f59caef82e` —
   `M10: freeze customer identity scope`
2. `5cc5ae1caea0e16fa9e45dadec92806d3ec349b2` —
   `M10: add encrypted customer identity contracts`
3. `3980534c5e3b1a1cc4ff96493a61cab63c2a7ad5` —
   `M10: add customer identity persistence`
4. `dceb5d6c15089d580e4802f554d8dad62aeeb19e` —
   `M10: add customer identity services`
5. `6b0dd4fed11925f3a79c07bf936c1abed098ccbd` —
   `M10: add customer document attachment`
6. `6b2d2c27843b36e08b0838d0196c6a42d9e0956d` —
   `M10: expose customer identity web flows`
7. `db8b4818248fef019d9f3d7dbee082eed7d410ea` —
   `M10: harden PII security and concurrency`
8. `b79250858a3f6a63908a288f891d5dad1126dd48` —
   `M10: complete customer identity foundation`

## Delivered And Accepted Boundary

- Existing authenticated users can maintain encrypted own-customer identity
  for an existing `draft` customer with stale-revision and duplicate-JSHSHIR
  protection.
- Concrete customer document attachments reuse M8 backend-mediated image
  ingest, metadata removal, private lifecycle, compensation reconciliation,
  and authorized 300-second temporary reads.
- One attachment remains `CURRENT`; replacement makes the prior attachment
  `SUPERSEDED`. Stale replacement is zero-write.
- CR-M10-01 keeps the early rate check read-only, makes M8 ingest the only
  attempt recorder, serializes attach/compensation on the object row, performs
  atomic TX-C `DELETE_PENDING` claims, and delegates provider cleanup to the
  existing M8 reconciliation path.
- Own-user authorization is server-derived. Shop roles and platform-admin
  status grant no cross-customer identity or document access.

## Genuine Chrome And Storage Acceptance

Only synthetic data was used. M10.81 passed create/update, masked display,
blank sensitive update fields, stale revision, duplicate rejection,
`no-store`, and draft containment. M10.82 passed backend JPEG/PNG/WebP upload,
metadata removal, one-current replacement, authorized fetch, and anonymous/
cross-user denial against real PostgreSQL and MinIO. M10.83 passed two-tab
replacement/stale behavior, atomic orphan claim, attached-object compensation
`NOOP`, ambiguous-delete recovery, and existing operator reconciliation.

## Scope Closure

Every customer remains `draft`. M10 adds no public registration, REGISTRATION
OTP, activation, customer lead, `shop_customer`, shop-assisted PII capture,
debt/payment, rating, disclosure, notification, scheduler, OCR/MRZ,
biometrics, registry integration, or generic attachment/CMS/admin/KMS
platform.

At the time this file was authored, the docs-only closeout commit carrying it
had not yet been created. That commit is a documentation-only descendant of
the remote-green implementation SHA and is not a ninth implementation
checkpoint. M11 implementation has not started.

`M10 REMOTE GREEN — CLOSED`
