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

MAX_BODY = 64 * 1024  # формы крошечные; всё большее — не наше
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
                form = {k: v[0] for k, v in
                        parse_qs(body.decode("utf-8", "replace")).items()} if method == "POST" else {}
                query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                done = threading.Event()
                box: dict = {}
                server.requests.put((method, urlparse(self.path).path, {**query, **form},
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
                method, path, form, cookies, done, box = self.requests.get_nowait()
            except queue.Empty:
                return
            try:
                box["response"] = self.app.handle(method, path, form, cookies)
            except Exception as e:  # noqa: BLE001 — один запрос не должен уронить сервер
                box["response"] = (500, {}, f"Ошибка: {e}".encode("utf-8"))
            done.set()

    def stop(self):
        self.timer.stop()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
