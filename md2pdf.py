"""Универсальный конвертер Markdown в PDF средствами Qt.

Разметка переводится в HTML и печатается в PDF. Шрифты загружаются из системы,
поэтому вид не зависит от платформы. Поддерживаются заголовки, абзацы, таблицы,
списки, блоки кода, цитаты, разделители, полужирный и курсив, ссылки и картинки.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PyQt5.QtCore import QMarginsF, QSizeF, QUrl
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QTextDocument,
)

BASE_FONT_FILES = ("segoeui.ttf", "arial.ttf", "calibri.ttf", "DejaVuSans.ttf")
MONO_FONT_FILES = ("consola.ttf", "cour.ttf", "lucon.ttf", "DejaVuSansMono.ttf")
BASE_FAMILY_FALLBACK = "Segoe UI"
MONO_FAMILY_FALLBACK = "Consolas"

PAGE_SIZES = {
    "A4": QPageSize.A4,
    "A5": QPageSize.A5,
    "A3": QPageSize.A3,
    "Letter": QPageSize.Letter,
}
MM_PER_INCH = 25.4
PREVIEW_FORMAT = QImage.Format_RGB32

TABLE_BORDER = "#9db2c4"
TABLE_HEADER_BG = "#e8eef4"
CODE_BG = "#f4f5f7"
QUOTE_BORDER = "#c8d4de"
HEADING_COLOR = "#12385c"
SUBTITLE_COLOR = "#4a6b85"
NOTE_COLOR = "#7a8b99"

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
FENCE = re.compile(r"^\s*```")
CODE_SPAN = re.compile(r"`([^`]+)`")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")


@dataclass
class PdfOptions:
    """Настройки сборки PDF."""

    title: str = ""
    subtitle: str = ""
    note: str = ""
    font_size: float = 10.0
    page_size: str = "A4"
    margin_mm: float = 16.0
    resolution: int = 300
    base_family: str = ""
    mono_family: str = ""
    image_width: int = 0


def _system_fonts_directory() -> Path:
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    return Path(root) / "Fonts"


def _load_first(directory: Path, names: Sequence[str], fallback: str) -> str:
    for name in names:
        path = directory / name
        if not path.exists():
            continue
        identifier = QFontDatabase.addApplicationFont(str(path))
        if identifier < 0:
            continue
        families = QFontDatabase.applicationFontFamilies(identifier)
        if families:
            return families[0]
    return fallback


def register_fonts() -> Tuple[str, str]:
    """Загружает системные шрифты и возвращает основное и моноширинное семейства."""
    directory = _system_fonts_directory()
    return (
        _load_first(directory, BASE_FONT_FILES, BASE_FAMILY_FALLBACK),
        _load_first(directory, MONO_FONT_FILES, MONO_FAMILY_FALLBACK),
    )


def page_text_size(options: PdfOptions) -> QSizeF:
    """Размер полосы набора в точках изображения."""
    size = QPageSize(PAGE_SIZES.get(options.page_size, QPageSize.A4)).size(QPageSize.Millimeter)
    scale = options.resolution / MM_PER_INCH
    return QSizeF(
        max(1.0, (size.width() - 2 * options.margin_mm) * scale),
        max(1.0, (size.height() - 2 * options.margin_mm) * scale),
    )


def page_text_width(options: PdfOptions) -> int:
    """Ширина полосы набора в точках изображения."""
    return int(page_text_size(options).width())


def inline(text: str, image_width: int = 0) -> str:
    """Разметка внутри строки."""
    escaped = html.escape(text, quote=False)
    escaped = CODE_SPAN.sub(lambda match: "<code>%s</code>" % match.group(1), escaped)
    escaped = IMAGE.sub(lambda match: _image(match, image_width), escaped)
    escaped = LINK.sub(lambda match: '<a href="%s">%s</a>' % (match.group(2), match.group(1)), escaped)
    escaped = BOLD.sub(lambda match: "<b>%s</b>" % match.group(1), escaped)
    escaped = ITALIC.sub(lambda match: "<i>%s</i>" % match.group(1), escaped)
    return escaped


def _image(match, image_width: int) -> str:
    alt, source = match.group(1), match.group(2)
    width = ' width="%s"' % image_width if image_width > 0 else ""
    return '<img src="%s" alt="%s"%s>' % (source, alt, width)


def _cells(line: str) -> List[str]:
    match = TABLE_ROW.match(line)
    body = match.group(1) if match else line
    return [cell.strip() for cell in body.split("|")]


def _is_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(cell and set(cell) <= set("-: ") for cell in cells)


def _cell(content: str, header: bool, image_width: int) -> str:
    background = " background-color:%s;" % TABLE_HEADER_BG if header else ""
    style = "border:1px solid %s;%s" % (TABLE_BORDER, background)
    value = inline(content, image_width)
    if header:
        value = "<b>%s</b>" % value
    return "<td style='%s'>%s</td>" % (style, value)


def _table(rows: Sequence[str], image_width: int) -> str:
    parsed = [_cells(row) for row in rows]
    header = parsed[0]
    body = [row for row in parsed[1:] if not _is_separator(row)]
    parts = [
        "<table width='100%' cellspacing='0' cellpadding='6' "
        "style='border-collapse:collapse; margin-bottom:8px;'>"
    ]
    parts.append("<tr>" + "".join(_cell(cell, True, image_width) for cell in header) + "</tr>")
    for row in body:
        parts.append("<tr>" + "".join(_cell(cell, False, image_width) for cell in row) + "</tr>")
    parts.append("</table>")
    return "".join(parts)


def _list(items: Sequence[str], ordered: bool, image_width: int) -> str:
    if not ordered:
        return "".join(
            "<p style='margin:2px 0 2px 12px;'>&#8226;&nbsp;&nbsp;%s</p>" % inline(item, image_width)
            for item in items
        )
    return "".join(
        "<p style='margin:2px 0 2px 12px;'>%s.&nbsp;&nbsp;%s</p>" % (index + 1, inline(item, image_width))
        for index, item in enumerate(items)
    )


def _code_block(lines: Sequence[str]) -> str:
    body = html.escape("\n".join(lines))
    return (
        "<pre style='background-color:%s; border:1px solid #dddddd; padding:6px;'>%s</pre>"
        % (CODE_BG, body)
    )


def _quote(lines: Sequence[str], image_width: int) -> str:
    body = inline(" ".join(line.strip() for line in lines), image_width)
    return (
        "<p style='margin:4px 0 4px 16px; color:#405060;'><i>%s</i></p>" % body
    )


def _collect_items(lines: Sequence[str], index: int, pattern) -> Tuple[List[str], int]:
    """Собирает пункты списка вместе со строками продолжения."""
    items: List[str] = []
    while index < len(lines):
        line = lines[index]
        match = pattern.match(line)
        if match:
            items.append(match.group(1).strip())
            index += 1
            continue
        if items and line.strip() and line[:1].isspace():
            items[-1] = "%s %s" % (items[-1], line.strip())
            index += 1
            continue
        break
    return items, index


def _starts_block(line: str) -> bool:
    stripped = line.strip()
    return bool(
        HEADING.match(stripped)
        or ORDERED.match(line)
        or BULLET.match(line)
        or TABLE_ROW.match(line)
        or RULE.match(stripped)
        or FENCE.match(line)
        or stripped.startswith(">")
    )


def markdown_to_html(text: str, image_width: int = 0) -> Tuple[str, str]:
    """Переводит разметку в HTML и возвращает заголовок первого уровня."""
    lines = text.splitlines()
    blocks: List[str] = []
    title = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if FENCE.match(line):
            index += 1
            code: List[str] = []
            while index < len(lines) and not FENCE.match(lines[index]):
                code.append(lines[index])
                index += 1
            index += 1
            blocks.append(_code_block(code))
            continue
        if RULE.match(stripped):
            blocks.append("<hr>")
            index += 1
            continue
        heading = HEADING.match(stripped)
        if heading:
            level = len(heading.group(1))
            content = inline(heading.group(2), image_width)
            if level == 1 and not title:
                title = heading.group(2)
            else:
                size = 14 if level <= 2 else 11
                blocks.append(
                    "<p style='font-size:%spt; color:%s; margin-top:%s; margin-bottom:%s;'>"
                    "<b>%s</b></p>"
                    % (size, HEADING_COLOR, 14 if level <= 2 else 10, 6 if level <= 2 else 4, content)
                )
            index += 1
            continue
        if TABLE_ROW.match(line):
            rows: List[str] = []
            while index < len(lines) and TABLE_ROW.match(lines[index]):
                rows.append(lines[index])
                index += 1
            blocks.append(_table(rows, image_width))
            continue
        if stripped.startswith(">"):
            quote: List[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip()[1:])
                index += 1
            blocks.append(_quote(quote, image_width))
            continue
        if ORDERED.match(line):
            items, index = _collect_items(lines, index, ORDERED)
            blocks.append(_list(items, True, image_width))
            continue
        if BULLET.match(line):
            items, index = _collect_items(lines, index, BULLET)
            blocks.append(_list(items, False, image_width))
            continue
        paragraph = [stripped]
        index += 1
        while index < len(lines) and lines[index].strip() and not _starts_block(lines[index]):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append("<p>%s</p>" % inline(" ".join(paragraph), image_width))
    return title, "".join(blocks)


def build_document(text: str, options: Optional[PdfOptions] = None, base_dir: Optional[Path] = None) -> QTextDocument:
    """Готовит документ к печати."""
    settings = options or PdfOptions()
    if not settings.base_family or not settings.mono_family:
        base, mono = register_fonts()
        settings.base_family = settings.base_family or base
        settings.mono_family = settings.mono_family or mono
    width = settings.image_width or page_text_width(settings)
    title, body = markdown_to_html(text, width)

    header = ""
    if settings.title or title:
        header += (
            "<p style='font-size:24pt; color:%s; margin-bottom:2px;'><b>%s</b></p>"
            % (HEADING_COLOR, html.escape(settings.title or title))
        )
    if settings.subtitle:
        header += "<p style='font-size:12pt; color:%s; margin-top:0;'>%s</p>" % (
            SUBTITLE_COLOR,
            html.escape(settings.subtitle),
        )
    if settings.note:
        header += "<p style='font-size:9pt; color:%s; margin-top:0;'>%s</p>" % (
            NOTE_COLOR,
            html.escape(settings.note),
        )
    if header:
        header += "<hr>"

    document = QTextDocument()
    font = QFont(settings.base_family)
    font.setPointSizeF(settings.font_size)
    document.setDefaultFont(font)
    document.setDefaultStyleSheet(
        "p { margin: 4px 0; }"
        "code { font-family: '%s'; font-size: %.1fpt; background-color: %s; }"
        "pre { font-family: '%s'; font-size: %.1fpt; }"
        "td { border: 1px solid %s; padding: 4px; }"
        % (
            settings.mono_family,
            settings.font_size - 1,
            CODE_BG,
            settings.mono_family,
            settings.font_size - 1,
            TABLE_BORDER,
        )
    )
    if base_dir is not None:
        document.setBaseUrl(QUrl.fromLocalFile(str(Path(base_dir).resolve()) + os.sep))
    document.setMetaInformation(
        QTextDocument.DocumentTitle, settings.title or title or "Документ"
    )
    document.setHtml(header + body)
    return document


def make_writer(target: Path, options: Optional[PdfOptions] = None) -> QPdfWriter:
    """Настраивает печать страницы."""
    settings = options or PdfOptions()
    writer = QPdfWriter(str(target))
    writer.setPageSize(QPageSize(PAGE_SIZES.get(settings.page_size, QPageSize.A4)))
    writer.setPageMargins(
        QMarginsF(settings.margin_mm, settings.margin_mm, settings.margin_mm, settings.margin_mm),
        QPageLayout.Millimeter,
    )
    writer.setResolution(settings.resolution)
    if settings.title:
        writer.setTitle(settings.title)
    return writer


def write_pdf(document: QTextDocument, target: Path, options: Optional[PdfOptions] = None) -> None:
    """Печатает документ в PDF.

    Устройство печати назначается документу до разбиения на страницы,
    иначе размер шрифта считается по экранному разрешению.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    writer = make_writer(target, options)
    document.documentLayout().setPaintDevice(writer)
    rect = writer.pageLayout().paintRectPixels(writer.resolution())
    document.setPageSize(QSizeF(rect.width(), rect.height()))
    document.print_(writer)


