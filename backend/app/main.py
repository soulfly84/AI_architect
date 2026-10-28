"""FastAPI-приложение: API + встроенный веб-интерфейс."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from . import commands, export, geometry, render2d, storage
from .models import Project

app = FastAPI(title="Interior AI", version="0.1.0",
              description="MVP: планировка → правки текстом → 3D")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("ALLOWED_ORIGIN", "*")],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB = Path(__file__).resolve().parent.parent / "web"


class CommandIn(BaseModel):
    text: str
    apply: bool = True


class OpIn(BaseModel):
    operations: list[dict]
    apply: bool = True


def _filename(name: str, ext: str) -> str:
    """Безопасное имя файла: ASCII для заголовка + UTF-8 вариант."""
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "plan"
    return (f'attachment; filename="{ascii_name}.{ext}"; '
            f"filename*=UTF-8''{quote(name)}.{ext}")


def _load(project_id: str) -> Project:
    try:
        return storage.load(project_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Проект {project_id} не найден")


def _apply_ops(project: Project, ops: list[dict]) -> tuple[list[str], list[str]]:
    """Выполнить операции над моделью. Возвращает (описание, предупреждения)."""
    summary: list[str] = []
    warnings: list[str] = []
    for op in ops:
        name = op.get("op")
        try:
            if name == "door_swing":
                summary += geometry.set_door_swing(project, op["door_ids"],
                                                   op.get("swing", "out"))
            elif name == "door_hinge":
                summary += geometry.set_door_hinge(project, op["door_ids"],
                                                   op.get("hinge", "left"))
            elif name == "set_color":
                summary += geometry.set_color(project, op["item_ids"],
                                              op.get("color", "#888888"),
                                              op.get("cascade", True))
            elif name == "resize_table":
                summary += geometry.resize_table(project, op["table_id"],
                                                 int(op.get("per_side", 4)))
            elif name == "glazed_wall":
                summary += geometry.glazed_wall(project, op["wall_id"])
            elif name == "sauna_benches":
                summary += geometry.sauna_benches_left(project, op["room_id"])
            elif name == "dedupe_room":
                summary += geometry.dedupe_room(
                    project, op["room_id"],
                    op.get("kinds", ["table", "sofa"]))
            elif name == "room_label":
                summary += geometry.set_room_label(
                    project, op["room_id"], op.get("text"),
                    op.get("color", "#ffffff"), op.get("visible", True))
            else:
                warnings.append(f"Неизвестная операция: {name}")
        except (KeyError, TypeError) as exc:
            # Одна кривая операция не должна ломать всю команду
            warnings.append(f"Операция {name} пропущена: {exc}")
    return summary, warnings


@app.get("/api/health")
def health():
    return {"status": "ok",
            "llm_enabled": os.environ.get("LLM_ENABLED") == "1"}


@app.get("/api/projects")
def projects():
    return storage.list_projects()


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    return _load(project_id)


@app.post("/api/projects/{project_id}/plan.svg")
def svg(project_id: str):
    p = _load(project_id)
    return Response(render2d.render_svg(p), media_type="image/svg+xml")


@app.get("/api/projects/{project_id}/plan.svg", response_class=Response)
def svg_get(project_id: str):
    return svg(project_id)


@app.post("/api/projects/{project_id}/command")
def run_command(project_id: str, body: CommandIn):
    """Разобрать команду, применить, сохранить версию, вернуть отчёт."""
    p = _load(project_id)
    before = p.model_copy(deep=True)
    parsed = commands.parse(body.text, p)
    ops = parsed.get("operations", [])

    if not body.apply:
        return {"parsed": parsed, "applied": False}

    summary, warnings = _apply_ops(p, ops)
    if not summary and not warnings:
        warnings.append("Не удалось определить, что изменить.")

    rev = storage.snapshot(before, p, body.text, summary)  # версия «до»
    storage.save(p)
    return {
        "parsed": parsed,
        "summary": summary,
        "warnings": warnings,
        "clarifications": parsed.get("clarifications", []),
        "changes": geometry.diff(p, before),
        "validation": geometry.validate(p),
        "revision_before": rev.id,
        "svg": render2d.render_svg(p),
    }


@app.post("/api/projects/{project_id}/operations")
def run_operations(project_id: str, body: OpIn):
    """Применить структурированные операции напрямую (интерфейс/агент)."""
    p = _load(project_id)
    before = p.model_copy(deep=True)
    summary, warnings = _apply_ops(p, body.operations)
    rev = storage.snapshot(before, p, "operations", summary)
    storage.save(p)
    return {"summary": summary, "warnings": warnings,
            "changes": geometry.diff(p, before),
            "validation": geometry.validate(p),
            "revision_before": rev.id, "svg": render2d.render_svg(p)}


@app.get("/api/projects/{project_id}/revisions")
def revisions(project_id: str):
    return _load(project_id).revisions


@app.post("/api/projects/{project_id}/revert/{revision_id}")
def revert(project_id: str, revision_id: str):
    try:
        p = storage.restore(project_id, revision_id)
    except KeyError:
        raise HTTPException(404, "Версия не найдена")
    return {"project_id": p.project_id, "svg": render2d.render_svg(p),
            "revisions": p.revisions}


@app.post("/api/projects/{project_id}/validate")
def validate(project_id: str):
    return geometry.validate(_load(project_id))


@app.get("/api/projects/{project_id}/export.dxf")
def export_dxf(project_id: str):
    p = _load(project_id)
    return Response(export.to_dxf(p), media_type="application/dxf",
                    headers={"Content-Disposition":
                             _filename(p.meta.name, "dxf")})


@app.get("/api/projects/{project_id}/model.obj")
def export_obj(project_id: str):
    p = _load(project_id)
    return Response(export.to_obj(p), media_type="text/plain",
                    headers={"Content-Disposition":
                             _filename(p.meta.name, "obj")})


@app.post("/api/projects/{project_id}/duplicate")
def duplicate(project_id: str, name: str | None = None):
    p = storage.duplicate(project_id, name)
    return {"project_id": p.project_id, "name": p.meta.name}


@app.post("/api/projects/{project_id}/upload")
async def upload(project_id: str, file: UploadFile = File(...)):
    """Загрузка исходного плана. Распознавание — отдельный следующий шаг."""
    storage.ensure_dirs()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "plan")
    dest = storage.UPLOADS / f"{project_id}_{safe}"
    dest.write_bytes(await file.read())
    p = _load(project_id)
    p.meta.source_image = str(dest.relative_to(storage.DATA_DIR))
    storage.save(p)
    return {"saved": p.meta.source_image}


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")
