"""Геометрические операции над планировкой.

Все правки проходят здесь, а не через генерацию картинки: каждый объект
меняет свои параметры, остальные остаются нетронутыми.
"""
from __future__ import annotations

import math
from typing import Iterable

from shapely.geometry import LineString, Polygon

from .models import Furniture, Project, Room, Wall, Window

# --------------------------------------------------------------------------
# вспомогательные
# --------------------------------------------------------------------------


def wall_vector(w: Wall) -> tuple[float, float]:
    return (w.end[0] - w.start[0], w.end[1] - w.start[1])


def wall_length(w: Wall) -> float:
    dx, dy = wall_vector(w)
    return math.hypot(dx, dy)


def point_on_wall(w: Wall, t: float) -> tuple[float, float]:
    return (w.start[0] + (w.end[0] - w.start[0]) * t,
            w.start[1] + (w.end[1] - w.start[1]) * t)


def wall_angle_deg(w: Wall) -> float:
    dx, dy = wall_vector(w)
    return math.degrees(math.atan2(dy, dx))


def item_footprint(f: Furniture) -> Polygon:
    """Прямоугольник мебели в мировых координатах (с учётом поворота)."""
    hw, hd = f.width / 2, f.depth / 2
    corners = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
    a = math.radians(f.rotation)
    ca, sa = math.cos(a), math.sin(a)
    pts = [(f.center[0] + x * ca - y * sa, f.center[1] + x * sa + y * ca)
           for x, y in corners]
    return Polygon(pts)


def room_polygon(r: Room) -> Polygon:
    return Polygon(r.polygon)


# --------------------------------------------------------------------------
# двери
# --------------------------------------------------------------------------


def set_door_swing(project: Project, door_ids: Iterable[str],
                   swing: str) -> list[str]:
    summary = []
    for did in door_ids:
        d = next((x for x in project.doors if x.id == did), None)
        if d is None or d.swing == swing:
            continue
        d.swing = swing  # type: ignore[assignment]
        summary.append(f"Дверь {d.id}: открывание → "
                       f"{'наружу' if swing == 'out' else 'внутрь'}")
    return summary


def set_door_hinge(project: Project, door_ids: Iterable[str],
                   hinge: str) -> list[str]:
    summary = []
    for did in door_ids:
        d = next((x for x in project.doors if x.id == did), None)
        if d is None or d.hinge == hinge:
            continue
        d.hinge = hinge  # type: ignore[assignment]
        summary.append(f"Дверь {d.id}: петли → {hinge}")
    return summary


# --------------------------------------------------------------------------
# мебель и цвет
# --------------------------------------------------------------------------


def set_color(project: Project, item_ids: Iterable[str], color: str,
              cascade: bool = True) -> list[str]:
    """Покрасить мебель. cascade — вместе с соседними стульями."""
    summary = []
    for iid in item_ids:
        obj = project.item(iid)
        if isinstance(obj, Furniture):
            obj.color = color
            summary.append(f"{obj.kind} {obj.id}: цвет → {color}")
            if cascade and obj.kind in ("table", "sofa"):
                for ch in _chairs_of(project, obj):
                    ch.color = color
                    summary.append(f"стул {ch.id}: цвет → {color}")
        elif isinstance(obj, Room):
            obj.floor_material = color
            summary.append(f"помещение {obj.name}: покрытие → {color}")
    return summary


def _chairs_of(project: Project, table: Furniture) -> list[Furniture]:
    """Стулья, привязанные к столу (по помещению и близости)."""
    out = []
    for f in project.furniture:
        if f.kind != "chair":
            continue
        if table.room_id and f.room_id != table.room_id:
            continue
        d = math.hypot(f.center[0] - table.center[0],
                       f.center[1] - table.center[1])
        if d < max(table.width, table.depth):
            out.append(f)
    return out


def resize_table(project: Project, table_id: str, per_side: int = 4,
                 chair_size: float = 0.45) -> list[str]:
    """Увеличить стол под заданное число стульев с каждой стороны.

    Ширина = n стульев + зазоры между ними; глубина — под посадку
    с двух сторон.
    """
    table = next((f for f in project.furniture if f.id == table_id), None)
    if table is None:
        return []
    summary = []
    seat_pitch = chair_size + 0.15
    new_w = round(per_side * seat_pitch + 0.30, 2)
    new_d = round(max(table.depth, chair_size + 0.55), 2)
    if per_side >= 3:
        new_d = round(max(new_d, 1.0), 2)
    table.width, table.depth = new_w, new_d
    table.seats = per_side * 2
    summary.append(f"Стол {table.id}: размер → {new_w} × {new_d} м, "
                   f"по {per_side} стула с каждой стороны")

    chairs = [f for f in project.furniture
              if f.kind == "chair" and f.room_id == table.room_id
              and math.hypot(f.center[0] - table.center[0],
                             f.center[1] - table.center[1]) < max(new_w, new_d)]
    need = per_side * 2
    while len(chairs) > need:
        project.furniture.remove(chairs.pop())

    moved = _resolve_position(project, table)
    if moved:
        summary.append(f"Стол {table.id}: сдвинут на "
                       f"{moved[0]:+.2f}/{moved[1]:+.2f} м, "
                       f"чтобы освободить проход")
    summary.extend(_place_chairs(project, table, per_side, chair_size, chairs))
    summary.extend(check_table_clearance(project, table))
    return summary


