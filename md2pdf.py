"""Универсальный конвертер Markdown в PDF средствами Qt.

Разметка переводится в семантический HTML и печатается в PDF. Оформление
задаётся внешней таблицей стилей CSS (по умолчанию style.css рядом с
программой), поэтому вид документа меняется без правки кода. Шрифты
загружаются из системы, поэтому результат не зависит от платформы.

Поддерживаются заголовки, абзацы, таблицы с выравниванием столбцов, списки,
блоки кода, цитаты, разделители, полужирный и курсив, ссылки и картинки.
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

VERSION = "1.0.0"

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
CSS_DPI = 96.0
PREVIEW_FORMAT = QImage.Format_RGB32

STYLE_FILENAME = "style.css"
STYLE_ENV_VAR = "MD2PDF_STYLE"

# Запасной вариант оформления: используется только если файл CSS не найден.
FALLBACK_STYLESHEET = """
p { margin: 0 0 7px 0; line-height: 135%; }
h1, h2, h3, h4, h5, h6 { color: #12385c; font-weight: bold; margin: 14px 0 5px 0; }
h1 { font-size: 19pt; }
h2 { font-size: 16pt; }
h3 { font-size: 13pt; }
h4, h5, h6 { font-size: 11pt; }
h1.md-title { font-size: 24pt; margin: 0 0 2px 0; }
p.md-subtitle { font-size: 12pt; color: #4a6b85; margin: 0 0 4px 0; }
p.md-note { font-size: 9pt; color: #7a8b99; margin: 0 0 8px 0; }
p.md-quote { background-color: #f1f5f9; color: #40566b; font-style: italic; margin: 6px 0 10px 8px; }
code { font-size: 9pt; background-color: #f1f3f5; }
pre { background-color: #f6f8fa; margin: 6px 0 10px 0; font-size: 9pt; }
table { border-collapse: collapse; margin: 4px 0 10px 0; }
th, td { border: 1px solid #b9c6d2; padding: 4px 7px; }
th { background-color: #e8eef4; color: #12385c; text-align: left; }
ul, ol { margin: 4px 0 8px 18px; }
li { margin: 1px 0 2px 0; }
a { color: #0b63a8; }
.md-align-center { text-align: center; }
.md-align-right { text-align: right; }
"""

# Символ обратной кавычки: ограничивает блоки кода и встроенный код.
BACKTICK = chr(96)

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
FENCE = re.compile(r"^\s*" + BACKTICK * 3)
CODE_SPAN = re.compile(BACKTICK + r"([^" + BACKTICK + r"]+)" + BACKTICK)
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
EMPHASIS_CHARS = re.compile(r"[*" + BACKTICK + r"]")
EMPHASIS_PAIR = re.compile(r"(?<!\w)[*_]([^*_\n]+)[*_](?!\w)")


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


def application_directory() -> Path:
    """Каталог, рядом с которым лежат программа и style.css."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_directory() -> Path:
    """Каталог распакованных ресурсов однофайлового .exe (PyInstaller)."""
    return Path(getattr(sys, "_MEIPASS", application_directory()))


def stylesheet_candidates(explicit: Optional[Path] = None) -> List[Path]:
    """Возможные места расположения таблицы стилей в порядке приоритета."""
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_path = os.environ.get(STYLE_ENV_VAR, "").strip()
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(application_directory() / STYLE_FILENAME)
    bundled = bundled_directory()
    if bundled != application_directory():
        candidates.append(bundled / STYLE_FILENAME)
    return candidates


def find_stylesheet(explicit: Optional[Path] = None) -> Optional[Path]:
    """Ищет существующий файл стилей; None — если не нашёл."""
    for candidate in stylesheet_candidates(explicit):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def read_stylesheet(path: Path) -> str:
    """Читает CSS в кодировке UTF-8 (допускается BOM)."""
    return Path(path).read_text(encoding="utf-8-sig")


def load_stylesheet(explicit: Optional[Path] = None) -> str:
    """Возвращает CSS из файла или встроенный запасной вариант."""
    found = find_stylesheet(explicit)
    if found is not None:
        try:
            return read_stylesheet(found)
        except OSError:
            pass
    return FALLBACK_STYLESHEET


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


def page_text_width_css(options: PdfOptions) -> int:
    """Ширина полосы набора в CSS-пикселях (1px = 1/96 дюйма).

    В этих единицах Qt понимает ширину картинки в HTML и отступы в CSS.
    """
    size = QPageSize(PAGE_SIZES.get(options.page_size, QPageSize.A4)).size(QPageSize.Millimeter)
    return max(1, int(round((size.width() - 2 * options.margin_mm) * CSS_DPI / MM_PER_INCH)))


def inline(text: str, image_width: int = 0) -> str:
    """Разметка внутри строки.

    Содержимое обратных кавычек выводится дословно, поэтому разметка внутри
    кода не срабатывает.
    """
    parts: List[str] = []
    position = 0
    for match in CODE_SPAN.finditer(text):
        parts.append(_inline_plain(text[position:match.start()], image_width))
        parts.append("<code>%s</code>" % html.escape(match.group(1), quote=False))
        position = match.end()
    parts.append(_inline_plain(text[position:], image_width))
    return "".join(parts)


def _inline_plain(text: str, image_width: int) -> str:
    escaped = html.escape(text, quote=False)
    escaped = IMAGE.sub(lambda match: _image(match, image_width), escaped)
    escaped = LINK.sub(
        lambda match: '<a href="%s">%s</a>'
        % ((match.group(2) or "").replace('"', "%22"), match.group(1)),
        escaped,
    )
    escaped = BOLD.sub(lambda match: "<b>%s</b>" % match.group(1), escaped)
    escaped = ITALIC.sub(lambda match: "<i>%s</i>" % match.group(1), escaped)
    return escaped


def _image(match, image_width: int) -> str:
    # Текст уже прошёл html.escape, поэтому здесь только чиним кавычки:
    # повторное экранирование испортило бы адреса с параметрами &.
    alt = (match.group(1) or "").replace('"', "&quot;")
    source = (match.group(2) or "").replace('"', "&quot;")
    width = ' width="%s"' % image_width if image_width > 0 else ""
    return '<img src="%s" alt="%s"%s>' % (source, alt, width)


def _cells(line: str) -> List[str]:
    match = TABLE_ROW.match(line)
    body = match.group(1) if match else line
    return [cell.strip() for cell in body.split("|")]


def _is_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(cell and set(cell) <= set("-: ") for cell in cells)


def _alignments(cells: Sequence[str], count: int) -> List[str]:
    """Выравнивание столбцов по строке-разделителю таблицы."""
    result: List[str] = []
    for cell in list(cells)[:count]:
        value = cell.strip()
        if value.startswith(":") and value.endswith(":"):
            result.append("center")
        elif value.endswith(":"):
            result.append("right")
        else:
            result.append("left")
    while len(result) < count:
        result.append("left")
    return result


def _fit(row: Sequence[str], count: int) -> List[str]:
    values = list(row)[:count]
    values.extend([""] * (count - len(values)))
    return values


def _cell(content: str, header: bool, image_width: int, align: str = "left") -> str:
    tag = "th" if header else "td"
    classes = "md-th" if header else "md-td"
    if align and align != "left":
        classes = "%s md-align-%s" % (classes, align)
    return '<%s class="%s" align="%s">%s</%s>' % (tag, classes, align, inline(content, image_width), tag)


def _table(rows: Sequence[str], image_width: int) -> str:
    parsed = [_cells(row) for row in rows]
    header = parsed[0]
    alignments = ["left"] * len(header)
    body_start = 1
    if len(parsed) > 1 and _is_separator(parsed[1]):
        alignments = _alignments(parsed[1], len(header))
        body_start = 2
    parts = ['<table class="md-table" width="100%" cellspacing="0" cellpadding="0">']
    parts.append(
        "<tr>%s</tr>"
        % "".join(_cell(value, True, image_width, alignments[index]) for index, value in enumerate(header))
    )
    for row in parsed[body_start:]:
        if _is_separator(row):
            continue
        values = _fit(row, len(header))
        parts.append(
            "<tr>%s</tr>"
            % "".join(_cell(value, False, image_width, alignments[index]) for index, value in enumerate(values))
        )
    parts.append("</table>")
    return "".join(parts)


def _list(items: Sequence[str], ordered: bool, image_width: int) -> str:
    tag = "ol" if ordered else "ul"
    body = "".join("<li>%s</li>" % inline(item, image_width) for item in items)
    return '<%s class="md-list">%s</%s>' % (tag, body, tag)


def _code_block(lines: Sequence[str]) -> str:
    body = html.escape("\n".join(lines), quote=False)
    return '<pre class="md-code">%s</pre>' % body


def _quote(lines: Sequence[str], image_width: int) -> str:
    body = inline(" ".join(line.strip() for line in lines), image_width)
    return '<p class="md-quote">%s</p>' % body


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


def plain_title(text: str) -> str:
    """Убирает из заголовка разметку, чтобы он годился для колонтитула."""
    value = IMAGE.sub(lambda match: match.group(1), text)
    value = LINK.sub(lambda match: match.group(1), value)
    value = value.replace(BACKTICK, "").replace("**", "")
    value = EMPHASIS_PAIR.sub(lambda match: match.group(1), value)
    value = EMPHASIS_CHARS.sub("", value)
    return value.strip()


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
            if level == 1 and not title:
                title = plain_title(heading.group(2))
            else:
                content = inline(heading.group(2).strip(), image_width)
                blocks.append(
                    '<h%s class="md-h%s">%s</h%s>' % (level, level, content, level)
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


def compose_html(text: str, options: Optional[PdfOptions] = None) -> Tuple[str, str]:
    """Собирает полный HTML документа: титульный блок и тело разметки."""
    settings = options or PdfOptions()
    width = settings.image_width or page_text_width_css(settings)
    title, body = markdown_to_html(text, width)

    header: List[str] = []
    if settings.title or title:
        header.append('<h1 class="md-title">%s</h1>' % html.escape(settings.title or title))
    if settings.subtitle:
        header.append('<p class="md-subtitle">%s</p>' % html.escape(settings.subtitle))
    if settings.note:
        header.append('<p class="md-note">%s</p>' % html.escape(settings.note))
    if header:
        header.append("<hr>")
    return title, "".join(header) + body


def build_document(
    text: str,
    options: Optional[PdfOptions] = None,
    base_dir: Optional[Path] = None,
    stylesheet: Optional[str] = None,
) -> QTextDocument:
    """Готовит документ к печати.

    stylesheet — текст CSS. Если не задан, стиль ищется в файле рядом с
    программой, затем во встроенных ресурсах, затем берётся запасной.
    """
    settings = options or PdfOptions()
    if not settings.base_family or not settings.mono_family:
        base, mono = register_fonts()
        settings.base_family = settings.base_family or base
        settings.mono_family = settings.mono_family or mono
    css = stylesheet if stylesheet is not None else load_stylesheet()
    family = (settings.mono_family or MONO_FAMILY_FALLBACK).replace("'", "")
    # Правило идёт первым: более поздние правила из style.css его перекрывают.
    css = "code, pre { font-family: '%s'; }\n%s" % (family, css)
    title, body = compose_html(text, settings)

    document = QTextDocument()
    font = QFont(settings.base_family)
    font.setPointSizeF(settings.font_size)
    document.setDefaultFont(font)
    document.setDefaultStyleSheet(css)
    if base_dir is not None:
        document.setBaseUrl(QUrl.fromLocalFile(str(Path(base_dir).resolve()) + os.sep))
    document.setMetaInformation(
        QTextDocument.DocumentTitle, settings.title or title or "Документ"
    )
    document.setHtml(body)
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


def convert(
    source: Path,
    target: Optional[Path] = None,
    options: Optional[PdfOptions] = None,
    stylesheet: Optional[str] = None,
) -> int:
    """Собирает PDF из файла Markdown и возвращает число страниц."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError("Нет файла разметки: %s" % source)
    settings = options or PdfOptions()
    document = build_document(
        source.read_text(encoding="utf-8-sig"), settings, source.parent, stylesheet
    )
    destination = Path(target) if target else source.with_suffix(".pdf")
    write_pdf(document, destination, settings)
    return document.pageCount()


def _configure_stdout() -> None:
    """Не даёт печати упасть на символах, которых нет в кодировке консоли."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _say(message: str) -> None:
    """Печатает в stdout, если он есть: у оконного .exe консоли может не быть."""
    if sys.stdout is not None:
        print(message)


def _warn(message: str) -> None:
    if sys.stderr is not None:
        print(message, file=sys.stderr)


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
    parser.add_argument("--image-width", type=int, default=0, help="ширина картинок, px (1/96 дюйма); 0 — по ширине набора")
    parser.add_argument("--css", default="", help="файл CSS; по умолчанию style.css рядом с программой")
    parser.add_argument("--preview", default="", help="файл изображения для проверки вида")
    parser.add_argument("--preview-pages", type=int, default=1, help="сколько страниц сохранить в изображения")
    arguments = parser.parse_args(argv)
    _configure_stdout()

    from PyQt5.QtWidgets import QApplication

    application = QApplication.instance() or QApplication(sys.argv[:1])
    try:
        explicit = Path(arguments.css) if arguments.css else None
        if explicit is not None and not explicit.is_file():
            _warn("предупреждение: нет файла CSS %s" % explicit)
        found = find_stylesheet(explicit)
        stylesheet = load_stylesheet(explicit)
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
            pages = convert(source, target, options, stylesheet)
        except (FileNotFoundError, OSError) as error:
            _warn("ошибка: %s" % error)
            return 2
        _say("готово: %s, страниц %s, шрифт %s" % (target, pages, options.base_family))
        _say("стиль: %s" % (found or "встроенный"))
        if arguments.preview:
            document = build_document(
                source.read_text(encoding="utf-8-sig"), options, source.parent, stylesheet
            )
            write_preview(document, Path(arguments.preview), arguments.preview_pages, options)
            _say("вид: %s" % arguments.preview)
        return 0
    finally:
        del application


if __name__ == "__main__":
    sys.exit(main())
