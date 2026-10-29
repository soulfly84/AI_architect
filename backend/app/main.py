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

from . import (commands, dimparse, export, geometry, moves, planparse,
               render2d, storage, view3d, visionplan, webui)
from .models import Furniture, Project

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
            elif name == "move_to_wall":
                summary += moves.move_to_wall(project, op["item_id"], op["side"])
            elif name == "rotate":
                summary += moves.rotate(project, op["item_id"],
                                        float(op.get("degrees", 90)))
            elif name == "remove_chair":
                summary += moves.remove_chair(project, op["table_id"])
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


def _recognize_plan(raw: bytes, filename: str, name: str,
                    plan_width_m: float | None,
                    engine: str) -> tuple[Project, dict]:
    """Распознать план. engine: auto | vision | pixels.

    auto — сначала модель (точные площади из подписей), при её отказе
    пиксельный разбор (работает без ключа и интернета).
    """
    img = planparse.load_image(raw, filename)
    engine = (engine or "auto").lower()

    if engine in ("auto", "vision"):
        try:
            project, report = visionplan.recognize(img, name=name)
            report["engine"] = "vision"
            return project, report
        except visionplan.PlanRecognitionError as exc:
            if engine == "vision":
                raise HTTPException(400, str(exc)) from exc
            vision_error = str(exc)
    else:
        vision_error = ""

    try:
        project, report = planparse.build_project(
            img, name=name, plan_width_m=plan_width_m)
    except ValueError as exc:
        message = str(exc)
        if vision_error:
            message += f" Модель тоже не справилась: {vision_error}"
        raise HTTPException(400, message) from exc

    report["engine"] = "pixels"
    if vision_error:
        report.setdefault("warnings", []).insert(
            0, f"Модель недоступна ({vision_error}), разбор по картинке. "
               "Площади при этом приблизительные.")
    return project, report


@app.post("/api/projects/{project_id}/upload")
async def upload(project_id: str, file: UploadFile = File(...),
                 plan_width_m: float | None = None,
                 replace: bool = True,
                 engine: str = "auto"):
    """Загрузить план картинкой или PDF и распознать помещения.

    engine: auto (модель, при сбое пиксели) | vision | pixels.
    plan_width_m нужен только пиксельному разбору: модель берёт масштаб
    из подписанных площадей.
    """
    storage.ensure_dirs()
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Файл пустой")

    parsed, report = _recognize_plan(
        raw, file.filename or "plan",
        Path(file.filename or "plan").stem or "Загруженный план",
        plan_width_m, engine)

    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "plan")
    dest = storage.UPLOADS / f"{project_id}_{safe}"
    dest.write_bytes(raw)

    p = _load(project_id)
    if replace:
        p.rooms = parsed.rooms
        p.walls = parsed.walls
        p.doors = parsed.doors
        p.windows = []
        p.furniture = []
        p.meta.name = parsed.meta.name or p.meta.name
    else:
        p.rooms += parsed.rooms
        p.walls += parsed.walls
        p.doors += parsed.doors
    p.meta.source_image = str(dest.relative_to(storage.DATA_DIR))
    p.meta.scale_px_per_m = parsed.meta.scale_px_per_m
    storage.save(p)

    return {"saved": p.meta.source_image, "report": report,
            "project_id": p.project_id, "svg": render2d.render_svg(p),
            "scene": view3d.scene(p),
            "validation": geometry.validate(p)}


@app.post("/api/upload-plan")
async def upload_plan(file: UploadFile = File(...),
                      plan_width_m: float | None = None,
                      name: str | None = None,
                      engine: str = "auto"):
    """Загрузить план и сразу создать из него новый проект."""
    storage.ensure_dirs()
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Файл пустой")

    parsed, report = _recognize_plan(
        raw, file.filename or "plan",
        name or Path(file.filename or "plan").stem or "Загруженный план",
        plan_width_m, engine)

    storage.save(parsed)
    return {"project_id": parsed.project_id, "report": report,
            "svg": render2d.render_svg(parsed),
            "scene": view3d.scene(parsed),
            "validation": geometry.validate(parsed)}


