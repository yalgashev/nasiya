# M16 known limitations

- M16 evidence is local only. It does not claim remote CI, staging, or
  production acceptance.
- Controlled Chrome acceptance used synthetic data and a temporary local
  PostgreSQL database. It is a release checklist, not telemetry or a
  replacement for operational rollout approval.
- The overdue batch path was exercised through its service harness only. M16
  intentionally adds no CLI, scheduler, job runner, notification, or batch
  route.
- The M16 migration requires an operational drain of old writers before the
  revision-local `debts` table lock. The lock protects the in-flight scan; it
  cannot make an old binary restarted after migration safe.
- A disclosure is an immutable historical band/purpose/time snapshot. It does
  not refresh on GET; an authorized staff member must submit a new POST for a
  fresh view.
- The rating ledger has no reversal, override, compensation, written-off, or
  cached score/band facility. Those remain deliberately OUT of scope.
