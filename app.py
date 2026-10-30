import argparse
import os

import uvicorn

from workstation.config import DEFAULT_MODEL, Settings
from workstation.hub.download_worker import FAST_DOWNLOAD


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM Workstation")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the server on")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model to load on startup (repo id, org/repo/file.gguf or a local path)")
    parser.add_argument("--no-model", action="store_true", help="Start without loading a model")
    parser.add_argument("--no-tunnel", action="store_true", help="Disable the Cloudflare tunnel")
    parser.add_argument("--mcp-stdio", action="store_true", help="Serve MCP over stdio instead of HTTP")
    parser.add_argument("--fast-download", action="store_true", help="Use hf_xet for chat models: faster, but a paused download restarts")
    return parser.parse_args()


def run_stdio(model: str) -> None:
    from workstation.engine.manager import ModelManager
    from workstation.mcp.server import build_server

    manager = ModelManager()
    if model:
        manager.load(model)
    build_server(manager).run("stdio")


def main() -> None:
    args = parse_args()
    if args.fast_download:
        os.environ[FAST_DOWNLOAD] = "1"
    model = None if args.no_model else args.model
    if args.mcp_stdio:
        run_stdio(model)
        return

    from workstation.server import create_app

    settings = Settings(host=args.host, port=args.port, model=model, tunnel=not args.no_tunnel)
    print(f"[Workstation] Starting on http://{args.host}:{args.port}")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
