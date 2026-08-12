# Nasiya M16 Persistence Plan

This is the frozen persistence and migration contract for later M16 implementation. Its exact baseline is `main` HEAD `547723ffc8e4148c5b4de86763b7c5add0588e86`, tree `a8bc494c90dde3cf186b49aad8b6b8470af99c00`, M15 implementation parent `13bda85fb5df99d1be2b1da578e0f1a256f1d336`, and sole Alembic head `b6c7d8e9f0a1`. It specifies planned schema and operational behavior; it does not claim that M16 tables, models, or migration symbols exist at the baseline.

## Migration shape and operational precondition

The one planned migration is exactly:

```python
revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
```

Before upgrade, operations must drain every old writer, wait for all its transactions to finish, and prohibit restarting an old application version until the migration completes. Inside the upgrade transaction, the first source-data action is:

```sql
LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE;
```

The lock is defense-in-depth against an in-flight source scan only; it is not a replacement for the drain/restart prohibition. The upgrade then creates redundant parent uniqueness before dependent foreign keys, creates the two M16 tables/checks/indexes, extends closed idempotency/audit registries, validates/reconciles sources, and leaves exactly one Alembic head. Any doubtful or contradictory source fact aborts the transaction and leaves no partial M16 row.

The operationally ordered schema actions are:

1. drain old writers and acquire the `debts` table lock;
2. add `uq_debts_id_shop_customer_id` and `uq_shop_customers_id_shop_id`;
3. create `rating_events`, its checks, unique constraints, foreign key, and indexes;
4. create `disclosure_view_logs`, its checks, foreign keys, and index;
5. extend the existing idempotency pair registry;
6. extend the existing audit event/object registry;
7. reconcile coherent immutable M14/M15 sources into rating rows only;
8. assert one head and release through normal transaction completion.

There is no score, band, event-count, hard-block, current-rating, or disclosure cache column; no source-table update; no new dependency; and no second migration.

The reconciled M15 source authority is the existing `debts` state machine and its named `ck_debts_status_allowed`, `ck_debts_status_metadata_matches_status`, `ck_debts_timestamp_order`, `ck_debts_overdue_metadata_pair`, `ck_debts_overdue_revision_positive`, and `ck_debts_overdue_revision_not_after_revision` checks; immutable `payments` rows are additionally bounded by `uq_payments_debt_id_debt_revision_after`. Source-audit coherence is evaluated only through the existing exact-shape `ck_audit_log_event_type_allowed`, `ck_audit_log_object_type_allowed`, `ck_audit_log_actor_matches_event`, `ck_audit_log_object_matches_event`, and `ck_audit_log_payload_exact_shape` registry. The existing `ck_idempotency_keys_endpoint_result_pair_allowed`, digest/hash checks, and actor/endpoint/key unique remain authoritative; M16 extends only the closed endpoint/result pair.

## Planned `rating_events` table

```text
id                UUID primary key
shop_customer_id  UUID not null
debt_id           UUID not null
event_type        VARCHAR(32) not null
delta             SMALLINT not null
occurred_at       TIMESTAMPTZ not null
business_date     DATE not null
recording_source  VARCHAR(32) not null
```

The immutable ledger constraints are named exactly:

```text
ck_rating_events_event_type_allowed
ck_rating_events_delta_matches_event
ck_rating_events_recording_source_allowed
ck_rating_events_business_date_matches_occurred_at
uq_debts_id_shop_customer_id
fk_rating_events_debt_shop_customer
uq_rating_events_debt_id_event_type
ux_rating_events_positive_shop_customer_business_date
ix_rating_events_shop_customer_occurred_debt_event
```

`event_type` is only `on_time_paid|overdue`. `delta` must match it exactly (`+5|-15`), and `recording_source` is only `live|historical_reconciliation`. `business_date` must be the Tashkent business date of `occurred_at`. The redundant parent key is `uq_debts_id_shop_customer_id (id, shop_customer_id)`; `fk_rating_events_debt_shop_customer (debt_id, shop_customer_id)` references that pair with `RESTRICT`.

`uq_rating_events_debt_id_event_type (debt_id, event_type)` is the one-source-event idempotency law. The anti-farming index is exactly partial:

```sql
UNIQUE (shop_customer_id, business_date)
WHERE event_type = 'on_time_paid'
```

and is named `ux_rating_events_positive_shop_customer_business_date`. Ordered read is exactly ascending `(shop_customer_id, occurred_at, debt_id, event_type)`, named `ix_rating_events_shop_customer_occurred_debt_event`, without `INCLUDE`. Insert is the only ledger mutation; UPDATE/DELETE/reversal/compensation are forbidden.

## Planned `disclosure_view_logs` table

```text
id                UUID primary key
actor_user_id     UUID not null
shop_id           UUID not null
shop_customer_id  UUID not null
purpose           VARCHAR(40) not null
band              VARCHAR(16) not null
created_at        TIMESTAMPTZ not null
```

The exact named constraints/indexes are:

```text
ck_disclosure_view_logs_purpose_allowed
ck_disclosure_view_logs_band_allowed
uq_shop_customers_id_shop_id
fk_disclosure_logs_shop_customer_shop
fk_disclosure_view_logs_actor_user_id_users_id
ix_disclosure_view_logs_shop_id_id
```

