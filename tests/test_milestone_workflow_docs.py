from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_repository_guide_routes_milestones_to_bounded_context() -> None:
    guide = _read("AGENTS.md")

    assert "docs/milestone_workflow.md" in guide
    assert "docs/milestone_task_packet.md" in guide
    assert "finite task read-set" in guide
    assert "focused checks" in guide


def test_workflow_freezes_discovery_escalation_and_test_ladder() -> None:
    workflow = _read("docs/milestone_workflow.md")

    for contract in (
        "One-time milestone discovery",
        "`EXISTS`",
        "`EXTEND`",
        "`PLANNED`",
        "concrete unresolved import, caller, constraint, or test",
        "Verification ladder",
        "Remote GREEN may be recorded only for the exact checkout SHA",
        "Context restart rule",
    ):
        assert contract in workflow


def test_task_packet_requires_finite_scope_and_compact_handoff() -> None:
    packet = _read("docs/milestone_task_packet.md")

    for field in (
        "Baseline checkpoint SHA/tree",
        "Authority sections/IDs",
        "Read only",
        "May change",
        "Must preserve",
        "Forbidden",
        "Focused commands",
        "Compact handoff",
        "Read-set expansions and reasons",
        "Next task and its minimum read-set",
    ):
        assert field in packet
