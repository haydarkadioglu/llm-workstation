import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from jinja2.sandbox import ImmutableSandboxedEnvironment

from .base import Message

CHATML_TEMPLATE = (
    "{% for message in messages %}"
    "{{ '<|im_start|>' + message['role'] + '\\n' + message['content'] + '<|im_end|>\\n' }}"
    "{% endfor %}"
    "{% if add_generation_prompt %}{{ '<|im_start|>assistant\\n' }}{% endif %}"
)

_MESSAGE_KEYS = ("role", "content", "name", "tool_calls", "tool_call_id")


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if p.get("type") == "text")
    return "" if content is None else str(content)


def _parse_arguments(arguments: Any) -> Any:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except ValueError:
            return {}
    return arguments


def normalize_messages(messages: List[Message], keep_images: bool = False) -> List[Message]:
    """Reduce messages to what chat templates understand.

    Templates serialise tool arguments themselves, so they must be dicts, and they
    index message['content'] directly, so it must not be None.
    """
    out = []
    for msg in messages:
        clean = {k: msg[k] for k in _MESSAGE_KEYS if msg.get(k) is not None}
        content = msg.get("content")
        clean["content"] = content if keep_images and isinstance(content, list) else text_of(content)
        if msg.get("tool_calls"):
            clean["tool_calls"] = [
                {**call, "function": {**call["function"], "arguments": _parse_arguments(call["function"].get("arguments"))}}
                for call in msg["tool_calls"]
            ]
        out.append(clean)
    return out


def merge_system_into_user(messages: List[Message]) -> List[Message]:
    """For templates that reject the system role (older Gemma, Mistral variants)."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [dict(m) for m in messages if m["role"] != "system"]
    if system:
        for msg in rest:
            if msg["role"] == "user":
                msg["content"] = f"{system}\n\n{msg['content']}"
                break
    return rest


def supports_tools(template: Optional[str]) -> bool:
    return bool(template) and "tools" in template


class ChatRenderer:
    def __init__(self, templates: Dict[str, str], bos_token: str = "", eos_token: str = ""):
        self.templates = templates
        self.bos_token = bos_token
        self.eos_token = eos_token
        env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.loopcontrols"])
        env.filters["tojson"] = lambda value, indent=None, ensure_ascii=False: json.dumps(
            value, indent=indent, ensure_ascii=ensure_ascii
        )
        env.globals["raise_exception"] = self._raise
        env.globals["strftime_now"] = lambda fmt: datetime.now().strftime(fmt)
        self.env = env

    @staticmethod
    def _raise(message: str):
        raise ValueError(message)

    def template_for(self, tools: Optional[List[dict]]) -> str:
        if tools and "tool_use" in self.templates:
            return self.templates["tool_use"]
        return self.templates.get("default") or next(iter(self.templates.values()), CHATML_TEMPLATE)

    def render(self, messages: List[Message], tools: Optional[List[dict]] = None) -> str:
        template = self.env.from_string(self.template_for(tools))
        args = dict(
            tools=tools or None,
            bos_token=self.bos_token,
            eos_token=self.eos_token,
            add_generation_prompt=True,
        )
        try:
            return template.render(messages=messages, **args)
        except ValueError:
            return template.render(messages=merge_system_into_user(messages), **args)
