import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx2
from mcp import Client, MCPError, StdioServerParameters, types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

logging.getLogger("httpx2").setLevel(logging.WARNING)

PING_INTERVAL = 30
MAX_BACKOFF = 60
SSE_READ_TIMEOUT = 24 * 3600
CONNECT_TIMEOUT = 30
CLOSE_TIMEOUT = 5


@dataclass
class ServerConfig:
    id: str
    transport: str = "auto"  # auto | streamable-http | sse | stdio
    url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    timeout: float = 120
    enabled: bool = True
    disabled_tools: List[str] = field(default_factory=list)


def root_cause(err: BaseException) -> str:
    while isinstance(err, BaseExceptionGroup):
        err = err.exceptions[0]
    return (str(err) or type(err).__name__).splitlines()[0]


def _transports(config: ServerConfig) -> List[tuple]:
    def streamable():
        http = httpx2.AsyncClient(
            headers=config.headers,
            timeout=httpx2.Timeout(CONNECT_TIMEOUT, read=config.timeout + 30),
            follow_redirects=True,
        )
        return streamable_http_client(config.url, http_client=http)

    def sse():
        return sse_client(config.url, headers=config.headers, timeout=CONNECT_TIMEOUT, sse_read_timeout=SSE_READ_TIMEOUT)

    if config.transport == "stdio":
        params = StdioServerParameters(command=config.command, args=config.args, env=config.env or None)
        return [("stdio", lambda: params)]
    if config.transport == "sse":
        return [("sse", sse)]
    if config.transport == "streamable-http":
        return [("streamable-http", streamable)]
    return [("streamable-http", streamable), ("sse", sse)]


def format_result(result: types.CallToolResult) -> dict:
    parts = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            parts.append(block.text)
        elif isinstance(block, types.ImageContent):
            parts.append(f"[image: {block.mime_type}, {len(block.data)} base64 chars]")
        elif isinstance(block, types.AudioContent):
            parts.append(f"[audio: {block.mime_type}]")
        elif isinstance(block, types.EmbeddedResource):
            parts.append(getattr(block.resource, "text", None) or f"[resource: {block.resource.uri}]")
        elif isinstance(block, types.ResourceLink):
            parts.append(f"[resource link: {block.uri}]")
    text = "\n".join(parts)
    if not text and result.structured_content is not None:
        text = json.dumps(result.structured_content, ensure_ascii=False)
    return {"is_error": bool(result.is_error), "text": text}


class McpConnection:
    """One remote server, kept alive by a task that reconnects with backoff."""

    def __init__(self, config: ServerConfig, loop: asyncio.AbstractEventLoop):
        self.config = config
        self.loop = loop
        self.status = "connecting"
        self.error: Optional[str] = None
        self.active_transport: Optional[str] = None
        self.tools: List[types.Tool] = []
        self.client: Optional[Client] = None
        self._ready = asyncio.Event()
        self._dirty = asyncio.Event()
        self._closing = False
        self._task =loop.create_task(self._run())

    async def _on_message(self, message: Any) -> None:
        root = getattr(message, "root", message)
        if isinstance(root, types.ToolListChangedNotification):
            self._dirty.set()

    async def _refresh_tools(self, client: Client) -> None:
        tools, cursor = [], None
        while True:
            page = await client.list_tools(cursor=cursor, cache_mode="refresh")
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
        self.tools = tools

    async def _serve(self, client: Client) -> None:
        while True:
            try:
                await asyncio.wait_for(self._dirty.wait(), PING_INTERVAL)
                self._dirty.clear()
                await self._refresh_tools(client)
            except asyncio.TimeoutError:
                try:
                    await asyncio.wait_for(client.send_ping(), CONNECT_TIMEOUT)
                except MCPError:
                    pass  # it answered, so it is alive; newer servers reject ping

    async def _attempt(self, name: str, factory) -> None:
        async with Client(factory(), message_handler=self._on_message, read_timeout_seconds=self.config.timeout) as client:
            await self._refresh_tools(client)
            self.client = client
            self.active_transport = name
            self.status = "connected"
            self.error = None
            self._ready.set()
            print(f"[MCP] {self.config.id}: connected over {name}, {len(self.tools)} tools")
            await self._serve(client)

    async def _run(self) -> None:
        backoff = 1
        options = _transports(self.config)
        index = 0
        while not self._closing:
            name, factory = options[index]
            was_connected = False
            try:
                await self._attempt(name, factory)
            except asyncio.CancelledError:
                raise
            except BaseException as err:
                was_connected = self.client is not None
                self.error = root_cause(err)
                print(f"[MCP] {self.config.id}: {name} failed: {self.error}")
            self.client = None
            self._ready.clear()
            if self._closing:
                return  # anyio can deliver the cancellation wrapped in an exception group
            if was_connected:
                backoff = 1
            else:
                index = (index + 1) % len(options)
                if index != 0:
                    continue  # try the next transport straight away
            self.status = "reconnecting"
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    async def wait_ready(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def call(self, name: str, arguments: dict) -> dict:
        if not await self.wait_ready(15):
            return {"is_error": True, "text": f"MCP server '{self.config.id}' is not connected: {self.error or 'still connecting'}"}
        try:
            result = await self.client.call_tool(name, arguments, read_timeout_seconds=self.config.timeout)
            return format_result(result)
        except Exception as err:
            return {"is_error": True, "text": f"Tool call failed: {root_cause(err)}"}

    async def close(self) -> None:
        self._closing = True
        self._task.cancel()
        await asyncio.wait({self._task}, timeout=CLOSE_TIMEOUT)


class McpRuntime:
    """Event loop thread hosting all connections, callable from sync code."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="mcp-runtime")
        self.thread.start()

    def run(self, coroutine, timeout: Optional[float] = None):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    def connect(self, config: ServerConfig) -> McpConnection:
        async def create():
            return McpConnection(config, self.loop)

        return self.run(create())
