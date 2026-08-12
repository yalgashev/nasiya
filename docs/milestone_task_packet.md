# Milestone Microtask Packet

Copy this template into the task prompt or working handoff. Replace every
placeholder; delete sections that truly do not apply.

## Identity

- Task: `MXX.YY — <title>`
- Baseline checkpoint SHA/tree: `<sha>` / `<tree>`
- Depends on: `<tasks or decision IDs>`
- Goal: `<one observable outcome>`

## Bounded context

- Authority sections/IDs: `<MXX-D.., MXX-OUT..>`
- Read only: `<finite docs/code/test path list>`
- May change: `<finite path list>`
- Expected new symbols: `<PLANNED symbols or none>`

Do not expand the read-set without an evidenced import, caller, constraint, or
test dependency. Record each expansion and its reason in the handoff.

## Frozen behavior

- Must preserve: `<existing behavior/invariants>`
- Must implement: `<exact contracts and ordering>`
- Forbidden: `<OUT vocabulary, routes, tables, side effects>`
- Transaction/lock/privacy rules: `<applicable decision IDs>`

## Verification

- Focused commands: `<exact tests and lint paths>`
- Checkpoint-only commands: `<affected families/Ruff, or none>`
- PASS: `<observable zero-nonpass condition>`

## Compact handoff

- Start and resulting SHA/tree:
- Changed paths:
- Decisions consumed:
- Read-set expansions and reasons:
- Checks with exact outcomes:
- Remaining limitation or blocker:
- Dirty tree:
- Next task and its minimum read-set:
