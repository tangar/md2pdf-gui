"""Дополнительные виджеты, используемые в main_window.ui.

Класс FileListWidget подключён в Qt Designer как promoted widget:
в .ui он выглядит как QListWidget, а в приложении получает перетаскивание
файлов и сигнал filesDropped.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import QAbstractItemView, QListWidget

MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdown", ".mkd", ".mdtxt")


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