def _preview_image(size: QSizeF, dots: int) -> QImage:
    image = QImage(int(size.width()), int(size.height()), PREVIEW_FORMAT)
    image.setDotsPerMeterX(dots)
    image.setDotsPerMeterY(dots)
    image.fill(QColor("white"))
    return image


def write_preview(
    document: QTextDocument,
    target: Path,
    pages: int = 1,
    options: Optional[PdfOptions] = None,
) -> None:
    """Сохраняет страницы документа изображениями для проверки вида."""
    settings = options or PdfOptions()
    size = document.pageSize()
    if size.width() < 1 or size.height() < 1:
        size = page_text_size(settings)
    dots = int(round(settings.resolution / (MM_PER_INCH / 1000.0)))
    first = _preview_image(size, dots)
    document.documentLayout().setPaintDevice(first)
    document.setPageSize(size)
    for page in range(min(pages, document.pageCount())):
        image = first if page == 0 else _preview_image(size, dots)
        painter = QPainter(image)
        painter.translate(0, -page * size.height())
        document.drawContents(painter)
        painter.end()
        path = target if page == 0 else target.with_name(
            "%s-%s%s" % (target.stem, page + 1, target.suffix)
        )
        image.save(str(path))


def convert(source: Path, target: Optional[Path] = None, options: Optional[PdfOptions] = None) -> int:
    """Собирает PDF из файла Markdown и возвращает число страниц."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError("Нет файла разметки: %s" % source)
    settings = options or PdfOptions()
    document = build_document(source.read_text(encoding="utf-8"), settings, source.parent)
    destination = Path(target) if target else source.with_suffix(".pdf")
    write_pdf(document, destination, settings)
    return document.pageCount()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Собирает PDF из разметки по параметрам командной строки."""
    parser = argparse.ArgumentParser(
        prog="md2pdf", description="Конвертер Markdown в PDF"
    )
    parser.add_argument("source", help="файл разметки Markdown")
    parser.add_argument("-o", "--output", default="", help="файл PDF, по умолчанию рядом с исходным")
    parser.add_argument("-t", "--title", default="", help="заголовок документа")
    parser.add_argument("--subtitle", default="", help="подзаголовок под заголовком")
    parser.add_argument("--note", default="", help="строка мелким шрифтом под подзаголовком")
    parser.add_argument("--font-size", type=float, default=10.0, help="размер основного шрифта, пт")
    parser.add_argument("--page", default="A4", choices=sorted(PAGE_SIZES), help="формат страницы")
    parser.add_argument("--margin", type=float, default=16.0, help="поле страницы, мм")
    parser.add_argument("--image-width", type=int, default=0, help="ширина картинок, точек; 0 — по ширине набора")
    parser.add_argument("--preview", default="", help="файл изображения для проверки вида")
    parser.add_argument("--preview-pages", type=int, default=1, help="сколько страниц сохранить в изображения")
    arguments = parser.parse_args(argv)

    from PyQt5.QtWidgets import QApplication

    application = QApplication.instance() or QApplication(sys.argv[:1])
    options = PdfOptions(
        title=arguments.title,
        subtitle=arguments.subtitle,
        note=arguments.note,
        font_size=arguments.font_size,
        page_size=arguments.page,
        margin_mm=arguments.margin,
        image_width=arguments.image_width,
    )
    source = Path(arguments.source)
    target = Path(arguments.output) if arguments.output else source.with_suffix(".pdf")
    try:
        pages = convert(source, target, options)
    except (FileNotFoundError, OSError) as error:
        print("ошибка: %s" % error, file=sys.stderr)
        del application
        return 2
    print("готово: %s, страниц %s, шрифт %s" % (target, pages, options.base_family))
    if arguments.preview:
        document = build_document(source.read_text(encoding="utf-8"), options, source.parent)
        write_preview(document, Path(arguments.preview), arguments.preview_pages, options)
        print("вид: %s" % arguments.preview)
    del application
    return 0


if __name__ == "__main__":
    sys.exit(main())
