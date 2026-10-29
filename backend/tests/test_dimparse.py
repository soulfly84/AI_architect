"""Тест: площади комнат точные, положение примерное.

Проверяем главное обещание: площадь каждой комнаты совпадает с подписью
на чертеже, даже если модель дала неточные координаты.
"""
from __future__ import annotations

import math

import pytest

from app import dimparse


def make_model_output(rooms, image=(800, 1000)):
    """Ответ модели в том виде, в каком он приходит."""
    w, h = image
    out = []
    for name, area, (x0, y0, x1, y1) in rooms:
        out.append({
            "name": name, "area_m2": area,
            "bbox_n": [x0 / w * 1000, y0 / h * 1000, x1 / w * 1000, y1 / h * 1000],
        })
    return {"rooms": out}


# реальный план БТИ: комнаты и их подписанные площади
PLAN = [
    ("6", 24.1, (440, 550, 690, 860)),
    ("7", 17.0, (250, 550, 440, 860)),
    ("1", 5.6, (390, 400, 490, 510)),
    ("3", 6.9, (360, 130, 460, 290)),
    ("5", 17.2, (520, 130, 690, 430)),
    ("2", 1.2, (355, 320, 420, 380)),
]


def test_areas_match_labels_exactly():
    data = make_model_output(PLAN)
    plan = dimparse.build(data, (800, 1000))
    assert len(plan.rooms) == len(PLAN)
    for room in plan.rooms:
        assert room.area == pytest.approx(room.area_m2, rel=0.01), \
            f"комната {room.name}: {room.area} вместо {room.area_m2}"


def test_rooms_do_not_overlap():
    data = make_model_output(PLAN)
    plan = dimparse.build(data, (800, 1000))
    for i, a in enumerate(plan.rooms):
        for b in plan.rooms[i + 1:]:
            ox = min(a.x1, b.x1) - max(a.x0, b.x0)
            oy = min(a.y1, b.y1) - max(a.y0, b.y0)
            assert ox <= 0.05 or oy <= 0.05, f"{a.name} и {b.name} перекрываются"


def test_relative_position_preserved():
    """Комната 6 должна остаться правее комнаты 7."""
    data = make_model_output(PLAN)
    plan = dimparse.build(data, (800, 1000))
    by = {r.name: r for r in plan.rooms}
    assert by["6"].cx > by["7"].cx
    assert by["5"].cy > by["1"].cy  # 5 выше 1


def test_tolerates_string_numbers():
    data = {"rooms": [{"name": "1", "area_m2": "5,6",
                       "bbox_n": [100, 100, 300, 300]}]}
    plan = dimparse.build(data, (800, 1000))
    assert plan.rooms[0].area_m2 == pytest.approx(5.6)


def test_skips_rooms_without_area():
    data = {"rooms": [
        {"name": "1", "area_m2": None, "bbox_n": [0, 0, 100, 100]},
        {"name": "2", "area_m2": 10.0, "bbox_n": [0, 0, 200, 200]},
    ]}
    plan = dimparse.build(data, (800, 1000))
    assert len(plan.rooms) == 1


def test_raises_when_nothing_to_build():
    with pytest.raises(ValueError):
        dimparse.build({"rooms": []}, (800, 1000))


def test_to_project_creates_rooms_and_walls():
    data = make_model_output(PLAN)
    plan = dimparse.build(data, (800, 1000))
    project = dimparse.to_project(plan, "тест")
    assert len(project.rooms) == len(PLAN)
    assert len(project.walls) >= len(PLAN) * 2  # хотя бы по две стены на комнату
    for w in project.walls:
        length = math.hypot(w.end[0] - w.start[0], w.end[1] - w.start[1])
        assert length > 0.3


def test_report_flags_unreliable_rooms():
    data = make_model_output(PLAN)
    plan = dimparse.build(data, (800, 1000))
    rep = dimparse.report(plan)
    assert rep["rooms"] == len(PLAN)
    assert rep["total_area_m2"] > 50
    assert all(d["ok"] for d in rep["details"])


def test_scale_is_consistent():
    data = make_model_output(PLAN)
    plan = dimparse.build(data, (800, 1000))
    # площади из подписей: 24.1+17.0+5.6+6.9+17.2+1.2 = 72.0
    assert plan.total_area == pytest.approx(72.0, rel=0.02)
    assert plan.px_per_m > 0
