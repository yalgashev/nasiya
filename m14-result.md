# M14 Result

Status: `M14 REMOTE GREEN — CLOSED`

M14 provides a bounded active-Debt payment ledger: whole-UZS partial/full
payments, immutable history/receipts, payment-aware exposure, tenant/own
Customer SSR views, CSRF/PRG, and PostgreSQL-backed idempotent concurrency.

Local Chrome 150 + controlled PostgreSQL acceptance passed for all three shop
roles, four methods, partial/replay/stale/overpayment/full-paid cases,
suspended/revoked/foreign denial, customer own history/receipt, UZ/RU, and
320–430px viewports. DevTools found no duplicate POST, cache/storage artifact,
or console error. Evidence is synthetic and sanitized.

The eighth implementation checkpoint is
`2293b97459218b61ab796863b4c0a0522edbdb6a` with tree
`db67e403b215e768a2d618e6b5dce695e7abcbb0`. Its exact checkout passed GitHub
Actions [run 31295227718](https://github.com/yalgashev/nasiya/actions/runs/31295227718),
[job 93199127585](https://github.com/yalgashev/nasiya/actions/runs/31295227718/job/93199127585),
with Alembic head `a5b6c7d8e9f0` and `3919` tests passed in `184.32s`; failed,
skipped, xfailed, and xpassed counts were all zero. The repeated local run had
already passed `3919` tests in `305.79s` with zero non-pass or warning outcomes.
Full evidence is in [docs/m14_final_report.md](docs/m14_final_report.md).

M14 deliberately excludes overdue/later payment, void/refund/correction,
rating, notification, scheduler, gateway, report/export, and customer
self-payment. A past-due active Debt is nonpayable.
