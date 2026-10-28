"""2D-отрисовка плана в SVG.

Рисуется из модели объектов, а не из исходной картинки — поэтому
изменения всегда согласованы с геометрией.
"""
from __future__ import annotations

import math

from .geometry import point_on_wall, wall_length
from .models import Project

PAD = 24
SCALE = 60  # пикселей на метр


def _transform(project: Project):
    xs, ys = [], []
    for w in project.walls:
        xs += [w.start[0], w.end[0]]
        ys += [w.start[1], w.end[1]]
    for r in project.rooms:
        for p in r.polygon:
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        xs, ys = [0, 5], [0, 5]
    minx, miny = min(xs), min(ys)
    maxx, maxy = max(xs), max(ys)

    def tx(x: float, y: float) -> tuple[float, float]:
        return (PAD + (x - minx) * SCALE, PAD + (y - miny) * SCALE)

    w = (maxx - minx) * SCALE + 2 * PAD
    h = (maxy - miny) * SCALE + 2 * PAD
    return tx, w, h


def render_svg(project: Project, show_labels: bool = True) -> str:
    tx, W, H = _transform(project)
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" '
        f'height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}" '
        f'font-family="Inter, Arial, sans-serif">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="#f7f5f2"/>',
    ]

    for r in project.rooms:
        pts = " ".join(f"{tx(x, y)[0]:.1f},{tx(x, y)[1]:.1f}"
                       for x, y in r.polygon)
        out.append(f'<polygon points="{pts}" fill="#e9e4dc" '
                   f'stroke="#cfc7bb" stroke-width="1"/>')

    for f in project.furniture:
        cx, cy = tx(*f.center)
        w = f.width * SCALE
        d = f.depth * SCALE
        out.append(
            f'<g transform="translate({cx:.1f},{cy:.1f}) '
            f'rotate({f.rotation:.1f})">'
            f'<rect x="{-w/2:.1f}" y="{-d/2:.1f}" width="{w:.1f}" '
            f'height="{d:.1f}" rx="{min(w, d)*0.18:.1f}" '
            f'fill="{f.color}" stroke="#00000022"/>')
        if f.kind == "table":
            out.append(f'<text x="0" y="5" text-anchor="middle" '
                       f'font-size="12" fill="#ffffff" opacity="0.85">'
                       f'{f.width:.1f}×{f.depth:.1f} м</text>')
        out.append("</g>")

    for w in project.walls:
        x1, y1 = tx(*w.start)
        x2, y2 = tx(*w.end)
        if w.kind == "glass":
            out.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                f'y2="{y2:.1f}" stroke="#9fd8f5" '
                f'stroke-width="{max(3, w.thickness*SCALE):.1f}" '
                f'stroke-linecap="butt" opacity="0.85"/>')
            out.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                f'y2="{y2:.1f}" stroke="#4aa8d8" stroke-width="1.4" '
                f'stroke-dasharray="10 5"/>')
        elif w.kind == "railing":
            out.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                f'y2="{y2:.1f}" stroke="#8b8b8b" stroke-width="4" '
                f'stroke-dasharray="6 6"/>')
        else:
            out.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                f'y2="{y2:.1f}" stroke="#5b5b5b" '
                f'stroke-width="{max(4, w.thickness*SCALE):.1f}" '
                f'stroke-linecap="butt"/>')

    for win in project.windows:
        wall = project.wall(win.wall_id)
        if wall is None:
            continue
        half = (win.width / 2) / max(wall_length(wall), 1e-6)
        p1 = point_on_wall(wall, win.t - half)
        p2 = point_on_wall(wall, win.t + half)
        a, b = tx(*p1), tx(*p2)
        color = "#7ec8e3" if win.kind == "panoramic" else "#ffffff"
        out.append(
            f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
            f'y2="{b[1]:.1f}" stroke="{color}" '
            f'stroke-width="{max(4, wall.thickness*SCALE*0.9):.1f}"/>')
        out.append(
            f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
            f'y2="{b[1]:.1f}" stroke="#2f80a8" stroke-width="1.2"/>')

    for d in project.doors:
        wall = project.wall(d.wall_id)
        if wall is None:
            continue
        half = (d.width / 2) / max(wall_length(wall), 1e-6)
        p1 = point_on_wall(wall, d.t - half)
        p2 = point_on_wall(wall, d.t + half)
        a, b = tx(*p1), tx(*p2)
        out.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
                   f'y2="{b[1]:.1f}" stroke="#f7f5f2" '
                   f'stroke-width="{max(5, wall.thickness*SCALE):.1f}"/>')

        hinge_pt = p1 if d.hinge == "left" else p2
        base_ang = math.degrees(math.atan2(wall.end[1] - wall.start[1],
                                           wall.end[0] - wall.start[0]))
        if d.hinge == "right":
            base_ang += 180
        sweep = -90 if d.swing == "out" else 90
        if d.hinge == "right":
            sweep = -sweep
        hx, hy = tx(*hinge_pt)
        r = d.width * SCALE
        leaf_ang = base_ang + sweep
        lx = hx + r * math.cos(math.radians(leaf_ang))
        ly = hy + r * math.sin(math.radians(leaf_ang))
        out.append(f'<line x1="{hx:.1f}" y1="{hy:.1f}" x2="{lx:.1f}" '
                   f'y2="{ly:.1f}" stroke="#3d3d3d" stroke-width="2.5"/>')
        out.append(
            f'<path d="M {lx:.1f} {ly:.1f} A {r:.1f} {r:.1f} 0 1 '
            f'{1 if sweep > 0 else 0} '
            f'{hx + r*math.cos(math.radians(base_ang)):.1f} '
            f'{hy + r*math.sin(math.radians(base_ang)):.1f}" '
            f'fill="none" stroke="#9a9a9a" stroke-width="1" '
            f'stroke-dasharray="4 3"/>')
        if d.marked:
            out.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="4" '
                       f'fill="#e03131"/>')

    if show_labels:
        for r in project.rooms:
            if not r.label_visible or not r.name.strip():
                continue
            pts = [tx(x, y) for x, y in r.polygon]
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            out.append(
                f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" '
                f'font-size="15" font-weight="600" fill="{r.label_color}" '
                f'stroke="#00000055" stroke-width="0.6" '
                f'paint-order="stroke">{_esc(r.name)}</text>')

    out.append("</svg>")
    return "\n".join(out)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
