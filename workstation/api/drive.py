import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..hub import backup, drive
from ..hub.transfer import plan_copies

router = APIRouter(prefix="/api/drive")


class ModePayload(BaseModel):
    enabled: bool


class TransferPayload(BaseModel):
    model_id: str
    to_drive: bool = False
    load: bool = False
    kind: str = "text"


class BackupPayload(BaseModel):
    include_token: bool = False


@router.get("/status")
def status(request: Request):
    data = drive.drive_status()
    data["backup"] = backup.info() if data["mounted"] else None
    data["auto_backup"] = backup.auto_enabled()
    data["backup_error"] = request.app.state.auto_backup.last_error
    return data


@router.post("/mode")
def set_mode(payload: ModePayload):
    result = drive.set_drive_mode(payload.enabled)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/browse")
def browse(path: str = ""):
    try:
        return drive.browse(path)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    except OSError:
        raise HTTPException(status_code=404, detail="Folder not found.")


@router.post("/transfer")
def transfer(payload: TransferPayload, request: Request):
    manager = request.app.state.manager
    try:
        copies, load_id = plan_copies(payload.model_id, payload.to_drive)
    except OSError as err:
        raise HTTPException(status_code=404, detail=str(err))

    on_done: Optional[callable] = None
    if payload.load:
        def on_done():
            threading.Thread(target=manager.load, args=(load_id, None, payload.kind), daemon=True).start()

    label = load_id.split("?")[0].replace("\\", "/").rsplit("/", 1)[-1]
    try:
        return request.app.state.transfers.start(label, copies, on_done)
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err))
    except OSError as err:
        raise HTTPException(status_code=507, detail=str(err))


@router.post("/transfer/cancel")
def cancel_transfer(request: Request):
    if not request.app.state.transfers.cancel():
        raise HTTPException(status_code=409, detail="No copy is running.")
    return {"status": "ok"}


@router.post("/backup")
def create_backup(payload: BackupPayload):
    try:
        return backup.create(payload.include_token)
    except (RuntimeError, OSError) as err:
        raise HTTPException(status_code=400, detail=str(err))


@router.post("/restore")
def restore_backup(request: Request):
    tasks = request.app.state.tasks
    if tasks.any_active():
        raise HTTPException(status_code=409, detail="Wait for running tasks to finish before restoring.")
    try:
        details = backup.restore()
    except OSError as err:
        raise HTTPException(status_code=404, detail=str(err))
    tasks.load_saved()
    request.app.state.registry.start()
    return details


@router.post("/auto-backup")
def set_auto_backup(payload: ModePayload, request: Request):
    backup.set_auto(payload.enabled)
    if payload.enabled:
        request.app.state.auto_backup.run_once()
    return {"auto_backup": payload.enabled}
