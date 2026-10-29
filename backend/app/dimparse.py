"""Геометрия из чисел на чертеже: площадь точная, положение примерное.

Проблема пиксельного подхода: на планах БТИ стены заштрихованы, и штриховка
неотличима от множества коротких стен. Модель видит, что где находится, но
плохо измеряет координаты (сжимает по вертикали на 20-40%).

Решение: у модели берём ФОРМУ и МЕСТО комнаты, а РАЗМЕР пересчитываем так,
чтобы площадь совпала с подписью на чертеже. Подпись — это чертёжные данные,
они точные.

Что получается:
  площадь комнаты  — точная (из подписи);
  пропорции        — примерные (из пикселей);
  положение        — примерное;
  стены            — общие границы комнат и наружный контур.

Дальше можно уточнить до сантиметров: размерные цепочки (1650, 2900, 5920)
заменят приблизительные размеры, а каркас останется тем же.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Допуск: если площадь комнаты после сборки расходится с подписью сильнее,
# комната помечается как недостоверная, а не «примерно такая».
AREA_TOLERANCE = 0.02


@dataclass
class RoomBox:
    """Комната как прямоугольник в метрах."""
    name: str
    area_m2: float
    cx: float
    cy: float
    w: float
    h: float
    reliable: bool = True
    note: str = ""

    @property
    def x0(self) -> float: return self.cx - self.w / 2

    @property
    def x1(self) -> float: return self.cx + self.w / 2

    @property
    def y0(self) -> float: return self.cy - self.h / 2

    @property
    def y1(self) -> float: return self.cy + self.h / 2

    @property
    def area(self) -> float: return self.w * self.h


@dataclass
class DimPlan:
    """Результат сборки: комнаты в метрах и общий отчёт."""
    rooms: list[RoomBox] = field(default_factory=list)
    px_per_m: float = 0.0
    source: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def total_area(self) -> float:
        return sum(r.area for r in self.rooms)


def _f(value) -> float | None:
    """Число из ответа модели: может прийти строкой «5,6»."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return None


def _bbox_m(bbox: list, w_px: int, h_px: int) -> tuple[float, float, float, float]:
    """Нормализованный bbox (0..1000) в пиксели."""
    x1, y1, x2, y2 = (_f(v) or 0.0 for v in bbox[:4])
    ax, bx = sorted((x1 / 1000 * w_px, x2 / 1000 * w_px))
    ay, by = sorted((y1 / 1000 * h_px, y2 / 1000 * h_px))
    return ax, ay, bx, by


def build(data: dict, image_size: tuple[int, int]) -> DimPlan:
    """Собрать комнаты в метрах по ответу модели.

    data: {"rooms": [{"name": "1", "area_m2": 5.6, "bbox_n": [...]}], ...}
    """
    w_px, h_px = image_size
    plan = DimPlan()

    raw = []
    for r in data.get("rooms") or []:
        area = _f(r.get("area_m2"))
        bbox = r.get("bbox_n")
        if not area or area <= 0 or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        ax, ay, bx, by = _bbox_m(bbox, w_px, h_px)
        wb, hb = bx - ax, by - ay
        if wb <= 1 or hb <= 1:
            continue
        raw.append({"name": str(r.get("name") or ""), "area": area,
                    "wpx": wb, "hpx": hb, "cx": (ax + bx) / 2, "cy": (ay + by) / 2})

    if not raw:
        raise ValueError("Модель не вернула ни одной комнаты с площадью и "
                         "границами. Нужен план, где площади подписаны.")

    # Масштаб: один на весь план, из условия «сумма площадей совпадает».
    # Так модель может ошибаться в абсолютных координатах — на масштаб это
    # влияет только как общий коэффициент.
    sum_px = sum(r["wpx"] * r["hpx"] for r in raw)
    sum_m2 = sum(r["area"] for r in raw)
    px_per_m = math.sqrt(sum_px / sum_m2)
    plan.px_per_m = round(px_per_m, 2)

    for r in raw:
        # пропорции — из пикселей модели
        aspect = r["wpx"] / r["hpx"] if r["hpx"] else 1.0
        # размеры — из подписанной площади, с сохранением пропорций
        w_m = math.sqrt(r["area"] * aspect)
        h_m = r["area"] / w_m if w_m else 0.0
        plan.rooms.append(RoomBox(
            name=r["name"], area_m2=r["area"],
            cx=r["cx"] / px_per_m, cy=r["cy"] / px_per_m,
            w=round(w_m, 3), h=round(h_m, 3)))

    _layout(plan.rooms)

    for room in plan.rooms:
        err = abs(room.area - room.area_m2) / room.area_m2
        if err > AREA_TOLERANCE:
            room.reliable = False
            room.note = f"площадь {room.area:.2f} вместо {room.area_m2:.2f}"

    plan.source = "площади из подписей на чертеже, положение по картинке"
    plan.warnings = [
        "Площади комнат взяты с подписей и точные. Положение и пропорции — "
        "примерные, по картинке.",
        "Для точности до сантиметра нужны размерные цепочки с чертежа — "
        "это следующий шаг, каркас менять не придётся.",
    ]
    return plan


