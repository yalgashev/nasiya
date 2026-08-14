# M18 Known Limitations

M18 is a narrow payment-correction capability, not a general accounting or
operations platform. These intentional limitations require a future authorized
milestone to change.

1. Payment records remain immutable. M18 adds one append-only void fact; it
   has no refund, payout, chargeback, unvoid, edit, delete, correction,
   import, reconciliation, gateway, fiscal, or external-settlement feature.
2. Only the latest non-voided Payment of the locked Debt may be voided. There
   is no arbitrary historical void, batch action, global search, or support
   console.
3. Reasons are the five closed values. There is no free text, reason edit,
   actor disclosure, dispute workflow, approval queue, or platform-admin
   override.
4. Compensation is source-paired `-5`/`-10` only. M18 does not reverse `-15`
   or `-40`, alter historical source events, override scores, or expose raw
   score/event analytics.
5. M18 adds no stored/cached balance, score, cap-slot, block, interest, fee,
   tax, exchange rate, currency, or instalment calculation. Whole-UZS ledger
   reads remain PostgreSQL-derived.
6. There is no scheduler, worker, queue, cron, CLI trigger, retry policy,
   notification/outbox, report, export, webhook, API/JSON/HTMX surface, or
   Customer self-void route.
7. Migration safety is operational: drain old writers before upgrade and do
   not restart an old binary after the source scan lock begins. Downgrade is
   only available for an M18-empty, M17-compatible database.
8. Browser surfaces are SSR-only and no-store. They do not store financial
   state in browser storage or implement client money/time/rating decisions.
9. M18.42–43 Chrome/PostgreSQL evidence is controlled local synthetic evidence
   only. It is not production-data acceptance, a production deployment claim,
   or remote CI evidence. Its retained output is sanitized PASS booleans; all
   temporary profiles, sessions, fixtures, keys, cookies, screenshots, and
   helper artifacts were removed.
