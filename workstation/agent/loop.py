import json
import threading
from dataclasses import replace
from typing import Callable, Iterator, List, Optional

from ..engine.base import GenerationParams, Usage
from ..engine.manager import ModelManager
from .context import clip_tool_result, fit_context
from .tool_format import StreamParser, ToolCall, legacy_messages, with_tool_guidance

DEFAULT_MAX_STEPS = 10
TOOL_TEMPERATURE = 0.2
# Some models carry on and invent the tool's answer themselves.
FAKE_RESULT_MARKERS = ["<tool_response>"]

ToolRunner = Callable[[str, dict], dict]


def _tool_params(params: GenerationParams) -> GenerationParams:
    """Sampling that keeps calls reliable.

    Measured with Qwen 2.5 1.5B: 3/10 runs called the tool at temperature 0.7, 9/10 at 0.2.
    A repeat penalty also works against JSON, which is full of repeated punctuation.
    """
    return replace(
        params,
        temperature=min(params.temperature, TOOL_TEMPERATURE),
        repeat_penalty=1.0,
        stop=params.stop + FAKE_RESULT_MARKERS,
    )


def _assistant_message(text: str, thinking: str, calls: List[ToolCall]) -> dict:
    message = {"role": "assistant", "content": text}
    if thinking:
        message["thinking"] = thinking
    if calls:
        message["tool_calls"] = [c.to_message() for c in calls]
    return message


def _run_call(call: ToolCall, known: set, run_tool: ToolRunner) -> dict:
    if call.error:
        return {"is_error": True, "text": call.error}
    if call.name not in known:
        return {"is_error": True, "text": f"Unknown tool '{call.name}'. Available tools: {', '.join(sorted(known))}"}
    try:
        return run_tool(call.name, call.arguments)
    except Exception as err:
        return {"is_error": True, "text": f"Tool failed: {err}"}


def _generate_step(
    manager: ModelManager,
    messages: List[dict],
    tools: Optional[List[dict]],
    params: GenerationParams,
    cancel: threading.Event,
    parser: StreamParser,
    collected: dict,
) -> Iterator[dict]:
    for item in manager.stream(messages, tools, params, cancel):
        if isinstance(item, Usage):
            collected["usage"] = item
            continue
        for kind, text in parser.feed(item):
            collected[kind] += text
            yield {"type": "token" if kind == "text" else "thinking", "text": text}
    for kind, text in parser.finish():
        collected[kind] += text
        yield {"type": "token" if kind == "text" else "thinking", "text": text}


def run_agent(
    manager: ModelManager,
    messages: List[dict],
    tools: List[dict],
    run_tool: ToolRunner,
    params: GenerationParams,
    cancel: threading.Event,
    max_steps: int = DEFAULT_MAX_STEPS,
    prepare: Callable[[List[dict]], List[dict]] = lambda messages: messages,
) -> Iterator[dict]:
    """Generate, execute any tool calls and repeat until the model answers.

    `prepare` rewrites the messages before each step without touching the history.
    """
    history = list(messages)
    capabilities = manager.backend.capabilities
    native = capabilities.tools or not tools
    known = {t["function"]["name"] for t in tools}
    seen: dict = {}
    usage = Usage()

    for step in range(max_steps + 1):
        # Last round has no tools, which forces a written answer out of the model.
        active = tools if step < max_steps else None
        prompt_messages = prepare(history)
        if active and native:
            prompt_messages = with_tool_guidance(prompt_messages)
        elif active:
            prompt_messages = legacy_messages(prompt_messages, active)
        template_tools = active if native else None

        fitted, used, dropped = fit_context(
            prompt_messages, template_tools, manager.count_tokens, capabilities.context_length, params.max_tokens
        )
        yield {"type": "context", "used": used, "limit": capabilities.context_length, "dropped": dropped}

        parser = StreamParser(active)
        collected = {"text": "", "thinking": ""}
        step_params = _tool_params(params) if active else params
        yield from _generate_step(manager, fitted, template_tools, step_params, cancel, parser, collected)

        usage = collected.get("usage", usage)
        calls = parser.calls
        calls.extend(ToolCall(f"call_invalid_{i}", "invalid_tool_call", {}, error=e) for i, e in enumerate(parser.errors))
        # Models batch dependent calls and invent the arguments they cannot know yet
        # (a price lookup for a made-up SKU), so only the first runs. The result reminds it of
        # the others, since it tends to announce the next step and stop otherwise.
        postponed = sorted({c.name for c in calls[1:]})
        calls = calls[:1]
        # A rescued call sits inside prose that would read as a second call on the next turn.
        text = "" if parser.rescued else collected["text"]
        assistant = _assistant_message(text, collected["thinking"], calls)
        history.append(assistant)
        yield {"type": "message", "message": assistant}

        if cancel.is_set() or not calls:
            break

        repeated = False
        for call in calls:
            signature = (call.name, json.dumps(call.arguments, sort_keys=True))
            yield {"type": "tool_call", "id": call.id, "name": call.name, "arguments": call.arguments}
            if signature in seen:
                repeated = True
                result = {"is_error": False, "text": f"{seen[signature]}\n(Same call as before, answer with this result.)"}
            else:
                result = _run_call(call, known, run_tool)
                if not result["is_error"]:  # a failed call may be worth one more try
                    seen[signature] = result["text"]
            text = clip_tool_result(result["text"], capabilities.context_length)
            yield {"type": "tool_result", "id": call.id, "name": call.name, "is_error": result["is_error"], "text": text}
            content = text
            if postponed:
                content += f"\n\nOnly one call runs at a time. Now call {', '.join(postponed)} using this result."
            tool_message = {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": content,
                "is_error": result["is_error"],
            }
            history.append(tool_message)
            yield {"type": "message", "message": tool_message}
            if cancel.is_set():
                break
        if cancel.is_set():
            break
        if repeated:
            tools = []

    yield {
        "type": "done",
        "finish_reason": "cancelled" if cancel.is_set() else usage.finish_reason,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "tokens_per_sec": usage.tokens_per_sec,
    }
