# M14 Result — Local Draft

M14 provides a bounded active-Debt payment ledger: whole-UZS partial/full
payments, immutable history/receipts, payment-aware exposure, tenant/own
Customer SSR views, CSRF/PRG, and PostgreSQL-backed idempotent concurrency.

Local Chrome 150 + controlled PostgreSQL acceptance passed for all three shop
roles, four methods, partial/replay/stale/overpayment/full-paid cases,
suspended/revoked/foreign denial, customer own history/receipt, UZ/RU, and
320–430px viewports. DevTools found no duplicate POST, cache/storage artifact,
or console error. Evidence is synthetic and sanitized.

The seventh checkpoint is `11d4575` (`M14: harden payment security and
concurrency`). The eighth checkpoint and all remote evidence are still pending;
M14 is not claimed remote GREEN here. The clean post-draft local validation
passed `3919` tests in `305.79s` with zero non-pass or warning outcomes; detail
is recorded in [docs/m14_final_report.md](docs/m14_final_report.md).

M14 deliberately excludes overdue/later payment, void/refund/correction,
rating, notification, scheduler, gateway, report/export, and customer
self-payment. A past-due active Debt is nonpayable.
