import hashlib
import http.server
import threading
from datetime import date

import pytest

from app import updater
from app.db import Database, Delivery
from app.updater import Release, UpdateError, download, install, is_newer, parse_version, release_from_json

URL = updater.DOWNLOAD_PREFIX + "v1.7/FlowerDeliveries.exe"


def sample(**asset):
    a = {"name": "FlowerDeliveries.exe", "browser_download_url": URL, "size": 5,
         "digest": "sha256:ABCDEF"}
    a.update(asset)
    return {"tag_name": "v1.7", "assets": [a]}


def test_versions():
    assert parse_version("v1.10") == (1, 10)
    assert parse_version("dev") is None
    assert is_newer("1.10", "1.9")  # не по алфавиту
    assert not is_newer("1.5", "1.5")
    assert not is_newer("1.4", "1.5")
    assert not is_newer("1.7", "dev")  # сборку для разработки не трогаем
    assert not is_newer("мусор", "1.5")


def test_release_from_json():
    r = release_from_json(sample())
    assert (r.version, r.url, r.size, r.sha256) == ("1.7", URL, 5, "abcdef")
    assert release_from_json(sample(digest=None)).sha256 is None
    assert release_from_json({"tag_name": "v1.7", "assets": []}) is None
    assert release_from_json({"tag_name": "nightly", "assets": []}) is None


def test_foreign_download_url_is_ignored():
    assert release_from_json(sample(browser_download_url="https://evil.example/FlowerDeliveries.exe")) is None
    assert release_from_json(sample(name="other.exe")) is None


@pytest.fixture
def server(tmp_path):
    (tmp_path / "file.bin").write_bytes(b"hello")
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **k)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/file.bin"
    httpd.shutdown()


def test_download_checks_integrity(server, tmp_path):
    good = Release("1.7", server, 5, hashlib.sha256(b"hello").hexdigest())
    seen = []
    dest = tmp_path / "ok.exe"
    download(good, str(dest), seen.append)
    assert dest.read_bytes() == b"hello" and seen[-1] == 100

    bad = Release("1.7", server, 5, "0" * 64)
    with pytest.raises(UpdateError):
        download(bad, str(tmp_path / "bad.exe"))
    assert not (tmp_path / "bad.exe").exists()  # битый файл не остаётся

    short = Release("1.7", server, 99, None)
    with pytest.raises(UpdateError):
        download(short, str(tmp_path / "short.exe"))
    assert not (tmp_path / "short.exe").exists()


def test_install_swaps_files(tmp_path):
    current, new = tmp_path / "App.exe", tmp_path / "App.exe.new"
    current.write_bytes(b"old")
    new.write_bytes(b"new")
    install(str(current), str(new))
    assert current.read_bytes() == b"new"
    assert (tmp_path / "App.exe.old").read_bytes() == b"old"
    assert not new.exists()

    updater.cleanup_old(str(current))
    assert not (tmp_path / "App.exe.old").exists() and current.exists()


def test_install_rolls_back(tmp_path):
    current = tmp_path / "App.exe"
    current.write_bytes(b"old")
    with pytest.raises(OSError):
        install(str(current), str(tmp_path / "missing.new"))
    assert current.read_bytes() == b"old"  # старая версия на месте


def test_database_backup(tmp_path):
    db = Database(str(tmp_path / "d.db"))
    db.add(Delivery(day=date(2026, 10, 20), items="Розы"))
    db.backup(str(tmp_path / "copy.db"))
    db.close()
    copy = Database(str(tmp_path / "copy.db"))
    assert [d.items for d in copy.for_day(date(2026, 10, 20))] == ["Розы"]
    copy.close()
