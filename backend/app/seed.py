"""Демонстрационный проект — реконструкция плана из брифа.

Служит тестовым набором: есть красные пометки (двери и стена к террасе),
сауна с печью, гостиная со столом, терраса с мебелью.
Запуск:  python -m app.seed  →  создаёт проект в DATA_DIR/projects/
"""
from __future__ import annotations

from . import storage
from .models import (Door, Furniture, Project, ProjectMeta, Room, Wall,
                     Window)

EXTERIOR, INTERNAL = 0.25, 0.12
H = 2.8


def _chairs_around(fx: float, fy: float, w: float, d: float,
                   per_side: int, room_id: str, color: str) -> list[Furniture]:
    out = []
    size = 0.45
    gap = 0.12
    for i in range(per_side):
        frac = (i + 0.5) / per_side
        lx = -w / 2 + w * frac
        out.append(Furniture(kind="chair", room_id=room_id, color=color,
                             center=[round(fx + lx, 2),
                                     round(fy - d / 2 - gap - size / 2, 2)],
                             width=size, depth=size, height=0.45))
        out.append(Furniture(kind="chair", room_id=room_id, color=color,
                             rotation=180,
                             center=[round(fx + lx, 2),
                                     round(fy + d / 2 + gap + size / 2, 2)],
                             width=size, depth=size, height=0.45))
    return out


