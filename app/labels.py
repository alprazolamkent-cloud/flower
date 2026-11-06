"""Этикетки со штрихкодами: EAN-13 (свои 200… и большинство заводских) и Code 128 (заводские с буквами).

Рисуем сами, в SVG: страница открывается в браузере и печатается на любом принтере — лист A4 с наклейками
или термопринтер этикеток 58×40 мм.
"""

import html
from dataclasses import dataclass

from .stock import is_ean13

# EAN-13: наборы L, G (левая половина), R (правая) и какие наборы брать по первой цифре
EAN_L = ("0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011")
EAN_G = ("0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111")
EAN_R = ("1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100")
EAN_PARITY = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")

# Code 128: ширины полос и пробелов для значений 0…106 (103–105 — старт A/B/C, 106 — стоп)
CODE128 = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)
START_B = 104


def ean13_modules(code: str) -> str:
    """95 модулей EAN-13: «1» — полоса, «0» — просвет."""
    parity = EAN_PARITY[int(code[0])]
    left = "".join((EAN_L if p == "L" else EAN_G)[int(d)] for p, d in zip(parity, code[1:7]))
    right = "".join(EAN_R[int(d)] for d in code[7:13])
    return "101" + left + "01010" + right + "101"


def code128_modules(text: str) -> str:
    """Code 128 (набор B: цифры, латиница, знаки) — модули «1»/«0» со стартом, контрольным знаком и стопом."""
    values = [ord(c) - 32 for c in text]
    if any(not 0 <= v <= 94 for v in values):
        raise ValueError("в штрихкоде есть символы, которые нельзя напечатать (только цифры и латиница)")
    check = (START_B + sum(i * v for i, v in enumerate(values, start=1))) % 103
    result = []
    for v in [START_B, *values, check, 106]:
        for i, width in enumerate(CODE128[v]):
            result.append(("1" if i % 2 == 0 else "0") * int(width))
    return "".join(result)


def barcode_svg(code: str, width_mm: float, height_mm: float) -> str:
    """Штрихкод в SVG нужного размера (с полями тишины по краям — иначе сканер не прочтёт)."""
    modules = ean13_modules(code) if is_ean13(code) else code128_modules(code)
    quiet = 11 if is_ean13(code) else 10
    total = len(modules) + 2 * quiet
    bars, x = [], quiet
    for run in _runs(modules):
        if run[0] == "1":
            bars.append(f'<rect x="{x}" y="0" width="{len(run)}" height="60"/>')
        x += len(run)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm" height="{height_mm}mm" '
            f'viewBox="0 0 {total} 60" preserveAspectRatio="none" shape-rendering="crispEdges">'
            + "".join(bars) + "</svg>")


def _runs(modules: str):
    start = 0
    for i in range(1, len(modules) + 1):
        if i == len(modules) or modules[i] != modules[start]:
            yield modules[start:i]
            start = i


@dataclass
class Label:
    title: str  # мелко сверху — короткая подпись («н15») или название
    code: str
    price: int | None = None  # None — без цены
    copies: int = 1


LAYOUTS = {
    "thermal": "Термопринтер (рулон этикеток)",
    "a4": "Лист A4, 24 наклейки (70×37 мм)",
}


