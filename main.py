"""Доставки цветов — запуск приложения."""

import os
import sys
import time

from PySide6.QtCore import QLibraryInfo, QLocale, QStandardPaths, QTranslator
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from app.db import Database
from app.main_window import MainWindow
from app.updater import cleanup_old

APP_FOLDER = "Доставки цветов"
INSTANCE_NAME = "FlowerDeliveries-single-instance"


def notify_running_instance(message: bytes = b"show") -> bool:
    """Если программа уже запущена — отправить ей сообщение и вернуть True."""
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_NAME)
    if not socket.waitForConnected(500):
        return False
    if message:
        socket.write(message)
        socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def wait_for_previous_instance(seconds: float = 10):
    """После обновления старая копия ещё закрывается — дождаться, пока она уйдёт."""
    deadline = time.monotonic() + seconds
    while notify_running_instance(b"") and time.monotonic() < deadline:
        time.sleep(0.25)


def data_path() -> str:
    """База лежит в «Документы/Доставки цветов», чтобы её было легко найти и скопировать."""
    docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    folder = os.path.join(docs, APP_FOLDER)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "deliveries.db")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Доставки цветов")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)  # при закрытии окна остаёмся в трее ради напоминаний

    if getattr(sys, "frozen", False):
        cleanup_old(sys.executable)  # остатки прежней версии после обновления
    if "--updated" in sys.argv:
        wait_for_previous_instance()
    if notify_running_instance():
        return 0

    QLocale.setDefault(QLocale(QLocale.Russian, QLocale.Russia))
    translator = QTranslator()
    if translator.load(QLocale(), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(translator)

    try:
        db = Database(data_path())
    except Exception as e:  # noqa: BLE001 — показать пользователю понятную ошибку
        QMessageBox.critical(None, "Ошибка", f"Не удалось открыть базу данных:\n{e}")
        return 1

    window = MainWindow(db)
    # При автозапуске окно (и вход) появятся, когда откроют программу из трея.
    if "--minimized" not in sys.argv:
        if not window.login():
            db.close()
            return 0
        window.show_window()

    QLocalServer.removeServer(INSTANCE_NAME)  # остаток после аварийного завершения
    server = QLocalServer()

    def on_connection():
        sock = server.nextPendingConnection()
        sock.readyRead.connect(lambda: window.show_window() if bytes(sock.readAll()) == b"show" else None)

    server.newConnection.connect(on_connection)
    server.listen(INSTANCE_NAME)
    code = app.exec()
    db.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
