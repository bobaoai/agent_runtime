from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ENTRY_SECTIONS = (
    "## Identity",
    "## Session Startup Protocol",
    "## First Principles",
    "## Product Boundary",
    "## Authority and Design Hierarchy",
    "## Core Rules",
    "## Task Routing",
    "## Deeper Context Pointers",
)

REQUIRED_STARTUP_PATHS = (
    "09_claude/core/SOUL.md",
    "09_claude/core/USER.md",
    "09_claude/core/COMMUNICATION.md",
    "09_claude/core/PROJECT_ADAPTER.md",
    "designDoc/the_charter.md",
    "CURRENT_HANDOFF.md",
)

REQUIRED_GOVERNANCE_SKILLS = (
    "engineering-change-review",
    "engineering-code-design",
    "the-contract-audit",
    "the-skill-management",
    "the-task-routing",
)


def test_claude_entry_contract_is_complete() -> None:
    entry = (REPOSITORY_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    for section in REQUIRED_ENTRY_SECTIONS:
        assert section in entry
    for path in REQUIRED_STARTUP_PATHS:
        assert f"`{path}`" in entry
        assert (REPOSITORY_ROOT / path).is_file()


def test_claude_and_codex_session_mirrors_are_present() -> None:
    for host in ("09_claude", "09_codex"):
        for relative_path in (
            "core/SOUL.md",
            "core/USER.md",
            "core/COMMUNICATION.md",
            "core/PROJECT_ADAPTER.md",
            "axioms/INDEX.md",
        ):
            assert (REPOSITORY_ROOT / host / relative_path).is_file()


def test_governance_skills_are_installed_for_both_hosts() -> None:
    for skill_id in REQUIRED_GOVERNANCE_SKILLS:
        assert (
            REPOSITORY_ROOT / ".claude" / "skills" / skill_id / "SKILL.md"
        ).is_file()
        assert (
            REPOSITORY_ROOT / ".agents" / "skills" / skill_id / "SKILL.md"
        ).is_file()


def test_portable_core_does_not_embed_a_project_adapter() -> None:
    assert not (
        REPOSITORY_ROOT
        / "09_soul"
        / "core"
        / "PROJECT_ADAPTER_trading_platform.md"
    ).exists()