def labels_html(labels: list[Label], layout: str = "thermal") -> str:
    """Страница для печати из браузера: по этикетке на штуку (copies)."""
    thermal = layout == "thermal"
    width, height = (58, 40) if thermal else (70, 37)
    cells = []
    for label in labels:  # сверху мелко подпись, посередине крупно цена, внизу штрихкод
        price = (f'<div class="price">{label.price:,} ₽</div>'.replace(",", " ") if label.price is not None
                 else '<div class="price"></div>')
        cell = (f'<div class="label"><div class="title">{html.escape(label.title)}</div>{price}'
                f'<div class="bars">{barcode_svg(label.code, width - 10, 11 if thermal else 10)}'
                f'<div class="code">{html.escape(label.code)}</div></div></div>')
        cells += [cell] * max(label.copies, 0)
    page = ("@page { size: 58mm 40mm; margin: 0; } .label { page-break-after: always; }" if thermal else
            "@page { size: A4; margin: 0; } .sheet { display: grid; grid-template-columns: repeat(3, 70mm); }"
            " .label { page-break-inside: avoid; }")
    return f"""<html><head><meta charset="utf-8"><title>Этикетки</title><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Arial, sans-serif; color: #000; }}
{page}
.label {{ width: {width}mm; height: {height}mm; padding: 2mm 5mm; overflow: hidden; display: flex;
          flex-direction: column; justify-content: space-between; }}
.title {{ font-size: 8pt; line-height: 1.15; max-height: 2.3em; overflow: hidden; }}
.price {{ font-size: {26 if thermal else 24}pt; font-weight: bold; line-height: 1; text-align: center; }}
.bars svg {{ display: block; margin: 0 auto; }}
.code {{ font-size: 7.5pt; letter-spacing: 1px; text-align: center; }}
.tip {{ font: 13px Arial; padding: 10px 14px; background: #fdeef3; border-bottom: 1px solid #f1c3d3; }}
@media print {{ .tip {{ display: none; }} }}
</style></head><body>
<div class="tip">🏷 Этикеток: {len(cells)}. Печать — Ctrl+P. В настройках печати: масштаб 100 % (без «по размеру страницы»),
поля — нет{', размер бумаги 58×40 мм' if thermal else ''}.</div>
<div class="sheet">{''.join(cells)}</div>
</body></html>"""


# ----- печать прямо на принтер (Xprinter и любой другой, через драйвер Windows) -----

LABEL_MM = {"thermal": (58, 40), "a4": (70, 37)}
A4_GRID = (3, 8)  # 24 наклейки на листе A4
# размеры рулонов для термопринтера (ширина × высота, мм) — какой стоит, смотрите на коробке рулона
THERMAL_SIZES = {"58x40": (58, 40), "58x30": (58, 30), "60x40": (60, 40), "40x30": (40, 30), "43x25": (43, 25)}


def barcode_modules(code: str) -> tuple[str, int]:
    """(модули «1»/«0», поле тишины в модулях) — EAN-13 или Code 128."""
    return (ean13_modules(code), 11) if is_ean13(code) else (code128_modules(code), 10)


