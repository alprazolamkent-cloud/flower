"""Резервные копии базы: всегда на компьютере и, если указана папка, на флешке (или в облачной папке)."""

import glob
import os
from dataclasses import dataclass
from datetime import datetime

PREFIX = "deliveries-"
LOCAL_FOLDER = "Резервные копии"
EXTRA_FOLDER = "Доставки цветов — резервные копии"  # прежнее имя: на флешках уже лежат копии под ним
DEFAULT_KEEP = 30


@dataclass
class BackupResult:
    local: str
    extra: str | None = None
    extra_error: str | None = None


def local_dir(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), LOCAL_FOLDER)


def _copies(folder: str) -> list[str]:
    return sorted(glob.glob(os.path.join(glob.escape(folder), PREFIX + "*.db")))


def prune(folder: str, keep: int):
    """Оставить только keep последних копий (имена с датой сортируются по времени)."""
    for path in _copies(folder)[:-keep] if keep > 0 else []:
        try:
            os.remove(path)
        except OSError:
            pass


def _copy_to(db, folder: str, now: datetime, keep: int) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{PREFIX}{now:%Y-%m-%d_%H-%M-%S}.db")
    db.backup(path)
    prune(folder, keep)
    return path


def make_backups(db, extra_dir: str, keep: int, now: datetime) -> BackupResult:
    result = BackupResult(_copy_to(db, local_dir(db.path), now, keep))
    if extra_dir:
        if not os.path.isdir(extra_dir):
            result.extra_error = "папка не найдена — флешка не вставлена?"
        else:
            try:
                result.extra = _copy_to(db, os.path.join(extra_dir, EXTRA_FOLDER), now, keep)
            except OSError as e:
                result.extra_error = str(e)
    return result


def latest(folder: str) -> str | None:
    copies = _copies(folder)
    return copies[-1] if copies else None
