import shlex
import uuid
from typing import Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..mcp.client import ServerConfig

router = APIRouter(prefix="/api/mcp")


class ServerPayload(BaseModel):
    id: Optional[str] = None
    transport: str = "auto"
    url: Optional[str] = None
    headers: Dict[str, str] = {}
    command: Optional[str] = None
    args: Union[str, List[str]] = []
    env: Dict[str, str] = {}
    timeout: float = 120


class EnabledPayload(BaseModel):
    enabled: bool


class ToolCallPayload(BaseModel):
    arguments: dict = {}


def _registry(request: Request):
    return request.app.state.registry


def _guard(action):
    try:
        return action()
    except KeyError:
        raise HTTPException(status_code=404, detail="MCP server not found.")


@router.get("/servers")
def list_servers(request: Request):
    return _registry(request).describe_all()


@router.post("/servers")
def add_server(payload: ServerPayload, request: Request):
    if payload.transport == "stdio" and not payload.command:
        raise HTTPException(status_code=400, detail="A stdio server needs a command.")
    if payload.transport != "stdio" and not payload.url:
        raise HTTPException(status_code=400, detail="This transport needs a URL.")
    args = shlex.split(payload.args) if isinstance(payload.args, str) else payload.args
    data = payload.model_dump(exclude={"id", "args"})
    config = ServerConfig(id=payload.id or uuid.uuid4().hex[:8], args=args, **data)
    return _registry(request).add(config)


@router.delete("/servers/{server_id}")
def remove_server(server_id: str, request: Request):
    if not _registry(request).remove(server_id):
        raise HTTPException(status_code=404, detail="MCP server not found.")
    return {"status": "ok"}


@router.post("/servers/{server_id}/reconnect")
def reconnect_server(server_id: str, request: Request):
    return _guard(lambda: _registry(request).reconnect(server_id))


@router.post("/servers/{server_id}/enabled")
def set_server_enabled(server_id: str, payload: EnabledPayload, request: Request):
    return _guard(lambda: _registry(request).set_enabled(server_id, payload.enabled))


@router.post("/servers/{server_id}/tools/{tool}/enabled")
def set_tool_enabled(server_id: str, tool: str, payload: EnabledPayload, request: Request):
    return _guard(lambda: _registry(request).set_tool_enabled(server_id, tool, payload.enabled))


@router.post("/servers/{server_id}/tools/{tool}/call")
def call_tool(server_id: str, tool: str, payload: ToolCallPayload, request: Request):
    return _guard(lambda: _registry(request).call_direct(server_id, tool, payload.arguments))
