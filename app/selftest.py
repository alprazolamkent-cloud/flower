"""Самопроверка собранной программы: `AloFlorist.exe --selftest отчёт.txt`.

Запускается при сборке на GitHub, до выпуска версии: если из сборки выпало что-то нужное (окна, фото, печать),
версия не выходит — и не прилетит в магазин автообновлением. Работает на временной базе, данные магазина
не трогает. В отчёте ещё — сколько весит распакованная программа и что в ней самое большое.
"""

import os
import sys
import tempfile
import traceback


def _bundle_report(lines: list[str]):
    root = getattr(sys, "_MEIPASS", "")
    if not root:
        return
    files = []
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            try:
                files.append((os.path.getsize(path), os.path.relpath(path, root)))
            except OSError:
                pass
    total = sum(size for size, _ in files)
    lines.append(f"распаковано: {len(files)} файлов, {total / 1e6:.1f} МБ")
    lines += [f"  {size / 1e6:6.1f} МБ  {name}" for size, name in sorted(files, reverse=True)[:25]]


def run(out_path: str) -> int:
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    lines, failed = [], []

    def step(name, check):
        try:
            result = check()
            lines.append(f"ok    {name}" + (f": {result}" if result not in (None, True) else ""))
        except Exception:  # noqa: BLE001 — собрать все поломки в один отчёт
            failed.append(name)
            lines.append(f"FAIL  {name}\n{traceback.format_exc()}")

    app = QApplication.instance() or QApplication(sys.argv)
    tmp = tempfile.mkdtemp(prefix="aloflorist-selftest-")
    state = {}

    def window():
        from app import theme
        from app.db import Database
        from app.main_window import MainWindow
        theme.apply(app, "light")
        db = Database(os.path.join(tmp, "selftest.db"))
        admin = db.add_user("selftest", "Проверка", "selftest-password", is_admin=True)
        w = MainWindow(db)
        w.set_user(admin)
        w.show()
        app.processEvents()
        state.update(db=db, window=w)

    def stock():
        w = state["window"]
        w.open_stock()
        for i in range(w.stock_window.tabs.count()):
            w.stock_window.tabs.setCurrentIndex(i)
            app.processEvents()
        w.stock_window.hide()

    def settings():
        from app.settings_dialog import SettingsDialog
        SettingsDialog(state["window"]).deleteLater()

    def photos():
        from app.photos import shrink, to_jpeg
        image = QImage(640, 480, QImage.Format_RGB32)
        image.fill(QColor("pink"))
        data = to_jpeg(image)
        assert data[:2] == b"\xff\xd8", "JPEG не сохраняется"
        assert not QImage.fromData(shrink(data)).isNull(), "JPEG не читается"
        return f"{len(data)} байт"

    def labels():
        from app.labels import Label, print_labels, render_label_image
        pdf = os.path.join(tmp, "labels.pdf")
        assert print_labels([Label("н15", "2000000000015", 150, 2)], "thermal", pdf_path=pdf) == 2
        assert not render_label_image(Label("н15", "2000000000015", 150)).isNull()

    def printers():
        from app.labels import printers as names
        found, default = names()
        return f"{len(found)} принтеров"

    def single_instance():
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
        name = f"aloflorist-selftest-{os.getpid()}"
        server = QLocalServer()
        assert server.listen(name), server.errorString()
        sock = QLocalSocket()
        sock.connectToServer(name)
        assert sock.waitForConnected(2000), "вторая копия не видит первую"
        sock.disconnectFromServer()
        server.close()

    def tls():
        import ssl
        ssl.create_default_context()

    def translations():
        translator = QTranslator()
        loaded = translator.load(QLocale(QLocale.Russian, QLocale.Russia), "qtbase", "_",
                                 QLibraryInfo.path(QLibraryInfo.TranslationsPath))
        return "русский перевод Qt есть" if loaded else "русского перевода Qt нет (кнопки Qt — по-английски)"

    def windows_plugin():
        import glob
        root = getattr(sys, "_MEIPASS", "")
        if not root or sys.platform != "win32":
            return "не Windows-сборка — пропускаю"
        found = glob.glob(os.path.join(root, "**", "platforms", "qwindows.dll"), recursive=True)
        assert found, "нет platforms/qwindows.dll — окна на Windows не откроются"
        return os.path.relpath(found[0], root)

    for name, check in (("окна Windows (qwindows.dll)", windows_plugin), ("главное окно", window), ("склад, все вкладки", stock), ("настройки", settings),
                        ("фото JPEG", photos), ("этикетки и PDF", labels), ("список принтеров", printers),
                        ("второй запуск", single_instance), ("https", tls), ("перевод", translations)):
        if check in (stock, settings) and "window" not in state:
            continue  # без главного окна их не открыть — поломка уже в отчёте
        step(name, check)
    _bundle_report(lines)
    if "db" in state:
        state["window"].hide()
        state["db"].close()
    text = ("OK" if not failed else "FAIL: " + ", ".join(failed)) + "\n" + "\n".join(lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return 0 if not failed else 1
