"""HTTP-обёртка для web.py: сокеты — в своём потоке, база — только в главном потоке программы.

Как и боты, сервер не трогает базу сам: запрос кладётся в очередь, главный поток разбирает её
по таймеру (process_pending) и кладёт ответ обратно. Поэтому SQLite-«одно соединение на программу»
не ломается, а телефон просто ждёт ответа долю секунды.
"""

import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, QSettings, QTimer

from .web import WebApp

MAX_BODY = 12 * 1024 * 1024  # фото с телефона — до ~10 МБ (сожмём на той стороне)
WAIT_SECONDS = 10  # сколько телефон ждёт ответа главного потока


def _settings() -> QSettings:
    return QSettings("FlowerDeliveries", "FlowerDeliveries")


def web_enabled() -> bool:
    return str(_settings().value("web_enabled", "false")) == "true"


def web_port() -> int:
    try:
        return int(_settings().value("web_port", 8080) or 8080)
    except (TypeError, ValueError):
        return 8080


def set_web(enabled: bool, port: int):
    _settings().setValue("web_enabled", "true" if enabled else "false")
    _settings().setValue("web_port", int(port))


def local_ip() -> str:
    """Адрес компьютера в сети магазина. Пакеты никуда не уходят — UDP «соединение» фиктивное."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _parse_cookies(header: str) -> dict:
    out = {}
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name:
            out[name] = value
    return out


def parse_multipart(body: bytes, content_type: str) -> tuple[dict, dict]:
    """(поля, файлы) из multipart/form-data. файлы: имя поля → (имя файла, байты)."""
    fields, files = {}, {}
    boundary = content_type.partition("boundary=")[2].strip().strip('"').encode()
    if not boundary:
        return fields, files
    for part in body.split(b"--" + boundary):
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep or not data.endswith(b"\r\n"):
            continue
        data = data[:-2]  # CRLF перед следующей границей — разделитель, не содержимое
        disposition = ""
        for line in head.decode("utf-8", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disposition = line
        name = filename = ""
        for piece in disposition.split(";"):
            piece = piece.strip()
            if piece.startswith("name="):
                name = piece[5:].strip('"')
            elif piece.startswith("filename="):
                filename = piece[9:].strip('"')
        if not name:
            continue
        if filename:
            files[name] = (filename, data)
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


class WebServer(QObject):
    def __init__(self, app: WebApp, port: int = 8080, parent=None):
        super().__init__(parent)
        self.app = app
        self.port = port
        self.requests: queue.Queue = queue.Queue()
        self.httpd: ThreadingHTTPServer | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.process_pending)

    @property
    def actual_port(self) -> int:
        return self.httpd.server_address[1] if self.httpd else self.port

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def log_message(self, *args):  # тихо: access-log не нужен
                pass

            def _handle(self, method: str):
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                except ValueError:
                    length = 0
                body = self.rfile.read(length) if length else b""
                content_type = self.headers.get("Content-Type", "")
                files: dict = {}
                if method == "POST" and content_type.startswith("multipart/form-data"):
                    form, files = parse_multipart(body, content_type)
                elif method == "POST":
                    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
                else:
                    form = {}
                query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                done = threading.Event()
                box: dict = {}
                server.requests.put((method, urlparse(self.path).path, {**query, **form}, files,
                                     _parse_cookies(self.headers.get("Cookie", "")), done, box))
                if not done.wait(WAIT_SECONDS):
                    self._reply(503, {}, "Программа занята — обновите страницу.".encode("utf-8"))
                    return
                status, headers, data = box["response"]
                self._reply(status, headers, data)

            def _reply(self, status: int, headers: dict, data: bytes):
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.timer.start(100)

    def process_pending(self):
        """Разобрать накопившиеся запросы. Только из главного потока (таймер или тест)."""
        while True:
            try:
                method, path, form, files, cookies, done, box = self.requests.get_nowait()
            except queue.Empty:
                return
            try:
                box["response"] = self.app.handle(method, path, form, cookies, files)
            except Exception as e:  # noqa: BLE001 — один запрос не должен уронить сервер
                box["response"] = (500, {}, f"Ошибка: {e}".encode("utf-8"))
            done.set()

    def stop(self):
        self.timer.stop()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
