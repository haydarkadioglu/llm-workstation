import json
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

THINK_TAGS = {"<think>": "</think>", "<thought>": "</thought>", "<|channel|>thought": "<channel|>"}
TOOL_TAGS = {"<tool_call>": "</tool_call>", "<|tool_call|>": "<|/tool_call|>"}
# These run to the end of the response, there is no closing marker.
TOOL_PREFIXES = ("[TOOL_CALLS]", "<|python_tag|>")
JSON_STARTS = ("{", "[", "```json")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    error: Optional[str] = None

    def to_message(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": json.dumps(self.arguments, ensure_ascii=False)},
        }


def _new_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


def _to_call(item: Any) -> Optional[ToolCall]:
    if not isinstance(item, dict):
        return None
    source = item.get("function") if isinstance(item.get("function"), dict) else item
    name = source.get("name") or source.get("tool")
    if not isinstance(name, str):
        return None
    arguments = source.get("arguments", source.get("parameters", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return ToolCall(_new_id(), name, {}, error=f"Arguments are not valid JSON: {arguments}")
    if not isinstance(arguments, dict):
        return ToolCall(_new_id(), name, {}, error="Arguments must be a JSON object.")
    return ToolCall(_new_id(), name, arguments)


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def parse_calls(text: str) -> Tuple[List[ToolCall], Optional[str]]:
    """Parse a tool call payload. Returns (calls, error when it is broken JSON)."""
    text = _strip_fence(text)
    decoder = json.JSONDecoder()
    items: List[Any] = []
    index = 0
    while index < len(text):
        if text[index] in " \n\r\t,;":
            index += 1
            continue
        try:
            value, index = decoder.raw_decode(text, index)
        except ValueError as err:
            return [], f"Invalid tool call JSON: {err}"
        items.extend(value if isinstance(value, list) else [value])
    calls = [call for call in map(_to_call, items) if call]
    return calls, None


class StreamParser:
    """Splits a model's token stream into text, thinking and tool calls.

    Markers may arrive split across tokens, so anything that could still become
    one stays buffered until it is ruled out.
    """

    def __init__(self, tools: Optional[List[dict]] = None):
        self.tool_names = {t["function"]["name"] for t in tools or []}
        self.detect_tools = bool(tools)
        self.buffer = ""
        self.mode = "text"  # text | thinking | tool | tool_tail | maybe_json
        self.closer = ""
        self.at_start = True
        self.calls: List[ToolCall] = []
        self.errors: List[str] = []

    def _openers(self) -> List[str]:
        openers = list(THINK_TAGS)
        if self.detect_tools:
            openers += list(TOOL_TAGS) + list(TOOL_PREFIXES)
        return openers

    def _collect(self, payload: str) -> None:
        calls, error = parse_calls(payload)
        if error:
            self.errors.append(error)
        self.calls.extend(calls)

    def _partial_tail(self, openers: List[str]) -> int:
        """Length of the longest buffer suffix that begins a marker."""
        best = 0
        for marker in openers:
            for size in range(min(len(marker) - 1, len(self.buffer)), best, -1):
                if self.buffer.endswith(marker[:size]):
                    best = size
                    break
        return best

    def _step_text(self, out: List[Tuple[str, str]]) -> bool:
        if self.at_start:
            stripped = self.buffer.lstrip()
            if not stripped:
                return False
            if self.detect_tools and not stripped.startswith(TOOL_PREFIXES):
                if any(p.startswith(stripped) for p in TOOL_PREFIXES + JSON_STARTS):
                    return False
                if stripped.startswith(JSON_STARTS):
                    self.mode = "maybe_json"
                    return False
            self.at_start = False

        openers = self._openers()
        hits = [(self.buffer.find(m), m) for m in openers if m in self.buffer]
        if hits:
            position, marker = min(hits)
            if position:
                out.append(("text", self.buffer[:position]))
            self.buffer = self.buffer[position + len(marker) :]
            if marker in THINK_TAGS:
                self.mode, self.closer = "thinking", THINK_TAGS[marker]
            elif marker in TOOL_TAGS:
                self.mode, self.closer = "tool", TOOL_TAGS[marker]
            else:
                self.mode = "tool_tail"
            return True

        tail = self._partial_tail(openers)
        cut = len(self.buffer) - tail
        if cut:
            out.append(("text", self.buffer[:cut]))
        self.buffer = self.buffer[cut:]
        return False

    def _step_closed(self, out: List[Tuple[str, str]]) -> bool:
        position = self.buffer.find(self.closer)
        if position == -1:
            if self.mode == "thinking":
                hold = next((n for n in range(len(self.closer) - 1, 0, -1) if self.buffer.endswith(self.closer[:n])), 0)
                cut = len(self.buffer) - hold
                if cut:
                    out.append(("thinking", self.buffer[:cut]))
                self.buffer = self.buffer[cut:]
            return False
        body = self.buffer[:position]
        self.buffer = self.buffer[position + len(self.closer) :]
        if self.mode == "thinking":
            if body:
                out.append(("thinking", body))
            self.at_start = True
        else:
            self._collect(body)
        self.mode = "text"
        return True

    def feed(self, text: str) -> List[Tuple[str, str]]:
        self.buffer += text
        out: List[Tuple[str, str]] = []
        progressed = True
        while progressed and self.buffer:
            if self.mode == "text":
                progressed = self._step_text(out)
            elif self.mode in ("thinking", "tool"):
                progressed = self._step_closed(out)
            else:
                progressed = False
        return out

    def finish(self) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        rest, self.buffer = self.buffer, ""
        if self.mode == "thinking":
            if rest:
                out.append(("thinking", rest))
        elif self.mode in ("tool", "tool_tail"):
            self._collect(rest)
        elif self.mode == "maybe_json":
            calls, _ = parse_calls(rest)
            known = [c for c in calls if c.name in self.tool_names]
            if known:
                self.calls.extend(known)
            elif rest:
                out.append(("text", rest))
        elif rest:
            out.append(("text", rest))
        return out


def legacy_prompt(tools: List[dict]) -> str:
    """For models whose chat template has no tool support."""
    specs = [t["function"] for t in tools]
    return (
        "You can call tools. To call one, reply with ONLY a JSON code block and nothing else:\n"
        '```json\n{"name": "tool_name", "arguments": {...}}\n```\n'
        "You will receive the result in the next message, and may then call another tool or answer. "
        "Do not call a tool when you can answer directly.\n"
        f"Available tools:\n{json.dumps(specs, ensure_ascii=False)}"
    )


def legacy_messages(messages: List[dict], tools: List[dict]) -> List[dict]:
    """Rewrite tool traffic as plain turns for templates that have no tool role."""
    out: List[dict] = []
    for msg in messages:
        if msg["role"] == "tool":
            label = msg.get("name", "tool")
            out.append({"role": "user", "content": f"[Result of {label}]\n{msg['content']}"})
        elif msg.get("tool_calls"):
            calls = [
                {"name": c["function"]["name"], "arguments": c["function"]["arguments"]}
                for c in msg["tool_calls"]
            ]
            text = "\n".join(f"```json\n{json.dumps(c, ensure_ascii=False)}\n```" for c in calls)
            out.append({"role": "assistant", "content": text})
        else:
            out.append(dict(msg))

    prompt = legacy_prompt(tools)
    if out and out[0]["role"] == "system":
        out[0]["content"] = f"{out[0]['content']}\n\n{prompt}"
    else:
        out.insert(0, {"role": "system", "content": prompt})
    return out
