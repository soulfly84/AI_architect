"""Распознавание плана через vision-модель VseGPT: площади точные.

Разделение работы:
  - модель читает ЧИСЛА (площади, размеры) и приблизительно место комнат;
  - код строит геометрию так, чтобы площадь каждой комнаты совпала с подписью.

Почему не «всё через модель»: она сжимает координаты на 20-40% и площадь
плывёт. Почему не «всё через пиксели»: на планах БТИ стены заштрихованы,
штриховка неотличима от множества коротких стен.

Модель берётся из переменных окружения: PLAN_MODEL (по умолчанию
vis-google/gemini-2.5-flash), ключ — PLAN_API_KEY или ~/.vsegpt_key.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from pathlib import Path

import httpx
from PIL import Image

from . import dimparse
from .models import Project

DEFAULT_MODEL = "vis-google/gemini-2.5-flash"
DEFAULT_BASE = "https://api.vsegpt.ru/v1"
MAX_SIDE = 1100
REQUEST_TIMEOUT = 240

PROMPT = """Это план квартиры или дома (техпаспорт БТИ, чертёж).

ЗАДАЧА: найти все помещения и вернуть их прямоугольники с площадями.

ПРАВИЛА:
1. Площадь (area_m2) — ЧИСЛО, подписанное рядом с номером комнаты (например "5.6", "17,2"). Если площади нет — поставь null.
2. bbox_n — прямоугольник комнаты в НОРМАЛИЗОВАННЫХ координатах от 0 до 1000.
   X: 0 — левый край картинки, 1000 — правый.
   Y: 0 — ВЕРХНИЙ край, 1000 — НИЖНИЙ.
   НЕ используй пиксели, только доли 0..1000.
3. Бери внутреннюю поверхность стен комнаты, без толщины стен.
4. Не выдумывай помещения, которых нет. Не пропускай те, что есть.
5. Комната может быть не прямоугольной — укажи описывающий прямоугольник.

Ответ СТРОГО JSON, без markdown, без пояснений:
{"rooms":[{"name":"1","area_m2":5.6,"bbox_n":[120,380,260,560]}],
 "px_per_m":null,
 "notes":"что не удалось прочитать"}"""


class PlanRecognitionError(Exception):
    """План не распознан — с понятной причиной для пользователя."""


def _api_key() -> str:
    key = os.environ.get("PLAN_API_KEY")
    if key:
        return key.strip()
    path = Path.home() / ".vsegpt_key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    raise PlanRecognitionError(
        "Не задан ключ модели. Сохраните его в ~/.vsegpt_key на сервере "
        "или в переменной PLAN_API_KEY.")


def _prepare(img: Image.Image) -> tuple[str, tuple[int, int], int]:
    """Сжать картинку и вернуть base64, размер и вес в килобайтах."""
    w, h = img.size
    k = MAX_SIDE / max(w, h)
    if k < 1:
        img = img.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    data = buf.getvalue()
    return base64.b64encode(data).decode(), img.size, len(data) // 1024


def _call(b64: str, model: str, base: str, key: str) -> dict:
    payload = {
        "model": model, "temperature": 0, "max_tokens": 3000,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + b64}},
        ]}],
    }
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            r = client.post(f"{base}/chat/completions",
                            headers={"Authorization": f"Bearer {key}",
                                     "Content-Type": "application/json",
                                     "X-Title": "interior-ai-plan"},
                            json=payload)
    except httpx.HTTPError as exc:
        raise PlanRecognitionError(f"Модель недоступна: {exc}") from exc

    if r.status_code != 200:
        detail = ""
        try:
            detail = r.json().get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            detail = r.text[:200]
        raise PlanRecognitionError(f"Модель отказала ({r.status_code}): {detail}")
    return r.json()


def _extract_json(text: str) -> dict:
    """Достать JSON из ответа модели, даже если он обёрнут в ```json."""
    text = (text or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lower().startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise PlanRecognitionError(
        "Модель вернула ответ не в формате JSON. Попробуйте другой план "
        "или смените модель.")


def recognize(img: Image.Image,
              name: str = "План из чертежа") -> tuple[Project, dict]:
    """Распознать план. Возвращает (проект, отчёт)."""
    key = _api_key()
    model = os.environ.get("PLAN_MODEL", DEFAULT_MODEL)
    base = os.environ.get("PLAN_API_BASE", DEFAULT_BASE).rstrip("/")

    b64, size, kb = _prepare(img)
    started = time.time()
    raw = _call(b64, model, base, key)
    elapsed = round(time.time() - started, 1)

    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise PlanRecognitionError("Пустой ответ модели") from exc

    data = _extract_json(content)
    if not data.get("rooms"):
        notes = data.get("notes") or ""
        raise PlanRecognitionError(
            "Модель не нашла на плане ни одного помещения с подписанной "
            "площадью. Нужен план БТИ или чертёж, где площади подписаны."
            + (f" ({notes})" if notes else ""))

    try:
        plan = dimparse.build(data, size)
    except ValueError as exc:
        raise PlanRecognitionError(str(exc)) from exc

    project = dimparse.to_project(plan, name=name)
    project.meta.name = name
    project.meta.scale_px_per_m = plan.px_per_m

    rep = dimparse.report(plan)
    usage = raw.get("usage") or {}
    rep.update({
        "model": model,
        "seconds": elapsed,
        "image_kb": kb,
        "image_px": list(size),
        "tokens": usage.get("total_tokens"),
        "notes": data.get("notes") or "",
    })
    return project, rep
