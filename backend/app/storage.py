"""Хранилище проектов: JSON на диске + история версий.

Для MVP локальная файловая система. Интерфейс намеренно простой,
чтобы позже заменить на PostgreSQL/S3 без переписывания логики.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .models import Project, Revision, new_id, now

# Каталог данных переопределяется переменной окружения — удобно для тестов
# и для развёртывания (например, смонтированный том).
DATA_DIR = Path(os.environ.get(
    "DATA_DIR", Path(__file__).resolve().parent.parent / "data")).resolve()
PROJECTS = DATA_DIR / "projects"
UPLOADS = DATA_DIR / "uploads"


def ensure_dirs() -> None:
    PROJECTS.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)


def project_dir(project_id: str) -> Path:
    return PROJECTS / project_id


def save(project: Project) -> Project:
    ensure_dirs()
    project.meta.updated_at = now()
    d = project_dir(project.project_id)
    (d / "revisions").mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(
        project.model_dump_json(indent=2), encoding="utf-8")
    return project


def load(project_id: str) -> Project:
    path = project_dir(project_id) / "project.json"
    if not path.exists():
        raise FileNotFoundError(project_id)
    return Project.model_validate_json(path.read_text(encoding="utf-8"))


def list_projects() -> list[dict]:
    ensure_dirs()
    out = []
    for d in sorted(PROJECTS.iterdir()):
        p = d / "project.json"
        if not p.exists():
            continue
        try:
            pr = Project.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "project_id": pr.project_id,
            "name": pr.meta.name,
            "updated_at": pr.meta.updated_at,
            "rooms": len(pr.rooms),
            "revisions": len(pr.revisions),
        })
    return out


def snapshot(state_before: Project, target: Project, command: str,
             summary: list[str]) -> Revision:
    """Сохранить состояние «до» правки и привязать версию к проекту.

    state_before — снимок для откáта, target — проект, в историю которого
    добавляется запись (тот, что сохраняется на диск).
    """
    ensure_dirs()
    rev = Revision(id=new_id("rev"), command=command, summary=summary)
    d = project_dir(target.project_id) / "revisions"
    d.mkdir(parents=True, exist_ok=True)
    safe_ts = rev.created_at.replace(":", "").replace("+", "Z")
    path = d / f"{safe_ts}_{rev.id}.json"
    path.write_text(state_before.model_dump_json(indent=2), encoding="utf-8")
    rev.snapshot = str(path.relative_to(DATA_DIR))
    target.revisions.append(rev)
    return rev


def restore(project_id: str, revision_id: str) -> Project:
    """Откат к указанной версии."""
    current = load(project_id)
    rev = next((r for r in current.revisions if r.id == revision_id), None)
    if rev is None:
        raise KeyError(revision_id)
    path = DATA_DIR / rev.snapshot
    restored = Project.model_validate_json(path.read_text(encoding="utf-8"))
    restored.revisions = current.revisions  # история не теряется
    return save(restored)


def duplicate(project_id: str, name: str | None = None) -> Project:
    """Копия проекта — удобно для вариантов планировки."""
    src = load(project_id)
    clone = src.model_copy(deep=True)
    clone.project_id = new_id("proj")
    clone.meta.name = name or f"{src.meta.name} (копия)"
    clone.meta.created_at = now()
    clone.revisions = []
    shutil.rmtree(project_dir(clone.project_id), ignore_errors=True)
    return save(clone)
