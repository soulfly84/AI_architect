"""Распознавание плана квартиры из картинки или PDF.

Работает и с чёрными чертежами, и с серыми сканами БТИ:
1. яркость сравнивается с локальным фоном, поэтому серые линии тоже видны;
2. стены — длинные горизонтальные и вертикальные полосы (короткие штрихи,
   цифры и размеры отбрасываются по длине);
3. помещения — замкнутые светлые области внутри контура здания; области,
   касающиеся края картинки или слишком маленькие, не берутся;
4. проёмы — разрывы в стенах шириной с дверь.

На выходе — обычный Project, с которым работают правила, экспорт и 3D.
Если план не распознан, поднимается ValueError с понятной причиной.
"""
from __future__ import annotations

import io
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image, ImageDraw, ImageOps
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .models import Door, Project, ProjectMeta, Room, Wall

MAX_SIDE = 1500           # длинная сторона после уменьшения, px
BG_WINDOW = 31            # окно локального фона, px
DARK_DELTA = 28           # на сколько пиксель темнее фона, чтобы быть линией
MIN_LINE_PX = 40          # минимальная длина стены, px
CLOSE_PX = 5              # закрытие мелких разрывов в линиях, px
MIN_ROOM_M2 = 1.0         # меньшие области — не помещения
DEFAULT_PLAN_WIDTH_M = 10.0
MIN_WALL_THICK_PX = 3     # тоньше — это размерные линии и штриховка, не стена
MAX_WALL_THICK_PX = 40    # толще этого — не стена, а текст или заливка
MERGE_GAP_PX = 14         # куски одной стены ближе этого склеиваем


# --------------------------------------------------------------------------
# загрузка
# --------------------------------------------------------------------------


def _pdf_to_image(data: bytes) -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plan.pdf"
        src.write_bytes(data)
        proc = subprocess.run(
            ["pdftoppm", "-r", "150", "-png", "-f", "1", "-l", "1",
             str(src), str(Path(tmp) / "page")],
            capture_output=True, timeout=180)
        pages = sorted(Path(tmp).glob("page*.png"))
        if not pages:
            msg = proc.stderr.decode("utf-8", "ignore").strip()[:200]
            raise ValueError("Не удалось прочитать PDF: "
                             + (msg or "файл пустой или защищён паролем"))
        return Image.open(pages[0]).convert("RGB")


def load_image(data: bytes, filename: str) -> Image.Image:
    """Прочитать картинку или PDF и уменьшить до разумного размера."""
    if not data:
        raise ValueError("Файл пустой.")
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf" or data[:4] == b"%PDF":
        img = _pdf_to_image(data)
    else:
        try:
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img).convert("RGB")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Не удалось открыть файл как изображение. "
                             "Поддерживаются PNG, JPG, WEBP и PDF. "
                             f"({exc})") from exc
    w, h = img.size
    if max(w, h) > MAX_SIDE:
        k = MAX_SIDE / max(w, h)
        img = img.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
    return img


# --------------------------------------------------------------------------
# маска линий
# --------------------------------------------------------------------------


