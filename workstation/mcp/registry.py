import dataclasses
import re
import threading
from typing import Dict, List, Optional, Tuple

from ..config import DATA_DIR, read_json, write_json
from .client import McpConnection, McpRuntime, ServerConfig

CONFIG_FILE = DATA_DIR / "mcp.json"
SEPARATOR = "__"
MAX_NAME = 64


def _safe(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)


def _qualified(server_id: str, tool_name: str) -> str:
    return f"{server_id}{SEPARATOR}{_safe(tool_name)}"[:MAX_NAME]


class McpRegistry:
    def __init__(self, path=CONFIG_FILE):
        self.path = path
        self.runtime = McpRuntime()
        self.configs: Dict[str, ServerConfig] = {}
        self.connections: Dict[str, McpConnection] = {}
        self.lock = threading.Lock()

    def start(self) -> None:
        """Connect saved servers. Safe to call again after restoring a backup."""
        for raw in read_json(self.path, []):
            config = ServerConfig(**raw)
            if config.id in self.configs:
                continue
            self.configs[config.id] = config
            if config.enabled:
                self.connections[config.id] = self.runtime.connect(config)

    def _save(self) -> None:
        write_json(self.path, [dataclasses.asdict(c) for c in self.configs.values()])

    def _disconnect(self, server_id: str) -> None:
        connection = self.connections.pop(server_id, None)
        if connection:
            self.runtime.run(connection.close())

    def add(self, config: ServerConfig, wait: float = 20) -> dict:
        config.id = _safe(config.id)
        with self.lock:
            self._disconnect(config.id)
            self.configs[config.id] = config
            self._save()
            connection = self.runtime.connect(config) if config.enabled else None
            if connection:
                self.connections[config.id] = connection
        if connection:
            self.runtime.run(connection.wait_ready(wait))
        return self.describe(config.id)

    def remove(self, server_id: str) -> bool:
        with self.lock:
            if server_id not in self.configs:
                return False
            self._disconnect(server_id)
            del self.configs[server_id]
            self._save()
        return True

    def reconnect(self, server_id: str, wait: float = 20) -> dict:
        return self.add(self.configs[server_id], wait)

    def set_enabled(self, server_id: str, enabled: bool) -> dict:
        config = self.configs[server_id]
        config.enabled = enabled
        return self.add(config)

    def set_tool_enabled(self, server_id: str, tool: str, enabled: bool) -> dict:
        config = self.configs[server_id]
        disabled = set(config.disabled_tools)
        disabled.discard(tool) if enabled else disabled.add(tool)
        config.disabled_tools = sorted(disabled)
        self._save()
        return self.describe(server_id)

    def describe(self, server_id: str) -> dict:
        config = self.configs[server_id]
        connection = self.connections.get(server_id)
        tools = connection.tools if connection else []
        return {
            "id": config.id,
            "transport": config.transport,
            "active_transport": connection.active_transport if connection else None,
            "url": config.url,
            "command": config.command,
            "args": config.args,
            "headers": {key: "••••" for key in config.headers},
            "timeout": config.timeout,
            "enabled": config.enabled,
            "status": connection.status if connection else "disabled",
            "error": connection.error if connection else None,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "enabled": t.name not in config.disabled_tools,
                    "schema": t.input_schema,
                }
                for t in tools
            ],
        }

    def describe_all(self) -> List[dict]:
        return [self.describe(server_id) for server_id in list(self.configs)]

    def openai_tools(self) -> List[dict]:
        tools = []
        for server_id, connection in self.connections.items():
            if connection.status != "connected":
                continue
            for tool in connection.tools:
                if tool.name in connection.config.disabled_tools:
                    continue
                tools.append({
                    "type": "function",
                    "function": {
                        "name": _qualified(server_id, tool.name),
                        "description": tool.description or "",
                        "parameters": tool.input_schema or {"type": "object", "properties": {}},
                    },
                })
        return tools

    def _resolve(self, qualified: str) -> Optional[Tuple[McpConnection, str]]:
        for server_id, connection in self.connections.items():
            for tool in connection.tools:
                if _qualified(server_id, tool.name) == qualified:
                    return connection, tool.name
        return None

    def call(self, qualified: str, arguments: dict) -> dict:
        target = self._resolve(qualified)
        if not target:
            return {"is_error": True, "text": f"Unknown tool '{qualified}'."}
        connection, name = target
        return self.runtime.run(connection.call(name, arguments))

    def call_direct(self, server_id: str, tool: str, arguments: dict) -> dict:
        return self.runtime.run(self.connections[server_id].call(tool, arguments))

    def stop(self) -> None:
        for connection in list(self.connections.values()):
            self.runtime.run(connection.close())
