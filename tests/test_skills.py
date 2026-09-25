from pathlib import Path

import pytest

from app.skills.service import SkillService


def write_skill(root: Path, name: str, body: str) -> None:
    path = root / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(body, encoding="utf-8")


def test_workspace_precedence_and_metadata(tmp_path):
    user = tmp_path / "user"
    workspace = tmp_path / "workspace"
    write_skill(user, "inspect", "---\ndescription: user\nversion: 1\n---\nuser instructions")
    write_skill(workspace / ".rexroad" / "skills", "inspect", "---\ndescription: local\nallowed_tools:\n  - filesystem.read\narguments:\n  - name: file\n    required: true\n---\nRead {{file}}")
    skills, errors = SkillService(user).discover(workspace)
    assert not errors
    assert skills[0].description == "local"
    assert skills[0].allowed_tools == ["filesystem.read"]
    assert SkillService.render(skills[0], {"file": "app/main.py"}) == "Read app/main.py"


def test_malformed_skill_isolated_and_strict_fields(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "good", "---\ndescription: good\n---\nWorks")
    write_skill(root, "bad", "---\ndescription: bad\nunknown: rejected\n---\nNo")
    skills, errors = SkillService(root).discover()
    assert [skill.name for skill in skills] == ["good"]
    assert errors[0].name == "bad"


def test_arguments_are_bounded_and_validated(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "inspect", "---\ndescription: inspect\narguments:\n  - name: file\n    required: true\n---\nRead {{file}}")
    skill = SkillService(root).get("inspect")
    assert skill is not None
    with pytest.raises(ValueError, match="missing required"):
        SkillService.render(skill, {})
    with pytest.raises(ValueError, match="unknown"):
        SkillService.render(skill, {"other": "x"})
    with pytest.raises(ValueError, match="invalid value"):
        SkillService.render(skill, {"file": "a\nb"})


def test_explicit_invocation_only():
    assert SkillService.invocation("hello") is None
    assert SkillService.invocation("/skill inspect file=main.py") == ("inspect", {"file": "main.py"})
