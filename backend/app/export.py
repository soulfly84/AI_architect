"""Экспорт: DXF (ezdxf) и простая 3D-модель (OBJ) для просмотра/рендера."""
from __future__ import annotations

import io
import math

from .geometry import item_footprint, wall_length
from .models import Project


def to_dxf(project: Project) -> bytes:
    import ezdxf

    doc = ezdxf.new("R2018", setup=True)
    doc.units = ezdxf.units.M
    msp = doc.modelspace()

    for name, color in (("ROOMS", 8), ("WALLS", 7), ("DOORS", 3),
                        ("WINDOWS", 5), ("FURNITURE", 2), ("LABELS", 1)):
        doc.layers.add(name, color=color)

    for r in project.rooms:
        msp.add_lwpolyline(r.polygon, close=True,
                           dxfattribs={"layer": "ROOMS"})
        # Подпись выводится только если помещение помечено как видимое:
        # туалет по брифу остаётся без подписи.
        if r.label_visible:
            cx = sum(p[0] for p in r.polygon) / len(r.polygon)
            cy = sum(p[1] for p in r.polygon) / len(r.polygon)
            msp.add_text(r.name, height=0.25,
                         dxfattribs={"layer": "LABELS"}).set_placement(
                             (cx, cy))

    for w in project.walls:
        layer = "WINDOWS" if w.kind == "glass" else "WALLS"
        msp.add_line(w.start, w.end,
                     dxfattribs={"layer": layer,
                                 "lineweight": int(w.thickness * 100)})

    for d in project.doors:
        wall = project.wall(d.wall_id)
        if wall is None:
            continue
        half = (d.width / 2) / max(wall_length(wall), 1e-6)
        p1 = (wall.start[0] + (wall.end[0] - wall.start[0]) * (d.t - half),
              wall.start[1] + (wall.end[1] - wall.start[1]) * (d.t - half))
        p2 = (wall.start[0] + (wall.end[0] - wall.start[0]) * (d.t + half),
              wall.start[1] + (wall.end[1] - wall.start[1]) * (d.t + half))
        msp.add_line(p1, p2, dxfattribs={"layer": "DOORS"})

    for f in project.furniture:
        pts = list(item_footprint(f).exterior.coords)
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "FURNITURE"})

    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def _box(vertices: list[float], faces: list[list[int]], cx: float, cy: float,
         cz: float, w: float, d: float, h: float,
         rot_deg: float = 0.0) -> None:
    """Добавить параллелепипед в буферы OBJ."""
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    base = len(vertices) // 3
    for z in (0.0, h):
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x, y = sx * w / 2, sy * d / 2
            rx, ry = x * ca - y * sa, x * sa + y * ca
            vertices.extend([cx + rx, z + cz, cy + ry])
    faces.extend([
        [base, base + 1, base + 2, base + 3],
        [base + 4, base + 5, base + 6, base + 7],
        [base, base + 1, base + 5, base + 4],
        [base + 1, base + 2, base + 6, base + 5],
        [base + 2, base + 3, base + 7, base + 6],
        [base + 3, base, base + 4, base + 7],
    ])


def to_obj(project: Project) -> str:
    """3D-модель: полы, стены, мебель блоками."""
    v: list[float] = []
    f: list[list[int]] = []

    for r in project.rooms:
        if r.polygon:
            minx = min(p[0] for p in r.polygon)
            maxx = max(p[0] for p in r.polygon)
            miny = min(p[1] for p in r.polygon)
            maxy = max(p[1] for p in r.polygon)
            _box(v, f, (minx + maxx) / 2, (miny + maxy) / 2, -0.05,
                 maxx - minx, maxy - miny, 0.05)

    for w in project.walls:
        L = wall_length(w)
        ang = math.degrees(math.atan2(w.end[1] - w.start[1],
                                      w.end[0] - w.start[0]))
        cx = (w.start[0] + w.end[0]) / 2
        cy = (w.start[1] + w.end[1]) / 2
        h = 0.05 if w.kind == "glass" else w.height
        _box(v, f, cx, cy, 0.0, L, w.thickness, h, ang)

    for fu in project.furniture:
        _box(v, f, fu.center[0], fu.center[1], 0.0,
             fu.width, fu.depth, fu.height, fu.rotation)

    lines = ["# interior-ai export", f"# project {project.project_id}"]
    for i in range(0, len(v), 3):
        lines.append(f"v {v[i]:.4f} {v[i+1]:.4f} {v[i+2]:.4f}")
    for face in f:
        lines.append("f " + " ".join(str(i + 1) for i in face))
    return "\n".join(lines) + "\n"
