"""Графическая оболочка конвертера Markdown в PDF (md2pdf.py).

Возможности:
    * выбор одного или нескольких файлов .md (кнопкой, из проводника);
    * перетаскивание файлов в список;
    * настройки страницы и титульного блока;
    * выбор файла CSS и панель предпросмотра, использующая тот же стиль;
    * сборка PDF в отдельном потоке, журнал и прогресс;
    * сохранение настроек между запусками.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PyQt5.QtCore import (
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
    QTextDocument,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import md2pdf

APP_NAME = "md2pdf-gui"
ORGANIZATION = "md2pdf"
APP_TITLE = "Markdown → PDF"

MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdown", ".mkd", ".mdtxt")
MARKDOWN_FILTER = "Markdown (*.md *.markdown *.mdown *.mkd);;Все файлы (*)"

LOG_COLORS = {
    "info": "#24292f",
    "ok": "#1a7f37",
    "warn": "#9a6700",
    "error": "#b42318",
    "muted": "#6b7785",
}

APP_STYLE = """
QPushButton#convertButton {
    background-color: #12385c;
    color: #ffffff;
    border: none;
    border-radius: 5px;
    padding: 10px 16px;
    font-size: 10pt;
    font-weight: bold;
}
QPushButton#convertButton:hover { background-color: #1b4d7a; }
QPushButton#convertButton:pressed { background-color: #0d2a45; }
QPushButton#convertButton:disabled { background-color: #9db2c4; color: #eef3f7; }
QListWidget, QTextBrowser, QTextEdit { background-color: #ffffff; border: 1px solid #d5dbe1; border-radius: 4px; }
QGroupBox { margin-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 3px; color: #12385c; }
QLineEdit[readOnly="true"] { background-color: #f6f8fa; color: #4a5560; }
"""


def is_markdown(path: Path) -> bool:
    """Похож ли файл на разметку Markdown."""
    return path.suffix.lower() in MARKDOWN_SUFFIXES


def paths_from_mime(mime) -> List[Path]:
    """Достаёт из перетаскиваемых данных локальные файлы Markdown."""
    result: List[Path] = []
    if not mime.hasUrls():
        return result
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        try:
            if path.is_file() and is_markdown(path):
                result.append(path)
        except OSError:
            continue
    return result


def application_icon() -> QIcon:
    """Иконка приложения рисуется кодом, чтобы не тащить отдельный файл."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#12385c"))
        radius = max(2.0, size * 0.22)
        painter.drawRoundedRect(0, 0, size, size, radius, radius)
        font = QFont("Segoe UI", max(5, int(size * 0.31)), QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "MD")
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class CollapsibleSection(QWidget):
    """Небольшой раздел с заголовком-кнопкой: содержимое можно свернуть."""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle.setStyleSheet(
            "QToolButton { border: none; color: #12385c; font-weight: bold;"
            " padding: 2px 0; text-align: left; }"
        )
        self.toggle.clicked.connect(self._on_toggle)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 4, 8, 8)
        self.content_layout.setSpacing(6)
        self.content.setVisible(expanded)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)

    def _on_toggle(self) -> None:
        visible = self.toggle.isChecked()
        self.toggle.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)
        self.content.setVisible(visible)

    def add_layout(self, layout) -> None:
        self.content_layout.addLayout(layout)


