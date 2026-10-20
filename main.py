"""Доставки цветов — запуск приложения."""

import os
import sys

from PySide6.QtCore import QLibraryInfo, QLocale, QStandardPaths, QTranslator
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from app.db import Database
from app.main_window import MainWindow

APP_FOLDER = "Доставки цветов"
INSTANCE_NAME = "FlowerDeliveries-single-instance"


def notify_running_instance() -> bool:
    """Если программа уже запущена — попросить её показать окно и вернуть True."""
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_NAME)
    if not socket.waitForConnected(500):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


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
    if "--minimized" not in sys.argv:
        window.show()

    QLocalServer.removeServer(INSTANCE_NAME)  # остаток после аварийного завершения
    server = QLocalServer()
    server.newConnection.connect(lambda: (server.nextPendingConnection(), window.show_window()))
    server.listen(INSTANCE_NAME)
    code = app.exec()
    db.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
