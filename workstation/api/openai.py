import threading
import time
import uuid
from typing import Any, List, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from ..agent.context import fit_context
from ..agent.tool_format import StreamParser, ToolCall
from ..engine.base import GenerationParams, Usage
from ..engine.manager import ModelManager
from .chat import ChatMessage
from .streaming import iterate_in_thread, sse

router = APIRouter()


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: Optional[str] = None
    messages: List[ChatMessage]
    tools: Optional[List[dict]] = None
    tool_choice: Any = None
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.05
    repeat_penalty: float = 1.1
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    seed: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    stream: bool = False
    stream_options: Optional[dict] = None


def _params(req: CompletionRequest) -> GenerationParams:
    stop = [req.stop] if isinstance(req.stop, str) else req.stop or []
    return GenerationParams(
        temperature=req.temperature,
        top_p=req.top_p,
        top_k=req.top_k,
        min_p=req.min_p,
        repeat_penalty=req.repeat_penalty,
        max_tokens=req.max_completion_tokens or req.max_tokens or 2048,
        seed=req.seed,
        stop=stop,
    )


def _generate(manager: ModelManager, req: CompletionRequest, cancel: threading.Event):
    """Yields ('text', str), ('call', ToolCall), then ('usage', Usage)."""
    tools = None if req.tool_choice == "none" else req.tools
    params = _params(req)
    messages = [m.model_dump() for m in req.messages]
    template_tools = tools if manager.backend.capabilities.tools else None
    fitted, _, _ = fit_context(
        messages, template_tools, manager.count_tokens, manager.backend.capabilities.context_length, params.max_tokens
    )
    parser = StreamParser(tools)
    for item in manager.stream(fitted, template_tools, params, cancel):
        if isinstance(item, Usage):
            usage = item
            continue
        for kind, text in parser.feed(item):
            if kind == "text":
                yield "text", text
    for kind, text in parser.finish():
        if kind == "text":
            yield "text", text
    for call in parser.calls:
        yield "call", call
    yield "usage", usage


def _usage_dict(usage: Usage) -> dict:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.prompt_tokens + usage.completion_tokens,
    }


def _finish_reason(usage: Usage, has_calls: bool) -> str:
    if has_calls:
        return "tool_calls"
    return "length" if usage.finish_reason == "length" else "stop"


async def _complete(manager: ModelManager, req: CompletionRequest, base: dict) -> dict:
    cancel = threading.Event()
    text = ""
    calls: List[ToolCall] = []
    usage = Usage()
    async for kind, value in iterate_in_thread(lambda: _generate(manager, req, cancel), cancel):
        if kind == "text":
            text += value
        elif kind == "call":
            calls.append(value)
        else:
            usage = value
    message: dict = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [c.to_message() for c in calls]
    return {
        **base,
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": _finish_reason(usage, bool(calls))}],
        "usage": _usage_dict(usage),
    }


async def _stream(manager: ModelManager, req: CompletionRequest, base: dict):
    def packet(delta: dict, finish: Optional[str] = None) -> str:
        return sse({
            **base,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        })

    cancel = threading.Event()
    calls = 0
    usage = Usage()
    yield packet({"role": "assistant", "content": ""})
    try:
        async for kind, value in iterate_in_thread(lambda: _generate(manager, req, cancel), cancel):
            if kind == "text":
                yield packet({"content": value})
            elif kind == "call":
                yield packet({"tool_calls": [{"index": calls, **value.to_message()}]})
                calls += 1
            else:
                usage = value
    except Exception as err:
        yield sse({"error": {"message": str(err), "type": "server_error"}})
        yield "data: [DONE]\n\n"
        return
    yield packet({}, _finish_reason(usage, calls > 0))
    if (req.stream_options or {}).get("include_usage"):
        yield sse({**base, "object": "chat.completion.chunk", "choices": [], "usage": _usage_dict(usage)})
    yield "data: [DONE]\n\n"


@router.get("/v1/models")
def list_models(request: Request):
    manager = request.app.state.manager
    models = [manager.model_id] if manager.backend else []
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": 0, "owned_by": "llm-workstation"} for m in models],
    }


@router.post("/v1/chat/completions")
async def chat_completions(req: CompletionRequest, request: Request):
    manager = request.app.state.manager
    if not manager.backend:
        raise HTTPException(status_code=503, detail="No model loaded. Load a model first.")
    base = {"id": f"chatcmpl-{uuid.uuid4().hex}", "created": int(time.time()), "model": manager.model_id}
    if req.stream:
        return StreamingResponse(_stream(manager, req, base), media_type="text/event-stream")
    return await _complete(manager, req, base)
