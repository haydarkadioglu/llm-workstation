import os
import sys
import uuid
import json
import time
import asyncio
import threading
from typing import List, Dict, Any, Optional, Union

# Dependency check
try:
    import fastapi
    from fastapi import FastAPI, HTTPException, Body, Request
    from fastapi.responses import HTMLResponse, StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:
    print("[Error] FastAPI is required. Please install: pip install fastapi")
    sys.exit(1)

try:
    import uvicorn
except ImportError:
    print("[Error] Uvicorn is required. Please install: pip install uvicorn")
    sys.exit(1)

try:
    from pydantic import BaseModel
except ImportError:
    print("[Error] Pydantic is required. Please install: pip install pydantic")
    sys.exit(1)

# Import custom modules
from tunnel import start_cloudflare_tunnel, get_cloudflare_url
from telemetry import get_system_telemetry, close_telemetry
from models import ModelManager
from templates import HTML_TEMPLATE

# Global state
model_manager = None
mcp_sessions = {}

# FastAPI Application
app = FastAPI(title="Colab LLM Workstation", description="High Performance Modular LLM Workspace")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Schemas
class ModelLoadPayload(BaseModel):
    model_id: str
    hf_token: Optional[str] = None
    model_type: Optional[str] = "text"

class ChatMessage(BaseModel):
    role: str
    content: Any  # Supports plain string or list of content blocks (for image uploads)

class ChatPayload(BaseModel):
    messages: List[ChatMessage]
    temperature: float = 0.7
    max_tokens: int = 512
    top_p: float = 0.9
    system_prompt: Optional[str] = "You are a helpful AI assistant running inside the LLM Workstation. You have access to Model Context Protocol (MCP) clients and servers to invoke external tools (such as web_search) when Agent Mode is enabled."
    agent_mode: Optional[bool] = False

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 0.9
    max_tokens: Optional[int] = 512
    stream: Optional[bool] = False

# API Routes
@app.get("/")
def get_root():
    from templates import load_ui_template
    return HTMLResponse(content=load_ui_template(), status_code=200)

@app.get("/api/telemetry")
def get_telemetry():
    sys_metrics = get_system_telemetry()
    return {
        "model_status": model_manager.status,
        "current_model": model_manager.model_id,
        "is_vision": model_manager.is_vision,
        "loading_progress": model_manager.loading_progress,
        "loading_speed": model_manager.loading_speed,
        "error_message": model_manager.error_message,
        "tokens_per_sec": model_manager.last_tokens_per_sec,
        "cloudflare_url": get_cloudflare_url(),
        "system": sys_metrics
    }

@app.post("/api/model/load")
def load_model_endpoint(payload: ModelLoadPayload):
    def bg_load():
        model_manager.load_model(payload.model_id, payload.hf_token, payload.model_type)
        
    threading.Thread(target=bg_load, daemon=True).start()
    return {"message": f"Load process initiated for '{payload.model_id}'."}

@app.post("/api/model/unload")
def unload_model_endpoint():
    model_manager.unload_current_model()
    return {"message": "Model unloaded and VRAM purged successfully."}

class ImageGeneratePayload(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = ""
    steps: Optional[int] = 25
    guidance_scale: Optional[float] = 7.5
    width: Optional[int] = 512
    height: Optional[int] = 512

@app.post("/api/image/generate")
def generate_image_endpoint(payload: ImageGeneratePayload):
    if not model_manager.image_pipeline:
        raise HTTPException(status_code=400, detail="No diffusion model loaded. Please load an image model first.")
    
    try:
        image = model_manager.generate_image(
            prompt=payload.prompt,
            negative_prompt=payload.negative_prompt,
            steps=payload.steps,
            guidance_scale=payload.guidance_scale,
            width=payload.width,
            height=payload.height
        )
        
        import io
        import base64
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        return {"image_base64": f"data:image/png;base64,{img_str}"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

class VideoGeneratePayload(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = ""
    steps: Optional[int] = 20
    frames: Optional[int] = 16
    fps: Optional[int] = 8

@app.post("/api/video/generate")
def generate_video_endpoint(payload: VideoGeneratePayload):
    if not model_manager.image_pipeline:
        raise HTTPException(status_code=400, detail="No video pipeline is loaded. Please load a video model first.")
    
    try:
        base64_gif = model_manager.generate_video(
            prompt=payload.prompt,
            negative_prompt=payload.negative_prompt,
            steps=payload.steps,
            frames=payload.frames,
            fps=payload.fps
        )
        return {"video_base64": f"data:image/gif;base64,{base64_gif}"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@app.post("/api/chat")
async def chat_endpoint(payload: ChatPayload):
    formatted_messages = []
    if payload.system_prompt:
        formatted_messages.append({"role": "system", "content": payload.system_prompt})
    
    for msg in payload.messages:
        formatted_messages.append({"role": msg.role, "content": msg.content})
        
    return StreamingResponse(
        model_manager.generate_stream(
            messages=formatted_messages,
            temperature=payload.temperature,
            top_p=payload.top_p,
            max_tokens=payload.max_tokens,
            agent_mode=payload.agent_mode
        ),
        media_type="text/plain"
    )

# OpenAI-Compatible completions endpoint
@app.post("/v1/chat/completions")
async def openai_chat_completions(request: ChatCompletionRequest):
    if not model_manager.model:
        raise HTTPException(status_code=503, detail="No model loaded in workstation. Load a model first.")
        
    chat_id = f"chatcmpl-{uuid.uuid4()}"
    created_time = int(time.time())
    model_name = model_manager.model_id or "local-model"
    
    messages_list = [{"role": msg.role, "content": msg.content} for msg in request.messages]
    
    if request.stream:
        async def event_generator():
            try:
                start_packet = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(start_packet)}\n\n"
                
                for text_chunk in model_manager.generate_stream(
                    messages=messages_list,
                    temperature=request.temperature,
                    top_p=request.top_p,
                    max_tokens=request.max_tokens
                ):
                    chunk_packet = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": model_name,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": text_chunk},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk_packet)}\n\n"
                    
                end_packet = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }]
                }
                yield f"data: {json.dumps(end_packet)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as stream_err:
                err_packet = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": f"\n[Stream Error: {str(stream_err)}]"},
                        "finish_reason": "error"
                    }]
                }
                yield f"data: {json.dumps(err_packet)}\n\n"
                yield "data: [DONE]\n\n"
                
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else:
        full_text = ""
        for chunk in model_manager.generate_stream(
            messages=messages_list,
            temperature=request.temperature,
            top_p=request.top_p,
            max_tokens=request.max_tokens
        ):
            full_text += chunk
            
        prompt_tokens = 0
        for m in request.messages:
            if isinstance(m.content, str):
                prompt_tokens += len(m.content) // 4
            else:
                prompt_tokens += 300
                
        completion_tokens = len(full_text) // 4
        
        return {
            "id": chat_id,
            "object": "chat.completion",
            "created": created_time,
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_text
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens
            }
        }

