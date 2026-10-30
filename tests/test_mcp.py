"""Talks to a real MCP server running in a separate process, as a remote one would."""
import socket
import subprocess
import sys
import textwrap
import time

import pytest

from workstation.mcp.client import ServerConfig
from workstation.mcp.registry import McpRegistry

SERVER = textwrap.dedent(
    """
    import asyncio, sys
    import uvicorn
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    port, transport, token = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    server = MCPServer("demo")

    @server.tool()
    def echo(text: str) -> str:
        '''Echo text back.'''
        return text

    @server.tool()
    async def slow(seconds: float) -> str:
        '''Wait, then answer.'''
        await asyncio.sleep(seconds)
        return "finished"

    @server.tool()
    def fails() -> str:
        '''Always raises.'''
        raise ValueError("boom")

    class Auth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if token != "-" and request.headers.get("authorization") != "Bearer " + token:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    build = server.sse_app if transport == "sse" else server.streamable_http_app
    app = build(transport_security=security)
    app.add_middleware(Auth)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")
    """
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_port(port: int) -> None:
    for _ in range(100):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"port {port} never opened")


@pytest.fixture
def spawn(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(SERVER)
    processes = []

    def start(port, transport="streamable-http", token="-"):
        process = subprocess.Popen([sys.executable, "-W", "ignore", str(script), str(port), transport, token])
        processes.append(process)
        wait_for_port(port)
        return process

    yield start
    for process in processes:
        process.kill()


@pytest.fixture
def registry(tmp_path):
    instance = McpRegistry(tmp_path / "mcp.json")
    yield instance
    instance.stop()


def wait_status(registry, server_id, wanted, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if registry.describe(server_id)["status"] == wanted:
            return True
        time.sleep(0.2)
    return False


def test_streamable_http_with_bearer_header(spawn, registry):
    port = free_port()
    spawn(port, token="s3cret")
    info = registry.add(ServerConfig(id="shop", url=f"http://127.0.0.1:{port}/mcp", headers={"Authorization": "Bearer s3cret"}))
    assert info["status"] == "connected" and info["active_transport"] == "streamable-http"
    assert info["headers"] == {"Authorization": "••••"}
    assert registry.call("shop__echo", {"text": "merhaba"}) == {"is_error": False, "text": "merhaba"}


def test_missing_token_reports_the_failure(spawn, registry):
    port = free_port()
    spawn(port, token="s3cret")
    info = registry.add(ServerConfig(id="shop", url=f"http://127.0.0.1:{port}/mcp"), wait=3)
    assert info["status"] != "connected"
    assert info["error"]
    assert registry.openai_tools() == []


def test_auto_falls_back_to_sse(spawn, registry):
    port = free_port()
    spawn(port, transport="sse")
    info = registry.add(ServerConfig(id="old", url=f"http://127.0.0.1:{port}/sse"))
    assert info["active_transport"] == "sse"
    assert registry.call("old__echo", {"text": "x"})["text"] == "x"


def test_tool_errors_are_flagged(spawn, registry):
    port = free_port()
    spawn(port)
    registry.add(ServerConfig(id="s", url=f"http://127.0.0.1:{port}/mcp"))
    assert registry.call("s__fails", {})["is_error"]


def test_tools_are_namespaced_and_can_be_disabled(spawn, registry):
    port = free_port()
    spawn(port)
    registry.add(ServerConfig(id="a", url=f"http://127.0.0.1:{port}/mcp"))
    registry.add(ServerConfig(id="b", url=f"http://127.0.0.1:{port}/mcp"))
    names = {t["function"]["name"] for t in registry.openai_tools()}
    assert {"a__echo", "b__echo"} <= names
    registry.set_tool_enabled("a", "echo", False)
    assert "a__echo" not in {t["function"]["name"] for t in registry.openai_tools()}


@pytest.mark.parametrize("transport,path", [("streamable-http", "/mcp"), ("sse", "/sse")])
def test_connection_survives_idling_past_a_ping(spawn, registry, transport, path, monkeypatch):
    monkeypatch.setattr("workstation.mcp.client.PING_INTERVAL", 1)
    port = free_port()
    spawn(port, transport=transport)
    registry.add(ServerConfig(id="s", url=f"http://127.0.0.1:{port}{path}"))
    time.sleep(4)
    assert registry.describe("s")["status"] == "connected"
    assert registry.call("s__echo", {"text": "still here"})["text"] == "still here"


def test_slow_call_outlives_pings(spawn, registry, monkeypatch):
    monkeypatch.setattr("workstation.mcp.client.PING_INTERVAL", 1)
    port = free_port()
    spawn(port)
    registry.add(ServerConfig(id="s", url=f"http://127.0.0.1:{port}/mcp"))
    assert registry.call("s__slow", {"seconds": 5}) == {"is_error": False, "text": "finished"}


def test_call_timeout_is_reported(spawn, registry):
    port = free_port()
    spawn(port)
    registry.add(ServerConfig(id="s", url=f"http://127.0.0.1:{port}/mcp", timeout=1))
    assert registry.call("s__slow", {"seconds": 5})["is_error"]


def test_reconnects_after_server_restart(spawn, registry, monkeypatch):
    monkeypatch.setattr("workstation.mcp.client.PING_INTERVAL", 1)
    port = free_port()
    process = spawn(port)
    registry.add(ServerConfig(id="s", url=f"http://127.0.0.1:{port}/mcp"))
    process.kill()
    process.wait()
    assert wait_status(registry, "s", "reconnecting")
    spawn(port)
    assert wait_status(registry, "s", "connected")
    assert registry.call("s__echo", {"text": "back"})["text"] == "back"


def test_config_persists_and_reconnects_on_start(spawn, tmp_path):
    port = free_port()
    spawn(port)
    path = tmp_path / "saved.json"
    first = McpRegistry(path)
    first.add(ServerConfig(id="s", url=f"http://127.0.0.1:{port}/mcp", headers={"X-Key": "1"}))
    first.stop()

    second = McpRegistry(path)
    second.start()
    try:
        assert wait_status(second, "s", "connected")
    finally:
        second.stop()


def test_disable_and_remove(spawn, registry):
    port = free_port()
    spawn(port)
    registry.add(ServerConfig(id="s", url=f"http://127.0.0.1:{port}/mcp"))
    assert registry.set_enabled("s", False)["status"] == "disabled"
    assert registry.openai_tools() == []
    assert registry.set_enabled("s", True)["status"] == "connected"
    assert registry.remove("s")
    assert registry.describe_all() == []