def build_demo() -> Project:
    p = Project(meta=ProjectMeta(name="Квартира — тестовый план"))

    rooms = {
        "bed1": Room(name="Спальня 1",
                     polygon=[[0, 0], [4.6, 0], [4.6, 4.6], [0, 4.6]]),
        "bed2": Room(name="Спальня 2",
                     polygon=[[8.8, 0], [13, 0], [13, 4.2], [8.8, 4.2]]),
        "living": Room(name="Кухня-гостиная",
                       polygon=[[0, 4.6], [8.8, 4.6], [8.8, 8.4], [6.6, 8.4],
                                [6.6, 11], [0, 11]]),
        "sauna": Room(name="Сауна", polygon=[[9.4, 4.2], [12.6, 4.2],
                                             [12.6, 6.8], [9.4, 6.8]]),
        "wc": Room(name="Туалет", polygon=[[9.4, 6.8], [12.6, 6.8],
                                           [12.6, 8.4], [9.4, 8.4]]),
        "bed3": Room(name="Спальня 3", polygon=[[8.8, 8.4], [13, 8.4],
                                                [13, 11], [8.8, 11]]),
        "terrace": Room(name="Терраса",
                        polygon=[[0, 11], [13, 11], [13, 14.2], [0, 14.2]],
                        floor_material="deck"),
    }
    p.rooms = list(rooms.values())
    sid = {k: v.id for k, v in rooms.items()}

    def wall(a, b, kind="internal", thickness=INTERNAL, marked=None):
        w = Wall(start=list(a), end=list(b), kind=kind, thickness=thickness,
                 height=H, marked=marked)
        p.walls.append(w)
        return w

    w_top = wall((0, 0), (13, 0), "external", EXTERIOR)
    w_right = wall((13, 0), (13, 11), "external", EXTERIOR)
    # стена дом ↔ терраса: отмечена красным, должна стать стеклянной
    wall((13, 11), (0, 11), "external", EXTERIOR, marked="red-wall")
    w_left = wall((0, 11), (0, 0), "external", EXTERIOR)

    w_b1_bottom = wall((0, 4.6), (4.6, 4.6))
    wall((4.6, 0), (4.6, 4.6))
    wall((4.6, 4.6), (4.6, 8.4))
    wall((8.8, 0), (8.8, 8.4))
    w_b2_bottom = wall((8.8, 4.2), (13, 4.2))
    w_sauna_left = wall((9.4, 4.2), (9.4, 6.8))
    wall((9.4, 6.8), (12.6, 6.8))
    wall((9.4, 6.8), (9.4, 8.4))
    w_b3_top = wall((9.4, 8.4), (13, 8.4))
    w_living_bottom = wall((0, 11), (6.6, 11))

    p.doors = [
        Door(wall_id=w_b1_bottom.id, t=0.72, width=0.9, hinge="left",
             swing="in", room_from=sid["living"], room_to=sid["bed1"],
             marked="red-door-1"),
        Door(wall_id=w_b2_bottom.id, t=0.18, width=0.9, hinge="right",
             swing="in", room_from=sid["bed2"], room_to="hall",
             marked="red-door-2"),
        Door(wall_id=w_sauna_left.id, t=0.55, width=0.7, hinge="left",
             swing="in", room_from=sid["wc"], room_to=sid["sauna"],
             marked="red-door-3"),
        Door(wall_id=w_b3_top.id, t=0.25, width=0.9, hinge="right",
             swing="in", room_from=sid["bed3"], room_to="hall"),
        Door(wall_id=w_living_bottom.id, t=0.7, width=1.0, hinge="left",
             swing="in", kind="sliding", room_from=sid["living"],
             room_to=sid["terrace"]),
    ]

    p.windows = [
        Window(wall_id=w_top.id, t=0.12, width=1.6, height=1.5),
        Window(wall_id=w_top.id, t=0.30, width=1.6, height=1.5),
        Window(wall_id=w_top.id, t=0.70, width=2.0, height=1.5),
        Window(wall_id=w_top.id, t=0.90, width=1.8, height=1.5),
        Window(wall_id=w_left.id, t=0.30, width=1.4, height=1.5),
        Window(wall_id=w_left.id, t=0.80, width=1.8, height=1.5),
        Window(wall_id=w_right.id, t=0.55, width=0.8, height=0.6, sill=1.6),
    ]

    F = Furniture
    p.furniture = [
        F(kind="bed", room_id=sid["bed1"], center=[1.5, 1.2], width=1.6,
          depth=2.0, height=0.5, color="#cfc7bb"),
        F(kind="nightstand", room_id=sid["bed1"], center=[0.45, 0.45],
          width=0.45, depth=0.4, height=0.5, color="#a08a70"),
        F(kind="wardrobe", room_id=sid["bed1"], center=[3.9, 3.6], width=0.6,
          depth=1.8, height=2.2, color="#bdb6ab"),
        F(kind="bed", room_id=sid["bed2"], center=[11.1, 1.3], width=1.8,
          depth=2.1, height=0.5, color="#cfc7bb"),
        F(kind="nightstand", room_id=sid["bed2"], center=[12.4, 0.4],
          width=0.45, depth=0.4, height=0.5, color="#a08a70"),
        F(kind="bed", room_id=sid["bed3"], center=[10.6, 9.7], width=1.6,
          depth=2.0, height=0.5, color="#cfc7bb"),
        F(kind="sofa", room_id=sid["living"], center=[1.7, 5.5], width=2.6,
          depth=0.95, height=0.8, color="#d8d2c6"),
        F(kind="table", room_id=sid["living"], center=[3.6, 7.7], width=2.4,
          depth=1.0, height=0.75, color="#7a5a3a", seats=6),
        F(kind="kitchen", room_id=sid["living"], center=[7.9, 5.9],
          width=0.65, depth=2.8, height=0.9, color="#f0ece5"),
        F(kind="fridge", room_id=sid["living"], center=[7.9, 7.9],
          width=0.65, depth=0.7, height=1.9, color="#dcdcdc"),
        F(kind="tv", room_id=sid["living"], center=[0.3, 7.6], width=0.12,
          depth=1.6, height=0.7, color="#2b2b2b"),
        F(kind="stove", room_id=sid["sauna"], center=[12.1, 5.5], width=0.45,
          depth=0.45, height=1.0, color="#2b2b2b"),
        F(kind="wc", room_id=sid["wc"], center=[10.0, 7.6], width=0.4,
          depth=0.6, height=0.4, color="#ffffff"),
        F(kind="shower", room_id=sid["wc"], center=[11.8, 7.6], width=1.0,
          depth=1.0, height=0.05, color="#e8f4f8"),
        F(kind="sofa", room_id=sid["terrace"], center=[11.6, 13.4], width=2.2,
          depth=0.9, height=0.75, color="#d8d2c6"),
        F(kind="table", room_id=sid["terrace"], center=[9.6, 13.4], width=0.9,
          depth=0.9, height=0.7, color="#b08d5c"),
    ]
    p.furniture += _chairs_around(3.6, 7.7, 2.4, 1.0, 3, sid["living"],
                                  "#8a7050")

    return storage.save(p)


if __name__ == "__main__":
    pr = build_demo()
    print(f"Создан проект: {pr.project_id}  ({pr.meta.name})")
    print(f"Помещений: {len(pr.rooms)}, стен: {len(pr.walls)}, "
          f"дверей: {len(pr.doors)}, мебели: {len(pr.furniture)}")
