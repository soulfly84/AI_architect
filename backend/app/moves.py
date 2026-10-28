"""Перемещение, поворот и удаление стульев.

Каждое действие сначала проверяется на коллизии: стены, зоны открывания
дверей, выход за пределы помещения и пересечение с другой мебелью.
Если место занято, действие не выполняется, а в отчёт попадает причина.
"""
from __future__ import annotations

import re
from typing import Any

from shapely.affinity import translate
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from . import geometry
from .models import Furniture, Project

SIDE_NAMES = {"left": "левой", "right": "правой", "top": "верхней", "bottom": "нижней"}

# Отступ от стены в метрах: сначала пробуем плотно к стене, потом чуть дальше.
MARGINS = (0.15, 0.2, 0.25, 0.3, 0.45)

# Зона открывания двери: половина ширины полотна плюс запас на дугу.
DOOR_SWING_RADIUS = 0.8


# --------------------------------------------------------------------------
# проверки
# --------------------------------------------------------------------------


def _door_zones(project: Project) -> list[Polygon]:
    zones = []
    for d in project.doors:
        w = project.wall(d.wall_id)
        if w is None:
            continue
        length = geometry.wall_length(w) or 1.0
        ux = (w.end[0] - w.start[0]) / length
        uy = (w.end[1] - w.start[1]) / length
        cx, cy = geometry.point_on_wall(w, d.t)
        h = d.width / 2
        seg = LineString([(cx - ux * h, cy - uy * h), (cx + ux * h, cy + uy * h)])
        zones.append(seg.buffer(DOOR_SWING_RADIUS))
    return zones


def _blocked(project: Project, room_poly: Polygon, polys: list[Polygon],
             own_ids: set[str]) -> str | None:
    """Вернуть причину, если мебель нельзя поставить так, или None, если можно."""
    union = unary_union(polys)
    if not room_poly.buffer(0.01).contains(union):
        return "выходит за пределы помещения"
    for w in project.walls:
        wall_area = LineString([w.start, w.end]).buffer(w.thickness / 2)
        if wall_area.intersects(union):
            return "упирается в стену"
    for zone in _door_zones(project):
        if zone.intersects(union):
            return "мешает открыванию двери"
    for f in project.furniture:
        if f.id in own_ids:
            continue
        if geometry.item_footprint(f).intersects(union):
            return f"пересекает другой предмет ({f.kind} {f.id})"
    return None


def _furn(project: Project, item_id: str) -> Furniture | None:
    return next((f for f in project.furniture if f.id == item_id), None)


# --------------------------------------------------------------------------
# действия
# --------------------------------------------------------------------------


def move_to_wall(project: Project, item_id: str, side: str) -> list[str]:
    """Сдвинуть предмет (со стульями, если это стол) вплотную к стене помещения."""
    item = _furn(project, item_id)
    if item is None:
        return [f"НЕ ВЫПОЛНЕНО: предмет {item_id} не найден"]
    room = project.room(item.room_id) if item.room_id else None
    if room is None:
        return [f"НЕ ВЫПОЛНЕНО: {item.kind} {item.id} не привязан к помещению"]

    chairs = geometry._chairs_of(project, item) if item.kind == "table" else []
    group = [item, *chairs]
    own = {f.id for f in group}
    room_poly = geometry.room_polygon(room)
    minx, miny, maxx, maxy = room_poly.bounds
    start_polys = [geometry.item_footprint(f) for f in group]
    gx0, gy0, gx1, gy1 = unary_union(start_polys).bounds

    reason = "нет подходящего места"
    for margin in MARGINS:
        if side == "left":
            dx, dy = (minx + margin) - gx0, 0.0
        elif side == "right":
            dx, dy = (maxx - margin) - gx1, 0.0
        elif side == "top":
            dx, dy = 0.0, (miny + margin) - gy0
        else:
            dx, dy = 0.0, (maxy - margin) - gy1
        moved = [translate(p, dx, dy) for p in start_polys]
        reason = _blocked(project, room_poly, moved, own) or None
        if reason is None:
            for f in group:
                f.center = [round(f.center[0] + dx, 3), round(f.center[1] + dy, 3)]
            return [f"{item.kind} {item.id}: сдвинут к {SIDE_NAMES[side]} стене"
                    + (f" вместе с {len(chairs)} стульями" if chairs else "")]
    return [f"НЕ ВЫПОЛНЕНО: {item.kind} {item.id} нельзя сдвинуть к "
            f"{SIDE_NAMES[side]} стене: {reason}"]