def _place_chairs(project: Project, table: Furniture, per_side: int,
                  chair_size: float, chairs: list[Furniture]) -> list[str]:
    """Расставить стулья по периметру стола: per_side с каждой стороны."""
    a = math.radians(table.rotation)
    ca, sa = math.cos(a), math.sin(a)
    gap = 0.10

    def to_world(lx: float, ly: float) -> tuple[float, float]:
        return (table.center[0] + lx * ca - ly * sa,
                table.center[1] + lx * sa + ly * ca)

    spots: list[tuple[float, float, float]] = []
    for i in range(per_side):
        frac = (i + 0.5) / per_side
        lx = -table.width / 2 + table.width * frac
        ly = -table.depth / 2 - gap - chair_size / 2
        spots.append((lx, ly, table.rotation + 180))  # ближняя сторона
        spots.append((lx, -ly, table.rotation))       # дальняя сторона

    sibling_color = chairs[0].color if chairs else table.color
    for idx, (lx, ly, rot) in enumerate(spots):
        if idx < len(chairs):
            ch = chairs[idx]
        else:
            ch = Furniture(kind="chair", room_id=table.room_id,
                           center=[0, 0], width=chair_size,
                           depth=chair_size, height=0.45,
                           color=sibling_color)
            project.furniture.append(ch)
        ch.center = [round(v, 3) for v in to_world(lx, ly)]
        ch.rotation = rot % 360
        ch.width = ch.depth = chair_size
        ch.room_id = table.room_id
    return [f"Стулья вокруг стола: {len(spots)} шт."] if spots else []


def _resolve_position(project: Project, table: Furniture,
                      clearance: float = 0.75) -> tuple[float, float] | None:
    """Найти положение стола без пересечений — с запасом под стулья."""
    def free(cx: float, cy: float) -> bool:
        probe = table.model_copy(deep=True)
        probe.center = [cx, cy]
        fp = item_footprint(probe)
        pad = fp.buffer(clearance)
        if probe.room_id:
            room = project.room(probe.room_id)
            if room is None or not room_polygon(room).contains(fp):
                return False
        for w in project.walls:
            if LineString([w.start, w.end]).intersects(pad):
                return False
        for f in project.furniture:
            if f.id == table.id or f.kind in ("chair", "bench"):
                continue
            if pad.intersects(item_footprint(f)):
                return False
        return True

    ox, oy = table.center
    if free(ox, oy):
        return None
    for step in (0.15, 0.3, 0.5, 0.75, 1.0, 1.3, 1.6, 2.0):
        for dx, dy in ((0, -step), (0, step), (-step, 0), (step, 0),
                       (-step, -step), (step, -step),
                       (-step, step), (step, step)):
            if free(ox + dx, oy + dy):
                table.center = [round(ox + dx, 3), round(oy + dy, 3)]
                return (dx, dy)
    return None


def check_table_clearance(project: Project, table: Furniture,
                          min_clearance: float = 0.6) -> list[str]:
    warnings = []
    fp = item_footprint(table).buffer(min_clearance)
    for w in project.walls:
        if LineString([w.start, w.end]).intersects(fp):
            warnings.append(f"ПРЕДУПРЕЖДЕНИЕ: стол {table.id} ближе "
                            f"{min_clearance} м к стене {w.id}")
    for f in project.furniture:
        if f.id == table.id or f.kind == "chair":
            continue
        if fp.intersects(item_footprint(f)):
            warnings.append(f"ПРЕДУПРЕЖДЕНИЕ: стол {table.id} пересекается "
                            f"с {f.kind} {f.id}")
    return warnings


# --------------------------------------------------------------------------
# стены и остекление
# --------------------------------------------------------------------------


def glazed_wall(project: Project, wall_id: str) -> list[str]:
    """Сделать стену полностью прозрачной: «в пол», панорамные стеклопакеты."""
    w = project.wall(wall_id)
    if w is None:
        return []
    w.kind = "glass"
    project.windows = [win for win in project.windows
                       if win.wall_id != wall_id]
    project.windows.append(Window(wall_id=wall_id, t=0.5,
                                  width=round(wall_length(w) - w.thickness, 2),
                                  height=round(w.height, 2), sill=0.0,
                                  kind="panoramic"))
    return [f"Стена {wall_id}: панорамное остекление в пол "
            f"({round(wall_length(w), 2)} м)"]


