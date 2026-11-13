from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from matrix_ai.integrations.skills import SkillsError, SkillsManager


def make_manager(tmp_path: Path) -> SkillsManager:
    return SkillsManager(SimpleNamespace(home=tmp_path))


def add_skill(manager: SkillsManager, directory: str, name: str, description: str, body: str = "Use the approved workflow.") -> Path:
    folder = manager.skills_dir / directory
    folder.mkdir(parents=True)
    skill = folder / "SKILL.md"
    skill.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill


def test_lists_only_valid_installed_skills(tmp_path):
    manager = make_manager(tmp_path)
    add_skill(manager, "frontend-design", "frontend-design", "Build polished frontend interfaces.")
    (manager.skills_dir / "not-a-skill").mkdir()
    assert [item["name"] for item in manager.list_installed()] == ["frontend-design"]


def test_routes_and_fences_relevant_skill_as_untrusted(tmp_path):
    manager = make_manager(tmp_path)
    add_skill(manager, "frontend-design", "frontend-design", "Design responsive frontend interfaces.")
    selected = manager._select_installed("design a responsive frontend interface")
    context = manager._context(selected)
    assert selected[0]["name"] == "frontend-design"
    assert "[UNTRUSTED SKILL INSTRUCTIONS:" in context
    assert "[END UNTRUSTED SKILL INSTRUCTIONS]" in context


def test_ignores_symlinked_skill_directories(tmp_path):
    manager = make_manager(tmp_path)
    external = tmp_path / "outside"
    external.mkdir()
    (external / "SKILL.md").write_text(
        "---\nname: outside\ndescription: external\n---\nbody\n", encoding="utf-8"
    )
    link = manager.skills_dir / "outside"
    try:
        link.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    assert manager.list_installed() == []


def test_install_rejects_untrusted_repo_and_skill_identifiers(tmp_path):
    manager = make_manager(tmp_path)
    with pytest.raises(SkillsError):
        manager.install("../../attacker/repo", "bad-skill")
    with pytest.raises(SkillsError):
        manager.install("owner/repo", "../../bad")


def test_install_uses_pinned_cli_and_never_shell_executes(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    monkeypatch.setattr(SkillsManager, "_node_ready", staticmethod(lambda: True))
    monkeypatch.setattr("matrix_ai.integrations.skills.shutil.which", lambda _name: "npx")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        folder = Path(kwargs["cwd"]) / ".agents" / "skills" / "frontend-design"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "---\nname: frontend-design\ndescription: frontend design\n---\nbody\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr("matrix_ai.integrations.skills.subprocess.run", fake_run)
    result = manager.install("vercel-labs/agent-skills", "frontend-design")
    command, kwargs = calls[0]
    assert result["status"] == "installed"
    assert "skills@1.8.1" in command
    assert "--agent" in command and "universal" in command
    assert "--copy" in command and "--yes" in command
    assert kwargs["shell"] is False
    assert "MATRIX_OWNER_TOKEN" not in kwargs["env"]
    assert result["code_executed"] is False


@pytest.mark.asyncio
async def test_prepare_for_task_uses_existing_skills_without_network_when_discovery_disabled(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    add_skill(manager, "python-testing", "python-testing", "Python pytest testing.")
    manager.auto_discover = False

    async def unexpected_search(*_args, **_kwargs):
        raise AssertionError("network search should be disabled")

    monkeypatch.setattr(manager, "search", unexpected_search)
    selected, context = await manager.prepare_for_task("write Python pytest tests")
    assert selected[0]["name"] == "python-testing"
    assert "UNTRUSTED SKILL INSTRUCTIONS" in context
