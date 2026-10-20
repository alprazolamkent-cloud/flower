"""Окна и фоновые потоки для обновления программы."""

import os
import sys

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressDialog, QPushButton,
)

from . import updater
from .updater import Release, UpdateError, is_newer, parse_version
from .version import VERSION

CHECK_EVERY_MS = 6 * 60 * 60 * 1000
FIRST_CHECK_MS = 5_000


class CheckThread(QThread):
    done = Signal(object, str)  # (Release | None, текст ошибки)

    def run(self):
        try:
            self.done.emit(updater.fetch_latest(), "")
        except Exception as e:  # noqa: BLE001 — любая сетевая ошибка = «не удалось проверить»
            self.done.emit(None, str(e))


class DownloadThread(QThread):
    progress = Signal(int)
    done = Signal(str)  # текст ошибки, пусто — всё хорошо

    def __init__(self, release: Release, dest: str, parent=None):
        super().__init__(parent)
        self.release, self.dest = release, dest

    def run(self):
        try:
            updater.download(self.release, self.dest, self.progress.emit)
            self.done.emit("")
        except UpdateError as e:
            self.done.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.done.emit(f"Не удалось скачать обновление: {e}")


class UpdateController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending: Release | None = None  # нашли, пока окно было скрыто
        self.declined: str | None = None  # от какой версии отказались в этот раз
        self.check_thread: CheckThread | None = None
        self.download_thread: DownloadThread | None = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.check(manual=False))
        self.timer.start(CHECK_EVERY_MS)
        if parse_version(VERSION) is not None:
            QTimer.singleShot(FIRST_CHECK_MS, lambda: self.check(manual=False))

    def build_box(self) -> QGroupBox:
        box = QGroupBox("Программа")
        box.setToolTip("Новые версии устанавливаются сами. Записи, сотрудники и настройки сохраняются.")
        layout = QHBoxLayout(box)
        shown = VERSION if parse_version(VERSION) is not None else "разработка"
        layout.addWidget(QLabel(f"Версия: <b>{shown}</b>"))
        layout.addStretch(1)
        button = QPushButton("🔄 Проверить обновления")
        button.clicked.connect(lambda: self.check(manual=True))
        layout.addWidget(button)
        return box

    # ----- проверка -----

    def check(self, manual: bool):
        if self.check_thread is not None and self.check_thread.isRunning():
            return
        self.check_thread = CheckThread(self)
        self.check_thread.done.connect(lambda release, error: self._checked(release, error, manual))
        self.check_thread.start()

    def _checked(self, release: Release | None, error: str, manual: bool):
        w = self.window
        if error:
            if manual:
                QMessageBox.warning(w, "Обновление",
                                    f"Не удалось проверить обновления. Проверьте интернет.\n\n{error}")
            return
        if parse_version(VERSION) is None:
            if manual:
                QMessageBox.information(w, "Обновление", "Это сборка для разработки, обновление недоступно.")
            return
        if release is None or not is_newer(release.version, VERSION):
            if manual:
                QMessageBox.information(w, "Обновление", f"Установлена последняя версия ({VERSION}).")
            return
        if manual:
            self.offer(release)
        elif release.version != self.declined:
            if w.isVisible() and w.user is not None:
                self.offer(release)
            else:  # окно свёрнуто в трей или ещё идёт вход
                self.pending = release
                w.tray.showMessage("Доступно обновление",
                                   f"Вышла версия {release.version}. Откройте программу, чтобы обновиться.",
                                   w.tray.MessageIcon.Information, 10_000)

    def show_pending(self):
        release, self.pending = self.pending, None
        if release is not None:
            QTimer.singleShot(0, lambda: self.offer(release))

    # ----- установка -----

    def offer(self, release: Release):
        answer = QMessageBox.question(
            self.window, "Доступна новая версия",
            f"Вышла новая версия {release.version} (у вас {VERSION}).\n\n"
            "Обновить сейчас? Программа перезапустится, записи, сотрудники и настройки сохранятся.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self.install(release)
        else:
            self.declined = release.version

    def install(self, release: Release):
        w = self.window
        if not updater.can_self_update():
            QMessageBox.information(
                w, "Обновление",
                f"Автоматически обновиться можно только в установленной программе для Windows.\n"
                f"Скачайте новую версию здесь:\n{updater.RELEASES_PAGE}")
            return

        # Копия записей на случай, если что-то пойдёт не так (не мешает обновлению, если не вышла).
        try:
            w.db.backup(os.path.join(os.path.dirname(w.db.path), "deliveries.before-update.db"))
        except Exception:  # noqa: BLE001
            pass

        exe = sys.executable
        new = exe + ".new"
        dialog = QProgressDialog("Скачиваю обновление…", None, 0, 100, w)
        dialog.setWindowTitle("Обновление")
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)

        thread = DownloadThread(release, new, self)
        thread.progress.connect(dialog.setValue)
        thread.done.connect(lambda error: self._downloaded(error, dialog, exe, new))
        self.download_thread = thread
        thread.start()

    def _downloaded(self, error: str, dialog: QProgressDialog, exe: str, new: str):
        dialog.close()
        w = self.window
        if error:
            QMessageBox.warning(w, "Обновление", error)
            return
        try:
            updater.install(exe, new)
        except OSError as e:
            QMessageBox.warning(
                w, "Обновление",
                "Не удалось заменить файл программы. Возможно, она лежит в защищённой папке.\n"
                f"Перенесите её на Рабочий стол и попробуйте снова.\n\n{e}")
            return
        updater.relaunch(exe)
        w.quit_app()
