"""Настройки склада, нужные и окну склада, и боту: «только сканером» и принтер этикеток."""

from PySide6.QtCore import QSettings

SCAN_ACTIONS = {"sale": "Продажа", "supply": "Приёмка поставки", "inventory": "Инвентаризация",
                "writeoff": "Списание"}
SCAN_ONLY = "🔒 Только сканером: отсканируйте штрихкод"


def _settings() -> QSettings:
    return QSettings("FlowerDeliveries", "FlowerDeliveries")


def scan_required(action: str) -> bool:
    """Действие только сканером: товар попадает в него лишь по штрихкоду (количество потом можно поправить)."""
    return str(_settings().value(f"scan_{action}", "false")) == "true"


def set_scan_required(action: str, on: bool):
    _settings().setValue(f"scan_{action}", "true" if on else "false")


def label_printer() -> str:
    """Принтер этикеток, выбранный в окне «Этикетки» (имя, как в Windows)."""
    return str(_settings().value("label_printer", "") or "")


def set_label_printer(name: str):
    _settings().setValue("label_printer", name)


WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def weekly_count_day() -> int:
    """День недели еженедельной инвентаризации (0 — понедельник), −1 — выключена."""
    try:
        return int(_settings().value("weekly_count_day", -1))
    except (TypeError, ValueError):
        return -1


def set_weekly_count_day(day: int):
    _settings().setValue("weekly_count_day", day)


def label_size() -> str:
    """Размер рулона этикеток для термопринтера: ключ из labels.THERMAL_SIZES."""
    return str(_settings().value("label_size", "58x40") or "58x40")


def label_offset() -> tuple[float, float]:
    """Сдвиг печати (вправо, вниз) в мм — подогнать под принтер."""
    try:
        return float(_settings().value("label_offset_x", 0) or 0), float(_settings().value("label_offset_y", 0) or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def set_label_setup(size: str, offset: tuple[float, float]):
    _settings().setValue("label_size", size)
    _settings().setValue("label_offset_x", offset[0])
    _settings().setValue("label_offset_y", offset[1])


def label_direct() -> tuple[bool, bool]:
    """(печатать напрямую командами TSPL, минуя драйвер; перевернуть) — по умолчанию через драйвер:
    принтер в режиме чеков печатает команды текстом и изводит рулон."""
    s = _settings()
    return str(s.value("label_direct_v2", "false")) == "true", str(s.value("label_flip", "false")) == "true"


def direct_checked(printer: str) -> bool:
    """На этом принтере прямую печать проверили маленькой пробной и подтвердили, что вышла этикетка."""
    return bool(printer) and str(_settings().value("label_direct_ok", "") or "") == printer


def set_direct_checked(printer: str):
    _settings().setValue("label_direct_ok", printer)


def set_label_direct(direct: bool, flip: bool):
    _settings().setValue("label_direct_v2", "true" if direct else "false")
    _settings().setValue("label_flip", "true" if flip else "false")
