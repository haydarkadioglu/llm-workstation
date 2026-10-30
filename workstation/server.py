import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from .api import chat, media, mcp, models, openai, system
from .config import Settings
from .engine.manager import ModelManager
from .mcp.registry import McpRegistry
from .mcp.server import build_server
from .telemetry import close_telemetry
from .tunnel import start_cloudflare_tunnel

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def create_app(settings: Settings, registry: McpRegistry = None) -> FastAPI:
    manager = ModelManager()
    registry = registry or McpRegistry()
    mcp_server = build_server(manager)
    # Requests arrive through tunnels with arbitrary Host headers.
    mcp_app = mcp_server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        registry.start()
        if settings.tunnel:
            start_cloudflare_tunnel(settings.port)
        if settings.model:
            def initial_load():
                time.sleep(1)
                manager.load(settings.model)

            threading.Thread(target=initial_load, daemon=True).start()
        async with mcp_server.session_manager.run():
            yield
        registry.stop()
        manager.unload()
        close_telemetry()

    app = FastAPI(title="LLM Workstation", lifespan=lifespan)
    app.state.manager = manager
    app.state.registry = registry
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (system, models, chat, openai, media, mcp):
        app.include_router(module.router)
    app.router.routes.extend(mcp_app.routes)
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
    return app
