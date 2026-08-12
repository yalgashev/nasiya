# M16 result

M16 is locally implemented through the seventh checkpoint. It provides
exact-once immutable `+5`/`-15` rating events, sequential score folding,
M15 hard-block overlay, deterministic source-preserving reconciliation, and
private band-only staff disclosures.

The schema head is `c7d8e9f0a1b2`; it adds only `rating_events` and
`disclosure_view_logs` with the planned composite tenant/source constraints.
There are exactly two disclosure routes: a CSRF/idempotent tenant POST and an
actor/shop-scoped immutable GET snapshot.

M16.42–43 controlled local acceptance retained only sanitized PASS booleans.
It covered producer edge cases, role/tenant privacy, no-store browser output,
and UZ/RU responsive presentation. Temporary browser and fixture artifacts
were discarded. See [the final report](docs/m16_final_report.md) and
[known limitations](docs/m16_known_limitations.md). No remote validation is
claimed.

M16.44 repeated the explicit-environment real-PostgreSQL suite: `4250 passed
in 328.22s (0:05:28)`, with zero non-pass outcomes or warnings. Alembic,
Ruff check, and Ruff format check were also GREEN locally.
