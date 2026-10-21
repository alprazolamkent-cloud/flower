"""Фото букетов и витрины: загрузка с уменьшением, миниатюры, просмотр."""

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QLabel, QMessageBox, QVBoxLayout

from .icon import flower_icon

MAX_SIDE = 1280  # больше Telegram всё равно сожмёт, а база не распухнет
FILTER = "Фото (*.jpg *.jpeg *.png *.webp *.bmp *.heic);;Все файлы (*)"


class PhotoError(Exception):
    pass


def to_jpeg(image: QImage, max_side: int = MAX_SIDE, quality: int = 85) -> bytes:
    if max(image.width(), image.height()) > max_side:
        image = image.scaled(max_side, max_side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if image.hasAlphaChannel():  # прозрачный PNG — на белом фоне, а не на чёрном
        flat = QImage(image.size(), QImage.Format_RGB32)
        flat.fill(Qt.white)
        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flat
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "JPEG", quality)
    return bytes(data.data())


def shrink(data: bytes, max_side: int = MAX_SIDE) -> bytes:
    """Фото из Telegram — уменьшить и пережать, если оно крупнее нужного. Не открылось — как есть."""
    image = QImage.fromData(data)
    if image.isNull() or (max(image.width(), image.height()) <= max_side and len(data) <= 300_000):
        return data
    return to_jpeg(image, max_side, 80)


def load_photo(path: str, max_side: int = MAX_SIDE, quality: int = 85) -> bytes:
    """Фото из файла: повернуть как снято (телефон пишет поворот отдельно), уменьшить, JPEG."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise PhotoError(f"Не получилось открыть фото: {reader.errorString()}")
    return to_jpeg(image, max_side, quality)


def pick_photo(parent, title: str = "Выберите фото", max_side: int = MAX_SIDE, quality: int = 85) -> bytes | None:
    """Спросить файл и вернуть готовое фото (или None, если передумали / не открылось).
    QR-код просят крупнее и без сильного сжатия, чтобы он сканировался."""
    path, _ = QFileDialog.getOpenFileName(parent, title, "", FILTER)
    if not path:
        return None
    try:
        return load_photo(path, max_side, quality)
    except PhotoError as e:
        QMessageBox.warning(parent, "Фото", str(e))
        return None


def pixmap(data: bytes | None, size: int) -> QPixmap:
    """Уменьшенное фото: не больше size по большей стороне (маленькое не растягиваем).
    Пустое, если фото нет или оно битое."""
    pm = QPixmap()
    if data:
        pm.loadFromData(data)
    if pm.isNull() or max(pm.width(), pm.height()) <= size:
        return pm
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class PhotoViewer(QDialog):
    def __init__(self, parent, data: bytes, title: str = "Фото"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(flower_icon())
        self.data = data
        layout = QVBoxLayout(self)
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        screen = parent.screen().availableGeometry() if parent is not None and parent.screen() else None
        side = min(screen.width(), screen.height()) - 160 if screen else 800
        pm = pixmap(data, max(side, 300))
        if pm.isNull():
            label.setText("Фото не открывается")
        else:
            label.setPixmap(pm)
        layout.addWidget(label, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить как…")
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить фото", "фото.jpg", "JPEG (*.jpg)")
        if path:
            with open(path, "wb") as f:
                f.write(self.data)