# Model Context Protocol (MCP) Server SSE endpoints
@app.get("/sse")
async def mcp_sse_endpoint(request: Request):
    session_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    mcp_sessions[session_id] = queue
    
    async def sse_event_generator():
        yield f"event: endpoint\ndata: /message?session_id={session_id}\n\n"
        
        try:
            while True:
                response_msg = await queue.get()
                yield f"event: message\ndata: {json.dumps(response_msg)}\n\n"
                queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            mcp_sessions.pop(session_id, None)

    return StreamingResponse(sse_event_generator(), media_type="text/event-stream")

@app.post("/message")
async def mcp_message_endpoint(session_id: str, request: Request):
    if session_id not in mcp_sessions:
        raise HTTPException(status_code=404, detail="MCP Session not found or expired")
        
    payload = await request.json()
    from mcp_server import handle_mcp_message
    
    response = handle_mcp_message(payload, model_manager)
    if response:
        await mcp_sessions[session_id].put(response)
        
    return {"status": "accepted"}

# MCP Client Endpoints (to connect to external MCP servers)
@app.get("/api/mcp/clients")
def get_mcp_clients_endpoint():
    from mcp_client import get_connected_clients_info
    return get_connected_clients_info()

class McpConnectPayload(BaseModel):
    id: Optional[str] = None
    type: str  # "stdio" or "sse"
    command: Optional[str] = None
    args: Optional[Union[str, List[str]]] = None
    url: Optional[str] = None

@app.post("/api/mcp/clients/connect")
def connect_mcp_client_endpoint(payload: McpConnectPayload):
    from mcp_client import connect_external_mcp
    try:
        config = payload.model_dump()
        result = connect_external_mcp(config)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to connect external MCP: {str(e)}")

@app.post("/api/mcp/clients/disconnect")
def disconnect_mcp_client_endpoint(conn_id: str = Body(..., embed=True)):
    from mcp_client import disconnect_external_mcp
    success = disconnect_external_mcp(conn_id)
    if success:
        return {"status": "ok", "message": f"Connection {conn_id} disconnected."}
    else:
        raise HTTPException(status_code=404, detail="Connection not found")

# HF Cache Management Endpoints
@app.get("/api/models/cached")
def get_cached_models_endpoint():
    from models import get_cached_models
    return get_cached_models()

@app.post("/api/models/cached/delete")
def delete_cached_model_endpoint(repo_id: str = Body(..., embed=True)):
    from models import delete_cached_model
    success = delete_cached_model(repo_id)
    if success:
        return {"status": "ok", "message": f"Model {repo_id} deleted successfully."}
    else:
        raise HTTPException(status_code=404, detail=f"Model {repo_id} not found in cache or delete failed")

# App Startup/Shutdown handlers
@app.on_event("startup")
def app_startup():
    global model_manager
    model_manager = ModelManager()
    
    import argparse
    parser = argparse.ArgumentParser(description="LLM Workstation Server Runner", add_help=False)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--no-tunnel", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mcp-stdio", action="store_true")
    cli_args, _ = parser.parse_known_args()
    
    if cli_args.mcp_stdio:
        return
        
    if not cli_args.no_tunnel:
        start_cloudflare_tunnel(cli_args.port)
        
    default_model = cli_args.model if cli_args.model else "Qwen/Qwen2.5-1.5B-Instruct"
    print(f"[Startup] Queued background loading for default model: {default_model}")
    
    def bg_initial_load():
        time.sleep(2)
        model_manager.load_model(default_model)
        
    threading.Thread(target=bg_initial_load, daemon=True).start()

@app.on_event("shutdown")
def app_shutdown():
    print("[Shutdown] purging state...")
    if model_manager:
        model_manager.unload_current_model()
    close_telemetry()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LLM Workstation")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Binding host")
    parser.add_argument("--port", type=int, default=8000, help="Port to run uvicorn server")
    parser.add_argument("--model", type=str, default="Qwen/Qwen2.5-1.5B-Instruct", help="Model ID to preload on startup")
    parser.add_argument("--no-tunnel", action="store_true", help="Disable Cloudflare Tunnel")
    parser.add_argument("--mcp-stdio", action="store_true", help="Run in Stdio JSON-RPC MCP server mode instead of HTTP")
    args = parser.parse_args()
    
    if args.mcp_stdio:
        model_manager = ModelManager()
        if args.model:
            model_manager.load_model(args.model)
        from mcp_server import run_stdio_mcp
        run_stdio_mcp(model_manager)
    else:
        print(f"[Workstation] Starting server on http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
