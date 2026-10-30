import base64
import io
import threading
import time
import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..engine.video import CancellationException, merge_videos

router = APIRouter()

TASK_TTL = 3600
MAX_FINISHED_TASKS = 8
video_tasks: Dict[str, dict] = {}


class ImagePayload(BaseModel):
    prompt: str
    negative_prompt: str = ""
    steps: int = 25
    guidance_scale: float = 7.5
    width: int = 512
    height: int = 512


class VideoPayload(BaseModel):
    prompt: str
    negative_prompt: str = ""
    steps: int = 20
    frames: int = 16
    fps: int = 8
    image: Optional[str] = None


class MergePayload(BaseModel):
    video_base64_list: List[str]
    fps: int = 8


def _prune_tasks() -> None:
    now = time.time()
    finished = [(tid, t) for tid, t in video_tasks.items() if t["status"] != "processing"]
    finished.sort(key=lambda item: item[1].get("finished", 0), reverse=True)
    for index, (task_id, task) in enumerate(finished):
        if index >= MAX_FINISHED_TASKS or now - task.get("finished", now) > TASK_TTL:
            video_tasks.pop(task_id, None)


@router.post("/api/image/generate")
def generate_image(payload: ImagePayload, request: Request):
    try:
        image = request.app.state.manager.generate_image(**payload.model_dump())
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {"image_base64": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")}


def _run_video(manager, task_id: str, payload: VideoPayload) -> None:
    task = video_tasks[task_id]

    def on_progress(step: int, total: int) -> None:
        task.update(current_step=step, total_steps=total, progress_pct=min(int(step / total * 100), 99))

    try:
        video = manager.generate_video(
            **payload.model_dump(),
            on_progress=on_progress,
            is_cancelled=lambda: task["cancel_requested"],
        )
        task.update(status="completed", result=f"data:video/mp4;base64,{video}", progress_pct=100)
    except CancellationException:
        task.update(status="failed", error="Task cancelled by user.")
    except Exception as err:
        task.update(status="failed", error=str(err))
    task["finished"] = time.time()


@router.post("/api/video/generate")
def generate_video(payload: VideoPayload, request: Request):
    manager = request.app.state.manager
    try:
        manager.require_pipeline("video")
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    _prune_tasks()
    task_id = str(uuid.uuid4())
    video_tasks[task_id] = {
        "status": "processing",
        "progress_pct": 0,
        "current_step": 0,
        "total_steps": payload.steps,
        "cancel_requested": False,
    }
    threading.Thread(target=_run_video, args=(manager, task_id, payload), daemon=True).start()
    return {"task_id": task_id, "status": "processing"}


def _task(task_id: str) -> dict:
    if task_id not in video_tasks:
        raise HTTPException(status_code=404, detail="Task not found.")
    return video_tasks[task_id]


@router.get("/api/video/status/{task_id}")
def video_status(task_id: str):
    return _task(task_id)


@router.post("/api/video/cancel/{task_id}")
def cancel_video(task_id: str):
    _task(task_id)["cancel_requested"] = True
    return {"status": "cancel_requested"}


@router.post("/api/video/merge")
def merge(payload: MergePayload):
    if not payload.video_base64_list:
        raise HTTPException(status_code=400, detail="Video list cannot be empty.")
    try:
        return {"video_base64": "data:video/mp4;base64," + merge_videos(payload.video_base64_list, payload.fps)}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))
