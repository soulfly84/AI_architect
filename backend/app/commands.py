"""Разбор текстовых команд в структурированные операции.

Два режима:
1. LLM-провайдер (если задан API-ключ) — модель возвращает JSON-план операций.
2. Правила (fallback) — работает офлайн, без ключа, детерминированно.

Оба режима возвращают один и тот же формат:
    {"operations": [...], "clarifications": [...]}

Операции никогда не меняют пиксели — только параметры объектов.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .models import Project

SCHEMA_HELP = """Ты — движок правок планировки. Получаешь описание проекта и команду.
Верни СТРОГО JSON: {"operations": [...], "clarifications": [...]}

Доступные операции:
  {"op":"door_swing","door_ids":[...],"swing":"in|out"}
  {"op":"door_hinge","door_ids":[...],"hinge":"left|right"}
  {"op":"set_color","item_ids":[...],"color":"#rrggbb","cascade":true}
  {"op":"resize_table","table_id":"...","per_side":4}
  {"op":"glazed_wall","wall_id":"..."}
  {"op":"sauna_benches","room_id":"..."}
  {"op":"dedupe_room","room_id":"...","kinds":["table","sofa"]}
  {"op":"room_label","room_id":"...","text":"...","color":"#ffffff","visible":true}

Правила: меняй только то, о чём просят; остальное не трогай.
Если объекта нет в проекте — не выдумывай ID, добавь вопрос в clarifications.
"""

COLOR_WORDS = {
    "бирюзов": "#2ec4b6", "turquoise": "#2ec4b6", "teal": "#2ec4b6",
    "зелен": "#4e9f3d", "green": "#4e9f3d",
    "бежев": "#d8c9b0", "beige": "#d8c9b0",
    "сер": "#8a8a8a", "gray": "#8a8a8a", "grey": "#8a8a8a",
    "бел": "#f5f5f5", "white": "#f5f5f5",
    "черн": "#2b2b2b", "black": "#2b2b2b",
    "син": "#2d5f9a", "blue": "#2d5f9a",
    "красн": "#c0392b", "red": "#c0392b",
    "терракот": "#b5651d",
}


def _color_from_text(text: str) -> str | None:
    low = text.lower()
    for key, hexv in COLOR_WORDS.items():
        if key in low:
            return hexv
    m = re.search(r"#([0-9a-fA-F]{6})", text)
    return f"#{m.group(1)}" if m else None


def _match_rooms(project: Project, frag: str) -> set[str]:
    """Найти помещения по упоминанию — по основе названия и синонимам."""
    hits: set[str] = set()
    for r in project.rooms:
        name = r.name.lower()
        if name[:5] and name[:5] in frag:
            hits.add(r.id)
        for alias in ("гостин", "кухн", "террас", "терас", "саун", "спаль",
                      "туалет", "санузел"):
            if alias in frag and alias in name:
                hits.add(r.id)
    return hits


def _clauses(low: str, project: Project) -> list[str]:
    """Разбить команду на смысловые фрагменты.

    Фрагмент без упоминания помещения склеивается с предыдущим: так
    «…сделай больше, чтобы поместились по 4 стула» остаётся одной мыслью.
    """
    raw = re.split(r"[,.;]|(?=\sи\s)|(?=\sа\s)", low)
    out: list[str] = []
    for frag in raw:
        frag = frag.strip()
        if not frag:
            continue
        if out and not _match_rooms(project, frag):
            out[-1] = out[-1] + " " + frag
        else:
            out.append(frag)
    return out


# --------------------------------------------------------------------------
# Rule-based разбор
# --------------------------------------------------------------------------


def parse_rules(command: str, project: Project) -> dict[str, Any]:
    low = command.lower()
    ops: list[dict] = []
    clar: list[str] = []

    # 1. Двери наружу / внутрь
    if re.search(r"двер\w*.*(наруж|outside|outward)", low) or \
       re.search(r"открыва\w*.*наруж", low):
        marked = [d.id for d in project.doors if d.marked]
        swing = "out"
        if re.search(r"(обратно|внутрь|inward)", low) and "наруж" not in low:
            swing = "in"
        if marked:
            ops.append({"op": "door_swing", "door_ids": marked,
                        "swing": swing})
        else:
            clar.append("Не нашёл дверей с красной пометкой. "
                        "Укажите ID дверей или отметьте их на плане.")
    elif re.search(r"двер\w*.*внутр", low):
        marked = [d.id for d in project.doors if d.marked]
        if marked:
            ops.append({"op": "door_swing", "door_ids": marked,
                        "swing": "in"})

    # 2. Цвет мебели — по фрагментам, чтобы цвет не переходил
    #    на объекты из другого помещения
    for frag in _clauses(low, project):
        color = _color_from_text(frag)
        if not color or not re.search(
                r"(диван|sofa|стул|chair|стол|table|мебел|полк)", frag):
            continue
        room_ids = _match_rooms(project, frag)
        base = [f for f in project.furniture
                if not room_ids or f.room_id in room_ids]
        targets: list[str] = []
        cascade = False
        if re.search(r"диван|sofa", frag):
            targets += [f.id for f in base if f.kind == "sofa"]
            cascade = True
        if re.search(r"стул|chair", frag):
            targets += [f.id for f in base if f.kind == "chair"]
        if re.search(r"стол|table", frag) and not targets:
            targets += [f.id for f in base if f.kind == "table"]
        if re.search(r"мебел", frag):
            targets += [f.id for f in base
                        if f.kind in ("sofa", "chair", "table")]
        if targets:
            ops.append({"op": "set_color", "item_ids": sorted(set(targets)),
                        "color": color, "cascade": cascade})

    # 3. Увеличение стола под N стульев
    if re.search(r"стол", low):
        for frag in _clauses(low, project):
            if not re.search(r"стол", frag):
                continue
            if not re.search(r"(увелич|больш|bigger|larger|expand|прямоуг)",
                             frag):
                continue
            room_ids = _match_rooms(project, frag)
            candidates = [f for f in project.furniture if f.kind == "table"
                          and (not room_ids or f.room_id in room_ids)]
            # «по 4 стула с каждой стороны» → 4 на сторону;
            # «4 стула» без уточнения → 4 всего, то есть 2 на сторону
            per = 2
            m_side = re.search(
                r"по\s*(\d)\s*(?:мягк\w*\s*)?(?:стул|chair)", frag)
            m_total = re.search(
                r"(\d)\s*(?:мягк\w*\s*)?(?:стул|chair)", frag)
            if m_side or "с каждой стороны" in frag:
                per = int((m_side or m_total).group(1))
            elif m_total:
                per = max(1, int(m_total.group(1)) // 2)
            for t in candidates:
                ops.append({"op": "resize_table", "table_id": t.id,
                            "per_side": per})
            if re.search(r"(одни|один|одна|только один|без лишн)", frag):
                for rid in {t.room_id for t in candidates if t.room_id}:
                    ops.append({"op": "dedupe_room", "room_id": rid,
                                "kinds": ["table", "sofa"]})

    # 4. Панорамное остекление стены
    if re.search(r"(панорам|стеклопакет|остекл|glass|прозрачн)", low):
        walls = [w for w in project.walls if w.marked]
        if not walls:
            from shapely.geometry import LineString

            from .geometry import room_polygon
            terrace = next((r for r in project.rooms
                            if "террас" in r.name.lower()
                            or "терас" in r.name.lower()), None)
            if terrace:
                poly = room_polygon(terrace)
                for w in project.walls:
                    if LineString([w.start, w.end]).intersects(poly.exterior):
                        walls.append(w)
        for w in walls:
            ops.append({"op": "glazed_wall", "wall_id": w.id})
        if not walls:
            clar.append("Не определил стену для остекления — "
                        "нужна пометка на плане.")

    # 5. Полки в сауне
    if re.search(r"(полк|bench)", low) and re.search(r"саун|sauna", low):
        saunas = [r for r in project.rooms if "саун" in r.name.lower()]
        if saunas:
            ops.append({"op": "sauna_benches", "room_id": saunas[0].id})
        else:
            clar.append("Помещение «Сауна» не найдено.")

    # 6. Подписи помещений белым
    m_label = re.search(r"подпиш|подпис|label|назван", low)
    if m_label:
        tail = low[m_label.start():]
        clauses = re.split(r"[,.;]|(?=кроме)|(?=без)", tail)
        hidden_ids: set[str] = set()
        wanted_rooms: set[str] = set()
        for frag in clauses:
            if re.search(r"(кроме|без|не\s*подпис)", frag):
                after = re.split(r"(?:кроме|без|не\s*подпис\w*)",
                                 frag, maxsplit=1)[-1]
                hidden_ids |= _match_rooms(project, after)
                continue
            wanted_rooms |= _match_rooms(project, frag)
        targets = (wanted_rooms if wanted_rooms
                   else {r.id for r in project.rooms}) - hidden_ids
        for rid in sorted(hidden_ids):
            ops.append({"op": "room_label", "room_id": rid, "text": None,
                        "color": "#ffffff", "visible": False})
        for rid in sorted(targets):
            room = project.room(rid)
            ops.append({"op": "room_label", "room_id": rid,
                        "text": room.name if room else None,
                        "color": "#ffffff", "visible": True})

    return {"operations": ops, "clarifications": clar, "engine": "rules"}


# --------------------------------------------------------------------------
# LLM-провайдер
# --------------------------------------------------------------------------


def project_brief(project: Project, limit: int = 120) -> str:
    """Компактное описание проекта для модели."""
    lines = []
    for r in project.rooms:
        lines.append(f"room {r.id} name='{r.name}'")
    for w in project.walls[:limit]:
        lines.append(f"wall {w.id} {w.start}->{w.end} kind={w.kind} "
                     f"marked={w.marked}")
    for d in project.doors:
        lines.append(f"door {d.id} wall={d.wall_id} swing={d.swing} "
                     f"hinge={d.hinge} marked={d.marked}")
    for x in project.furniture[:limit]:
        lines.append(f"furniture {x.id} kind={x.kind} room={x.room_id} "
                     f"size={x.width}x{x.depth} color={x.color}")
    return "\n".join(lines)


def parse_llm(command: str, project: Project) -> dict[str, Any]:
    """Вызов внешней модели. Ключ берётся только из переменных окружения."""
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    if not api_key:
        raise RuntimeError("LLM_API_KEY не задан")

    import httpx

    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SCHEMA_HELP},
            {"role": "user", "content": f"ПРОЕКТ:\n{project_brief(project)}"
                                        f"\n\nКОМАНДА:\n{command}"},
        ],
    }
    with httpx.Client(timeout=60) as client:
        r = client.post(f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    data["engine"] = "llm"
    data.setdefault("operations", [])
    data.setdefault("clarifications", [])
    return data


def parse(command: str, project: Project) -> dict[str, Any]:
    """Основная точка входа: LLM, при отсутствии ключа — правила."""
    if os.environ.get("LLM_ENABLED", "0") == "1":
        try:
            return parse_llm(command, project)
        except Exception as exc:  # noqa: BLE001
            fallback = parse_rules(command, project)
            fallback["clarifications"].append(
                f"Модель недоступна ({exc.__class__.__name__}), "
                f"использован детерминированный разбор.")
            return fallback
    return parse_rules(command, project)