@app.get("/api/plan/engines")
def plan_engines():
    """Какие способы распознавания доступны на этой установке."""
    key = os.environ.get("PLAN_API_KEY")
    key_file = Path.home() / ".vsegpt_key"
    has_key = bool(key) or key_file.exists()
    return {
        "vision": {"available": has_key,
                   "model": os.environ.get("PLAN_MODEL",
                                           visionplan.DEFAULT_MODEL)},
        "pixels": {"available": True},
        "default": "auto" if has_key else "pixels",
    }


class FurnitureIn(BaseModel):
    kind: str
    center: list[float]
    width: float
    depth: float
    height: float | None = None
    rotation: float = 0.0
    color: str = "#b8b0a4"
    room_id: str | None = None
    seats: int | None = None
    name: str = ""


class FurniturePatch(BaseModel):
    center: list[float] | None = None
    rotation: float | None = None
    width: float | None = None
    depth: float | None = None
    color: str | None = None
    room_id: str | None = None


def _furniture_reply(p: Project) -> dict:
    return {"scene": view3d.scene(p), "svg": render2d.render_svg(p),
            "validation": geometry.validate(p)}


@app.post("/api/projects/{project_id}/furniture")
def add_furniture(project_id: str, body: FurnitureIn):
    """Добавить мебель. Помещение и высота определяются автоматически."""
    p = _load(project_id)
    if body.width <= 0 or body.depth <= 0:
        raise HTTPException(400, "Ширина и глубина должны быть больше нуля")
    room = p.room(body.room_id) if body.room_id else None
    if room is None:
        room = view3d.room_for_point(p, body.center[0], body.center[1])
    height = body.height or view3d.FURNITURE_HEIGHTS.get(body.kind, 0.8)
    item = Furniture(kind=body.kind, name=body.name, center=body.center,
                     width=round(body.width, 3), depth=round(body.depth, 3),
                     height=height, rotation=body.rotation, color=body.color,
                     room_id=room.id if room else None, seats=body.seats)
    before = p.model_copy(deep=True)
    p.furniture.append(item)
    storage.snapshot(before, p, f"добавлена мебель {item.kind}", [item.id])
    storage.save(p)
    return {"id": item.id, "room_id": item.room_id, **_furniture_reply(p)}


@app.patch("/api/projects/{project_id}/furniture/{item_id}")
def patch_furniture(project_id: str, item_id: str, body: FurniturePatch):
    """Переместить или повернуть мебель — для перетаскивания мышью."""
    p = _load(project_id)
    item = next((f for f in p.furniture if f.id == item_id), None)
    if item is None:
        raise HTTPException(404, "Мебель не найдена")
    before = p.model_copy(deep=True)
    if body.center is not None:
        item.center = [round(body.center[0], 3), round(body.center[1], 3)]
        room = view3d.room_for_point(p, *item.center)
        if room is not None:
            item.room_id = room.id
    if body.rotation is not None:
        item.rotation = float(body.rotation) % 360
    if body.width is not None and body.width > 0:
        item.width = round(body.width, 3)
    if body.depth is not None and body.depth > 0:
        item.depth = round(body.depth, 3)
    if body.color:
        item.color = body.color
    if body.room_id is not None:
        item.room_id = body.room_id
    storage.snapshot(before, p, "правка мебели", [item.id])
    storage.save(p)
    return {"id": item.id, **_furniture_reply(p)}


@app.delete("/api/projects/{project_id}/furniture/{item_id}")
def delete_furniture(project_id: str, item_id: str):
    p = _load(project_id)
    before = p.model_copy(deep=True)
    found = [f for f in p.furniture if f.id == item_id]
    if not found:
        raise HTTPException(404, "Мебель не найдена")
    p.furniture = [f for f in p.furniture if f.id != item_id]
    storage.snapshot(before, p, "удалена мебель", [item_id])
    storage.save(p)
    return _furniture_reply(p)


@app.get("/api/projects/{project_id}/scene")
def scene(project_id: str):
    """Данные для 3D-просмотра."""
    p = _load(project_id)
    return {"scene": view3d.scene(p), "meta": p.meta.model_dump()}


@app.get("/", response_class=HTMLResponse)
def index():
    return webui.index()


@app.get("/static/{rel_path:path}")
def static_file(rel_path: str):
    """three.js и прочая статика локально: внешний CDN не всегда доступен."""
    return webui.static(rel_path)
