"""«Чёрный ящик»: если окно не отвечает дольше HANG_AFTER, записать, на какой строке кода программа
застряла (и при аварии — где упала). При следующем запуске запись уходит администраторам в Telegram.

В файл попадают только места в коде (имена файлов и функций), без данных магазина.
"""

import faulthandler
import os
import threading
import time
from datetime import datetime

from PySide6.QtCore import QSettings, QTimer

HANG_AFTER = 8  # секунд без ответа — уже «зависла»
FILE_NAME = "зависания.log"


class HangLog:
    def __init__(self, folder: str, version: str):
        self.path = os.path.join(folder, FILE_NAME)
        self.version = version
        self.file = open(self.path, "a", encoding="utf-8")  # noqa: SIM115 — открыт всё время работы
        faulthandler.enable(self.file, all_threads=True)  # авария (падение внутри Qt) — тоже в файл
        self.beat = time.monotonic()
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(500)
        threading.Thread(target=self._watch, daemon=True).start()

    def _tick(self):
        self.beat = time.monotonic()

    def _watch(self):
        reported = False
        while True:
            time.sleep(1)
            stuck = time.monotonic() - self.beat
            if stuck >= HANG_AFTER and not reported:
                reported = True
                self.file.write(f"\n=== {datetime.now():%d.%m.%Y %H:%M:%S} · версия {self.version} · "
                                f"окно не отвечает {stuck:.0f} с — где застряла:\n")
                self.file.flush()
                faulthandler.dump_traceback(self.file, all_threads=True)
                self.file.flush()
            elif stuck < 2 and reported:
                reported = False
                self.file.write(f"--- {datetime.now():%H:%M:%S} снова отвечает\n")
                self.file.flush()


def unsent_report(path: str, settings: QSettings | None = None, limit: int = 3500) -> str:
    """Новое в файле с прошлой отправки (последний кусок) — или "". Отметка «отправлено» — в настройках."""
    settings = settings or QSettings("FlowerDeliveries", "FlowerDeliveries")
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    sent = int(settings.value("hang_log_sent", 0) or 0)
    if size <= sent:
        if size < sent:  # файл удалили или он начался заново
            settings.setValue("hang_log_sent", size)
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        f.seek(sent)
        text = f.read()
    settings.setValue("hang_log_sent", size)
    text = text.strip()
    if "\n=== " in "\n" + text:  # последнее зависание; самое важное (где застряла) — в его начале
        text = "=== " + ("\n" + text).rsplit("\n=== ", 1)[1]
    return text[:limit]
