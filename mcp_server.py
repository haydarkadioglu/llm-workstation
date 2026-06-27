import json
import sys
import threading
from typing import Dict, Any, Optional

def handle_mcp_message(message: Dict[str, Any], model_manager) -> Optional[Dict[str, Any]]:
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {})

    if not method:
        # Check if initialize notification or response packet
        return None

    # Handle initialize
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "llm-workstation-mcp",
                    "version": "1.0.0"
                }
            }
        }

    # Handle tools/list
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "get_telemetry",
                        "description": "Get current GPU VRAM utilization, RAM details, server status and model loading metadata.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    },
                    {
                        "name": "load_model",
                        "description": "Load a Hugging Face model dynamically.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "model_id": {
                                    "type": "string",
                                    "description": "Hugging Face Model Hub ID (e.g. Qwen/Qwen2.5-1.5B-Instruct)"
                                }
                            },
                            "required": ["model_id"]
                        }
                    },
                    {
                        "name": "unload_model",
                        "description": "Unload the current model to free GPU VRAM and host RAM.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {}
                        }
                    },
                    {
                        "name": "generate",
                        "description": "Generate text from the loaded model. Supports vision prompt when VLM is active.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "prompt": {
                                    "type": "string",
                                    "description": "The user prompt to generate text for."
                                },
                                "temperature": {
                                    "type": "number",
                                    "description": "Sampling temperature (0.0 to 1.5). Default is 0.7."
                                },
                                "max_tokens": {
                                    "type": "integer",
                                    "description": "Maximum new tokens to generate. Default is 512."
                                },
                                "image_base64": {
                                    "type": "string",
                                    "description": "Optional Base64 encoded image attachment (with or without data URL scheme) for multimodal vision models."
                                }
                            },
                            "required": ["prompt"]
                        }
                    }
                ]
            }
        }

    # Handle tools/call
    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "get_telemetry":
            from telemetry import get_system_telemetry
            from tunnel import get_cloudflare_url
            sys_metrics = get_system_telemetry()
            telemetry_data = {
                "model_status": model_manager.status,
                "current_model": model_manager.model_id,
                "is_vision": model_manager.is_vision,
                "loading_progress": model_manager.loading_progress,
                "loading_speed": model_manager.loading_speed,
                "tokens_per_sec": model_manager.last_tokens_per_sec,
                "cloudflare_url": get_cloudflare_url(),
                "system": sys_metrics
            }
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(telemetry_data, indent=2)
                        }
                    ]
                }
            }

        elif tool_name == "load_model":
            model_id = args.get("model_id")
            if not model_id:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32602, "message": "Missing 'model_id' argument"}
                }
            
            def bg_load():
                model_manager.load_model(model_id)
            threading.Thread(target=bg_load, daemon=True).start()
            
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Model load sequence initiated for '{model_id}'."
                        }
                    ]
                }
            }

        elif tool_name == "unload_model":
            model_manager.unload_current_model()
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Model successfully unloaded and VRAM purged."
                        }
                    ]
                }
            }

        elif tool_name == "generate":
            prompt = args.get("prompt")
            temp = float(args.get("temperature", 0.7))
            max_toks = int(args.get("max_tokens", 512))
            img_b64 = args.get("image_base64")

            if not prompt:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32602, "message": "Missing 'prompt' argument"}
                }

            if not model_manager.model:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Error: No model loaded in workstation. Load a model first."
                            }
                        ],
                        "isError": True
                    }
                }

            # Build messages format
            messages = []
            if img_b64:
                content_payload = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": img_b64}}
                ]
                messages.append({"role": "user", "content": content_payload})
            else:
                messages.append({"role": "user", "content": prompt})

            generated_text = ""
            for chunk in model_manager.generate_stream(
                messages=messages,
                temperature=temp,
                max_tokens=max_toks
            ):
                # Avoid streaming error tags to MCP client directly if not text
                if chunk.startswith("Error") or chunk.startswith("\n[Generation"):
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": chunk}],
                            "isError": True
                        }
                    }
                generated_text += chunk

            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": generated_text
                        }
                    ]
                }
            }

        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found."}
            }

    # Method Not Found
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method '{method}' not found."}
    }

def run_stdio_mcp(model_manager):
    print("[MCP] Stdio JSON-RPC server active. Listening on stdin...", file=sys.stderr)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
                response = handle_mcp_message(message, model_manager)
                if response:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except Exception as parse_err:
                print(f"[MCP Error] Failed to parse message: {parse_err}", file=sys.stderr)
    except KeyboardInterrupt:
        print("[MCP] Stdio session interrupted. Shutting down.", file=sys.stderr)
