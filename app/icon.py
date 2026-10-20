"""Значок программы (цветок), рисуется в коде — без отдельных файлов."""

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


def flower_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        c = size / 2
        petal_r = size * 0.2
        p.setBrush(QColor("#e91e63"))
        for i in range(5):
            a = math.radians(i * 72 - 90)
            p.drawEllipse(QPointF(c + math.cos(a) * size * 0.25, c + math.sin(a) * size * 0.25),
                          petal_r, petal_r)
        p.setBrush(QColor("#ffc107"))
        p.drawEllipse(QPointF(c, c), size * 0.15, size * 0.15)
        p.end()
        icon.addPixmap(pix)
    return icon
