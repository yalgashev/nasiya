# M11 Result

Status: `M11 REMOTE GREEN — CLOSED`

Date: 2026-08-06

## Milestone

M11 — Existing-Customer Registration OTP And Activation

## Exact Implementation And Remote Evidence

| Evidence | Exact result |
|---|---|
| Exact pushed SHA | `8741ffe7eeb710d05342b43473281d4a5f9c316b` |
| Recovery checkpoint | `6818b55cd51778270853eb72d63b8bd45f3b9884` |
| GitHub Actions run | `31122632059` |
| Workflow / job | `CI` / `dependency-sync` |
| Job identifier | `92686276933` |
| Run and job conclusion | `success` / `success` |
| Full remote pytest | `3252 passed` |
| Outcome matrix | `0 failed`, `0 skipped`, `0 xfailed`, `0 xpassed` |
| Alembic head/current | `d2e3f4a5b6c7` / `d2e3f4a5b6c7` |
| Implementation checkpoints | `8/8` exact commits, intact ancestry |
| Post-manual bounded correction | One audited descendant; no checkpoint relabel or history rewrite |
| Controlled Chrome/Telegram/PostgreSQL acceptance | M11.R11 GREEN |

## Eight Implementation Checkpoints

1. `17934cec798aa8ffb49eb886bc86342645ca7df1` —
   `M11: freeze customer activation scope`
2. `486e0f0574a570805e87d3b078161f28ff0ec985` —
   `M11: add registration OTP contracts`
3. `8b117dd39cbc3e5040f43f2ae34db8eaeab7842d` —
   `M11: extend customer activation persistence`
4. `7a9ff508344f2197f3d8b21e8b90001f4ed68df0` —
   `M11: add registration OTP issuance`
5. `f83b18de79c6a35c8a0159e160825089b482d7f2` —
   `M11: add atomic customer activation`
6. `a91cc70038e245d17b2d5dc645dbe7da47f98786` —
   `M11: expose customer activation web flow`
7. `b8ca6e9b8bcdb5895176fa91eb1914a9b24ee1af` —
   `M11: harden activation security and concurrency`
8. `6818b55cd51778270853eb72d63b8bd45f3b9884` —
   `fix: require self-phone verification for Telegram OTP`

The exact pushed SHA is the R12-authorized bounded descendant
`8741ffe7eeb710d05342b43473281d4a5f9c316b` (`fix: register OTP runtime model
metadata`). It corrects fresh-process runtime composition without creating a
ninth checkpoint, rewriting ancestry, or changing product semantics.

## Delivered And Accepted Boundary

- REGISTRATION purpose is server-selected for an authenticated user's existing
  eligible `draft` customer.
- OTP authority requires a current active self-phone-verified Telegram
  generation owned by that exact user.
- A mismatching self-contact is generic and zero-mutation; legacy unverified
  links cannot issue LOGIN or REGISTRATION OTP.
- Offer, identity, document, object, and verified-link generation changes stale
  an outstanding activation code before candidate comparison.
- Successful verification performs exactly one atomic activation, typed event,
  central audit write, and current-session/CSRF rotation.
- Active ordinary unlink is denied; protected same-phone re-verification is
  allowed and invalidates older LOGIN/REGISTRATION challenges.
- Telegram network I/O occurs only after database Session/transaction closure.

## Manual And Runtime Acceptance

Controlled development acceptance passed different-phone rejection, matching
self-contact verification, four stale-generation cases, exactly-once
activation and replay, active unlink denial, and active protected same-phone
re-verification. Exact-image smoke then passed worker contact handling and a
real durable LOGIN dispatch. No sensitive value or identifier is recorded.

## Scope Closure

Public/anonymous registration, new user/customer creation, public Telegram
bootstrap, customer lead, shop-assisted onboarding, `shop_customer`, debt,
payment, rating, disclosure, notification, scheduler, alternate OTP transport,
generic OTP infrastructure, and M12 implementation remain outside M11.

At the time this file was authored, the docs-only closeout commit carrying it
had not yet been created. That commit is a documentation-only descendant of
the exact remote-green SHA and is not another implementation checkpoint.

`M11 REMOTE GREEN — CLOSED`
