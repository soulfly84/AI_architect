"""Модель данных проекта планировки.

Координаты — в метрах, начало в левом верхнем углу плана,
ось X вправо, ось Y вниз (как на изображении плана).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


Point = list[float]  # [x, y]


class Wall(BaseModel):
    """Стена — отрезок с толщиной и высотой."""

    id: str = Field(default_factory=lambda: new_id("wall"))
    start: Point
    end: Point
    thickness: float = 0.15
    height: float = 2.7
    kind: Literal["external", "internal", "glass", "railing"] = "internal"
    marked: Optional[str] = None  # красная пометка пользователя


class Door(BaseModel):
    id: str = Field(default_factory=lambda: new_id("door"))
    wall_id: str
    t: float = 0.5  # положение вдоль стены, 0..1
    width: float = 0.8
    hinge: Literal["left", "right"] = "left"
    swing: Literal["in", "out"] = "in"
    kind: Literal["swing", "sliding", "double"] = "swing"
    room_from: Optional[str] = None
    room_to: Optional[str] = None
    marked: Optional[str] = None


class Window(BaseModel):
    id: str = Field(default_factory=lambda: new_id("win"))
    wall_id: str
    t: float = 0.5
    width: float = 1.4
    height: float = 1.5
    sill: float = 0.9
    kind: Literal["window", "panoramic"] = "window"


class Room(BaseModel):
    id: str = Field(default_factory=lambda: new_id("room"))
    name: str
    polygon: list[Point]
    label_visible: bool = True
    label_color: str = "#ffffff"
    floor_material: str = "wood"


class Furniture(BaseModel):
    id: str = Field(default_factory=lambda: new_id("furn"))
    kind: str  # sofa, table, chair, bed, bench, stove, kitchen, wc, shower...
    name: str = ""
    room_id: Optional[str] = None
    center: Point
    width: float
    depth: float
    rotation: float = 0.0  # градусы, по часовой стрелке
    height: float = 0.8
    color: str = "#b8b0a4"
    seats: Optional[int] = None
    marked: Optional[str] = None


class Fixture(BaseModel):
    """Инженерный элемент: сантехника или электрика. Задел на будущее."""

    id: str = Field(default_factory=lambda: new_id("fix"))
    system: Literal["plumbing", "electrical"] = "plumbing"
    kind: str
    center: Point
    room_id: Optional[str] = None
    meta: dict = Field(default_factory=dict)


class Revision(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rev"))
    created_at: str = Field(default_factory=now)
    command: str = ""
    summary: list[str] = Field(default_factory=list)
    snapshot: str = ""


class ProjectMeta(BaseModel):
    name: str = "Новый проект"
    units: Literal["m"] = "m"
    scale_px_per_m: Optional[float] = None
    source_image: Optional[str] = None
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class Project(BaseModel):
    schema_version: str = "1.0"
    project_id: str = Field(default_factory=lambda: new_id("proj"))
    meta: ProjectMeta = Field(default_factory=ProjectMeta)
    rooms: list[Room] = Field(default_factory=list)
    walls: list[Wall] = Field(default_factory=list)
    doors: list[Door] = Field(default_factory=list)
    windows: list[Window] = Field(default_factory=list)
    furniture: list[Furniture] = Field(default_factory=list)
    fixtures: list[Fixture] = Field(default_factory=list)
    revisions: list[Revision] = Field(default_factory=list)

    def room(self, room_id: str) -> Optional[Room]:
        return next((r for r in self.rooms if r.id == room_id), None)

    def wall(self, wall_id: str) -> Optional[Wall]:
        return next((w for w in self.walls if w.id == wall_id), None)

    def item(self, item_id: str):
        for coll in (self.walls, self.doors, self.windows,
                     self.furniture, self.rooms):
            for obj in coll:
                if obj.id == item_id:
                    return obj
        return None
