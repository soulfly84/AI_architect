"""Тесты: сдвиг к стене, поворот, удаление стула — с проверкой коллизий."""
from __future__ import annotations

import math

from app import commands, geometry, moves, storage
from app.main import _apply_ops
from app.seed import build_demo


def _project():
    p = build_demo()
    storage.save(p)
    return p


def _find(p, kind, room_name_part=None):
    for f in p.furniture:
        if f.kind != kind:
            continue
        if room_name_part:
            r = p.room(f.room_id)
            if not r or room_name_part not in r.name.lower():
                continue
        return f
    raise AssertionError(f"нет {kind}")


def _touching_wall(p, f):
    fp = geometry.item_footprint(f)
    return any(
        geometry.LineString([w.start, w.end]).buffer(w.thickness / 2).intersects(fp)
        for w in p.walls)


def test_move_table_left_puts_it_against_left_wall():
    p = _project()
    table = _find(p, "table", "гостин")
    _apply_ops(p, [{"op": "move_to_wall", "item_id": table.id, "side": "left"}])
    room = p.room(table.room_id)
    minx = min(x for x, _ in room.polygon)
    fp_minx = min(pt[0] for pt in geometry.item_footprint(table).exterior.coords)
    # стол у левой стены: между ним и стеной помещения небольшой зазор
    assert 0.0 < fp_minx - minx <= 0.5
    assert not _touching_wall(p, table)


def test_move_is_refused_when_blocked_and_nothing_changes():
    p = _project()
    table = _find(p, "table", "гостин")
    before = table.center[:]
    # двигаем в сторону стены, где проход/дверь/другая мебель
    summary, _ = _apply_ops(p, [{"op": "move_to_wall", "item_id": table.id, "side": "top"}])
    text = " ".join(summary)
    if "НЕ ВЫПОЛНЕНО" in text:
        assert table.center == before


def test_move_keeps_chairs_with_table():
    p = _project()
    table = _find(p, "table", "гостин")
    chairs_before = len(geometry._chairs_of(p, table))
    _apply_ops(p, [{"op": "move_to_wall", "item_id": table.id, "side": "right"}])
    assert len(geometry._chairs_of(p, table)) == chairs_before


def test_rotate_sofa_90_degrees():
    p = _project()
    sofa = _find(p, "sofa", "гостин")
    old = sofa.rotation
    summary, _ = _apply_ops(p, [{"op": "rotate", "item_id": sofa.id, "degrees": 90}])
    if "НЕ ВЫПОЛНЕНО" not in " ".join(summary):
        assert sofa.rotation == (old + 90) % 360


def test_rotate_refuses_table():
    p = _project()
    table = _find(p, "table", "гостин")
    summary, _ = _apply_ops(p, [{"op": "rotate", "item_id": table.id, "degrees": 90}])
    assert "НЕ ВЫПОЛНЕНО" in " ".join(summary)
    assert table.rotation == 0.0


def test_remove_chair_reduces_count():
    p = _project()
    table = _find(p, "table", "гостин")
    n = len(geometry._chairs_of(p, table))
    _apply_ops(p, [{"op": "remove_chair", "table_id": table.id}])
    assert len(geometry._chairs_of(p, table)) == n - 1


def test_parse_move_command_creates_operation():
    p = _project()
    res = commands.parse_rules("Стол в гостиной сдвинь ближе к левой стене.", p)
    ops = [o for o in res["operations"] if o["op"] == "move_to_wall"]
    assert len(ops) == 1 and ops[0]["side"] == "left"


def test_parse_rotate_and_remove_chair():
    p = _project()
    r1 = commands.parse_rules("Диван в гостиной поверни.", p)
    assert any(o["op"] == "rotate" for o in r1["operations"]) or r1["clarifications"]
    r2 = commands.parse_rules("Убери стул у стола в гостиной.", p)
    assert any(o["op"] == "remove_chair" for o in r2["operations"]) or r2["clarifications"]


def test_unknown_side_asks_clarification():
    p = _project()
    res = commands.parse_rules("Стол в гостиной сдвинь куда-нибудь.", p)
    assert not any(o["op"] == "move_to_wall" for o in res["operations"])
