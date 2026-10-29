"""Тесты распознавания плана: синтетические картинки с известной геометрией."""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw
from shapely.geometry import Polygon

from app import planparse, view3d
from app.models import Furniture, Project, Room


def make_plan(rooms_x=(0, 240, 480), walls_y=(0, 200, 360),
              door_gap=(100, 140), wall_t=8, size=(480, 360)) -> Image.Image:
    """Простой план: наружные стены, вертикальная перегородка с проёмом,
    горизонтальная перегородка с проёмом."""
    W, H = size
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def wall(x1, y1, x2, y2):
        d.rectangle([x1 - wall_t // 2, y1 - wall_t // 2,
                     x2 + wall_t // 2, y2 + wall_t // 2], fill="black")

    wall(0, 0, W, 0)
    wall(0, H, W, H)
    wall(0, 0, 0, H)
    wall(W, 0, W, H)
    mid = rooms_x[1]
    wall(mid, 0, mid, 150)
    wall(mid, 190, mid, H)
    y = walls_y[1]
    wall(0, y, door_gap[0], y)
    wall(door_gap[1], y, mid, y)
    return img


def test_recognises_walls_and_rooms():
    """Перегородка x=240 делит план пополам, горизонтальная стена делит
    левую половину ещё на две — значит комнат три."""
    img = make_plan()
    project, report = planparse.build_project(img, "тест", plan_width_m=12)
    assert report["walls"] >= 6
    assert report["rooms"] == 3
    for r in project.rooms:
        assert Polygon(r.polygon).is_valid
        assert Polygon(r.polygon).area > 3


def test_doorways_do_not_merge_rooms():
    """Проём в стене не должен превращать две комнаты в одну."""
    img = make_plan()
    project, _ = planparse.build_project(img, "тест", plan_width_m=12)
    assert len(project.rooms) == 3
    polys = [Polygon(r.polygon) for r in project.rooms]
    total = sum(p.area for p in polys)
    largest = max(p.area for p in polys)
    # ни одна комната не должна занимать почти весь план: иначе комнаты
    # слиплись через проём и «комната» стала целым этажом
    assert largest < total * 0.6, (
        f"крупнейшая комната {largest:.1f} м² при общей {total:.1f} м²")


def test_scale_from_plan_width():
    img = make_plan()
    project, report = planparse.build_project(img, "тест", plan_width_m=12)
    # ширина плана 12 м при 480 px → 40 px/м
    assert 35 <= report["scale_px_per_m"] <= 45
    assert "12" in report["scale_source"]
    xs = [p[0] for r in project.rooms for p in r.polygon]
    assert max(xs) - min(xs) > 8  # план шире 8 м


def test_scale_defaults_when_not_given():
    img = make_plan()
    _, report = planparse.build_project(img, "тест")
    assert "оценка" in report["scale_source"]


def test_doorways_are_sealed_but_not_lost():
    """Проёмы заделываются для поиска комнат, но на плане не остаётся дыр:
    каждая комната замкнута."""
    img = make_plan()
    project, _ = planparse.build_project(img, "тест", plan_width_m=12)
    for r in project.rooms:
        assert Polygon(r.polygon).is_valid
        assert Polygon(r.polygon).area > 3


def test_rooms_do_not_overlap():
    img = make_plan()
    project, _ = planparse.build_project(img, "тест", plan_width_m=12)
    polys = [Polygon(r.polygon) for r in project.rooms]
    for i, a in enumerate(polys):
        for b in polys[i + 1:]:
            assert a.intersection(b).area < 0.01


def test_blank_image_raises():
    img = Image.new("RGB", (400, 300), "white")
    with pytest.raises(ValueError):
        planparse.build_project(img, "пусто")


def test_too_few_walls_raises():
    img = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 390, 18], fill="black")
    with pytest.raises(ValueError):
        planparse.build_project(img, "одна стена")


def test_load_image_accepts_png_bytes():
    img = make_plan()
    buf = io.BytesIO()
    img.save(buf, "PNG")
    loaded = planparse.load_image(buf.getvalue(), "plan.png")
    assert loaded.size == img.size


def test_load_image_rejects_garbage():
    with pytest.raises(ValueError):
        planparse.load_image(b"not an image at all", "plan.png")


def test_view3d_scene_has_rooms_and_walls():
    img = make_plan()
    project, _ = planparse.build_project(img, "тест", plan_width_m=12)
    scene = view3d.scene(project)
    assert len(scene["rooms"]) == 3
    assert len(scene["walls"]) >= 6
    for r in scene["rooms"]:
        assert len(r["vertices"]) >= 3
        assert len(r["faces"]) >= 1
        for f in r["faces"]:
            assert len(f) == 3
            for i in f:
                assert 0 <= i < len(r["vertices"])


def test_room_for_point_finds_room():
    project = Project()
    project.rooms = [Room(name="A", polygon=[[0, 0], [4, 0], [4, 4], [0, 4]])]
    assert view3d.room_for_point(project, 2, 2) is not None
    assert view3d.room_for_point(project, 9, 9) is None


def test_furniture_gets_height_from_kind():
    project = Project()
    project.furniture = [Furniture(kind="wardrobe", center=[1, 1],
                                   width=0.6, depth=1.8)]
    scene = view3d.scene(project)
    # высота по умолчанию для шкафа, а не 0
    assert scene["furniture"][0]["height"] > 1.5