def paint_label(painter, rect, label: Label, dpmm: float, frame: bool = False):
    """Нарисовать этикетку в прямоугольнике rect (точки устройства): сверху мелко подпись, крупно цена,
    внизу штрихкод. Ширина полоски — целое число точек: у термопринтера дробные полоски расплываются."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetricsF

    pad_x, pad_y = 2.5 * dpmm, 2 * dpmm
    inner = QRectF(rect).adjusted(pad_x, pad_y, -pad_x, -pad_y)
    painter.save()
    painter.setPen(QColor("black"))
    if frame:  # пробная: рамка по краю — видно, куда сдвинулась печать
        from PySide6.QtGui import QPen
        pen = QPen(QColor("black"))
        pen.setWidthF(0.3 * dpmm)
        painter.setPen(pen)
        painter.drawRect(QRectF(rect).adjusted(0.6 * dpmm, 0.6 * dpmm, -0.6 * dpmm, -0.6 * dpmm))
        painter.setPen(QColor("black"))
    font = QFont("Arial")
    font.setPixelSize(max(int(2.6 * dpmm), 6))
    painter.setFont(font)
    title = QFontMetricsF(font).elidedText(label.title, Qt.ElideRight, inner.width())
    top_h = 3.4 * dpmm
    painter.drawText(QRectF(inner.left(), inner.top(), inner.width(), top_h), Qt.AlignCenter, title)

    code_h, bars_h = 2.8 * dpmm, min(11 * dpmm, inner.height() * 0.38)
    bottom = inner.bottom()
    if label.price is not None:
        text = f"{label.price:,} ₽".replace(",", " ")
        big = QFont("Arial")
        big.setBold(True)
        space_h = bottom - code_h - bars_h - inner.top() - top_h
        size = int(min(space_h * 0.9, 10 * dpmm))
        while size > 6:
            big.setPixelSize(size)
            if QFontMetricsF(big).horizontalAdvance(text) <= inner.width():
                break
            size -= 1
        painter.setFont(big)
        painter.drawText(QRectF(inner.left(), inner.top() + top_h, inner.width(), space_h), Qt.AlignCenter, text)

    modules, quiet = barcode_modules(label.code)
    total = len(modules) + 2 * quiet
    unit = max(int(inner.width() // total), 1)
    x = inner.left() + (inner.width() - unit * total) / 2 + quiet * unit
    bars_top = bottom - code_h - bars_h
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("black"))
    start = None
    for i, bit in enumerate(modules + "0"):
        if bit == "1" and start is None:
            start = i
        elif bit == "0" and start is not None:
            painter.drawRect(QRectF(round(x + start * unit), bars_top, (i - start) * unit, bars_h))
            start = None
    painter.setPen(QColor("black"))
    small = QFont("Arial")
    small.setPixelSize(max(int(2.3 * dpmm), 6))
    painter.setFont(small)
    painter.drawText(QRectF(inner.left(), bottom - code_h, inner.width(), code_h), Qt.AlignCenter, label.code)
    painter.restore()


def render_label_image(label: Label, layout: str = "thermal", dpi: int = 203, size: tuple | None = None):
    """Этикетка картинкой (как напечатает принтер с таким разрешением) — для просмотра и проверки."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    w_mm, h_mm = size or LABEL_MM[layout]
    dpmm = dpi / 25.4
    image = QImage(round(w_mm * dpmm), round(h_mm * dpmm), QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    paint_label(painter, QRectF(0, 0, image.width(), image.height()), label, dpmm)
    painter.end()
    return image


def print_labels(labels: list[Label], layout: str = "thermal", printer_name: str = "", pdf_path: str = "",
                 size: tuple | None = None, offset: tuple = (0, 0), frame: bool = False) -> int:
    """Напечатать на принтер (по имени, как в Windows) или в PDF. Возвращает число страниц.
    Термопринтер — по этикетке на страницу (size, мм; по умолчанию 58×40); A4 — сеткой 3×8.
    offset — (вправо, вниз) в мм: подвинуть печать, если принтер кладёт её мимо этикетки."""
    from PySide6.QtCore import QMarginsF, QRectF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter
    from PySide6.QtPrintSupport import QPrinter

    copies = [label for label in labels for _ in range(max(label.copies, 0))]
    if not copies:
        return 0
    printer = QPrinter(QPrinter.HighResolution)
    if pdf_path:
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(pdf_path)
    elif printer_name:
        printer.setPrinterName(printer_name)
    printer.setFullPage(True)
    w_mm, h_mm = (size or LABEL_MM[layout]) if layout == "thermal" else LABEL_MM[layout]
    if layout == "thermal":
        page = QPageSize(QSizeF(w_mm, h_mm), QPageSize.Millimeter, f"Этикетка {w_mm:g}×{h_mm:g}", QPageSize.ExactMatch)
        orientation = QPageLayout.Landscape if page.size(QPageSize.Millimeter).width() < w_mm else QPageLayout.Portrait
        printer.setPageLayout(QPageLayout(page, orientation, QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter))
    else:
        printer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0),
                                          QPageLayout.Millimeter))
    painter = QPainter()
    if not painter.begin(printer):
        raise OSError(f"принтер «{printer_name or 'по умолчанию'}» не отвечает — проверьте, что он включён")
    dpmm = printer.resolution() / 25.4
    per_page = 1 if layout == "thermal" else A4_GRID[0] * A4_GRID[1]
    pages = 0
    for i, label in enumerate(copies):
        slot = i % per_page
        if slot == 0:
            if i:
                printer.newPage()
            pages += 1
        col, row = slot % A4_GRID[0], slot // A4_GRID[0]
        x, y = (0, 0) if layout == "thermal" else (col * w_mm, row * h_mm)
        x, y = x + offset[0], y + offset[1]
        paint_label(painter, QRectF(x * dpmm, y * dpmm, w_mm * dpmm, h_mm * dpmm), label, dpmm, frame)
    painter.end()
    return pages


def printers() -> tuple[list[str], str]:
    """(все принтеры Windows, какой выбрать по умолчанию — похожий на принтер этикеток)."""
    from PySide6.QtPrintSupport import QPrinterInfo
    names = QPrinterInfo.availablePrinterNames()
    label_like = [n for n in names if any(w in n.lower() for w in ("xprinter", "xp-", "label", "этикет", "tsc",
                                                                     "godex", "zebra", "atol bp", "атол bp"))]
    default = QPrinterInfo.defaultPrinterName()
    return names, (label_like[0] if label_like else default if default in names else (names[0] if names else ""))
