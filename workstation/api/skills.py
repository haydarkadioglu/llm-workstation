import base64
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..skills.generate import draft_skill
from ..skills.store import Skill

router = APIRouter(prefix="/api/skills")


class SkillPayload(BaseModel):
    name: str
    description: str
    instructions: str


class DraftPayload(BaseModel):
    description: str
    current: Optional[SkillPayload] = None


class ImportPayload(BaseModel):
    filename: str
    text: Optional[str] = None
    base64: Optional[str] = None


class EnabledPayload(BaseModel):
    enabled: bool


def _store(request: Request):
    return request.app.state.skills


def _skill_or_404(request: Request, name: str) -> Skill:
    try:
        skill = _store(request).get(name)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found.")
    return skill


@router.get("")
def list_skills(request: Request):
    return _store(request).describe_all()


@router.get("/export")
def export_all(request: Request):
    headers = {"Content-Disposition": 'attachment; filename="skills.zip"'}
    return Response(_store(request).export_zip(), media_type="application/zip", headers=headers)


@router.post("/draft")
def draft(payload: DraftPayload, request: Request):
    manager = request.app.state.manager
    if not manager.backend:
        raise HTTPException(status_code=503, detail="Load a chat model first.")
    current = Skill(**payload.current.model_dump()).to_markdown() if payload.current else None
    try:
        name = payload.current.name if payload.current else None
        skill = draft_skill(manager, payload.description, current, name)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=f"The model did not produce a usable skill: {err}")
    return vars(skill)


@router.post("/import")
def import_skills(payload: ImportPayload, request: Request):
    try:
        if payload.base64:
            skills = _store(request).import_zip(base64.b64decode(payload.base64))
        else:
            skills = [_store(request).import_text(payload.text or "", payload.filename)]
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Could not import {payload.filename}: {err}")
    return {"imported": [s.name for s in skills]}


@router.post("")
def save_skill(payload: SkillPayload, request: Request):
    try:
        return vars(_store(request).save(Skill(**payload.model_dump())))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@router.get("/{name}")
def get_skill(name: str, request: Request):
    return vars(_skill_or_404(request, name))


@router.get("/{name}/download")
def download_skill(name: str, request: Request):
    skill = _skill_or_404(request, name)
    headers = {"Content-Disposition": f'attachment; filename="{skill.name}.md"'}
    return Response(skill.to_markdown(), media_type="text/markdown", headers=headers)


@router.post("/{name}/enabled")
def set_enabled(name: str, payload: EnabledPayload, request: Request):
    _skill_or_404(request, name)
    _store(request).set_enabled(name, payload.enabled)
    return {"status": "ok"}


@router.delete("/{name}")
def delete_skill(name: str, request: Request):
    _skill_or_404(request, name)
    _store(request).delete(name)
    return {"status": "ok"}
