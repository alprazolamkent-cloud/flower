"""Резервные копии базы: всегда на компьютере и, если указана папка, на флешке (или в облачной папке)."""

import glob
import os
import shutil
import sqlite3
import tempfile
import zipfile
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


def without_photos(db_copy: str) -> str:
    """Копия базы без фото букетов, переписки и оплат (витрина остаётся): для Telegram, где предел 50 МБ.
    Полные копии с фото — на компьютере и флешке."""
    path = os.path.join(tempfile.mkdtemp(prefix="aloflorist-"), os.path.basename(db_copy))
    shutil.copyfile(db_copy, path)
    conn = sqlite3.connect(path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "delivery_photos" in tables:
            conn.execute("DELETE FROM delivery_photos")
            conn.execute("UPDATE client_messages SET photo=NULL")
            conn.execute("UPDATE client_orders SET paid_photo=NULL")
            conn.execute("UPDATE client_outbox SET photo=NULL, sent_at=COALESCE(sent_at, created_at)")
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    return path


def zip_copy(db_copy: str, now: datetime) -> str:
    """Архив копии для отправки в Telegram (база сжимается в несколько раз). Лежит во временной папке."""
    path = os.path.join(tempfile.gettempdir(), f"AloFlorist-backup-{now:%Y-%m-%d_%H-%M}.zip")
    light = without_photos(db_copy)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(light, os.path.basename(db_copy))
    finally:
        shutil.rmtree(os.path.dirname(light), ignore_errors=True)
    return path


def extract_db(zip_path: str) -> str:
    """Достать базу из архива-копии (из Telegram) во временную папку. Путь к извлечённому файлу."""
    from .db import UserError
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".db") and "/" not in n and "\\" not in n]
            if not names:
                raise UserError("В архиве нет файла копии (.db).")
            folder = tempfile.mkdtemp(prefix="aloflorist-")
            return z.extract(names[0], folder)
    except zipfile.BadZipFile:
        raise UserError("Архив повреждён или это не копия программы.") from None
