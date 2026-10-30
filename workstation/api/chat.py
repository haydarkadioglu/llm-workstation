import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from ..agent.builtin_tools import WEB_SEARCH, web_search
from ..agent.loop import DEFAULT_MAX_STEPS, run_agent
from ..config import DATA_DIR, read_json, write_json
from ..engine.base import GenerationParams
from .streaming import iterate_in_thread, sse

router = APIRouter()
CHATS_DIR = DATA_DIR / "chats"


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = ""


class ChatPayload(BaseModel):
    messages: List[ChatMessage]
    system_prompt: Optional[str] = None
    agent_mode: bool = False
    max_steps: int = DEFAULT_MAX_STEPS
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.05
    repeat_penalty: float = 1.1
    max_tokens: int = 1024
    seed: Optional[int] = None
    stop: List[str] = []


def _tool_runner(registry):
    def run(name: str, arguments: dict) -> dict:
        if name == WEB_SEARCH["function"]["name"]:
            return web_search(arguments.get("query", ""))
        return registry.call(name, arguments)

    return run


@router.post("/api/chat")
async def chat(payload: ChatPayload, request: Request):
    manager = request.app.state.manager
    registry = request.app.state.registry
    if not manager.backend:
        raise HTTPException(status_code=503, detail="No chat model is loaded.")

    messages: List[Dict[str, Any]] = []
    if payload.system_prompt:
        messages.append({"role": "system", "content": payload.system_prompt})
    messages.extend(m.model_dump() for m in payload.messages)

    tools = [WEB_SEARCH] + registry.openai_tools() if payload.agent_mode else []
    params = GenerationParams(
        temperature=payload.temperature,
        top_p=payload.top_p,
        top_k=payload.top_k,
        min_p=payload.min_p,
        repeat_penalty=payload.repeat_penalty,
        max_tokens=payload.max_tokens,
        seed=payload.seed,
        stop=payload.stop,
    )
    cancel = threading.Event()

    async def events():
        try:
            async for event in iterate_in_thread(
                lambda: run_agent(manager, messages, tools, _tool_runner(registry), params, cancel, payload.max_steps),
                cancel,
            ):
                yield sse(event)
        except Exception as err:
            yield sse({"type": "error", "message": str(err)})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _chat_path(chat_id: str):
    if not chat_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid chat id.")
    return CHATS_DIR / f"{chat_id}.json"


@router.get("/api/chats")
def list_chats():
    chats = [read_json(path, None) for path in CHATS_DIR.glob("*.json")]
    summaries = [{k: c.get(k) for k in ("id", "title", "updated")} for c in chats if c]
    return sorted(summaries, key=lambda c: c.get("updated") or 0, reverse=True)


@router.get("/api/chats/{chat_id}")
def get_chat(chat_id: str):
    chat_data = read_json(_chat_path(chat_id), None)
    if chat_data is None:
        raise HTTPException(status_code=404, detail="Chat not found.")
    return chat_data


@router.put("/api/chats/{chat_id}")
def save_chat(chat_id: str, chat_data: Dict[str, Any]):
    write_json(_chat_path(chat_id), {**chat_data, "id": chat_id})
    return {"status": "ok"}


@router.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    _chat_path(chat_id).unlink(missing_ok=True)
    return {"status": "ok"}
