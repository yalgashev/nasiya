# Nasiya M16 Decisions

This is the final decision record for M16. It implements the owner-approved Product Gate and Final Scope Freeze without broadening either document.

## Baseline and authority decision

M16 starts at M15 docs-closeout `547723ffc8e4148c5b4de86763b7c5add0588e86`, tree `a8bc494c90dde3cf186b49aad8b6b8470af99c00`, and Alembic head `b6c7d8e9f0a1`. The exact M15 implementation parent is `13bda85fb5df99d1be2b1da578e0f1a256f1d336`. The frozen TT blob is `d77c0f0f330a1330155a4ee3c46b05d97cf5561` with SHA-256 `569c54c67f33925714039bf3312ce47dd6b0f6b4d39d1cf1756408fbd2f00aab`. Remote M15 implementation/docs-closeout CI evidence is `31347914959/93333216249` (`4090 passed in 213.63s`) and `31348199466/93334007139` (`4090 passed in 196.59s`).

M16.00 owner approval is final. The protected planning SHA-256 values at M16 start are:

| Planning authority | SHA-256 |
| --- | --- |
| Product Gate | `1c57d9a31d5b02275925a37b59025c48c85908af45a34e837eacf0824944f379` |
| Final Scope Freeze | `2c094903780a5f3526e6275162501974d058831deda55a9b4b593cc88a105b25` |
| Micro-task guide | `5fa102979315cef760f35607da0b272b0aa25d46f704342d360819586377343f` |

M16.06 creates repository authority only. External planning files and the TT are not part of implementation edits.

## Product Owner decisions — final

1. **PO-M16-01:** M16 is the global `+5/-15` rating ledger, anti-farming, and privacy-safe band-disclosure foundation, not a full collections/admin platform.
2. **PO-M16-02:** Persist only `on_time_paid` and `overdue`; written-off, settlement, override, compensation, and future vocabulary are runtime/persistence OUT.
3. **PO-M16-03:** Initial score is 60; deltas are exactly `+5/-15`; clamp occurs after every event in `[0,100]`.
4. **PO-M16-04:** Score is derived from immutable events and is never a Customer cache/column.
5. **PO-M16-05:** Total order is exactly `(occurred_at, debt_id, event_type)`.
6. **PO-M16-06:** No event means `NEW`; the first eligible event permanently ends `NEW`.
7. **PO-M16-07:** Hard block wins as `BLOCKED`; otherwise bands are GREEN 75–100, YELLOW 50–74, RED 0–49.
8. **PO-M16-08:** `RED/YELLOW` do not deny Debt creation; inherited Debt-derived hard block is the only denial authority.
9. **PO-M16-09:** Hard block stays a post-Customer-lock M15 effective/persisted Debt read, never a rating cache/flag.
10. **PO-M16-10:** `+5` is considered only for locked pre-active, exact-full, on-time `active -> paid`; partial/late/marked/replay paths do not earn it.
11. **PO-M16-11:** Positive eligibility is original amount `>=100000`, accepted Tashkent day before paid day, and paid day not after due date.
12. **PO-M16-12:** Positive cap is one `+5` per `(shop_customer_id, paid Tashkent date)`.
13. **PO-M16-13:** Historical same-day winner is earliest `(paid_at,debt_id)`; live race is serialized by Customer lock and DB partial unique.
14. **PO-M16-14:** Each lawful `active -> overdue` writes exactly one `-15`, regardless of amount, discount, or positive cap.
15. **PO-M16-15:** Full late payoff retains `-15` and creates no `+5`; it only removes the overlay if no other overdue remains.
16. **PO-M16-16:** Effective-but-unmaterialized overdue is immediately `BLOCKED`, but receives no historical/live `-15` until lawful materialization; GET never mutates it.
17. **PO-M16-17:** Rating events are append-only and unique by `(debt_id,event_type)`; no update/delete/reversal.
18. **PO-M16-18:** Live rating, source transition, Payment/Debt, key, and audit commit or roll back in the same caller-owned transaction.
19. **PO-M16-19:** Customer lock remains the serialization point for rating append, cap, disclosure, create/accept, overdue, and payment.
20. **PO-M16-20:** Completed Payment replay and overdue no-op write no rating event, clock, or audit.
21. **PO-M16-21:** Upgrade deterministically reconciles immutable M14/M15 facts, never updates source rows, and first locks `debts` in `SHARE ROW EXCLUSIVE` mode.
22. **PO-M16-22:** Historical positives require coherent full ledger/terminal Payment/lawful audits; negatives require paired marker/revision and overdue/clawback audits; contradiction aborts upgrade.
23. **PO-M16-23:** Backfill uses `historical_reconciliation`, creates neither notification nor retroactive generic audit.
24. **PO-M16-24:** Operational writer drain, transaction completion, and old-version restart prohibition are mandatory; the table lock is defense-in-depth only.
25. **PO-M16-25:** Fresh disclosure requires active current-Shop live OWNER/MANAGER/CASHIER and its own linked ShopCustomer; platform-admin has no bypass.
26. **PO-M16-26:** Purpose is the exact closed set `debt_proposal_review|credit_limit_review|existing_debt_review`.
27. **PO-M16-27:** Fresh view is CSRF/idempotent POST-created audit snapshot; PRG/repeated GET retrieve it only; same-key replay returns it without a write.
28. **PO-M16-28:** Snapshot stores only band, purpose, view time, and internal authority FKs; never score/history/count/cause.
29. **PO-M16-29:** Shop-safe output/repr/log/audit exposes only band/purpose/time, subject solely to the frozen POST/raw-key/opaque-GET transport exceptions.
30. **PO-M16-30:** Suspended Shop cannot create a view but a still-live authorised member may read that actor/Shop's immutable historical snapshot; revoked/foreign/guessed lookup is generic unavailable.
31. **PO-M16-31:** Schema adds exactly `rating_events`, `disclosure_view_logs`, and two redundant parent uniques/composite FKs; no score/band/block cache column.
32. **PO-M16-32:** One fresh disclosure writes one snapshot plus one `disclosure.risk_band_viewed` USER audit in one transaction; replay duplicates neither.
33. **PO-M16-33:** Disclosure extends endpoint/result idempotency; raw key is transient/no-store only, while digest/hash stay in `idempotency_keys` and never reach browser/log/audit/error/repr.
34. **PO-M16-34:** No Customer self-rating/history, admin raw score/event UI, global search, or rating override.
35. **PO-M16-35:** Web adds exactly one tenant disclosure POST and one actor/shop-scoped snapshot GET; no JSON/API/fragment/admin route.
36. **PO-M16-36:** One linear Alembic child, real PostgreSQL backfill/concurrency/privacy evidence, eight checkpoints, exact-SHA CI, and docs-only closeout are required.

