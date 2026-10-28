"""Тесты: каждое требование из брифа — отдельная проверка.

Запуск:  pytest -q
"""
from __future__ import annotations

import math

import pytest

from app import commands, geometry, storage
from app.main import _apply_ops
from app.seed import build_demo

BRIEF = (
    "двери помеченные красным должны открываться наружу, "
    "диван в гостиной сделай бирюзового цвета, "
    "стол в гостиной сделай больше, чтобы поместились по 4 мягких стула "
    "с каждой стороны, "
    "мягкие стулья в гостиной сделай бирюзового цвета, "
    "на террасе стол сделай большой прямоугольный и за ним должно быть "
    "4 стула, рядом с диваном, стол и диван на террасе должны быть одни, "
    "стена между террасой и домом прозрачная из панорамных стеклопакетов "
    "в пол, в сауне полки слева, при этом печь должна остаться справа, "
    "подпиши все помещения кроме туалета белым цветом"
)

TEAL = "#2ec4b6"


@pytest.fixture(scope="module")
def project():
    p = build_demo()
    parsed = commands.parse(BRIEF, p)
    _apply_ops(p, parsed["operations"])
    storage.save(p)
    return p


def room(p, needle: str):
    return next(r for r in p.rooms if needle in r.name.lower())


# ------------------------------------------------------------------ двери
def test_marked_doors_open_outward(project):
    marked = [d for d in project.doors if d.marked]
    assert len(marked) == 3
    for d in marked:
        assert d.swing == "out", f"дверь {d.id} всё ещё открывается внутрь"


def test_unmarked_doors_untouched(project):
    unmarked = [d for d in project.doors if not d.marked]
    assert unmarked
    for d in unmarked:
        assert d.swing == "in"


# ------------------------------------------------------------------ цвет
def test_living_sofa_is_teal(project):
    living = room(project, "гостин")
    sofas = [f for f in project.furniture
             if f.kind == "sofa" and f.room_id == living.id]
    assert len(sofas) == 1
    assert sofas[0].color == TEAL


def test_living_chairs_are_teal(project):
    living = room(project, "гостин")
    chairs = [f for f in project.furniture
              if f.kind == "chair" and f.room_id == living.id]
    assert chairs and all(c.color == TEAL for c in chairs)


def test_terrace_furniture_stays_natural(project):
    """Цвет гостиной не должен «протечь» на террасу."""
    terrace = room(project, "террас")
    terrace_items = [f for f in project.furniture if f.room_id == terrace.id]
    assert terrace_items
    assert not any(f.color == TEAL for f in terrace_items)


# ------------------------------------------------- стол в гостиной
def test_living_table_fits_four_per_side(project):
    living = room(project, "гостин")
    table = next(f for f in project.furniture
                 if f.kind == "table" and f.room_id == living.id)
    chairs = [f for f in project.furniture
              if f.kind == "chair" and f.room_id == living.id
              and math.hypot(f.center[0] - table.center[0],
                             f.center[1] - table.center[1]) < table.width]
    assert len(chairs) == 8, f"ожидалось 8 стульев, найдено {len(chairs)}"
    above = [c for c in chairs if c.center[1] < table.center[1]]
    below = [c for c in chairs if c.center[1] > table.center[1]]
    assert len(above) == 4 and len(below) == 4
    assert table.width >= 4 * 0.45 + 0.3


def test_chairs_do_not_overlap_table(project):
    living = room(project, "гостин")
    table = next(f for f in project.furniture
                 if f.kind == "table" and f.room_id == living.id)
    tfp = geometry.item_footprint(table)
    for c in project.furniture:
        if c.kind == "chair" and c.room_id == living.id:
            assert not geometry.item_footprint(c).intersects(tfp)


# ------------------------------------------------------------------ терраса
def test_terrace_has_exactly_one_table_and_sofa(project):
    terrace = room(project, "террас")
    tables = [f for f in project.furniture
              if f.kind == "table" and f.room_id == terrace.id]
    sofas = [f for f in project.furniture
             if f.kind == "sofa" and f.room_id == terrace.id]
    assert len(tables) == 1 and len(sofas) == 1