def rotate(project: Project, item_id: str, degrees: float) -> list[str]:
    """Повернуть предмет без стульев (диван, кровать и т.п.) на заданный угол."""
    item = _furn(project, item_id)
    if item is None:
        return [f"НЕ ВЫПОЛНЕНО: предмет {item_id} не найден"]
    if item.kind == "table":
        return [f"НЕ ВЫПОЛНЕНО: поворот стола {item.id} со стульями пока не поддерживается"]
    room = project.room(item.room_id) if item.room_id else None
    if room is None:
        return [f"НЕ ВЫПОЛНЕНО: {item.kind} {item.id} не привязан к помещению"]

    new_rot = (item.rotation + degrees) % 360
    probe = item.model_copy(deep=True)
    probe.rotation = new_rot
    reason = _blocked(project, geometry.room_polygon(room),
                      [geometry.item_footprint(probe)], {item.id})
    if reason:
        return [f"НЕ ВЫПОЛНЕНО: {item.kind} {item.id} не повернулся на {degrees:g}°: {reason}"]
    item.rotation = new_rot
    return [f"{item.kind} {item.id}: поворот на {degrees:g}°"]


def remove_chair(project: Project, table_id: str) -> list[str]:
    """Убрать один стул у стола (последний из его стульев) и уменьшить число мест."""
    table = _furn(project, table_id)
    if table is None or table.kind != "table":
        return [f"НЕ ВЫПОЛНЕНО: стол {table_id} не найден"]
    chairs = geometry._chairs_of(project, table)
    if not chairs:
        return [f"НЕ ВЫПОЛНЕНО: у стола {table.id} нет стульев"]
    chair = chairs[-1]
    project.furniture.remove(chair)
    if table.seats:
        table.seats -= 1
    return [f"убран стул {chair.id} у стола {table.id}"]


# --------------------------------------------------------------------------
# разбор команд
# --------------------------------------------------------------------------


def _side(frag: str) -> str | None:
    if re.search(r"(левой|левая|левую|слева|левее)", frag):
        return "left"
    if re.search(r"(правой|правая|правую|справа|правее)", frag):
        return "right"
    if re.search(r"(верхней|верхняя|верхнюю|наверх|сверху)", frag):
        return "top"
    if re.search(r"(нижней|нижняя|нижнюю|снизу|внизу)", frag):
        return "bottom"
    return None


def _targets(project: Project, frag: str, kinds: tuple[str, ...]) -> list[Furniture]:
    from .commands import _match_rooms  # импорт здесь, чтобы не было циклов

    room_ids = _match_rooms(project, frag)
    return [f for f in project.furniture
            if f.kind in kinds and (not room_ids or f.room_id in room_ids)]


def _ask_which(targets: list[Furniture], what: str) -> str:
    listing = ", ".join(f"{f.kind} {f.id}" for f in targets)
    return f"Уточните, какой {what}: {listing}"


def parse_moves(low: str, project: Project, clauses: list[str]) -> tuple[list[dict], list[str]]:
    """Разобрать команды сдвига, поворота и удаления стула. Возвращает (операции, уточнения)."""
    ops: list[dict[str, Any]] = []
    clar: list[str] = []

    for frag in clauses:
        if re.search(r"(сдвин|передвин|придвин|подвин|двинь)", frag):
            side = _side(frag)
            kinds = []
            if re.search(r"(стол|стола|столе|table)", frag):
                kinds.append("table")
            if re.search(r"(диван|sofa)", frag):
                kinds.append("sofa")
            if not kinds:
                kinds = ["table", "sofa"]
            if side is None:
                clar.append("Уточните, к какой стене сдвинуть: левой, правой, верхней или нижней.")
                continue
            targets = _targets(project, frag, tuple(kinds))
            if len(targets) == 1:
                ops.append({"op": "move_to_wall", "item_id": targets[0].id, "side": side})
            elif not targets:
                clar.append("Не нашла стол или диван в указанном помещении.")
            else:
                clar.append(_ask_which(targets, "предмет двигать"))

        elif re.search(r"(поверн|разверн)", frag):
            if re.search(r"(стол|стола|столе|table)", frag):
                clar.append("Поворот стола со стульями пока не поддерживается.")
                continue
            degrees = 180 if (re.search(r"разверн", frag) or "180" in frag) else 90
            targets = _targets(project, frag, ("sofa",))
            if len(targets) == 1:
                ops.append({"op": "rotate", "item_id": targets[0].id, "degrees": degrees})
            elif not targets:
                clar.append("Не нашла диван в указанном помещении. Поворот пока работает для дивана.")
            else:
                clar.append(_ask_which(targets, "диван повернуть"))

        elif re.search(r"(убер|удал|сними|вынеси)", frag) and re.search(r"(стул|стуль|chair)", frag):
            tables = _targets(project, frag, ("table",))
            tables = [t for t in tables if geometry._chairs_of(project, t)]
            if len(tables) == 1:
                ops.append({"op": "remove_chair", "table_id": tables[0].id})
            elif not tables:
                clar.append("Не нашла стол со стульями в указанном помещении.")
            else:
                clar.append(_ask_which(tables, "стол, у которого убрать стул"))

    return ops, clar
