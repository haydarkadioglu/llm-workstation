import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import DATA_DIR, read_json, write_json

SKILLS_DIR = DATA_DIR / "skills"
MAX_NAME = 60
HEADER_LINE = re.compile(r"\s*(name|description)\s*:", re.IGNORECASE)


@dataclass
class Skill:
    name: str
    description: str
    instructions: str

    def to_markdown(self) -> str:
        return f"---\nname: {self.name}\ndescription: {self.description}\n---\n\n{self.instructions.strip()}\n"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:MAX_NAME].strip("-") or "skill"


def _split_header(lines: List[str]) -> Tuple[List[str], List[str]]:
    """Separate front matter from the body. Models often forget the closing '---'."""
    if not lines or lines[0].strip() != "---":
        return [], lines
    rest = lines[1:]
    for index, line in enumerate(rest):
        if line.strip() == "---":
            return rest[:index], rest[index + 1 :]
    header = [line for line in rest if HEADER_LINE.match(line)]
    return header, [line for line in rest if not HEADER_LINE.match(line)]


def parse_skill(text: str, fallback_name: str = "skill") -> Skill:
    """Read a skill file. Plain markdown without a header is accepted as instructions."""
    lines = text.lstrip("﻿").strip().splitlines()
    header, rest = _split_header(lines)
    fields = {}
    for line in header:
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip().lower()] = value.strip().strip("\"'")
    body = "\n".join(rest).strip()
    if not body:
        raise ValueError("The skill has no instructions.")
    first_line = next((l.strip("# ").strip() for l in body.splitlines() if l.strip()), "")
    return Skill(slugify(fields.get("name") or fallback_name), fields.get("description") or first_line[:120], body)


class SkillStore:
    def __init__(self, root: Path = None):
        self.root = Path(root) if root else SKILLS_DIR
        self.enabled_file = self.root / "enabled.json"

    def _path(self, name: str) -> Path:
        if slugify(name) != name:
            raise ValueError(f"Invalid skill name '{name}'.")
        return self.root / f"{name}.md"

    def _enabled(self) -> List[str]:
        return read_json(self.enabled_file, [])

    def all(self) -> List[Skill]:
        skills = []
        for path in sorted(self.root.glob("*.md")):
            try:
                skills.append(parse_skill(path.read_text(encoding="utf-8"), path.stem))
            except ValueError as err:
                print(f"[Skills] Skipping {path.name}: {err}")
        return skills

    def describe_all(self) -> List[dict]:
        enabled = self._enabled()
        return [
            {"name": s.name, "description": s.description, "size": len(s.instructions), "enabled": s.name in enabled}
            for s in self.all()
        ]

    def get(self, name: str) -> Optional[Skill]:
        path = self._path(name)
        return parse_skill(path.read_text(encoding="utf-8"), name) if path.is_file() else None

    def save(self, skill: Skill, enabled: bool = True) -> Skill:
        skill.name = slugify(skill.name)
        path = self._path(skill.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill.to_markdown(), encoding="utf-8")
        self.set_enabled(skill.name, enabled)
        return skill

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.is_file():
            return False
        path.unlink()
        self.set_enabled(name, False)
        return True

    def set_enabled(self, name: str, enabled: bool) -> None:
        names = set(self._enabled())
        names.add(name) if enabled else names.discard(name)
        write_json(self.enabled_file, sorted(names))

    def import_text(self, text: str, filename: str = "skill.md") -> Skill:
        return self.save(parse_skill(text, Path(filename).stem))

    def import_zip(self, data: bytes) -> List[Skill]:
        imported = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.namelist():
                if entry.lower().endswith(".md"):
                    text = archive.read(entry).decode("utf-8")
                    imported.append(self.import_text(text, Path(entry).name))
        if not imported:
            raise ValueError("The archive has no .md skills.")
        return imported

    def export_zip(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for skill in self.all():
                archive.writestr(f"{skill.name}.md", skill.to_markdown())
        return buffer.getvalue()

    def _full_prompt(self) -> str:
        enabled = self._enabled()
        parts = [f"## Skill: {s.name}\n{s.instructions}" for s in self.all() if s.name in enabled]
        if not parts:
            return ""
        return "# Skills\nFollow these instructions whenever they apply.\n\n" + "\n\n".join(parts)

    def needs_lookup(self, max_chars: int) -> bool:
        return len(self._full_prompt()) > max_chars

    def prompt(self, max_chars: int) -> str:
        """Enabled skills in full, or only their list when they would crowd the context."""
        text = self._full_prompt()
        if len(text) <= max_chars:
            return text
        return "When a skill you know applies, call use_skill to read it first."
