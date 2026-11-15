"""Тесты конвертера Markdown → PDF и графической оболочки.

Запуск:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Тесты идут без экрана: Qt всё равно нужен для QTextDocument.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QSettings
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QApplication

import md2pdf

APPLICATION = QApplication.instance() or QApplication([])
BASE_FAMILY, MONO_FAMILY = md2pdf.register_fonts()
BACKTICK = chr(96)
FENCE = BACKTICK * 3


def make_options(**overrides) -> md2pdf.PdfOptions:
    values = {"base_family": BASE_FAMILY, "mono_family": MONO_FAMILY}
    values.update(overrides)
    return md2pdf.PdfOptions(**values)


class MarkdownTests(unittest.TestCase):
    def test_first_heading_becomes_title(self):
        title, html = md2pdf.markdown_to_html("# Мой **документ**\n\n## Раздел\n")
        self.assertEqual(title, "Мой документ")
        self.assertIn('<h2 class="md-h2">Раздел</h2>', html)
        self.assertNotIn("md-title", html)

    def test_second_h1_stays_heading(self):
        title, html = md2pdf.markdown_to_html("# Первый\n\n# Второй\n")
        self.assertEqual(title, "Первый")
        self.assertIn('<h1 class="md-h1">Второй</h1>', html)

    def test_inline_code_is_literal(self):
        _title, html = md2pdf.markdown_to_html("Текст " + BACKTICK + "**x**" + BACKTICK + " и **жирный**\n")
        self.assertIn("<code>**x**</code>", html)
        self.assertIn("<b>жирный</b>", html)
        self.assertNotIn("<code><b>", html)

    def test_links_images_emphasis(self):
        _title, html = md2pdf.markdown_to_html(
            "**жирный**, *курсив*, [сайт](https://example.com), ![схема](img/a.png)\n",
            300,
        )
        self.assertIn("<b>жирный</b>", html)
        self.assertIn("<i>курсив</i>", html)
        self.assertIn('<a href="https://example.com">сайт</a>', html)
        self.assertIn('<img src="img/a.png" alt="схема" width="300">', html)

    def test_table_alignment(self):
        text = "| Слева | Центр | Справа |\n|:------|:-----:|-------:|\n| 1 | 2 | 3 |\n"
        _title, html = md2pdf.markdown_to_html(text)
        self.assertIn('<table class="md-table" width="100%"', html)
        self.assertIn('<th class="md-th" align="left">Слева</th>', html)
        self.assertIn('<th class="md-th md-align-center" align="center">Центр</th>', html)
        self.assertIn('<th class="md-th md-align-right" align="right">Справа</th>', html)
        self.assertIn('<td class="md-td" align="left">1</td>', html)
        self.assertIn('<td class="md-td md-align-right" align="right">3</td>', html)

    def test_lists_use_ul_and_ol(self):
        _title, html = md2pdf.markdown_to_html("- один\n- два\n\n1. первый\n2. второй\n")
        self.assertIn('<ul class="md-list"><li>один</li><li>два</li></ul>', html)
        self.assertIn('<ol class="md-list"><li>первый</li><li>второй</li></ol>', html)

    def test_code_fence_escapes_html(self):
        text = FENCE + "\n<div>&\n" + FENCE + "\n"
        _title, html = md2pdf.markdown_to_html(text)
        self.assertIn('<pre class="md-code">&lt;div&gt;&amp;</pre>', html)

    def test_quote_and_rule(self):
        _title, html = md2pdf.markdown_to_html("> цитата\n\n---\n")
        self.assertIn('<p class="md-quote">цитата</p>', html)
        self.assertIn("<hr>", html)

    def test_compose_html_header(self):
        options = md2pdf.PdfOptions(
            title="Заголовок", subtitle="Подзаголовок", note="Примечание"
        )
        _title, html = md2pdf.compose_html("# Из файла\n", options)
        self.assertIn('<h1 class="md-title">Заголовок</h1>', html)
        self.assertIn('<p class="md-subtitle">Подзаголовок</p>', html)
        self.assertIn('<p class="md-note">Примечание</p>', html)

    def test_image_width_in_css_pixels(self):
        options = md2pdf.PdfOptions(margin_mm=16.0)
        _title, html = md2pdf.compose_html("![x](img.png)\n", options)
        expected = md2pdf.image_max_width(options)
        self.assertIn('width="%s"' % expected, html)
        self.assertGreater(expected, 600)
        self.assertLess(expected, 700)


class StylesheetTests(unittest.TestCase):
    def test_explicit_stylesheet_wins(self):
        with tempfile.TemporaryDirectory() as raw:
            first = Path(raw) / "a.css"
            second = Path(raw) / "b.css"
            first.write_text("p { color: #123456; }", encoding="utf-8")
            second.write_text("p { color: #654321; }", encoding="utf-8")
            self.assertEqual(md2pdf.find_stylesheet(first), first)
            self.assertEqual(md2pdf.load_stylesheet(first).strip(), "p { color: #123456; }")

    def test_environment_variable(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "style.css"
            path.write_text("p { color: #abcdef; }", encoding="utf-8")
            with mock.patch.dict(os.environ, {"MD2PDF_STYLE": str(path)}):
                self.assertEqual(md2pdf.find_stylesheet(), path)
                self.assertIn("#abcdef", md2pdf.load_stylesheet())

    def test_fallback_when_nothing_found(self):
        with tempfile.TemporaryDirectory() as raw:
            empty = Path(raw)
            with mock.patch.dict(os.environ, {"MD2PDF_STYLE": ""}):
                with mock.patch.object(md2pdf, "application_directory", return_value=empty):
                    with mock.patch.object(md2pdf, "bundled_directory", return_value=empty):
                        self.assertIsNone(md2pdf.find_stylesheet())
                        self.assertEqual(
                            md2pdf.load_stylesheet(), md2pdf.FALLBACK_STYLESHEET
                        )


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def write_source(self, text="# Заголовок\n\nАбзац **жирный** и " + BACKTICK + "код" + BACKTICK + ".\n"):
        source = self.directory / "note.md"
        source.write_text(text, encoding="utf-8")
        return source

    def test_convert_writes_pdf(self):
        source = self.write_source()
        pages = md2pdf.convert(source, options=make_options())
        target = source.with_suffix(".pdf")
        self.assertTrue(target.is_file())
        self.assertGreaterEqual(pages, 1)
        self.assertTrue(target.read_bytes().startswith(b"%PDF"))
        self.assertGreater(target.stat().st_size, 1000)

    def test_custom_stylesheet_applied(self):
        source = self.write_source()
        css = "h1 { color: #ff0000; } p { color: #00ff00; }"
        document = md2pdf.build_document(
            source.read_text(encoding="utf-8"), make_options(), source.parent, css
        )
        html = document.toHtml()
        self.assertIn("#ff0000", html)
        self.assertIn("#00ff00", html)

    def test_monochrome_stylesheet_builds(self):
        source = self.write_source()
        css = (ROOT / "monochrome.css").read_text(encoding="utf-8-sig")
        target = self.directory / "mono.pdf"
        pages = md2pdf.convert(source, target, make_options(), css)
        self.assertTrue(target.is_file())
        self.assertGreaterEqual(pages, 1)
        html = md2pdf.build_document(
            source.read_text(encoding="utf-8"), make_options(), source.parent, css
        ).toHtml()
        self.assertNotIn("#0b63a8", html)
        self.assertNotIn("#12385c", html)

    def test_images_keep_natural_size_and_fit_column(self):
        from PyQt5.QtGui import QColor, QImage

        for name, width, height in (("small.png", 200, 100), ("large.png", 1200, 600)):
            image = QImage(width, height, QImage.Format_RGB32)
            image.fill(QColor("white"))
            self.assertTrue(image.save(str(self.directory / name)))
        text = "![small](small.png)\n\n![large](large.png)\n"
        options = make_options(margin_mm=16.0)
        _title, html = md2pdf.compose_html(text, options, self.directory)
        self.assertIn('width="200" height="100"', html)
        self.assertIn('width="%d" height="332"' % md2pdf.image_max_width(options), html)
        # Без base_dir натуральный размер неизвестен — берётся ширина набора.
        _title, html = md2pdf.compose_html(text, options)
        self.assertNotIn('width="200"', html)
        # Ключ --image-width ограничивает ширину сверху.
        limited = make_options(margin_mm=16.0, image_width=150)
        _title, html = md2pdf.compose_html(text, limited, self.directory)
        self.assertIn('width="150"', html)

    def test_preview_image(self):
        source = self.write_source()
        options = make_options(resolution=150)
        document = md2pdf.build_document(
            source.read_text(encoding="utf-8"), options, source.parent
        )
        target = self.directory / "preview.png"
        md2pdf.write_preview(document, target, 1, options)
        self.assertTrue(target.is_file())
        self.assertGreater(target.stat().st_size, 500)

    def test_cli_main(self):
        source = self.write_source()
        target = self.directory / "cli.pdf"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = md2pdf.main([str(source), "-o", str(target)])
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())
        self.assertIn("готово", output.getvalue())


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QSettings.setDefaultFormat(QSettings.IniFormat)
        cls._settings = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls._settings.name)

    @classmethod
    def tearDownClass(cls):
        cls._settings.cleanup()

    def _make_window(self):
        import md2pdf_gui

        settings = QSettings(
            str(Path(self._settings.name) / "gui.ini"), QSettings.IniFormat
        )
        settings.clear()
        return md2pdf_gui.MainWindow(settings=settings)

    def test_ui_file_from_env(self):
        import md2pdf_gui

        path = (ROOT / "design" / "main_window.ui").resolve()
        with mock.patch.dict(os.environ, {"MD2PDF_UI": str(path)}):
            self.assertEqual(md2pdf_gui.find_ui_file().resolve(), path)

    def test_ui_file_and_title_toggle(self):
        import md2pdf_gui

        path = md2pdf_gui.find_ui_file()
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "main_window.ui")
        window = self._make_window()
        try:
            self.assertIsNotNone(window.fileList)
            self.assertTrue(window.titleFields.isHidden())
            window.titleGroup.setChecked(True)
            self.assertFalse(window.titleFields.isHidden())
        finally:
            window.close()

    def test_window_adds_unique_files_and_previews(self):
        window = self._make_window()
        try:
            demo = ROOT / "examples" / "demo.md"
            window.add_paths([demo, demo])
            self.assertEqual(window.fileList.count(), 1)
            self.assertIn("style.css", window.cssEdit.text())
            window.refresh_preview()
            self.assertGreater(len(window.preview.toPlainText()), 500)
        finally:
            window.close()

    def test_conversion_in_worker(self):
        import time

        window = self._make_window()
        application = QApplication.instance()
        try:
            window.add_paths([ROOT / "examples" / "demo.md"])
            window.dirRadio.setChecked(True)
            window.dirEdit.setText(str(self._settings.name))
            window.overwriteCheck.setChecked(True)
            window._start_conversion()
            deadline = time.time() + 60
            while window._worker is not None and time.time() < deadline:
                application.processEvents()
                time.sleep(0.02)
            self.assertIsNone(window._worker)
            self.assertEqual(len(window._produced), 1)
            self.assertTrue(window._produced[0].is_file())
            self.assertTrue(window._produced[0].read_bytes().startswith(b"%PDF"))
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
