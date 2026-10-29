"""Веб-интерфейс: загрузка плана, расстановка мебели, 3D-просмотр.

Страница отдаётся из web/index.html, three.js раздаётся локально из
web/static — на слабом VPS внешний CDN может быть недоступен.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse

WEB = Path(__file__).resolve().parent.parent / "web"

MIME = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def index() -> HTMLResponse:
    path = WEB / "index.html"
    if not path.exists():
        raise HTTPException(500, "Интерфейс не найден на сервере")
    return HTMLResponse(path.read_text(encoding="utf-8"))


def static(rel_path: str) -> FileResponse:
    """Безопасная раздача статики: только из web/static."""
    base = (WEB / "static").resolve()
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(target, media_type=MIME.get(target.suffix, "application/octet-stream"))
