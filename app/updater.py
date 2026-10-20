"""Проверка и установка новых версий из релизов GitHub."""

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

REPO = "alprazolamkent-cloud/flower"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
# Имена файла программы в релизе по предпочтению. Старое имя остаётся в релизах, пока у людей стоят версии,
# которые ищут именно его: иначе они перестали бы видеть обновления.
ASSET_NAMES = ("AloFlorist.exe", "FlowerDeliveries.exe")
TIMEOUT = 20
CHUNK = 256 * 1024


class UpdateError(Exception):
    """Ошибка с понятным пользователю текстом."""


@dataclass
class Release:
    version: str
    url: str
    size: int = 0
    sha256: str | None = None


def parse_version(text: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(candidate: str, current: str) -> bool:
    """«dev» и любые непонятные версии никогда не считаются устаревшими."""
    new, cur = parse_version(candidate), parse_version(current)
    return new is not None and cur is not None and new > cur


def release_from_json(data: dict) -> Release | None:
    tag = str(data.get("tag_name", ""))
    if parse_version(tag) is None:
        return None
    assets = {a.get("name"): a for a in data.get("assets", [])}
    for name in ASSET_NAMES:
        asset = assets.get(name)
        url = str(asset.get("browser_download_url", "")) if asset else ""
        if url.startswith(DOWNLOAD_PREFIX):
            digest = str(asset.get("digest") or "")
            sha = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None
            return Release(tag.lstrip("v"), url, int(asset.get("size") or 0), sha)
    return None


def _open(url: str):
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "FlowerDeliveries"}
    )
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def fetch_latest() -> Release | None:
    """Последний релиз. Бросает исключение, если нет интернета."""
    with _open(API_URL) as response:
        return release_from_json(json.load(response))


def download(release: Release, dest: str, progress=None):
    """Скачать файл и проверить, что он целый. progress(процент) вызывается по ходу."""
    sha = hashlib.sha256()
    received = 0
    try:
        with _open(release.url) as response, open(dest, "wb") as out:
            total = int(response.headers.get("Content-Length") or release.size or 0)
            while chunk := response.read(CHUNK):
                out.write(chunk)
                sha.update(chunk)
                received += len(chunk)
                if progress and total:
                    progress(min(99, received * 100 // total))
        if release.size and received != release.size:
            raise UpdateError("Файл скачался не полностью. Попробуйте ещё раз.")
        if release.sha256 and sha.hexdigest() != release.sha256:
            raise UpdateError("Скачанный файл не прошёл проверку целостности.")
    except BaseException:
        if os.path.exists(dest):
            os.remove(dest)
        raise
    if progress:
        progress(100)


def install(current: str, new: str):
    """Подменить файл программы. Работающий .exe нельзя удалить, но можно переименовать."""
    old = current + ".old"
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        old = f"{current}.old{os.getpid()}"
    os.replace(current, old)
    try:
        os.replace(new, current)
    except OSError:
        os.replace(old, current)  # откат
        raise


def cleanup_old(current: str):
    for path in glob.glob(glob.escape(current) + ".old*") + [current + ".new"]:
        try:
            os.remove(path)
        except OSError:
            pass


def can_self_update() -> bool:
    return getattr(sys, "frozen", False) and sys.platform == "win32"


def relaunch(exe: str):
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    # Без этого новая копия попробует взять распакованные файлы старой, а те исчезнут при её выходе.
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([exe, "--updated"], env=env, close_fds=True, **kwargs)