`purpose` is only `debt_proposal_review|credit_limit_review|existing_debt_review`; `band` is only `new|green|yellow|red|blocked`. The redundant parent key is `uq_shop_customers_id_shop_id (id, shop_id)`. `fk_disclosure_logs_shop_customer_shop (shop_customer_id, shop_id)` references that pair with `RESTRICT`; `fk_disclosure_view_logs_actor_user_id_users_id` references `users(id)` with `RESTRICT`. The authorization/index access path is ascending `(shop_id, id)`, named `ix_disclosure_view_logs_shop_id_id`, without `INCLUDE`.

A snapshot is append-only and stores the already-derived safe band, purpose, time, and internal authority keys. It stores no score, event/delta/count, amount/balance, hard-block cause, PII, business identifier, idempotency key/digest/request hash, or disclosure payload blob.

## Idempotency, audit, and disclosure transaction

The existing `idempotency_keys` table gets no M16 column/index. Its closed pair registry is extended by exactly:

```text
endpoint = shop.risk_band_disclosures.create
result_object_type = disclosure_view
```

The canonical v1 hash is over:

```text
domain + actor_user_id + current_shop_id + shop_customer_id + purpose
```

The raw key is never persisted beyond the existing protected key representation and never appears in browser, audit, log, error, or repr. Same key plus same hash resolves/replays the original disclosure row; same key plus different hash conflicts.

The audit registry extension is exactly one USER event:

```text
event_type = disclosure.risk_band_viewed
object_type = disclosure_view
payload keys = purpose, band
```

The M16 ledger does not append generic rating audit events. A fresh disclosure is one transaction in this order:

```text
parse/CSRF -> discover scalar target
-> Shop -> ShopStaff -> User -> Customer -> ShopCustomer -> IdempotencyKey
-> replay/conflict -> one trusted now -> M15 hard-block read + ordered event read
-> derive band -> DisclosureViewLog -> AuditLog -> flush/commit -> 303
```

No new GET row, target lock, current clock, or band recomputation is permitted. GET looks up through the current Shop/actor scope and stored parent chain, then returns the stored band/purpose/time only. Suspended own-Shop historical read is allowed; revoked/foreign/guessed/corrupt/missing requests are generic unavailable.

## Reconciliation contract

Only M14/M15 immutable facts may produce historical rows. Event IDs are deterministic UUIDv5 from a revision-local namespace and `(event_type, debt_id)`; historic rows set `recording_source = historical_reconciliation`. The authoritative fold afterwards is:

```text
ORDER BY occurred_at ASC, debt_id ASC, event_type ASC
score_0 = 60
score_i = min(100, max(0, score_(i-1) + delta_i))
```

A positive is inserted only for a paid Debt with `paid_at`, null overdue marker/revision, original amount at least 100000, Tashkent accepted day before paid day, paid day on/before due date, exact Payment sum equal to discounted amount, terminal Payment created at `paid_at` and carrying final Debt revision, plus lawful USER `payment.recorded` and `debt.paid` audits. Partition candidates by `(shop_customer_id, Tashkent paid date)` and retain the earliest `(paid_at, debt.id)`.

A negative is inserted only for `overdue|paid` source status with both overdue marker/revision and matching SYSTEM `debt.overdue` plus `debt.clawback_applied` audits at that exact marker/revision. Active past-due debt without a marker produces no historical negative: it remains effectively hard-blocked until a future lawful materialization. Historical reconciliation inserts only rating rows: no Debt/Payment/Audit update, generic retroactive audit, notification, or event correction.

## Downgrade and verification guard

Downgrade begins by refusing to proceed if any of these exists:

```text
rating_events row
disclosure_view_logs row
M16 idempotency endpoint/result row
M16 disclosure audit event/object row
any invalid row under the exact M15 idempotency or audit registry checks
```

The error prefix is exactly `M16 downgrade blocked:`. The downgrade path never DELETEs or truncates M16/source data to make itself succeed. Only an empty, valid M16 state may structurally remove the schema extension.

Every guard executes before the first downgrade DDL. After the registries are restored to their exact M15 predicates, downgrade drops the two child tables and their indexes/foreign keys first; only then may it drop `uq_shop_customers_id_shop_id` and `uq_debts_id_shop_customer_id`. Thus no downgrade can silently discard an event, disclosure, idempotency result, audit fact, or parent-chain authority.

Real PostgreSQL evidence is mandatory:

- fresh M15 source: upgrade -> empty guarded downgrade -> re-upgrade;
- deterministic mixed M15 fixture: coherent reconciliation and byte-preserved sources;
- malformed/contradictory fixture: whole-upgrade rollback with no partial rating rows;
- operational writer-drain/table-lock proof;
- non-empty M16 data: guarded downgrade denial with the exact prefix;
- one-head assertion and migration discovery through normal Alembic metadata.

SQLite, `create_all`, manual DDL, skips/xfails, retry loops, sleeps, timeouts, NOWAIT, SKIP LOCKED, and advisory locks are not equivalent evidence.
