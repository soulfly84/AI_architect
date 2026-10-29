"""Распознавание плана квартиры из картинки или PDF.

Принцип «тёмное = стена»:
1. файл приводится к картинке (PDF — через pdftoppm);
2. длинные тёмные горизонтальные и вертикальные полосы — это стены;
3. светлые связные области, не касающиеся края картинки, — помещения;
4. разрывы в стене шириной примерно с дверь — дверные проёмы; стена в этом
   месте разрезается на две, между ними ставится дверь.

Результат — обычный Project: дальше с ним работают те же правила, экспорт
в DXF и 3D-модель.
"""
from __future__ import annotations

import io
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .models import Door, Project, ProjectMeta, Room, Wall

MAX_SIDE = 1500          # больше не нужно, память и время экономятся
WALL_DARKNESS = 128      # пиксель темнее этого — часть стены
MIN_WALL_PX = 35         # короче этого не стена, а штриховка или мебель
DEFAULT_PLAN_WIDTH_M = 10.0


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


def wall_mask(img: Image.Image) -> np.ndarray:
    """True там, где пиксель достаточно тёмный, чтобы быть стеной."""
    gray = np.asarray(img.convert("L"), dtype=np.uint8)
    return gray < WALL_DARKNESS


# --------------------------------------------------------------------------
# стены
# --------------------------------------------------------------------------


def _row_runs(row: np.ndarray, min_len: int = 1) -> list[tuple[int, int]]:
    """Непрерывные отрезки True в строке: [(начало, конец_включительно)]."""
    padded = np.concatenate(([0], row.astype(np.int8), [0]))
    idx = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b) - 1) for a, b in zip(idx[0::2], idx[1::2])
            if b - a >= min_len]


def _overlap(a0: int, a1: int, b0: int, b1: int) -> float:
    inter = min(a1, b1) - max(a0, b0)
    if inter <= 0:
        return 0.0
    return inter / max(1, min(a1 - a0, b1 - b0))


def _segments(mask: np.ndarray, horizontal: bool) -> list[dict]:
    """Собрать стены одного направления: длинные полосы, склеенные по толщине."""
    m = mask if horizontal else mask.T
    groups: list[dict] = []
    for i in range(m.shape[0]):
        for a, b in _row_runs(m[i], MIN_WALL_PX):
            for g in groups:
                if i - g["last"] <= 2 and _overlap(a, b, g["a"], g["b"]) >= 0.6:
                    g["last"] = i
                    g["starts"].append(a)
                    g["ends"].append(b)
                    break
            else:
                groups.append({"first": i, "last": i, "a": a, "b": b,
                               "starts": [a], "ends": [b]})

    out: list[dict] = []
    for g in groups:
        a = float(np.median(g["starts"]))
        b = float(np.median(g["ends"]))
        pos = (g["first"] + g["last"]) / 2
        thick = float(g["last"] - g["first"] + 1)
        if b - a < MIN_WALL_PX:
            continue
        if horizontal:
            out.append({"start": (a, pos), "end": (b, pos), "thickness": thick})
        else:
            out.append({"start": (pos, a), "end": (pos, b), "thickness": thick})
    return out


def _length(s: dict) -> float:
    return math.hypot(s["end"][0] - s["start"][0], s["end"][1] - s["start"][1])


def _drop_duplicates(segs: list[dict]) -> list[dict]:
    """Убрать короткие стены внутри длинных: штриховку, мебель, двойные линии."""
    kept: list[dict] = []
    for s in sorted(segs, key=lambda s: -_length(s)):
        same = False
        for k in kept:
            if (_overlap(int(s["start"][0]), int(s["end"][0]),
                         int(k["start"][0]), int(k["end"][0])) > 0.85
                    and abs(s["start"][1] - k["start"][1]) < 4):
                same = True
                break
        if not same:
            kept.append(s)
    return kept


def _median_thickness(segs: list[dict]) -> float:
    return float(np.median([s["thickness"] for s in segs])) or 8.0


def _merge_collinear(segs: list[dict], gap_max: float) -> list[dict]:
    """Склеить стены на одной линии, между которыми нет проёма."""
    out = list(segs)
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if abs(a["start"][1] - b["start"][1]) > 3:
                    continue
                left, right = ((a, b) if a["start"][0] <= b["start"][0]
                               else (b, a))
                gap = right["start"][0] - left["end"][0]
                if 0 <= gap <= gap_max:
                    out[i] = {"start": (left["start"][0], a["start"][1]),
                              "end": (right["end"][0], a["end"][1]),
                              "thickness": max(a["thickness"], b["thickness"])}
                    out.pop(j)
                    changed = True
                    break
            if changed:
                break
    return out


