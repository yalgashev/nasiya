# Nasiya M17 Known Limitations

M17 is a written-off Debt and recovery foundation, not a general collections or
accounting platform. These are intentional boundaries.

1. Only a persisted, source-coherent overdue Debt is writable off. Effective
   active past-due state is not an admin write-off shortcut.
2. Write-off is direct active-platform-admin authority with a closed reason;
   there is no shop request, approval queue, bulk operation, global search,
   impersonation, or platform-admin payment action.
3. Payments stay immutable whole-UZS staff-recorded values. There is no void,
   refund, reversal, correction, forgiveness, import, gateway, or accounting
   compensation. Therefore `written_off_settled` cannot return to
   `written_off` in M17.
4. There is no scheduler, cron, worker, queue, CLI trigger, retry policy,
   notification/outbox, report, export, loss accounting, override, setting, or
   raw-rating admin analytics surface.
5. M17 adds no historical `-40/+10` backfill and does not rewrite source rows.
   Its migration requires an operational old-writer drain and is guarded on
   downgrade; real correctness evidence is PostgreSQL-specific.
6. Risk disclosure stays private, actor/shop-scoped, historical, and band-only.
   It is not a Customer/API/global-search feature and never exposes score,
   event history, reason, administrator identity, causes, keys, hashes, or
   other-Shop data.
7. Browser surfaces are SSR-only: no local/session/Cache Storage, client money
   or time calculation, JSON/HTMX/API endpoint, or offline recovery operation.
8. Chrome/PostgreSQL manual acceptance is controlled local synthetic evidence,
   not production data or remote CI. It retains only sanitized booleans;
   temporary browser profiles, sessions, fixtures, cookies, screenshots, and
   identifiers are removed after use.

Remote validation and docs-only closeout are deliberately not claimed until a
later authorized push/CI stage.