## Implementation decisions fixed by M16.01–05

- The existing overdue helper currently appends its audit pair before inline Payment. M16 must split its locked core from its audit append into a typed pending-effect boundary.
- `app.debt` and `app.payment` own local structural append ports; a composition-only concrete adapter is required, may not re-lock Customer, and has no reachable production no-op/default.
- The exact lock tail is `Customer -> ShopCustomer -> IdempotencyKey -> Debt -> Payment -> RatingEvent -> DisclosureViewLog -> AuditLog`. The inherited pre-Customer classes and same-class UUID ordering remain normative.
- The migration requires operational old-writer drain before the table lock. The lock closes only an in-flight source scan race; it cannot prove a restarted old writer safe after commit.
- Source rows are immutable. Reconciliation is revision-local SQL/Python, uses UUIDv5, and must fail closed rather than skip a doubtful fact.
- Disclosure GET does no target lock, no fresh clock, no hard-block/score recomputation, and no M16/domain write. Inherited auth/session touch is allowed.
- Static guards are source-scoped. Baseline inherited text/vocabulary may remain; new M16 runtime/persistence/router wiring for OUT scope is denied.

## Stable outcome decision

The lifecycle, score, privacy, IN/OUT, and evidence boundary are normative in [`m16_scope_contract.md`](m16_scope_contract.md). Existing and prospective placement is normative in [`m16_repository_map.md`](m16_repository_map.md). Exact schema, migration, reconciliation, and downgrade contracts are normative in [`m16_persistence_plan.md`](m16_persistence_plan.md).

## M16.40 local validation evidence

The controlled local validation ran from sixth-checkpoint SHA `1461a9495017002bc44ede339623a014020c9996` with the M16.37–40 worktree on 2026-08-12. `uv sync --dev --frozen` checked 48 packages. Explicit local PostgreSQL `alembic upgrade head`, `current`, and `heads` all resolved to the single head `c7d8e9f0a1b2`; the full suite included the controlled M15 mixed-fixture upgrade, revision-local Debt table-lock barrier, reconciliation, downgrade guards, inherited containment, and MinIO/security integration gates. Ruff check passed and the repo-wide format check reported 652 files formatted. The final explicit-env real-PostgreSQL run was exactly `4250 passed in 337.88s (0:05:37)` with zero failed, skipped, xfailed, xpassed, or warnings.

## Closure decision

M16.06 authorizes the later bounded implementation described by these documents. It does not add M16 product code, migration, or a checkpoint commit.
