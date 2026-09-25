from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SKILL_FILE_LIMIT = 64_000
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SkillArgument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    required: bool = False


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = Field(max_length=500)
    version: str = "1"
    instructions: str = Field(min_length=1, max_length=48_000)
    user_invocable: bool = True
    model_invocable: bool = True
    allowed_tools: list[str] = Field(default_factory=list, max_length=50)
    arguments: list[SkillArgument] = Field(default_factory=list, max_length=20)
    context_mode: str = Field(default="inline", pattern=r"^inline$")
    model_hint: str | None = Field(default=None, max_length=100)
    agent_hint: str | None = Field(default=None, max_length=100)
    effort_hint: str | None = Field(default=None, max_length=50)
    paths: list[str] | None = Field(default=None, max_length=50)
    source_path: str
    origin: str


class SkillLoadError(BaseModel):
    name: str
    reason: str = Field(max_length=300)


class SkillService:
    def __init__(self, user_root: Path | None = None) -> None:
        self.user_root = (user_root or (Path.home() / ".rexroad" / "skills")).resolve()

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
        lines = text.splitlines()
        if len(lines) < 3 or lines[0].strip() != "---":
            return {}, text
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration as exc:
            raise ValueError("unterminated frontmatter") from exc
        result: dict[str, Any] = {}
        i = 1
        while i < end:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            if line.startswith(" ") or ":" not in line:
                raise ValueError("invalid frontmatter field")
            key, raw = line.split(":", 1)
            key = key.strip()
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            raw = raw.strip()
            if raw == "":
                if key == "arguments" and i + 1 < end and lines[i + 1].startswith("  - "):
                    items: list[dict[str, Any]] = []
                    i += 1
                    while i < end and lines[i].startswith("  - "):
                        first = lines[i][4:]
                        if ":" not in first:
                            raise ValueError("invalid argument definition")
                        arg_key, arg_value = first.split(":", 1)
                        item: dict[str, Any] = {arg_key.strip(): arg_value.strip()}
                        i += 1
                        while i < end and lines[i].startswith("    "):
                            nested = lines[i].strip()
                            if ":" not in nested:
                                raise ValueError("invalid argument field")
                            nested_key, nested_value = nested.split(":", 1)
                            value = nested_value.strip()
                            item[nested_key.strip()] = value.lower() == "true" if value.lower() in {"true", "false"} else value
                            i += 1
                        items.append(item)
                    result[key] = items
                    continue
                values: list[Any] = []
                i += 1
                while i < end and lines[i].startswith("  - "):
                    values.append(lines[i][4:].strip())
                    i += 1
                result[key] = values
                continue
            if raw.lower() in {"true", "false"}:
                result[key] = raw.lower() == "true"
            elif raw.isdigit():
                result[key] = raw
            elif raw.startswith("[") and raw.endswith("]"):
                result[key] = [x.strip() for x in raw[1:-1].split(",") if x.strip()]
            else:
                result[key] = raw.strip('"\'')
            i += 1
        return result, "\n".join(lines[end + 1 :])

    def _load(self, directory: Path, origin: str) -> tuple[list[SkillDefinition], list[SkillLoadError]]:
        skills: list[SkillDefinition] = []
        errors: list[SkillLoadError] = []
        if not directory.exists() or not directory.is_dir():
            return skills, errors
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if not entry.is_dir() or not SKILL_NAME.fullmatch(entry.name):
                continue
            path = (entry / "SKILL.md").resolve()
            try:
                if path.parent != entry.resolve() or path.stat().st_size > SKILL_FILE_LIMIT:
                    raise ValueError("invalid path or file size")
                text = path.read_text(encoding="utf-8")
                metadata, body = self._parse_frontmatter(text)
                metadata.update(name=entry.name, instructions=body, source_path=str(path), origin=origin)
                skill = SkillDefinition.model_validate(metadata)
                skills.append(skill)
            except (OSError, UnicodeError, ValueError, ValidationError) as exc:
                errors.append(SkillLoadError(name=entry.name, reason=str(exc)[:300]))
        return skills, errors

    def discover(self, workspace_root: Path | None = None) -> tuple[list[SkillDefinition], list[SkillLoadError]]:
        user, errors = self._load(self.user_root, "user")
        local: list[SkillDefinition] = []
        if workspace_root is not None:
            local, local_errors = self._load((workspace_root / ".rexroad" / "skills").resolve(), "workspace")
            errors.extend(local_errors)
        merged = {skill.name: skill for skill in user}
        merged.update({skill.name: skill for skill in local})
        return sorted(merged.values(), key=lambda skill: skill.name), errors

    def get(self, name: str, workspace_root: Path | None = None) -> SkillDefinition | None:
        return next((skill for skill in self.discover(workspace_root)[0] if skill.name == name), None)

    @staticmethod
    def render(skill: SkillDefinition, raw_args: dict[str, str]) -> str:
        expected = {arg.name: arg for arg in skill.arguments}
        unknown = set(raw_args) - set(expected)
        if unknown:
            raise ValueError(f"unknown skill arguments: {', '.join(sorted(unknown))}")
        missing = [name for name, arg in expected.items() if arg.required and name not in raw_args]
        if missing:
            raise ValueError(f"missing required skill arguments: {', '.join(missing)}")
        rendered = skill.instructions
        for name, value in raw_args.items():
            if len(value) > 1000 or "\n" in value or "\r" in value:
                raise ValueError(f"invalid value for argument: {name}")
            rendered = rendered.replace("{{" + name + "}}", value)
        return rendered[:48_000]

    @staticmethod
    def invocation(message: str) -> tuple[str, dict[str, str]] | None:
        if not message.startswith("/skill "):
            return None
        parts = message[7:].strip().split()
        if not parts:
            raise ValueError("skill name is required")
        args: dict[str, str] = {}
        for token in parts[1:]:
            if "=" not in token:
                raise ValueError("skill arguments must use name=value")
            key, value = token.split("=", 1)
            args[key] = value
        return parts[0], args