def dark_mask(img: Image.Image) -> np.ndarray:
    """True там, где пиксель заметно темнее локального фона."""
    a = np.asarray(img.convert("L"), dtype=np.float32)
    p = np.pad(a, BG_WINDOW // 2, mode="edge")
    bg = sliding_window_view(p, (BG_WINDOW, BG_WINDOW)).mean(axis=(2, 3))
    return (bg - a) > DARK_DELTA


def _runs(row: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    """Отрезки True длиной не меньше min_len: [(начало, конец_не_включая)]."""
    pad = np.concatenate(([0], row.astype(np.int8), [0]))
    idx = np.flatnonzero(np.diff(pad))
    return [(int(a), int(b)) for a, b in zip(idx[0::2], idx[1::2])
            if b - a >= min_len]


def long_lines(mask: np.ndarray, min_len: int) -> np.ndarray:
    """Оставить только горизонтальные отрезки длиннее min_len."""
    out = np.zeros_like(mask)
    for y in range(mask.shape[0]):
        for a, b in _runs(mask[y], min_len):
            out[y, a:b] = True
    return out


def _dilate(mask: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return mask
    p = np.pad(mask, k // 2, mode="constant")
    return sliding_window_view(p, (k, k)).max(axis=(2, 3)).astype(bool)


def _erode(mask: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return mask
    p = np.pad(mask, k // 2, mode="constant", constant_values=False)
    return sliding_window_view(p, (k, k)).min(axis=(2, 3)).astype(bool)


def wall_lines(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Горизонтальные и вертикальные стены как маски."""
    h = long_lines(mask, MIN_LINE_PX)
    v = long_lines(mask.T, MIN_LINE_PX).T
    return h, v


# --------------------------------------------------------------------------
# линии -> отрезки
# --------------------------------------------------------------------------


def _segments(lines: np.ndarray, horizontal: bool) -> list[dict]:
    """Отрезки из маски: склеиваем соседние строки в одну стену."""
    m = lines if horizontal else lines.T
    groups: list[dict] = []
    for i in range(m.shape[0]):
        for a, b in _runs(m[i], MIN_LINE_PX):
            for g in groups:
                if i - g["last"] <= 2 and min(b, g["b"]) - max(a, g["a"]) \
                        >= 0.6 * min(b - a, g["b"] - g["a"]):
                    g["last"] = i
                    g["starts"].append(a)
                    g["ends"].append(b)
                    g["a"], g["b"] = int(np.median(g["starts"])), int(np.median(g["ends"]))
                    break
            else:
                groups.append({"first": i, "last": i, "a": a, "b": b,
                               "starts": [a], "ends": [b]})

    out = []
    for g in groups:
        thick = g["last"] - g["first"] + 1
        if thick > MAX_WALL_THICK_PX:
            continue
        a = float(np.median(g["starts"]))
        b = float(np.median(g["ends"]))
        pos = (g["first"] + g["last"]) / 2
        if horizontal:
            out.append({"start": (a, pos), "end": (b, pos), "thickness": float(thick)})
        else:
            out.append({"start": (pos, a), "end": (pos, b), "thickness": float(thick)})
    return out


def _median_thickness(segs: list[dict]) -> float:
    if not segs:
        return 6.0
    return float(np.median([s["thickness"] for s in segs]))


def _find_gaps(segs: list[dict], horizontal: bool, thick: float) -> list[dict]:
    """Разрывы в одной линии шириной с дверь — это проёмы."""
    lo, hi = thick * 2.5, thick * 16

    def line(s):  return s["start"][1] if horizontal else s["start"][0]
    def span(s):  return (s["start"][0], s["end"][0]) if horizontal else (s["start"][1], s["end"][1])

    gaps = []
    for i, s in enumerate(segs):
        for t in segs[i + 1:]:
            if abs(line(s) - line(t)) > 3:
                continue
            (a0, a1), (b0, b1) = span(s), span(t)
            if a1 <= b0:
                g = b0 - a1
                mid = (a1 + b0) / 2
            elif b1 <= a0:
                g = a0 - b1
                mid = (b1 + a0) / 2
            else:
                continue
            if lo <= g <= hi:
                pos = (line(s) + line(t)) / 2
                gaps.append({"x": mid if horizontal else pos,
                             "y": pos if horizontal else mid,
                             "width": g, "horizontal": horizontal})
    return gaps


# --------------------------------------------------------------------------
# помещения
# --------------------------------------------------------------------------


def _label(free: np.ndarray) -> tuple[dict[int, list[tuple[int, int, int]]], dict[int, int]]:
    """Связные области по отрезкам строк (union-find, без scipy)."""
    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    rows: list[list[tuple[int, int, int]]] = []
    for y in range(free.shape[0]):
        cur = []
        for a, b in _runs(free[y], 1):
            lab = len(parent)
            parent.append(lab)
            cur.append((a, b, lab))
        rows.append(cur)
    for y in range(1, len(rows)):
        for a, b, lab in rows[y]:
            for pa, pb, pl in rows[y - 1]:
                if pa < b and a < pb:
                    ri, rj = find(lab), find(pl)
                    if ri != rj:
                        parent[rj] = ri
    regions: dict[int, list[tuple[int, int, int]]] = {}
    for y, cur in enumerate(rows):
        for a, b, lab in cur:
            regions.setdefault(find(lab), []).append((y, a, b))
    return regions, {}


def _region_poly(runs: list[tuple[int, int, int]], tol: float) -> Polygon | None:
    parts = [box(float(a), float(y), float(b), float(y) + 1) for y, a, b in runs]
    poly = unary_union(parts).buffer(0)
    if poly.is_empty:
        return None
    poly = poly.simplify(tol, preserve_topology=True)
    if poly.geom_type != "Polygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly


def _touches_border(runs, shape) -> bool:
    h, w = shape
    return any(y <= 1 or y >= h - 2 or a <= 1 or b >= w - 2 for y, a, b in runs)


# --------------------------------------------------------------------------
# сборка
# --------------------------------------------------------------------------


def build_project(img: Image.Image, name: str = "Загруженный план",
                  plan_width_m: float | None = None) -> tuple[Project, dict]:
    """Превратить картинку плана в проект. Возвращает (проект, отчёт)."""
    dark = dark_mask(img)
    if dark.sum() < 100:
        raise ValueError("На картинке почти нет линий. Нужен план с "
                         "тёмными стенами на светлом фоне.")

    h_lines, v_lines = wall_lines(dark)
    h_segs = _segments(h_lines, True)
    v_segs = _segments(v_lines, False)
    segs = h_segs + v_segs
    if len(segs) < 4:
        raise ValueError(f"Распознано {len(segs)} стен — этого мало, чтобы "
                         "построить план. Проверьте, что стены видны и "
                         "не слишком светлые.")

    thick = _median_thickness(segs)
    h_gaps = _find_gaps(h_segs, True, thick)
    v_gaps = _find_gaps(v_segs, False, thick)
    all_gaps = h_gaps + v_gaps

    # масштаб: по самой длинной горизонтальной стене
    span_px = max((abs(s["end"][0] - s["start"][0]) for s in h_segs), default=0)
    if span_px <= 0:
        span_px = max((abs(s["end"][0] - s["start"][0]) for s in segs), default=img.size[0])
    if plan_width_m and plan_width_m > 1:
        px_per_m = span_px / float(plan_width_m)
        scale_source = f"задана вами: ширина плана {plan_width_m:g} м"
    else:
        px_per_m = span_px / DEFAULT_PLAN_WIDTH_M
        scale_source = f"оценка: ширина плана принята за {DEFAULT_PLAN_WIDTH_M:g} м"
    px_per_m = max(px_per_m, 1.0)

    # помещения: светлые области, отделённые стенами и закрытыми проёмами
    closed = _dilate(h_lines | v_lines, CLOSE_PX)
    closed = _close_doorways(closed, all_gaps, thick)
    free = ~closed
    regions, _ = _label(free)

    min_area_px = MIN_ROOM_M2 * px_per_m * px_per_m
    cand = []
    skipped_outside = 0
    for runs in regions.values():
        if _touches_border(runs, free.shape):
            skipped_outside += 1
            continue
        area = sum(b - a for _, a, b in runs)
        if area < min_area_px:
            continue
        cand.append((area, runs))
    if not cand:
        raise ValueError("Не удалось найти помещения: между стенами нет "
                         "замкнутых светлых областей. Проверьте, что стены "
                         "сплошные и на светлом фоне.")

    # внешние области (терраса, крыльцо) обычно сильно меньше внутренних,
    # но мы не угадываем: берём всё замкнутое и сообщаем в отчёте
    project = Project(meta=ProjectMeta(name=name))
    project.meta.scale_px_per_m = round(px_per_m, 3)

    min_x = min(min(s["start"][0], s["end"][0]) for s in segs)
    max_y = max(max(s["start"][1], s["end"][1]) for s in segs)

    def to_m(x: float, y: float) -> tuple[float, float]:
        return (round((x - min_x) / px_per_m, 3), round((max_y - y) / px_per_m, 3))

    rooms: list[Room] = []
    for area, runs in sorted(cand, key=lambda t: -t[0]):
        poly = _region_poly(runs, tol=max(0.6, px_per_m * 0.02))
        if poly is None or poly.area < min_area_px:
            continue
        pts = [to_m(x, y) for x, y in poly.exterior.coords[:-1]]
        if len(pts) >= 3:
            rooms.append(Room(name=f"Помещение {len(rooms) + 1}", polygon=pts))
    project.rooms = rooms

    for s in segs:
        x1, y1 = to_m(*s["start"])
        x2, y2 = to_m(*s["end"])
        if math.hypot(x2 - x1, y2 - y1) < 0.2:
            continue
        project.walls.append(Wall(
            start=[x1, y1], end=[x2, y2],
            thickness=round(max(0.06, s["thickness"] / px_per_m), 3),
            height=2.7, kind="internal"))

    for gap in all_gaps:
        gx, gy = to_m(gap["x"], gap["y"])
        width_m = round(max(0.6, min(1.3, gap["width"] / px_per_m)), 2)
        horiz_gap = gap["horizontal"]
        best, best_d = None, 1e9
        for wall in project.walls:
            horiz = abs(wall.start[1] - wall.end[1]) < 0.02
            if horiz != horiz_gap:
                continue
            coord = gy if horiz else gx
            along = gx if horiz else gy
            wa = (wall.start[0], wall.end[0]) if horiz else (wall.start[1], wall.end[1])
            if not (min(wa) - 0.5 <= along <= max(wa) + 0.5):
                continue
            wc = wall.start[1] if horiz else wall.start[0]
            d = abs(wc - coord)
            if d < best_d:
                best, best_d = wall, d
        if best is None or best_d > 0.6:
            continue
        horiz = abs(best.start[1] - best.end[1]) < 0.02
        if horiz:
            s0, s1 = sorted([best.start[0], best.end[0]])
            t = (gx - s0) / max(s1 - s0, 1e-6)
        else:
            s0, s1 = sorted([best.start[1], best.end[1]])
            t = (gy - s0) / max(s1 - s0, 1e-6)
        project.doors.append(Door(wall_id=best.id,
                                  t=round(min(0.95, max(0.05, t)), 3),
                                  width=width_m, swing="in", hinge="left"))

    report = {
        "walls": len(project.walls),
        "rooms": len(project.rooms),
        "doors": len(project.doors),
        "scale_px_per_m": project.meta.scale_px_per_m,
        "scale_source": scale_source,
        "image_px": list(img.size),
        "outside_regions_skipped": skipped_outside,
        "warnings": [
            f"Масштаб — {scale_source}. Если размеры не совпадают с реальными, "
            "укажите ширину плана в метрах и повторите распознавание.",
            "Распознаются прямые стены. Дуги, наклонные стены и лестницы могут "
            "потеряться.",
            "Двери находятся по разрывам в стенах и могут быть пропущены.",
            "Мебель и подписи с картинки не переносятся: расставляйте заново.",
        ],
    }
    return project, report


def _close_doorways(closed: np.ndarray, gaps: list[dict], thick: float) -> np.ndarray:
    """Закрыть проёмы, чтобы комнаты не сливались через дверь."""
    out = closed.copy()
    t = max(2, int(round(thick)))
    for g in gaps:
        x, y, w = g["x"], g["y"], g["width"]
        if g["horizontal"]:
            x0, x1 = int(x - w / 2), int(x + w / 2) + 1
            out[max(0, int(y) - t // 2):int(y) + t // 2 + 1, max(0, x0):x1] = True
        else:
            y0, y1 = int(y - w / 2), int(y + w / 2) + 1
            out[max(0, y0):y1, max(0, int(x) - t // 2):int(x) + t // 2 + 1] = True
    return out