def _split_at_gaps(segs: list[dict], thickness: float,
                   horizontal: bool) -> tuple[list[dict], list[dict]]:
    """Найти проёмы в стенах одного направления.

    Возвращает (стены, проёмы). Стены остаются как есть: проём и так пустое
    место, а вот комнаты склеиваются через него — поэтому проёмы ещё и
    «закрываются» отдельной маской при поиске помещений.
    """
    lo_gap = thickness * 3.0
    hi_gap = thickness * 18.0

    def line_of(s: dict) -> float:
        return s["start"][1] if horizontal else s["start"][0]

    def from_to(s: dict) -> tuple[float, float]:
        return ((s["start"][0], s["end"][0]) if horizontal
                else (s["start"][1], s["end"][1]))

    groups: list[list[dict]] = []
    for s in sorted(segs, key=lambda s: (round(line_of(s) / 3), from_to(s)[0])):
        if groups and abs(line_of(groups[-1][-1]) - line_of(s)) <= 3:
            groups[-1].append(s)
        else:
            groups.append([s])

    walls: list[dict] = []
    gaps: list[dict] = []
    for group in groups:
        group.sort(key=lambda s: from_to(s)[0])
        walls.append(group[0])
        for prev, cur in zip(group, group[1:]):
            gap = from_to(cur)[0] - from_to(prev)[1]
            if lo_gap <= gap <= hi_gap:
                mid = (from_to(prev)[1] + from_to(cur)[0]) / 2
                if horizontal:
                    gaps.append({"x": mid, "y": line_of(prev), "width": gap})
                else:
                    gaps.append({"x": line_of(prev), "y": mid, "width": gap,
                                 "vertical": True})
                walls.append(cur)
            elif -3 <= gap <= lo_gap:
                # почти вплотную: это одна стена, склеиваем
                walls[-1] = {
                    "start": prev["start"], "end": cur["end"],
                    "thickness": max(prev["thickness"], cur["thickness"])}
            else:
                walls.append(cur)
    return walls, gaps


