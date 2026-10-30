import threading
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel

from .. import secrets
from ..engine.manager import saved_model_settings
from ..hub import cache, drive
from ..hub.source import parse_model_source

router = APIRouter()


class LoadPayload(BaseModel):
    model_id: str
    hf_token: Optional[str] = None
    model_type: str = "text"
    n_ctx: Optional[int] = None
    n_gpu_layers: Optional[int] = None


class TokenPayload(BaseModel):
    token: str


class DriveModePayload(BaseModel):
    enabled: bool


@router.get("/api/hf/token")
def hf_token_status():
    return secrets.token_status()


@router.post("/api/hf/token")
def save_hf_token(payload: TokenPayload):
    token = payload.token.strip()
    try:
        account = HfApi().whoami(token=token)["name"]
    except HfHubHTTPError as err:
        status = err.response.status_code if err.response is not None else 502
        if status in (401, 403):
            raise HTTPException(status_code=400, detail="Hugging Face rejected this token.")
        raise HTTPException(status_code=502, detail=f"Could not check the token: {err}")
    except Exception as err:  # huggingface_hub 2 raises httpx errors, which are not OSError
        raise HTTPException(status_code=502, detail=f"Could not reach Hugging Face: {err}")
    secrets.save_token(token, account)
    return secrets.token_status()


@router.delete("/api/hf/token")
def delete_hf_token():
    secrets.clear_token()
    return secrets.token_status()


@router.post("/api/model/load")
def load_model(payload: LoadPayload, request: Request):
    try:
        source = parse_model_source(payload.model_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    if source.needs_file_choice and payload.model_type == "text":
        return {"choose_file": source.model_id}
    options ={k: v for k, v in (("n_ctx", payload.n_ctx), ("n_gpu_layers", payload.n_gpu_layers)) if v is not None}
    threading.Thread(
        target=request.app.state.manager.load,
        args=(payload.model_id, payload.hf_token, payload.model_type, options),
        daemon=True,
    ).start()
    return {"message": f"Loading '{payload.model_id}'."}


def _control(done: bool) -> dict:
    if not done:
        raise HTTPException(status_code=409, detail="No download to control right now.")
    return {"status": "ok"}


@router.post("/api/model/load/pause")
def pause_load(request: Request):
    return _control(request.app.state.manager.pause_load())


@router.post("/api/model/load/resume")
def resume_load(request: Request):
    return _control(request.app.state.manager.resume_load())


@router.post("/api/model/load/cancel")
def cancel_load(request: Request):
    return _control(request.app.state.manager.cancel_load())


@router.post("/api/model/unload")
def unload_model(request: Request):
    request.app.state.manager.unload()
    return {"message": "Model unloaded."}


@router.get("/api/model/settings")
def model_settings(model_id: str):
    return saved_model_settings(model_id)


@router.get("/api/models/cached")
def cached_models():
    return cache.list_cached()


@router.post("/api/models/cached/delete")
def delete_cached_model(request: Request, model_id: str = Body(..., embed=True)):
    if request.app.state.manager.model_id == model_id:
        raise HTTPException(status_code=409, detail="Unload this model before deleting it.")
    try:
        found = cache.delete_cached(model_id)
    except OSError as err:
        raise HTTPException(status_code=500, detail=str(err))
    if not found:
        raise HTTPException(status_code=404, detail=f"'{model_id}' is not in the cache.")
    return {"status": "ok"}


@router.get("/api/hf/search")
def hf_search(query: Optional[str] = None):
    try:
        return {"models": cache.search_gguf(query)}
    except Exception as err:
        raise HTTPException(status_code=502, detail=str(err))


@router.get("/api/hf/repo_files")
def hf_repo_files(repo_id: str):
    try:
        return {"repo_id": repo_id, "files": cache.list_gguf_files(repo_id)}
    except Exception as err:
        raise HTTPException(status_code=502, detail=str(err))


@router.get("/api/drive/status")
def drive_status():
    return drive.drive_status()


@router.post("/api/drive/mode")
def drive_mode(payload: DriveModePayload):
    result = drive.set_drive_mode(payload.enabled)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result
