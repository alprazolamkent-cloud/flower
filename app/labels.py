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
    "thermal": "Термопринтер, этикетка 58×40 мм",
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
