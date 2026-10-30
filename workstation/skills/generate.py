import threading
from typing import Optional

from ..agent.tool_format import StreamParser
from ..engine.base import GenerationParams, Usage
from ..engine.manager import ModelManager
from .store import Skill, parse_skill

SYSTEM = """You write skills for an AI assistant. A skill is a set of instructions the assistant follows later.
Reply with only the skill file, in exactly this format:

---
name: short-kebab-case-name
description: One sentence saying what the skill does and when to use it.
---

# Title

Clear, numbered steps telling the assistant what to do, in the imperative.
Include the output format it should use, and any rules it must not break."""

DRAFT_PARAMS = dict(temperature=0.3, repeat_penalty=1.05, max_tokens=900)


def _request(description: str, current: Optional[str]) -> str:
    if current:
        return f"Here is the current skill:\n\n{current}\n\nChange it as follows, and output the whole skill again:\n{description}"
    return f"Write a skill for this request:\n{description}"


def _strip_outer_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text


def draft_skill(
    manager: ModelManager, description: str, current: Optional[str] = None, name: Optional[str] = None
) -> Skill:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": _request(description, current)},
    ]
    parser = StreamParser()
    pieces = []
    for item in manager.stream(messages, None, GenerationParams(**DRAFT_PARAMS), threading.Event()):
        if isinstance(item, Usage):
            continue
        pieces.extend(text for kind, text in parser.feed(item) if kind == "text")
    pieces.extend(text for kind, text in parser.finish() if kind == "text")
    skill = parse_skill(_strip_outer_fence("".join(pieces)), name or description[:40])
    return Skill(name, skill.description, skill.instructions) if name else skill
