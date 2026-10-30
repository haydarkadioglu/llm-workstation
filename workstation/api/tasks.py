from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..tasks.runner import DEFAULT_MAX_STEPS

router = APIRouter(prefix="/api/tasks")


class TaskPayload(BaseModel):
    goal: str
    max_steps: int = DEFAULT_MAX_STEPS
    temperature: float = 0.2
    system_prompt: Optional[str] = None


def _tasks(request: Request):
    return request.app.state.tasks


@router.get("")
def list_tasks(request: Request):
    return _tasks(request).summaries()


@router.post("")
def create_task(payload: TaskPayload, request: Request):
    if not payload.goal.strip():
        raise HTTPException(status_code=400, detail="Describe the task.")
    if not request.app.state.manager.backend:
        raise HTTPException(status_code=503, detail="Load a chat model first.")
    task = _tasks(request).create(payload.goal, payload.max_steps, payload.temperature, payload.system_prompt)
    return {"id": task["id"]}


@router.get("/{task_id}")
def get_task(task_id: str, request: Request, after: int = 0):
    task = _tasks(request).view(task_id, after)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str, request: Request):
    if not _tasks(request).cancel(task_id):
        raise HTTPException(status_code=409, detail="This task is not running.")
    return {"status": "ok"}


@router.post("/{task_id}/resume")
def resume_task(task_id: str, request: Request):
    if not request.app.state.manager.backend:
        raise HTTPException(status_code=503, detail="Load a chat model first.")
    if not _tasks(request).resume(task_id):
        raise HTTPException(status_code=409, detail="This task cannot be resumed.")
    return {"status": "ok"}


@router.delete("/{task_id}")
def delete_task(task_id: str, request: Request):
    if not _tasks(request).delete(task_id):
        raise HTTPException(status_code=409, detail="Stop the task before deleting it.")
    return {"status": "ok"}