def test_terrace_table_rectangular_with_four_chairs(project):
    terrace = room(project, "террас")
    table = next(f for f in project.furniture
                 if f.kind == "table" and f.room_id == terrace.id)
    assert table.width > table.depth, "стол должен быть прямоугольным"
    chairs = [f for f in project.furniture
              if f.kind == "chair" and f.room_id == terrace.id]
    assert len(chairs) == 4


# ------------------------------------------------------------- остекление
def test_terrace_wall_is_glass_floor_to_ceiling(project):
    glass = [w for w in project.walls if w.kind == "glass"]
    assert len(glass) == 1
    win = next(w for w in project.windows if w.wall_id == glass[0].id)
    assert win.kind == "panoramic"
    assert win.sill == 0.0, "остекление должно быть в пол"
    assert geometry.wall_length(glass[0]) > 10


# ------------------------------------------------------------------ сауна
def test_sauna_benches_left_stove_right(project):
    sauna = room(project, "саун")
    poly = geometry.room_polygon(sauna)
    minx, _, maxx, _ = poly.bounds
    mid = (minx + maxx) / 2
    benches = [f for f in project.furniture
               if f.kind == "bench" and f.room_id == sauna.id]
    stoves = [f for f in project.furniture
              if f.kind == "stove" and f.room_id == sauna.id]
    assert len(benches) == 2 and len(stoves) == 1
    for b in benches:
        assert b.center[0] < mid, "полка должна быть слева"
    assert stoves[0].center[0] > mid, "печь должна остаться справа"


def test_sauna_benches_do_not_overlap(project):
    sauna = room(project, "саун")
    benches = [f for f in project.furniture
               if f.kind == "bench" and f.room_id == sauna.id]
    a, b = benches
    assert not geometry.item_footprint(a).intersects(
        geometry.item_footprint(b))


# ---------------------------------------------------------------- подписи
def test_all_rooms_labelled_white_except_wc(project):
    for r in project.rooms:
        if "туалет" in r.name.lower():
            assert r.label_visible is False
        else:
            assert r.label_visible is True, f"{r.name} без подписи"
            assert r.label_color == "#ffffff"
            assert r.name.strip()


def test_expected_room_names(project):
    names = {r.name for r in project.rooms}
    for expected in ("Спальня 1", "Спальня 2", "Спальня 3",
                     "Сауна", "Терраса", "Кухня-гостиная"):
        assert expected in names


# ------------------------------------------- «остальное сохранить как есть»
def test_untouched_objects_preserved():
    p = build_demo()
    bedroom_ids = {r.id for r in p.rooms if "спальн" in r.name.lower()}
    before = {f.id: f.model_dump() for f in p.furniture
              if f.room_id in bedroom_ids}
    parsed = commands.parse(BRIEF, p)
    _apply_ops(p, parsed["operations"])
    after = {f.id: f.model_dump() for f in p.furniture
             if f.room_id in bedroom_ids}
    assert before.keys() == after.keys()
    for k in before:
        assert before[k] == after[k], f"мебель спальни {k} изменилась"


def test_validation_clean(project):
    result = geometry.validate(project)
    assert result["errors"] == []
    assert result["warnings"] == []


def test_brief_parses_without_clarifications(project):
    parsed = commands.parse(BRIEF, project)
    assert parsed["clarifications"] == []
    assert len(parsed["operations"]) >= 7


# ------------------------------------------------------------------ механика
def test_revert_restores_previous_state():
    p = build_demo()
    before = p.model_copy(deep=True)
    rev = storage.snapshot(before, p, "test", ["snapshot"])
    storage.save(p)
    parsed = commands.parse("двери помеченные красным наружу", p)
    _apply_ops(p, parsed["operations"])
    storage.save(p)
    assert any(d.swing == "out" for d in storage.load(p.project_id).doors)
    restored = storage.restore(p.project_id, rev.id)
    assert all(d.swing == "in" for d in restored.doors)


def test_svg_contains_labels_and_glass(project):
    from app import render2d
    svg = render2d.render_svg(project)
    assert "Кухня-гостиная" in svg
    assert "Терраса" in svg
    assert "Туалет" not in svg
    assert "#9fd8f5" in svg
    assert TEAL in svg


def test_dxf_and_obj_export(project):
    from app import export
    dxf = export.to_dxf(project)
    assert b"ROOMS" in dxf and b"WALLS" in dxf
    obj = export.to_obj(project)
    assert obj.startswith("# interior-ai")
    assert obj.count("\nv ") > 50