def _closed_mask(mask: np.ndarray, gaps: list[dict], thickness: float) -> np.ndarray:
    """Маска для поиска помещений: стены плюс закрытые дверные проёмы.

    Без этого соседние комнаты считаются одной: они соединены проёмом.
    """
    img = Image.fromarray((mask * 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    t = max(2, int(round(thickness)))
    for gap in gaps:
        a = gap["x"] - gap["width"] / 2
        b = gap["x"] + gap["width"] / 2
        if gap.get("vertical"):
            draw.rectangle([gap["x"] - t / 2, a, gap["x"] + t / 2, b],
                           fill=255)
        else:
            draw.rectangle([a, gap["y"] - t / 2, b, gap["y"] + t / 2],
                           fill=255)
    return np.asarray(img, dtype=np.uint8) > 127


# --------------------------------------------------------------------------
# помещения
# --------------------------------------------------------------------------


def _label_regions(free: np.ndarray) -> dict[int, list[tuple[int, int, int]]]:
    """Связные области без scipy: отрезки строк + объединение меток."""
    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    rows: list[list[tuple[int, int, int]]] = []
    for y in range(free.shape[0]):
        cur = []
        for a, b in _row_runs(free[y]):
            label = len(parent)
            parent.append(label)
            cur.append((a, b, label))
        rows.append(cur)

    for y in range(1, len(rows)):
        for a, b, lab in rows[y]:
            for pa, pb, plab in rows[y - 1]:
                if pa <= b and a <= pb:
                    union(lab, plab)

    roots: dict[int, list[tuple[int, int, int]]] = {}
    for y, cur in enumerate(rows):
        for a, b, lab in cur:
            roots.setdefault(find(lab), []).append((y, a, b))
    return roots


def _region_polygon(runs: list[tuple[int, int, int]],
                    tolerance: float) -> Polygon | None:
    if not runs:
        return None
    parts = [box(float(a), float(y), float(b) + 1, float(y) + 1)
             for y, a, b in runs]
    poly = unary_union(parts).buffer(0)
    if poly.is_empty:
        return None
    poly = poly.simplify(tolerance, preserve_topology=True)
    if poly.geom_type != "Polygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly


def _touches_border(runs: list[tuple[int, int, int]], shape: tuple[int, int]) -> bool:
    h, w = shape
    return any(y <= 1 or y >= h - 2 or a <= 1 or b >= w - 2
               for y, a, b in runs)


# --------------------------------------------------------------------------
# сборка проекта
# --------------------------------------------------------------------------


def build_project(img: Image.Image, name: str = "Загруженный план",
                  plan_width_m: float | None = None) -> tuple[Project, dict]:
    """Превратить картинку плана в проект. Возвращает (проект, отчёт)."""
    mask = wall_mask(img)
    h, w = mask.shape
    if not mask.any():
        raise ValueError("На картинке нет тёмных линий. Нужен план с чёрными "
                         "стенами на светлом фоне.")

    h_segs = _drop_duplicates(_segments(mask, True))
    v_segs = _drop_duplicates(_segments(mask, False))
    all_segs = h_segs + v_segs
    if len(all_segs) < 3:
        raise ValueError("Распознано меньше трёх стен — похоже, это не план "
                         "или стены слишком светлые.")

    thick_ref = _median_thickness(all_segs)
    h_segs = _merge_collinear(h_segs, gap_max=thick_ref * 2)
    v_segs = _merge_collinear(v_segs, gap_max=thick_ref * 2)

    h_walls, h_gaps = _split_at_gaps(h_segs, thick_ref, horizontal=True)
    v_walls, v_gaps = _split_at_gaps(v_segs, thick_ref, horizontal=False)
    segs = h_walls + v_walls
    all_gaps = h_gaps + v_gaps

    # масштаб
    span_px = max((s["end"][0] - s["start"][0] for s in h_segs), default=w)
    if plan_width_m and plan_width_m > 1:
        px_per_m = span_px / float(plan_width_m)
        scale_source = f"задана вами: ширина плана {plan_width_m:g} м"
    else:
        px_per_m = span_px / DEFAULT_PLAN_WIDTH_M
        scale_source = (f"оценка: ширина плана принята за "
                        f"{DEFAULT_PLAN_WIDTH_M:g} м")
    if px_per_m <= 0:
        px_per_m = 60.0

    min_x = min(min(s["start"][0], s["end"][0]) for s in segs)
    max_y = max(max(s["start"][1], s["end"][1]) for s in segs)

    def to_m(x: float, y: float) -> tuple[float, float]:
        return (round((x - min_x) / px_per_m, 3),
                round((max_y - y) / px_per_m, 3))

    project = Project(meta=ProjectMeta(name=name))
    project.meta.scale_px_per_m = round(px_per_m, 3)

    # помещения: светлые области, не касающиеся края картинки
    free = ~_closed_mask(mask, all_gaps, thick_ref)
    min_area_px = (1.0 * px_per_m) ** 2
    rooms: list[Room] = []
    skipped_outside = 0
    for runs in _label_regions(free).values():
        if _touches_border(runs, mask.shape):
            skipped_outside += 1
            continue
        poly = _region_polygon(runs, tolerance=max(0.8, px_per_m * 0.015))
        if poly is None or poly.area < min_area_px:
            continue
        pts = [to_m(x, y) for x, y in poly.exterior.coords[:-1]]
        if len(pts) < 3:
            continue
        rooms.append(Room(name=f"Помещение {len(rooms) + 1}", polygon=pts))
    if not rooms:
        raise ValueError("Не удалось найти помещения: между стенами нет "
                         "замкнутых светлых областей. Проверьте, что план "
                         "на белом фоне и стены на нём сплошные.")
    project.rooms = rooms

    # стены
    for s in segs:
        x1, y1 = to_m(*s["start"])
        x2, y2 = to_m(*s["end"])
        if math.hypot(x2 - x1, y2 - y1) < 0.2:
            continue
        project.walls.append(Wall(
            start=[x1, y1], end=[x2, y2],
            thickness=round(max(0.06, s["thickness"] / px_per_m), 3),
            height=2.7, kind="internal"))

    # двери
    for gap in all_gaps:
        gx, gy = to_m(gap["x"], gap["y"])
        width_m = round(max(0.6, min(1.3, gap["width"] / px_per_m)), 2)
        best, best_d = None, 1e9
        for wall in project.walls:
            horizontal = abs(wall.start[1] - wall.end[1]) < 0.02
            if horizontal != (not gap.get("vertical")):
                continue
            lo = min(wall.start[0], wall.end[0]) - 0.5
            hi = max(wall.start[0], wall.end[0]) + 0.5
            coord = gy if horizontal else gx
            near = min(abs(wall.start[1] - coord), abs(wall.end[1] - coord),
                       abs(wall.start[0] - coord), abs(wall.end[0] - coord))
            if not (lo <= gx <= hi or lo <= gy <= hi):
                continue
            if near < best_d:
                best, best_d = wall, near
        if best is None or best_d > max(0.6, width_m):
            continue
        if abs(best.start[1] - best.end[1]) < 0.02:
            length = abs(best.end[0] - best.start[0]) or 1.0
            t = (gx - min(best.start[0], best.end[0])) / length
        else:
            length = abs(best.end[1] - best.start[1]) or 1.0
            t = (gy - min(best.start[1], best.end[1])) / length
        project.doors.append(Door(wall_id=best.id,
                                  t=round(min(0.95, max(0.05, t)), 3),
                                  width=width_m, swing="in", hinge="left"))

    report = {
        "walls": len(project.walls),
        "rooms": len(project.rooms),
        "doors": len(project.doors),
        "scale_px_per_m": project.meta.scale_px_per_m,
        "scale_source": scale_source,
        "image_px": [w, h],
        "outside_regions_skipped": skipped_outside,
        "warnings": [
            f"Масштаб — {scale_source}. Если размеры не совпадают с реальными, "
            f"укажите ширину плана в метрах и повторите распознавание.",
            "Распознаются только прямые стены. Дуги, эркеры под углом и "
            "лестницы могут быть потеряны.",
            "Двери находятся по разрывам в стенах и могут быть пропущены.",
            "Мебель и подписи с исходной картинки не переносятся: расставляйте "
            "заново командами или через панель.",
        ],
    }
    return project, report
