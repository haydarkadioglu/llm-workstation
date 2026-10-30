import threading
import time
import uuid
from typing import Dict

TIMEOUT = 300


class Approvals:
    """Pauses a tool call until the person in the chat allows or declines it."""

    def __init__(self, tools=()):
        self.tools = set(tools)
        self.pending: Dict[str, dict] = {}

    def open(self) -> str:
        ticket = uuid.uuid4().hex
        self.pending[ticket] = {"event": threading.Event(), "allowed": False}
        return ticket

    def answer(self, ticket: str, allowed: bool) -> bool:
        entry = self.pending.get(ticket)
        if not entry:
            return False
        entry["allowed"] = allowed
        entry["event"].set()
        return True

    def wait(self, ticket: str, cancel: threading.Event) -> bool:
        entry = self.pending[ticket]
        deadline = time.time() + TIMEOUT
        try:
            while time.time() < deadline and not cancel.is_set():
                if entry["event"].wait(0.2):
                    return entry["allowed"]
            return False
        finally:
            self.pending.pop(ticket, None)