def sauna_benches_left(project: Project, room_id: str) -> list[str]:
    """Полки в сауне слева, печь остаётся справа."""
    room = project.room(room_id)
    if room is None:
        return []
    poly = room_polygon(room)
    minx, miny, maxx, maxy = poly.bounds
    depth = round((maxx - minx) * 0.28, 2)
    summary = []
    # две полки ярусом: нижняя шире, верхняя уже — одна над другой
    for i in range(2):
        bench_w = round(min(depth, 0.6) if i == 0 else 0.38, 2)
        x = minx + 0.05 + bench_w / 2
        if i == 1:
            x += min(depth, 0.6) + 0.02
        bench = Furniture(
            kind="bench", name=f"Полка {i + 1}", room_id=room_id,
            center=[round(x, 2), round((miny + maxy) / 2, 2)],
            width=bench_w, depth=round(maxy - miny - 0.3, 2),
            height=0.5 + i * 0.35, color="#c8a06a",
        )
        project.furniture.append(bench)
        summary.append(f"Сауна: полка слева {bench.width}×{bench.depth} м")

    stove = next((f for f in project.furniture
                  if f.kind == "stove" and f.room_id == room_id), None)
    if stove is None:
        project.furniture.append(Furniture(
            kind="stove", name="Печь", room_id=room_id,
            center=[round(maxx - 0.45, 2), round((miny + maxy) / 2, 2)],
            width=0.45, depth=0.45, height=1.0, color="#2b2b2b"))
        summary.append("Сауна: печь установлена справа")
    else:
        stove.center = [round(maxx - stove.width / 2 - 0.1, 2),
                        stove.center[1]]
        summary.append("Сауна: печь осталась справа")
    return summary


# --------------------------------------------------------------------------
# подписи помещений
# --------------------------------------------------------------------------


def set_room_label(project: Project, room_id: str, text: str | None,
                   color: str = "#ffffff",
                   visible: bool = True) -> list[str]:
    room = project.room(room_id)
    if room is None:
        return []
    room.label_visible = visible
    room.label_color = color
    if text is not None:
        room.name = text
    return [f"Подпись «{room.name}»: "
            f"{'показана' if visible else 'скрыта'}, {color}"]


def hide_labels(project: Project, room_ids: Iterable[str]) -> list[str]:
    out = []
    for rid in room_ids:
        out.extend(set_room_label(project, rid, None, visible=False))
    return out


def dedupe_room(project: Project, room_id: str,
                kinds: list[str]) -> list[str]:
    """Оставить по одному объекту каждого типа в помещении."""
    summary = []
    for kind in kinds:
        items = [f for f in project.furniture
                 if f.room_id == room_id and f.kind == kind]
        if len(items) <= 1:
            continue
        keep = max(items, key=lambda f: f.width * f.depth)
        for extra in items:
            if extra.id == keep.id:
                continue
            project.furniture.remove(extra)
            summary.append(f"Удалён дубль {kind} {extra.id} "
                           f"в помещении {room_id}")
    return summary


# --------------------------------------------------------------------------
# проверки качества
# --------------------------------------------------------------------------


def validate(project: Project) -> dict:
    """Автоматический валидатор проекта."""
    errors: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    for r in project.rooms:
        poly = room_polygon(r)
        if not poly.is_valid or poly.area <= 0.01:
            errors.append(f"Помещение {r.name}: некорректный контур")
        else:
            ok.append(f"Помещение {r.name}: {poly.area:.1f} м²")
        if r.label_visible and not r.name.strip():
            warnings.append(f"Помещение {r.id}: пустая подпись")

    for d in project.doors:
        if project.wall(d.wall_id) is None:
            errors.append(f"Дверь {d.id}: стена {d.wall_id} не найдена")

    for w in project.windows:
        if project.wall(w.wall_id) is None:
            errors.append(f"Окно {w.id}: стена {w.wall_id} не найдена")

    for f in project.furniture:
        if f.room_id and project.room(f.room_id) is None:
            warnings.append(f"Мебель {f.id}: помещение {f.room_id} не найдено")

    furn = [f for f in project.furniture if f.kind != "chair"]
    for i, a in enumerate(furn):
        for b in furn[i + 1:]:
            if item_footprint(a).intersects(item_footprint(b)):
                inter = item_footprint(a).intersection(
                    item_footprint(b)).area
                if inter > 0.05:
                    warnings.append(
                        f"Мебель {a.kind} {a.id} пересекается с "
                        f"{b.kind} {b.id} ({inter:.2f} м²)")

    return {"errors": errors, "warnings": warnings, "ok": ok,
            "passed": not errors}


def diff(project: Project, snapshot: Project) -> list[str]:
    """Что изменилось между версиями — для «показать изменения»."""
    changes = []
    old = {o.id: o for coll in ("rooms", "walls", "doors", "windows",
                                "furniture")
           for o in getattr(snapshot, coll)}
    new = {o.id: o for coll in ("rooms", "walls", "doors", "windows",
                                "furniture")
           for o in getattr(project, coll)}
    for oid, obj in new.items():
        if oid not in old:
            changes.append(f"добавлено: "
                           f"{getattr(obj, 'kind', type(obj).__name__)} {oid}")
        elif obj.model_dump() != old[oid].model_dump():
            changes.append(f"изменено: {oid}")
    for oid in old:
        if oid not in new:
            changes.append(f"удалено: {oid}")
    return changes
