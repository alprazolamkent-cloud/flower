# Сборка AloFlorist.exe (PyInstaller): python -m PyInstaller --noconfirm AloFlorist.spec
#
# Программа — один .exe (так её обновляет автообновление), а он при каждом запуске распаковывается во временную
# папку, и антивирус проверяет каждый файл. Чем меньше внутри, тем быстрее открывается. Здесь выкидываем то, что
# программе не нужно: окна рисуются без OpenGL, PDF она только пишет (не читает), QML не использует, переводы Qt
# нужны только русские. Если выкинули лишнее — самопроверка при сборке (--selftest) не выпустит версию.

import os

EXCLUDES = [
    "tkinter", "unittest", "pydoc", "pydoc_data", "test", "lib2to3", "xmlrpc", "doctest",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtMultimedia", "PySide6.QtDBus", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    # openpyxl подхватывает их, если установлены, — для чтения таблиц они не нужны
    "numpy", "PIL", "lxml", "pandas", "pytest", "_pytest", "IPython",
]
# файлы, которые Qt кладёт «на всякий случай»: программный OpenGL (~20 МБ), шейдеры Direct3D, PDF, QML
DROP = ("opengl32sw", "d3dcompiler", "qt6pdf", "qt6qml", "qt6quick", "qt6opengl", "qt6virtualkeyboard",
        "qtuiotouchplugin", "qdirect2d",  # запасной способ рисовать окна — хватает обычного qwindows
        # своё шифрование Qt (~6 МБ): из Qt-сети программа берёт только «уже запущена?», в интернет ходит через
        # Python (у него свои libcrypto-3-x64 / libssl-3-x64 — их не трогаем)
        "qopensslbackend", "qschannelbackend", "libcrypto-3.dll", "libssl-3.dll")
IMAGE_FORMATS = {"qjpeg", "qgif", "qico", "qsvg", "qwebp"}  # фото — JPEG, остальное — на всякий случай


def keep(entry) -> bool:
    dest = entry[0].replace("\\", "/").lower()
    name = os.path.basename(dest)
    if any(word in name for word in DROP):
        return False
    if "/imageformats/" in dest and os.path.splitext(name)[0].removeprefix("lib") not in IMAGE_FORMATS:
        return False
    if "/translations/" in dest and name.endswith(".qm") and not name.endswith("_ru.qm"):
        return False
    return True


a = Analysis(["main.py"], excludes=EXCLUDES, noarchive=False)
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AloFlorist",
    console=False,  # окно без чёрной консоли (--windowed)
    upx=False,  # сжатие UPX портит библиотеки Qt и замедляет запуск
    runtime_tmpdir=None,
)
