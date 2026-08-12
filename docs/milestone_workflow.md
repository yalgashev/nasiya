# Bounded Milestone Workflow

This workflow keeps milestone work auditable without repeatedly loading the
whole repository. Product Gate, Freeze, explicit task requirements, and
tracked decisions remain authoritative; context reduction never weakens them.

## One-time milestone discovery

Perform broad discovery once, before implementation. Record the baseline
commit/tree, upstream relation, Alembic head, protected planning hashes, exact
IN/OUT boundary, existing constraints, relevant packages, and test families.
Freeze that knowledge in the milestone scope contract, decisions, repository
map, and—only when schema changes—persistence plan.

The repository map must label every named symbol or placement as:

- `EXISTS`: usable without semantic change;
- `EXTEND`: existing code that the milestone may alter;
- `PLANNED`: not present yet and never described as existing.

Give stable decisions short IDs such as `M17-D07`, `M17-LOCK-03`, and
`M17-OUT-05`. Later tasks cite IDs instead of repeating entire documents.

## Bounded task intake

Create each microtask from `docs/milestone_task_packet.md`. Its read-set and
write-set must be finite. Start by reading only:

1. `AGENTS.md` and the task packet;
2. cited sections or decision IDs in current milestone authority docs;
3. explicitly listed implementation and test files.

Use targeted `rg` symbol/import/caller searches. Do not scan every package,
load previous milestone reports, or replay old prompts by default. Expand the
read-set only when a concrete unresolved import, caller, constraint, or test
dependency proves it necessary. Add the file and reason to the task handoff.
Security-sensitive paths may expand as far as needed to prove the invariant.

## Verification ladder

Run validation in proportion to the change:

- contract/value work: focused unit and static-contract tests;
- repository/migration work: focused real-PostgreSQL tests and Alembic checks;
- service work: focused unit, fault, transaction, and relevant PG tests;
- web work: focused route/template/security tests;
- checkpoint: all affected families plus Ruff check/format check;
- hardening or closure: frozen sync, one-head migration evidence, all required
  external-service gates, and the full real-PostgreSQL suite.

A microtask must not run the full suite merely for reassurance. Conversely, a
declared checkpoint or closure cannot replace its required full gate with
focused tests.

## Checkpoint and evidence discipline

End each task with the packet's handoff fields: baseline, resulting state,
changed paths, decisions consumed, read-set expansions, checks, and next task.
Keep logs summarized as command, exact outcome/count/duration, head, SHA, tree,
and safe run/job URL. Do not retain raw logs, fixtures, credentials, customer
data, opaque locators, or idempotency keys.

At a checkpoint, audit scope before commit, use the exact required subject,
record SHA/tree, and require a clean tree. Before push, verify linear history,
upstream counts, protected hashes, unchanged TT/planning files, and one
Alembic head. Remote GREEN may be recorded only for the exact checkout SHA.

## Context restart rule

After a new chat or context compaction, resume from the latest checkpoint and
handoff. Do not repeat discovery unless the baseline, authority, dependency
graph, or repository state changed. If it changed, reconcile only the affected
map entries and decisions, then record the delta.
