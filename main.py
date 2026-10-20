"""Доставки цветов — запуск приложения."""

import os
import sys

from PySide6.QtCore import QLibraryInfo, QLocale, QStandardPaths, QTranslator
from PySide6.QtWidgets import QApplication, QMessageBox

from app.db import Database
from app.main_window import MainWindow

APP_FOLDER = "Доставки цветов"


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
    window.show()
    code = app.exec()
    db.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
