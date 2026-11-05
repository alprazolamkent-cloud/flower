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
