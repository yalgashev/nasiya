# M7 Result

Status: M7 REMOTE GREEN — CLOSED

Date: 2026-07-29

## Capability

M7 — Telegram OTP Authentication

## Validated implementation baseline

- Exact implementation SHA:
  `2c0c783db35a7a7e8dddeb7ecb6c5b20531a17c3`.
- Branch: `main`.
- GitHub Actions run: `30453909901`.
- Workflow: `CI`.
- Job: `dependency-sync`.
- Conclusion: `success`.
- Alembic head: `e7f8a9b0c1d2`.
- Ruff check: PASS.
- Ruff format check: PASS.
- Full pytest: `1666 passed`.
- Failed/skipped/xfailed/xpassed: `0/0/0/0`.
- Real Telegram acceptance: `M7 ACCEPTANCE GREEN — 16/16`.
- Local technical validation: GREEN.
- Implementation baseline sync:
  `HEAD == origin/main`, divergence `0 0`, clean worktree.

## Delivered capability summary

- LOGIN-only Telegram OTP for existing active Telegram-linked users.
- Exactly six ASCII digits protected at rest by HMAC-SHA-256.
- Browser/session binding, a `180s` TTL, and at most `5` verification
  attempts.
- A `60s` new-code cooldown and a durable OTP dispatcher.
- Web never calls the Telegram network directly.
- Existing password login remains available.
- Registration, recovery, and SMS delivery remain out of scope.

## Checkpoints

1. `f2a7fac04061e4fa035f751ff85b35b91669445f` —
   `M7: freeze Telegram OTP authentication scope`
2. `f70c81a79bf1300874bfc9d8eb14a7db7f0b2248` —
   `M7: add OTP cryptography and settings`
3. `6c57da15f6eb01f526a2a86335c1186d20f1647b` —
   `M7: add OTP challenge persistence`
4. `87828b053815d48e1fecf6f0bb2699f590bee2b8` —
   `M7: add OTP issuance and rate limits`
5. `3d5456055569a07d1349cee66e8ac0789a7f5a3c` —
   `M7: add durable Telegram OTP dispatcher`
6. `fe4c679d7dcde03aa9e52a722ea73df23e395565` —
   `M7: add OTP verification and session login`
7. `05deba9f4faaf8e87371525821f47e629b2874be` —
   `M7: add phone OTP web flow`
8. `2c0c783db35a7a7e8dddeb7ecb6c5b20531a17c3` —
   `M7: complete Telegram OTP authentication`

## Closure note

- `2c0c783db35a7a7e8dddeb7ecb6c5b20531a17c3` is the immutable M7
  implementation baseline validated by remote CI.
- This `m7-result.md` and the remote-status corrections are recorded in the
  subsequent docs-only closeout commit.
- The docs-only closeout commit does not change product code, schema,
  migrations, dependencies, tests, CI, or the deployment contract.
- The implementation SHA and remote run are the authoritative technical
  closure evidence for M7.
- The docs-only closeout commit SHA is deliberately taken from Git history
  after commit creation and is not self-referenced here.

M7 REMOTE GREEN — CLOSED
