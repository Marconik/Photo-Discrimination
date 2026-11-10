from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor

from .album import Album, keywords, search_text


class PhotoModel(QAbstractTableModel):
    checked_changed = Signal()
    NAME, TEXT, CHECK = range(3)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.album: Album | None = None
        self.checked: set[str] = set()
        self.searchable: dict[str, str] = {}

    def set_album(self, album: Album | None, checked: set[str] | None = None):
        self.beginResetModel()
        self.album = album
        self.checked = (checked or set()) & {photo.name for photo in album.photos} if album else set()
        self.searchable = {name: search_text(value) for name, value in album.index.texts.items()} if album else {}
        self.endResetModel()
        self.checked_changed.emit()

    def rowCount(self, parent=QModelIndex()):
        return len(self.album.photos) if self.album and not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ("照片", "OCR 文字", "勾选")[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not self.album:
            return None
        name = self.album.photos[index.row()].name
        text = self.album.index.texts.get(name)
        if role == Qt.ItemDataRole.UserRole:
            return name
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == self.NAME:
                return name
            if index.column() == self.TEXT:
                return "未识别" if text is None else (text[:80] if text else "无文字")
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == self.CHECK:
            return Qt.CheckState.Checked if name in self.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == self.TEXT:
            return QColor("#8790a4" if text is None else "#52617a")
        if role == Qt.ItemDataRole.ToolTipRole:
            return name if index.column() == self.NAME else (text if text is not None else "还没有保存 OCR 文字")
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == self.CHECK:
            return Qt.AlignmentFlag.AlignCenter
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == self.CHECK:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if self.album and index.isValid() and index.column() == self.CHECK and role == Qt.ItemDataRole.CheckStateRole:
            name = self.album.photos[index.row()].name
            if value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value):
                self.checked.add(name)
            else:
                self.checked.discard(name)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.checked_changed.emit()
            return True
        return False

    def set_checked(self, names, checked):
        if checked:
            self.checked.update(names)
        else:
            self.checked.difference_update(names)
        if self.rowCount():
            self.dataChanged.emit(self.index(0, self.CHECK), self.index(self.rowCount() - 1, self.CHECK), [Qt.ItemDataRole.CheckStateRole])
        self.checked_changed.emit()

    def text_updated(self, name):
        self.searchable[name] = search_text(self.album.index.texts[name])
        for row, photo in enumerate(self.album.photos):
            if photo.name == name:
                self.dataChanged.emit(self.index(row, self.TEXT), self.index(row, self.TEXT))
                break


class SearchProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.terms: tuple[str, ...] = ()
        self.only_checked = False
        self.setDynamicSortFilter(True)

    def set_query(self, query: str):
        self.terms = keywords(query)
        self.invalidateFilter()

    def set_only_checked(self, checked: bool):
        self.only_checked = checked
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        if not model.album:
            return False
        name = model.album.photos[row].name
        if self.only_checked and name not in model.checked:
            return False
        if not self.terms:
            return True
        # Only OCR content is searched; file names never produce false hits.
        return name in model.searchable and all(term in model.searchable[name] for term in self.terms)
