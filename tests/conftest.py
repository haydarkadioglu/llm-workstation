import threading
from typing import List

import pytest

from workstation.engine.base import Capabilities, ChatBackend, Usage
from workstation.engine.manager import ModelManager


class FakeBackend(ChatBackend):
    """Replays scripted responses and records every prompt it was given."""

    def __init__(self, script: List[str], tools: bool = True, context_length: int = 8192):
        self.script = list(script)
        self.capabilities = Capabilities(tools=tools, context_length=context_length)
        self.prompts = []
        self.tools_seen = []

    def count_tokens(self, messages, tools=None):
        return sum(len(str(m.get("content"))) // 4 + 4 for m in messages)

    def stream(self, messages, tools, params, cancel: threading.Event):
        self.prompts.append(messages)
        self.tools_seen.append(tools)
        text = self.script.pop(0)
        for start in range(0, len(text), 5):
            if cancel.is_set():
                yield Usage(1, 1, "cancelled")
                return
            yield text[start : start + 5]
        yield Usage(10, 5, "stop", 50.0)


@pytest.fixture
def make_manager():
    def build(script, **kwargs):
        manager = ModelManager()
        manager.backend = FakeBackend(script, **kwargs)
        manager.model_id = "fake/model"
        return manager

    return build