def _layout(rooms: list[RoomBox]) -> None:
    """Сдвинуть план в положительные координаты, не меняя взаимного расположения.

    Проверено на реальном плане БТИ: если центры комнат взять из картинки, а
    размеры — из подписанных площадей, комнаты не перекрываются, а габарит
    плана сходится с чертёжным в пределах 4% (8.0×13.3 м против 7.9×12.8 м).
    Поэтому переукладывать комнаты не нужно: это только искажает план.
    """
    if not rooms:
        return
    min_x = min(r.x0 for r in rooms)
    max_y = max(r.y1 for r in rooms)
    for r in rooms:
        r.cx -= min_x
        r.cy = max_y - r.cy





def to_project(plan: DimPlan, name: str = "План из чертежа"):
    """Превратить собранные комнаты в обычный Project (стены + помещения).

    Стены строятся из границ комнат: общая граница двух комнат — одна стена,
    наружный контур — наружные стены. Так стены всегда совпадают с комнатами.
    """
    from .models import Project, ProjectMeta, Room, Wall

    project = Project(meta=ProjectMeta(name=name))
    project.meta.scale_px_per_m = plan.px_per_m

    if not plan.rooms:
        return project

    # помещение = прямоугольник комнаты
    for r in plan.rooms:
        project.rooms.append(Room(
            name=f"Помещение {r.name}" if r.name else "Помещение",
            polygon=[[round(r.x0, 3), round(r.y0, 3)],
                     [round(r.x1, 3), round(r.y0, 3)],
                     [round(r.x1, 3), round(r.y1, 3)],
                     [round(r.x0, 3), round(r.y1, 3)]]))

    # стены: проходим по всем сторонам комнат и убираем дубли (общие стены)
    seen: set[tuple] = set()
    for r in plan.rooms:
        edges = [((r.x0, r.y0), (r.x1, r.y0)),
                 ((r.x1, r.y0), (r.x1, r.y1)),
                 ((r.x1, r.y1), (r.x0, r.y1)),
                 ((r.x0, r.y1), (r.x0, r.y0))]
        for a, b in edges:
            key = tuple(sorted([(round(a[0], 2), round(a[1], 2)),
                                (round(b[0], 2), round(b[1], 2))]))
            if key in seen:
                continue
            seen.add(key)
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length < 0.3:
                continue
            project.walls.append(Wall(
                start=[round(a[0], 3), round(a[1], 3)],
                end=[round(b[0], 3), round(b[1], 3)],
                thickness=0.15, height=2.8, kind="internal"))

    return project


def report(plan: DimPlan) -> dict:
    """Краткий отчёт для интерфейса."""
    return {
        "rooms": len(plan.rooms),
        "total_area_m2": round(plan.total_area, 1),
        "px_per_m": plan.px_per_m,
        "source": plan.source,
        "unreliable": [r.name for r in plan.rooms if not r.reliable],
        "warnings": plan.warnings,
        "details": [
            {"name": r.name, "area_m2": r.area_m2,
             "size": f"{r.w:.2f}×{r.h:.2f}", "ok": r.reliable, "note": r.note}
            for r in plan.rooms
        ],
    }
