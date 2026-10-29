"""Данные для 3D-просмотра в браузере (three.js) и автоопределение помещения.

Отдаётся плоский JSON: пол, стены, двери, мебель блоками. Ничего не считает
на клиенте — быстрый и предсказуемый рендер на слабом VPS.
"""
from __future__ import annotations

import math

from shapely.geometry import Point

from .models import Furniture, Project, Room
from .geometry import item_footprint, wall_length

FURNITURE_HEIGHTS = {
    "table": 0.75, "kitchen": 0.9, "sofa": 0.85, "bed": 0.5, "bench": 0.45,
    "chair": 0.45, "wardrobe": 2.2, "nightstand": 0.55, "fridge": 1.9,
    "stove": 0.85, "tv": 0.6, "wc": 0.4, "shower": 2.0, "sink": 0.85,
}


def room_for_point(project: Project, x: float, y: float) -> Room | None:
    """В каком помещении точка: по контуру комнаты."""
    pt = Point(x, y)
    for r in project.rooms:
        try:
            if Polygon_safe(r).contains(pt):
                return r
        except Exception:  # noqa: BLE001
            continue
    return None


def Polygon_safe(r: Room):
    from shapely.geometry import Polygon
    return Polygon(r.polygon)


def _polygon_triangles(points: list[list[float]], z: float) -> tuple[list, list]:
    """Триангуляция простого многоугольника «веером» от центра.

    Работает для выпуклых и звёздчатых контуров, чего для комнат достаточно.
    """
    if len(points) < 3:
        return [], []
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    verts: list[list[float]] = [[cx, z, cy]]
    faces: list[list[int]] = []
    for i, p in enumerate(points):
        verts.append([p[0], z, p[1]])
    n = len(points)
    for i in range(n):
        a = 1 + i
        b = 1 + (i + 1) % n
        faces.append([0, a, b])
    return verts, faces


def effective_height(f: Furniture) -> float:
    """Высота мебели: заданная явно, иначе типовая для этого вида.

    0 означает «не задана» — тогда берётся значение по виду (шкаф выше стула).
    """
    if f.height and f.height > 0:
        return float(f.height)
    return FURNITURE_HEIGHTS.get(f.kind, 0.8)


def scene(project: Project) -> dict:
    """Геометрия для three.js: комнаты, стены, двери, мебель."""
    out: dict = {"rooms": [], "walls": [], "furniture": [], "doors": []}

    for r in project.rooms:
        if len(r.polygon) < 3:
            continue
        verts, faces = _polygon_triangles(r.polygon, 0.0)
        out["rooms"].append({
            "id": r.id, "name": r.name,
            "vertices": verts, "faces": faces,
            "label": (sum(p[0] for p in r.polygon) / len(r.polygon),
                      sum(p[1] for p in r.polygon) / len(r.polygon)),
        })

    for w in project.walls:
        dx = w.end[0] - w.start[0]
        dy = w.end[1] - w.start[1]
        out["walls"].append({
            "id": w.id,
            "start": w.start, "end": w.end,
            "thickness": w.thickness,
            "height": 0.05 if w.kind == "glass" else w.height,
            "kind": w.kind,
            "length": round(math.hypot(dx, dy), 3),
            "angle": round(math.degrees(math.atan2(dy, dx)), 2),
        })

    for d in project.doors:
        wall = project.wall(d.wall_id)
        if wall is None:
            continue
        length = wall_length(wall) or 1.0
        cx = wall.start[0] + (wall.end[0] - wall.start[0]) * d.t
        cy = wall.start[1] + (wall.end[1] - wall.start[1]) * d.t
        out["doors"].append({
            "id": d.id, "center": [round(cx, 3), round(cy, 3)],
            "width": d.width, "swing": d.swing,
            "angle": round(math.degrees(math.atan2(
                wall.end[1] - wall.start[1], wall.end[0] - wall.start[0])), 2),
        })

    for f in project.furniture:
        height = effective_height(f)
        out["furniture"].append({
            "id": f.id, "kind": f.kind, "name": f.name,
            "room_id": f.room_id,
            "center": f.center, "width": f.width, "depth": f.depth,
            "height": round(height, 3), "rotation": f.rotation,
            "color": f.color, "seats": f.seats,
        })

    return out


def furniture_boxes(project: Project) -> list[dict]:
    """Плоские боксы мебели — для быстрого рендера, если сцена не нужна."""
    out = []
    for f in project.furniture:
        fp = item_footprint(f)
        out.append({"id": f.id, "bounds": list(fp.bounds),
                    "height": f.height or FURNITURE_HEIGHTS.get(f.kind, 0.8),
                    "color": f.color})
    return out
