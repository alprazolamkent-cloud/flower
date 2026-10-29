"""AloFlorist — запуск приложения."""

import os
import sys
import time

from PySide6.QtCore import QLibraryInfo, QLocale, QStandardPaths, QTranslator
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from app import theme
from app.db import Database
from app.hang_log import HangLog, unsent_report
from app.main_window import MainWindow
from app.settings_dialog import theme_mode
from app.updater import cleanup_old

# Внутренние имена остались прежними, чтобы не потерять данные и настройки при переименовании программы:
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
    app.setApplicationName("AloFlorist")
    app.setStyle("Fusion")
    # Тему выбирает админ в настройках; свои подсветки программы перекрашиваются вместе с ней.
    theme.apply(app, theme_mode())
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
        from app.version import VERSION
        hang_log = HangLog(os.path.dirname(data_path()), VERSION)  # зависла / упала — где именно
    except Exception:  # noqa: BLE001 — без «чёрного ящика» программа всё равно работает
        hang_log = None
    try:
        db = Database(data_path())
    except Exception as e:  # noqa: BLE001 — показать пользователю понятную ошибку
        QMessageBox.critical(None, "Ошибка", f"Не удалось открыть базу данных:\n{e}")
        return 1

    window = MainWindow(db)
    report = unsent_report(hang_log.path) if hang_log else ""
    if report and ("не отвечает" in report or "Fatal Python error" in report):
        window.telegram.queue("⚠ Программа в магазине зависала или закрывалась с ошибкой. Для разработчика "
                              "(перешлите ему):\n\n" + report, key=f"hang:{int(time.time())}")
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

        def read():
            if bytes(sock.readAll()) == b"show":
                window.show_window()

        sock.readyRead.connect(read)
        if sock.bytesAvailable():  # «show» мог прийти раньше, чем подключили сигнал
            read()

    server.newConnection.connect(on_connection)
    server.listen(INSTANCE_NAME)
    code = app.exec()
    db.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
