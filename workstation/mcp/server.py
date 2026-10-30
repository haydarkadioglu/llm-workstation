import json
import threading

import anyio
from mcp.server.mcpserver import MCPServer

from ..engine.base import GenerationParams, Usage
from ..engine.manager import ModelManager
from ..telemetry import get_system_telemetry
from ..tunnel import get_cloudflare_url


def build_server(manager: ModelManager) -> MCPServer:
    server = MCPServer("llm-workstation", log_level="WARNING")

    @server.tool()
    def get_telemetry() -> str:
        """Current model status, GPU memory and RAM usage."""
        data = {**manager.snapshot(), "cloudflare_url": get_cloudflare_url(), "system": get_system_telemetry()}
        return json.dumps(data, indent=2)

    @server.tool()
    def load_model(model_id: str) -> str:
        """Load a Hugging Face model, e.g. Qwen/Qwen2.5-1.5B-Instruct or org/repo/file.gguf."""
        threading.Thread(target=manager.load, args=(model_id,), daemon=True).start()
        return f"Loading '{model_id}'. Poll get_telemetry for progress."

    @server.tool()
    def unload_model() -> str:
        """Unload the current model and free its memory."""
        manager.unload()
        return "Model unloaded."

    def _generate(prompt: str, temperature: float, max_tokens: int, image_base64: str) -> str:
        content = prompt
        if image_base64:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_base64}},
            ]
        params = GenerationParams(temperature=temperature, max_tokens=max_tokens)
        stream = manager.stream([{"role": "user", "content": content}], None, params, threading.Event())
        return "".join(item for item in stream if not isinstance(item, Usage))

    @server.tool()
    async def generate(prompt: str, temperature: float = 0.7, max_tokens: int = 512, image_base64: str = "") -> str:
        """Generate text with the loaded model. Pass image_base64 when a vision model is loaded."""
        return await anyio.to_thread.run_sync(_generate, prompt, temperature, max_tokens, image_base64)

    return server