class FileListWidget(QListWidget):
    """Список файлов, принимающий перетаскивание из проводника."""

    filesDropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(16, 16))
        self.setMinimumHeight(116)
        self.setToolTip("Перетащите сюда файлы .md или нажмите «Добавить»")

    def dragEnterEvent(self, event):
        if paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = paths_from_mime(event.mimeData())
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class ConvertWorker(QThread):
    """Сборка PDF в отдельном потоке, чтобы окно оставалось отзывчивым."""

    progressChanged = pyqtSignal(int, int, str)
    itemConverted = pyqtSignal(str, str, int)
    itemSkipped = pyqtSignal(str, str)
    itemFailed = pyqtSignal(str, str)
    finishedAll = pyqtSignal(int, int, int)

    def __init__(
        self,
        jobs: Sequence[Tuple[Path, Path]],
        options: md2pdf.PdfOptions,
        stylesheet: str,
        overwrite: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._jobs = list(jobs)
        self._options = options
        self._stylesheet = stylesheet
        self._overwrite = overwrite
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        converted = 0
        failed = 0
        skipped = 0
        total = len(self._jobs)
        for index, (source, target) in enumerate(self._jobs, 1):
            if self._cancelled:
                break
            self.progressChanged.emit(index - 1, total, source.name)
            if target.exists() and not self._overwrite:
                skipped += 1
                self.itemSkipped.emit(str(source), str(target))
                self.progressChanged.emit(index, total, source.name)
                continue
            try:
                text = source.read_text(encoding="utf-8-sig")
                document = md2pdf.build_document(
                    text, self._options, source.parent, self._stylesheet
                )
                md2pdf.write_pdf(document, target, self._options)
                converted += 1
                self.itemConverted.emit(str(source), str(target), document.pageCount())
            except Exception as error:  # noqa: BLE001 — в журнал уходит любой сбой
                failed += 1
                self.itemFailed.emit(
                    str(source), "%s: %s" % (type(error).__name__, error)
                )
            self.progressChanged.emit(index, total, source.name)
        self.finishedAll.emit(converted, failed, skipped)


class MainWindow(QMainWindow):
    """Главное окно: список файлов, параметры, предпросмотр и журнал."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("%s — конвертер Markdown" % APP_TITLE)
        self.setWindowIcon(application_icon())
        self.resize(1220, 800)
        self.setMinimumSize(1000, 660)
        self.setAcceptDrops(True)

        self.settings = QSettings(ORGANIZATION, APP_NAME)
        self.base_family, self.mono_family = md2pdf.register_fonts()
        self._stylesheet_override: Optional[Path] = None
        self._worker: Optional[ConvertWorker] = None
        self._converting = False
        self._produced: List[Path] = []
        self._pending_summary: Tuple[int, int, int] = (0, 0, 0)
        self._cancelled_by_user = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self.refresh_preview)

        self._build_ui()
        self._build_menus()
        self._connect_signals()
        self._restore_settings()
        self._update_output_mode()
        self._refresh_css_label()
        self._update_actions()
        self._schedule_preview()
        self._update_status()
        self._log("Готово к работе. Добавьте файлы .md кнопкой или перетащите их в список.", "muted")

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.setStyleSheet(APP_STYLE)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        self.mainSplitter = splitter
        self.setCentralWidget(splitter)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 740])

        self.status = self.statusBar()
        self.fileCountLabel = QLabel("Файлов: 0")
        self.status.addPermanentWidget(self.fileCountLabel)

    def _build_left_panel(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # --- список файлов -------------------------------------------------
        files_group = QGroupBox("Файлы Markdown")
        files_layout = QVBoxLayout(files_group)
        files_layout.setContentsMargins(8, 10, 8, 8)
        files_layout.setSpacing(6)

        self.fileList = FileListWidget()
        files_layout.addWidget(self.fileList, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.addButton = QPushButton("Добавить…")
        self.addButton.setToolTip("Выбрать один или несколько файлов (Ctrl+O)")
        self.removeButton = QPushButton("Удалить")
        self.removeButton.setToolTip("Убрать выбранные файлы из списка (Delete)")
        self.clearButton = QPushButton("Очистить")
        buttons.addWidget(self.addButton)
        buttons.addWidget(self.removeButton)
        buttons.addWidget(self.clearButton)
        buttons.addStretch(1)
        files_layout.addLayout(buttons)

        hint = QLabel("Файлы можно перетащить из проводника.")
        hint.setStyleSheet("color: #6b7785;")
        files_layout.addWidget(hint)
        layout.addWidget(files_group, 1)

        # --- параметры страницы -------------------------------------------
        page_group = QGroupBox("Параметры страницы")
        page_grid = QGridLayout(page_group)
        page_grid.setContentsMargins(8, 10, 8, 8)
        page_grid.setHorizontalSpacing(8)
        page_grid.setVerticalSpacing(6)

        self.pageCombo = QComboBox()
        for name in ("A4", "A5", "A3", "Letter"):
            self.pageCombo.addItem(name)
        self.fontSpin = QDoubleSpinBox()
        self.fontSpin.setRange(6.0, 28.0)
        self.fontSpin.setSingleStep(0.5)
        self.fontSpin.setDecimals(1)
        self.fontSpin.setSuffix(" пт")
        self.fontSpin.setValue(10.0)
        self.fontSpin.setMaximumWidth(110)
        self.marginSpin = QDoubleSpinBox()
        self.marginSpin.setRange(0.0, 40.0)
        self.marginSpin.setSingleStep(1.0)
        self.marginSpin.setDecimals(1)
        self.marginSpin.setSuffix(" мм")
        self.marginSpin.setValue(16.0)
        self.marginSpin.setMaximumWidth(110)

        page_grid.addWidget(QLabel("Формат:"), 0, 0)
        page_grid.addWidget(self.pageCombo, 0, 1)
        page_grid.addWidget(QLabel("Кегль:"), 0, 2)
        page_grid.addWidget(self.fontSpin, 0, 3)
        page_grid.addWidget(QLabel("Поля:"), 1, 0)
        page_grid.addWidget(self.marginSpin, 1, 1)
        page_grid.setColumnStretch(1, 1)
        layout.addWidget(page_group)

        # --- CSS -----------------------------------------------------------
        css_group = QGroupBox("Оформление (CSS)")
        css_layout = QVBoxLayout(css_group)
        css_layout.setContentsMargins(8, 10, 8, 8)
        css_layout.setSpacing(6)

        css_row = QHBoxLayout()
        css_row.setSpacing(6)
        self.cssEdit = QLineEdit()
        self.cssEdit.setReadOnly(True)
        self.cssEdit.setPlaceholderText("встроенный стиль")
        self.cssChooseButton = QPushButton("Выбрать…")
        self.cssChooseButton.setToolTip("Указать другой файл CSS")
        self.cssOpenButton = QPushButton("Изменить")
        self.cssOpenButton.setToolTip("Открыть CSS в редакторе по умолчанию")
        self.cssResetButton = QPushButton("Сбросить")
        self.cssResetButton.setToolTip("Вернуться к style.css рядом с программой")
        css_row.addWidget(self.cssEdit, 1)
        css_row.addWidget(self.cssChooseButton)
        css_layout.addLayout(css_row)

        css_buttons = QHBoxLayout()
        css_buttons.setSpacing(6)
        css_buttons.addWidget(self.cssOpenButton)
        css_buttons.addWidget(self.cssResetButton)
        css_buttons.addStretch(1)
        css_layout.addLayout(css_buttons)

        css_hint = QLabel(
            "Файл style.css рядом с программой подхватывается сам. "
            "Правки в CSS сразу видны в предпросмотре."
        )
        css_hint.setWordWrap(True)
        css_hint.setStyleSheet("color: #6b7785;")
        css_layout.addWidget(css_hint)
        layout.addWidget(css_group)

        # --- титульный блок: свёрнут, чтобы не занимать место ---------------
        self.titleSection = CollapsibleSection("Титульный блок (необязательно)", False)
        title_form = QFormLayout()
        title_form.setContentsMargins(0, 0, 0, 0)
        title_form.setSpacing(6)
        self.titleEdit = QLineEdit()
        self.titleEdit.setPlaceholderText("Пусто — берётся первый заголовок #")
        self.subtitleEdit = QLineEdit()
        self.subtitleEdit.setPlaceholderText("Необязательно")
        self.noteEdit = QLineEdit()
        self.noteEdit.setPlaceholderText("Необязательно, мелким шрифтом")
        title_form.addRow("Заголовок:", self.titleEdit)
        title_form.addRow("Подзаголовок:", self.subtitleEdit)
        title_form.addRow("Примечание:", self.noteEdit)
        self.titleSection.add_layout(title_form)
        layout.addWidget(self.titleSection)

        # --- сохранение ----------------------------------------------------
        output_group = QGroupBox("Сохранение")
        output_layout = QVBoxLayout(output_group)
        output_layout.setContentsMargins(8, 10, 8, 8)
        output_layout.setSpacing(6)

        self.nearRadio = QRadioButton("Рядом с исходными файлами")
        self.nearRadio.setChecked(True)
        self.dirRadio = QRadioButton("В выбранную папку:")
        output_layout.addWidget(self.nearRadio)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        self.dirEdit = QLineEdit()
        self.dirEdit.setPlaceholderText("Папка для PDF")
        self.dirBrowseButton = QPushButton("…")
        self.dirBrowseButton.setFixedWidth(32)
        self.dirBrowseButton.setToolTip("Выбрать папку")
        dir_row.addWidget(self.dirEdit, 1)
        dir_row.addWidget(self.dirBrowseButton)
        output_layout.addWidget(self.dirRadio)
        output_layout.addLayout(dir_row)

        self.overwriteCheck = QCheckBox("Перезаписывать существующие PDF")
        self.overwriteCheck.setChecked(True)
        self.openAfterCheck = QCheckBox("Открыть первый PDF после сборки")
        output_layout.addWidget(self.overwriteCheck)
        output_layout.addWidget(self.openAfterCheck)
        layout.addWidget(output_group)

        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)

        # --- запуск: кнопка и итог всегда на виду, вне прокрутки -----------
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(10, 8, 10, 10)
        footer_layout.setSpacing(6)

        self.convertButton = QPushButton("Собрать PDF")
        self.convertButton.setObjectName("convertButton")
        self.convertButton.setMinimumHeight(44)
        self.convertButton.setToolTip("Собрать PDF из файлов списка (Ctrl+Enter)")
        footer_layout.addWidget(self.convertButton)

        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 1)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setFormat("%v из %m")
        self.progressBar.setVisible(False)
        footer_layout.addWidget(self.progressBar)

        self.summaryLabel = QLabel("")
        self.summaryLabel.setWordWrap(True)
        self.summaryLabel.setStyleSheet("color: #4a5560;")
        footer_layout.addWidget(self.summaryLabel)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("color: #d8dee4;")

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        panel_layout.addWidget(scroll, 1)
        panel_layout.addWidget(separator)
        panel_layout.addWidget(footer)
        panel.setMinimumWidth(430)
        panel.setMaximumWidth(600)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 10, 10, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Предпросмотр")
        title.setStyleSheet("font-weight: bold; color: #12385c;")
        header.addWidget(title)
        header.addStretch(1)
        self.previewCheck = QCheckBox("Показывать")
        self.previewCheck.setChecked(True)
        self.refreshButton = QPushButton("Обновить")
        self.refreshButton.setToolTip("Перерисовать предпросмотр (F5)")
        header.addWidget(self.previewCheck)
        header.addWidget(self.refreshButton)
        layout.addLayout(header)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setMinimumHeight(240)

        log_title = QLabel("Журнал")
        log_title.setStyleSheet("font-weight: bold; color: #12385c;")
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        self.log.setMinimumHeight(100)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        vertical = QSplitter(Qt.Vertical)
        vertical.setChildrenCollapsible(False)
        preview_box = QWidget()
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)
        preview_layout.addWidget(self.preview)
        log_box = QWidget()
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(4)
        log_layout.addWidget(log_title)
        log_layout.addWidget(self.log)
        vertical.addWidget(preview_box)
        vertical.addWidget(log_box)
        vertical.setSizes([560, 160])
        self.verticalSplitter = vertical
        layout.addWidget(vertical, 1)
        return panel

    def _build_menus(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&Файл")
        self.addActionShortcut(file_menu, "Добавить файлы…", "Ctrl+O", self._add_files)
        self.addActionShortcut(file_menu, "Удалить выбранные", "Delete", self._remove_selected)
        self.addActionShortcut(file_menu, "Очистить список", "Ctrl+Shift+Delete", self._clear_files)
        file_menu.addSeparator()
        self.addActionShortcut(file_menu, "Собрать PDF", "Ctrl+Return", self._start_conversion)
        file_menu.addSeparator()
        self.addActionShortcut(file_menu, "Выход", "Ctrl+Q", self.close)

        service_menu = menu.addMenu("&Сервис")
        self.addActionShortcut(service_menu, "Обновить предпросмотр", "F5", self.refresh_preview)
        self.addActionShortcut(service_menu, "Открыть CSS в редакторе", "", self._open_css)
        self.addActionShortcut(service_menu, "Открыть папку с PDF", "", self._open_output_folder)
        service_menu.addSeparator()
        self.addActionShortcut(service_menu, "Открыть папку программы", "", self._open_app_folder)

        help_menu = menu.addMenu("&Справка")
        self.addActionShortcut(help_menu, "О программе", "", self._show_about)

    def addActionShortcut(self, menu: QMenu, text: str, shortcut: str, handler) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(handler)
        menu.addAction(action)
        return action

    def _connect_signals(self) -> None:
        self.addButton.clicked.connect(self._add_files)
        self.removeButton.clicked.connect(self._remove_selected)
        self.clearButton.clicked.connect(self._clear_files)
        self.fileList.filesDropped.connect(self._append_files)
        self.fileList.itemSelectionChanged.connect(self._schedule_preview)

        self.cssChooseButton.clicked.connect(self._choose_css)
        self.cssOpenButton.clicked.connect(self._open_css)
        self.cssResetButton.clicked.connect(self._reset_css)

        self.nearRadio.toggled.connect(self._update_output_mode)
        self.dirRadio.toggled.connect(self._update_output_mode)
        self.dirBrowseButton.clicked.connect(self._choose_output_dir)
        self.previewCheck.toggled.connect(self._schedule_preview)
        self.refreshButton.clicked.connect(self.refresh_preview)
        self.convertButton.clicked.connect(self._on_convert_clicked)

        self.pageCombo.currentTextChanged.connect(self._schedule_preview)
        self.fontSpin.valueChanged.connect(self._schedule_preview)
        self.marginSpin.valueChanged.connect(self._schedule_preview)
        self.titleEdit.textChanged.connect(self._schedule_preview)
        self.subtitleEdit.textChanged.connect(self._schedule_preview)
        self.noteEdit.textChanged.connect(self._schedule_preview)

    # ------------------------------------------------------------- settings

    def _restore_settings(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        main_state = self.settings.value("main_splitter")
        if main_state:
            self.mainSplitter.restoreState(main_state)
        vertical_state = self.settings.value("vertical_splitter")
        if vertical_state:
            self.verticalSplitter.restoreState(vertical_state)

        page = self.settings.value("page", "A4", type=str)
        index = self.pageCombo.findText(page)
        if index >= 0:
            self.pageCombo.setCurrentIndex(index)
        self.fontSpin.setValue(self.settings.value("font_size", 10.0, type=float))
        self.marginSpin.setValue(self.settings.value("margin", 16.0, type=float))

        self.dirEdit.setText(self.settings.value("output_dir", "", type=str))
        mode = self.settings.value("output_mode", "near", type=str)
        self.dirRadio.setChecked(mode == "dir")
        self.nearRadio.setChecked(mode != "dir")
        self.overwriteCheck.setChecked(self.settings.value("overwrite", True, type=bool))
        self.openAfterCheck.setChecked(self.settings.value("open_after", False, type=bool))
        self.previewCheck.setChecked(self.settings.value("preview", True, type=bool))

        css = self.settings.value("css_override", "", type=str)
        if css:
            self._stylesheet_override = Path(css)
        if not self.settings.value("splitter_set", False, type=bool):
            self.mainSplitter.setSizes([480, 740])
            self.verticalSplitter.setSizes([560, 160])

    def _save_settings(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("main_splitter", self.mainSplitter.saveState())
        self.settings.setValue("vertical_splitter", self.verticalSplitter.saveState())
        self.settings.setValue("splitter_set", True)
        self.settings.setValue("page", self.pageCombo.currentText())
        self.settings.setValue("font_size", self.fontSpin.value())
        self.settings.setValue("margin", self.marginSpin.value())
        self.settings.setValue("output_dir", self.dirEdit.text())
        self.settings.setValue("output_mode", "dir" if self.dirRadio.isChecked() else "near")
        self.settings.setValue("overwrite", self.overwriteCheck.isChecked())
        self.settings.setValue("open_after", self.openAfterCheck.isChecked())
        self.settings.setValue("preview", self.previewCheck.isChecked())
        self.settings.setValue(
            "css_override", str(self._stylesheet_override) if self._stylesheet_override else ""
        )

    # ---------------------------------------------------------------- files

    def _sources(self) -> List[Path]:
        result: List[Path] = []
        for row in range(self.fileList.count()):
            item = self.fileList.item(row)
            value = item.data(Qt.UserRole)
            if value:
                result.append(Path(value))
        return result

    def _current_source(self) -> Optional[Path]:
        items = self.fileList.selectedItems()
        if items:
            value = items[0].data(Qt.UserRole)
            if value:
                return Path(value)
        if self.fileList.count():
            value = self.fileList.item(0).data(Qt.UserRole)
            if value:
                return Path(value)
        return None

    def _add_files(self) -> None:
        start = self.settings.value("last_input_dir", str(Path.home()), type=str)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выберите файлы Markdown", start, MARKDOWN_FILTER
        )
        if not paths:
            return
        self.settings.setValue("last_input_dir", str(Path(paths[0]).parent))
        self._append_files([Path(path) for path in paths])

    def _append_files(self, paths: Sequence[Path]) -> None:
        known = {str(path).lower() for path in self._sources()}
        added = 0
        for path in paths:
            key = str(path).lower()
            if key in known:
                continue
            if not is_markdown(path):
                self._log("пропуск (не Markdown): %s" % path, "warn")
                continue
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            item.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))
            self.fileList.addItem(item)
            known.add(key)
            added += 1
        if added:
            self._log("добавлено файлов: %s" % added, "info")
            self.status.showMessage("Добавлено файлов: %s" % added, 4000)
        self._update_status()
        if self.fileList.count() and not self.fileList.selectedItems():
            self.fileList.setCurrentRow(self.fileList.count() - 1)
        self._schedule_preview()
        self._update_actions()

    def _remove_selected(self) -> None:
        rows = sorted(
            (self.fileList.row(item) for item in self.fileList.selectedItems()),
            reverse=True,
        )
        for row in rows:
            self.fileList.takeItem(row)
        if rows:
            self.status.showMessage("Удалено файлов: %s" % len(rows), 3000)
        self._update_status()
        self._schedule_preview()
        self._update_actions()

    def _clear_files(self) -> None:
        self.fileList.clear()
        self.status.showMessage("Список файлов очищен", 3000)
        self._update_status()
        self._schedule_preview()
        self._update_actions()

    def _update_status(self) -> None:
        count = self.fileList.count()
        self.fileCountLabel.setText("Файлов: %s" % count)

    # ------------------------------------------------------------------ CSS

    def _stylesheet_path(self) -> Optional[Path]:
        if self._stylesheet_override is not None:
            path = Path(self._stylesheet_override)
            return path if path.is_file() else None
        return md2pdf.find_stylesheet()

    def _stylesheet_text(self) -> str:
        path = self._stylesheet_path()
        if path is None:
            return md2pdf.FALLBACK_STYLESHEET
        try:
            return md2pdf.read_stylesheet(path)
        except OSError as error:
            self._log("не удалось прочитать CSS: %s" % error, "error")
            return md2pdf.FALLBACK_STYLESHEET

    def _refresh_css_label(self) -> None:
        path = self._stylesheet_path()
        if path is not None:
            self.cssEdit.setText(str(path))
            self.cssEdit.setToolTip("Применяется: %s" % path)
            self.cssOpenButton.setEnabled(True)
        elif self._stylesheet_override is not None:
            self.cssEdit.setText("не найден: %s" % self._stylesheet_override)
            self.cssEdit.setToolTip("Файл не найден, используется встроенный стиль")
            self.cssOpenButton.setEnabled(False)
        else:
            self.cssEdit.setText("встроенный стиль")
            self.cssEdit.setToolTip(
                "Положите style.css рядом с программой, чтобы менять оформление"
            )
            self.cssOpenButton.setEnabled(False)
        self.cssResetButton.setEnabled(self._stylesheet_override is not None)

    def _choose_css(self) -> None:
        start = str(self._stylesheet_path() or md2pdf.application_directory())
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл стилей", start, "CSS (*.css);;Все файлы (*)"
        )
        if not path:
            return
        self._stylesheet_override = Path(path)
        self._refresh_css_label()
        self._log("стиль: %s" % path, "info")
        self._schedule_preview()

    def _reset_css(self) -> None:
        self._stylesheet_override = None
        self._refresh_css_label()
        path = self._stylesheet_path()
        self._log(
            "стиль сброшен: %s" % (path if path else "встроенный"), "info"
        )
        self._schedule_preview()

    def _open_css(self) -> None:
        path = self._stylesheet_path()
        if path is None:
            QMessageBox.information(
                self,
                "Файл стилей не найден",
                "Рядом с программой нет style.css, используется встроенный стиль.\n"
                "Положите style.css в папку программы или выберите другой файл.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # --------------------------------------------------------------- output

    def _update_output_mode(self) -> None:
        by_folder = self.dirRadio.isChecked()
        self.dirEdit.setEnabled(by_folder)
        self.dirBrowseButton.setEnabled(by_folder)

    def _choose_output_dir(self) -> None:
        start = self.dirEdit.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Выберите папку для PDF", start)
        if path:
            self.dirEdit.setText(path)
            self.dirRadio.setChecked(True)

    def _output_directory(self) -> Optional[Path]:
        if not self.dirRadio.isChecked():
            return None
        raw = self.dirEdit.text().strip()
        if not raw:
            return None
        return Path(raw)

    def _unique_target(self, target: Path, used: set) -> Path:
        if target not in used:
            return target
        counter = 2
        while True:
            candidate = target.with_name(
                "%s (%s)%s" % (target.stem, counter, target.suffix)
            )
            if candidate not in used:
                return candidate
            counter += 1

    def _open_output_folder(self) -> None:
        directory: Optional[Path] = None
        if self._produced:
            directory = self._produced[-1].parent
        else:
            directory = self._output_directory()
            if directory is None:
                sources = self._sources()
                directory = sources[0].parent if sources else md2pdf.application_directory()
        if directory is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _open_app_folder(self) -> None:
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(md2pdf.application_directory()))
        )

    # ------------------------------------------------------------- conversion

    def _options(self) -> md2pdf.PdfOptions:
        return md2pdf.PdfOptions(
            title=self.titleEdit.text().strip(),
            subtitle=self.subtitleEdit.text().strip(),
            note=self.noteEdit.text().strip(),
            font_size=self.fontSpin.value(),
            page_size=self.pageCombo.currentText(),
            margin_mm=self.marginSpin.value(),
            base_family=self.base_family,
            mono_family=self.mono_family,
        )

    def _on_convert_clicked(self) -> None:
        if self._converting:
            self._cancel_conversion()
        else:
            self._start_conversion()

    def _start_conversion(self) -> None:
        if self._converting:
            return
        sources = self._sources()
        if not sources:
            QMessageBox.information(
                self, "Нет файлов", "Сначала добавьте хотя бы один файл .md."
            )
            return

        directory = self._output_directory()
        if self.dirRadio.isChecked():
            if directory is None:
                QMessageBox.warning(
                    self, "Не выбрана папка", "Укажите папку для готовых PDF."
                )
                return
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                QMessageBox.critical(
                    self, "Не удалось создать папку", "%s\n%s" % (directory, error)
                )
                return

        jobs: List[Tuple[Path, Path]] = []
        used: set = set()
        for source in sources:
            if directory is None:
                target = source.with_suffix(".pdf")
            else:
                target = self._unique_target(
                    directory / (source.stem + ".pdf"), used
                )
            used.add(target)
            jobs.append((source, target))

        self._produced = []
        self._cancelled_by_user = False
        self._set_busy(True)
        self.progressBar.setRange(0, len(jobs))
        self.progressBar.setValue(0)
        self.progressBar.setVisible(True)
        self.summaryLabel.setStyleSheet("color: #4a5560;")
        self.summaryLabel.setText("Сборка…")

        worker = ConvertWorker(
            jobs,
            self._options(),
            self._stylesheet_text(),
            self.overwriteCheck.isChecked(),
            self,
        )
        worker.progressChanged.connect(self._on_progress)
        worker.itemConverted.connect(self._on_converted)
        worker.itemSkipped.connect(self._on_skipped)
        worker.itemFailed.connect(self._on_failed)
        worker.finishedAll.connect(self._on_finished)
        worker.finished.connect(self._on_thread_finished)
        self._worker = worker
        self._log(
            "сборка %s файл(ов), формат %s, кегль %s, поля %s мм"
            % (
                len(jobs),
                self.pageCombo.currentText(),
                self.fontSpin.value(),
                self.marginSpin.value(),
            ),
            "info",
        )
        worker.start()

    def _cancel_conversion(self) -> None:
        if self._worker is not None:
            self._cancelled_by_user = True
            self._worker.cancel()
            self.convertButton.setEnabled(False)
            self.convertButton.setText("Останавливаю…")
            self._log("запрошена остановка после текущего файла", "warn")

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.progressBar.setRange(0, total)
        self.progressBar.setValue(done)
        self.status.showMessage("Сборка: %s (%s из %s)" % (name, done, total))

    def _on_converted(self, source: str, target: str, pages: int) -> None:
        self._produced.append(Path(target))
        self._log(
            "%s → %s, страниц %s" % (Path(source).name, Path(target).name, pages),
            "ok",
        )

    def _on_skipped(self, source: str, target: str) -> None:
        self._log("пропущен (файл уже есть): %s" % Path(target).name, "warn")

    def _on_failed(self, source: str, error: str) -> None:
        self._log("ошибка в %s: %s" % (Path(source).name, error), "error")

    def _on_finished(self, converted: int, failed: int, skipped: int) -> None:
        # Итог показываем только после остановки потока: иначе окно могут
        # закрыть, пока QThread ещё завершается.
        self._pending_summary = (converted, failed, skipped)

    def _on_thread_finished(self) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        converted, failed, skipped = self._pending_summary
        self._set_busy(False)
        self.progressBar.setVisible(False)
        parts = ["собрано: %s" % converted]
        if skipped:
            parts.append("пропущено: %s" % skipped)
        if failed:
            parts.append("с ошибками: %s" % failed)
        text = "Готово. " + ", ".join(parts) + "."
        if self._cancelled_by_user:
            text = "Остановлено. " + ", ".join(parts) + "."
        self.summaryLabel.setText(text)
        self.summaryLabel.setStyleSheet(
            "color: %s;" % ("#b42318" if failed else "#1a7f37")
        )
        self._log(text, "error" if failed else "ok")
        self.status.showMessage(text, 8000)
        if converted and self.openAfterCheck.isChecked() and self._produced:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._produced[0])))
        self._update_output_mode()
        self._refresh_css_label()

    def _set_busy(self, busy: bool) -> None:
        self._converting = busy
        for widget in (
            self.fileList,
            self.addButton,
            self.removeButton,
            self.clearButton,
            self.pageCombo,
            self.fontSpin,
            self.marginSpin,
            self.titleEdit,
            self.subtitleEdit,
            self.noteEdit,
            self.cssChooseButton,
            self.cssOpenButton,
            self.cssResetButton,
            self.nearRadio,
            self.dirRadio,
            self.dirEdit,
            self.dirBrowseButton,
            self.overwriteCheck,
        ):
            widget.setEnabled(not busy)
        self.convertButton.setText("Остановить" if busy else "Собрать PDF")
        self.convertButton.setEnabled(True)
        if not busy:
            self._update_output_mode()
            self._refresh_css_label()
        self._update_actions()

    # ---------------------------------------------------------------- preview

    def _schedule_preview(self) -> None:
        self._preview_timer.start()

    def refresh_preview(self) -> None:
        if not self.previewCheck.isChecked():
            self.preview.setHtml(
                "<p style='color:#8b949e'>Предпросмотр отключён.</p>"
            )
            return
        source = self._current_source()
        if source is None:
            self.preview.setHtml(
                "<p style='color:#8b949e'>Добавьте файлы .md — здесь появится "
                "их вид в будущем PDF.</p>"
            )
            self._update_actions()
            return
        try:
            text = source.read_text(encoding="utf-8-sig")
        except OSError as error:
            self.preview.setHtml(
                "<p style='color:#b42318'>Не удалось прочитать %s: %s</p>"
                % (escape(source.name), escape(str(error)))
            )
            return
        try:
            options = self._options()
            available = self.preview.viewport().width() - 40
            if available > 200:
                # В предпросмотре картинка должна влезать по ширине панели;
                # на PDF это не влияет — там своя ширина полосы набора.
                options.image_width = available
            _title, html_text = md2pdf.compose_html(text, options, source.parent)
            document = QTextDocument()
            font = QFont(self.base_family)
            font.setPointSizeF(self.fontSpin.value())
            document.setDefaultFont(font)
            document.setDefaultStyleSheet(self._stylesheet_text())
            document.setBaseUrl(
                QUrl.fromLocalFile(str(source.parent.resolve()) + os.sep)
            )
            document.setDocumentMargin(16)
            document.setHtml(html_text)
            scroll = self.preview.verticalScrollBar().value()
            self.preview.setDocument(document)
            self.preview.verticalScrollBar().setValue(scroll)
        except Exception as error:  # noqa: BLE001
            self.preview.setHtml(
                "<p style='color:#b42318'>Ошибка предпросмотра: %s</p>"
                % escape(str(error))
            )
        self._update_actions()

    # ------------------------------------------------------------------ misc

    def _log(self, message: str, kind: str = "info") -> None:
        color = LOG_COLORS.get(kind, LOG_COLORS["info"])
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.append(
            '<span style="color:#8b949e">%s</span>&nbsp;'
            '<span style="color:%s">%s</span>' % (stamp, color, escape(message))
        )

    def _update_actions(self) -> None:
        has_files = self.fileList.count() > 0
        self.removeButton.setEnabled(has_files and not self._converting)
        self.clearButton.setEnabled(has_files and not self._converting)
        if not self._converting:
            self.convertButton.setEnabled(has_files)

    def _show_about(self) -> None:
        from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR

        path = self._stylesheet_path()
        text = (
            "<h3>%s, версия %s</h3>"
            "<p>Сборка PDF из Markdown без офисного пакета. "
            "Оформление задаётся файлом style.css.</p>"
            "<p><b>Qt:</b> %s<br><b>PyQt:</b> %s<br>"
            "<b>Стиль:</b> %s<br><b>Программа:</b> %s</p>"
            % (
                APP_TITLE,
                md2pdf.VERSION,
                QT_VERSION_STR,
                PYQT_VERSION_STR,
                escape(str(path)) if path else "встроенный",
                escape(str(md2pdf.application_directory())),
            )
        )
        QMessageBox.about(self, "О программе", text)

    def closeEvent(self, event) -> None:
        if self._converting and self._worker is not None:
            answer = QMessageBox.question(
                self,
                "Идёт сборка",
                "Сборка ещё не закончена. Остановить её и выйти?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._worker.cancel()
            self._worker.wait(5000)
        self._save_settings()
        event.accept()

    def dragEnterEvent(self, event) -> None:
        if paths_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = paths_from_mime(event.mimeData())
        if paths:
            self._append_files(paths)
            event.acceptProposedAction()

    def add_paths(self, paths: Sequence[Path]) -> None:
        """Добавляет файлы, переданные в командной строке или извне."""
        self._append_files([Path(path) for path in paths])


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    # Пакетный режим без окна: md2pdf-gui.exe --convert file.md -o file.pdf
    if "--convert" in arguments:
        forwarded = [value for value in arguments[1:] if value != "--convert"]
        return md2pdf.main(forwarded)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    application = QApplication(arguments)
    application.setApplicationName(APP_NAME)
    application.setOrganizationName(ORGANIZATION)
    application.setApplicationVersion(md2pdf.VERSION)
    application.setWindowIcon(application_icon())
    application.setStyle("Fusion")
    application.setFont(QFont("Segoe UI", 10))
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "md2pdf.gui.1"
            )
        except Exception:  # noqa: BLE001 — не критично для работы
            pass

    window = MainWindow()
    if len(arguments) > 1:
        window.add_paths([Path(value) for value in arguments[1:] if Path(value).is_file()])
    window.show()
    return application.exec_()


if __name__ == "__main__":
    sys.exit(main())
