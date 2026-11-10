from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThread, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QImageReader, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFrame,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSplitter, QTableView, QVBoxLayout, QWidget,
)

from .album import Album, AlbumError, compact_text, move_selected
from .fonts import ensure_fonts
from .models import PhotoModel, SearchProxy
from .style import STYLE
from .ocr_runtime import ROOT, RuntimeErrorInfo, load_active, ready_status
from .ocr_service import recognize_album
from .ocr_settings import OcrSettingsDialog


def label(text: str, style: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if style:
        widget.setObjectName(style)
    widget.setWordWrap(wrap)
    return widget


def button(text, callback, style="", tooltip=""):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    if style:
        widget.setObjectName(style)
    if tooltip:
        widget.setToolTip(tooltip)
    return widget


class Operation(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function(self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


class ImageSignals(QObject):
    loaded = Signal(int, QImage, str, str)


class ImageTask(QRunnable):
    def __init__(self, token: int, path: Path):
        super().__init__()
        self.token, self.path = token, path
        self.signals = ImageSignals()

    def run(self):
        reader = QImageReader(str(self.path))
        reader.setAutoTransform(True)
        original = reader.size()
        if original.isValid() and max(original.width(), original.height()) > 2600:
            reader.setScaledSize(original.scaled(QSize(2600, 2600), Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        info = f"{original.width()} × {original.height()}" if original.isValid() else ""
        self.signals.loaded.emit(self.token, image, reader.errorString() if image.isNull() else "", info)


class ImageView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.canvas = QGraphicsScene(self)
        self.setScene(self.canvas)
        self.setBackgroundBrush(QColor("#f5f7fb"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.fit_mode = True
        self.has_image = False
        self.message("选择一张照片\n在这里查看图片预览")

    def message(self, text):
        self.canvas.clear()
        self.has_image = False
        self.resetTransform()
        item = self.canvas.addText(text)
        item.setDefaultTextColor(QColor("#8b98ad"))
        self.canvas.setSceneRect(item.boundingRect())

    def show_image(self, image):
        self.canvas.clear()
        self.canvas.addPixmap(QPixmap.fromImage(image))
        self.canvas.setSceneRect(self.canvas.itemsBoundingRect())
        self.has_image = True
        self.fit_image()

    def fit_image(self):
        self.fit_mode = True
        if self.has_image:
            self.fitInView(self.canvas.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor):
        if self.has_image and 0.015 < self.transform().m11() * factor < 15:
            self.fit_mode = False
            self.scale(factor, factor)

    def wheelEvent(self, event):
        if self.has_image:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit_image()


class MainWindow(QMainWindow):
    def __init__(self, runtime_root: Path | None = None):
        super().__init__()
        ensure_fonts()
        self.setWindowTitle("Photo Discrimination · 相册文字搜索")
        self.resize(1400, 900)
        self.setMinimumSize(1080, 720)
        self.setStyleSheet(STYLE)
        self.album: Album | None = None
        self.current_name: str | None = None
        self.drafts: dict[str, str] = {}
        self.busy = False
        self.runtime_root = Path(runtime_root) if runtime_root is not None else ROOT
        self.ocr_choice = None
        self.ocr_ready = False
        self.ocr_cancel = None
        self.operation_kind = "处理"
        self.operation: Operation | None = None
        self.image_token = 0
        self.image_pool = QThreadPool(self)
        self.image_pool.setMaxThreadCount(2)
        self.model = PhotoModel(self)
        self.proxy = SearchProxy(self)
        self.proxy.setSourceModel(self.model)
        self._build_ui()
        self._refresh_ocr_config()
        self.model.checked_changed.connect(self._checked_changed)
        self._update_controls()
        self.statusBar().showMessage("准备就绪 · 导入文件夹，载入本地 OCR.json")
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.choose_folder)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search.setFocus)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_current)
        QShortcut(QKeySequence("F5"), self, activated=self.reload_album)

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(18, 18, 18, 10)
        outer.setSpacing(18)
        self.setCentralWidget(root)

        sidebar = QFrame()
        sidebar.setObjectName("SideBar")
        sidebar.setFixedWidth(204)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 26, 20, 22)
        side.setSpacing(14)
        side.addWidget(label("PHOTO\nDISCRIMINATION", "Brand"))
        side.addWidget(label("相册文字搜索", "SideLabel"))
        side.addSpacing(20)
        side.addWidget(label("当前相册", "SideTitle"))
        self.album_name = label("尚未导入", "SideTitle", True)
        self.album_path = label("选择文件夹后，照片与文字\n将在本机载入。", "SideLabel", True)
        self.album_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        side.addWidget(self.album_name)
        side.addWidget(self.album_path)
        self.import_button = button("＋  导入文件夹", self.choose_folder, "SideButton")
        side.addWidget(self.import_button)
        self.reload_button = button("刷新文件夹", self.reload_album, "SideButton", "重新读取照片和 OCR.json（F5）")
        side.addWidget(self.reload_button)
        side.addSpacing(20)
        self.total_metric = label("0", "SideMetric")
        side.addWidget(self.total_metric)
        side.addWidget(label("张照片", "SideLabel"))
        self.index_metric = label("0 张有文字索引", "SideLabel", True)
        side.addWidget(self.index_metric)
        side.addSpacing(14)
        self.settings_button = button("识别工具设置", self.open_ocr_settings, "SideButton")
        side.addWidget(self.settings_button)
        self.tool_status = label("尚未配置识别工具", "SideLabel", True)
        side.addWidget(self.tool_status)
        side.addStretch()
        side.addWidget(label("本地存储", "SideTitle"))
        side.addWidget(label("OCR.json\n与照片保存在同一文件夹", "SideLabel", True))
        side.addSpacing(10)
        side.addWidget(label("LOCAL OCR  /  0.2", "SideLabel"))
        outer.addWidget(sidebar)

        workspace = QVBoxLayout()
        workspace.setSpacing(14)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("在照片里，找到文字", "Title"))
        titles.addWidget(label("导入相册，浏览文字，用关键词找回那张照片。", "Muted"))
        heading.addLayout(titles)
        heading.addStretch()
        heading.addWidget(label("本地相册 · JSON 索引", "Pill"))
        workspace.addLayout(heading)

        top = QFrame()
        top.setObjectName("TopBar")
        search_layout = QVBoxLayout(top)
        search_layout.setContentsMargins(16, 14, 16, 12)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("SearchInput")
        self.search.setAccessibleName("关键词搜索")
        self.search.setPlaceholderText("搜索 OCR 文字，例如：东京 2026（空格分隔，全部匹配）")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._schedule_search)
        self.search.returnPressed.connect(self.apply_search)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(130)
        self.search_timer.timeout.connect(self.apply_search)
        search_row.addWidget(self.search, 1)
        self.search_button = button("搜索", self.apply_search, "Primary")
        search_row.addWidget(self.search_button)
        search_layout.addLayout(search_row)
        search_layout.addWidget(label("多个关键词须同时出现 · 英文不区分大小写 · 符号按字面匹配", "Muted"))
        workspace.addWidget(top)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(14)
        self.splitter.setChildrenCollapsible(False)
        listing = QFrame()
        listing.setObjectName("Card")
        listing.setMinimumWidth(320)
        list_layout = QVBoxLayout(listing)
        list_layout.setContentsMargins(14, 14, 14, 12)
        list_heading = QHBoxLayout()
        self.results_label = label("照片列表 · 0 张", "Section")
        list_heading.addWidget(self.results_label)
        list_heading.addStretch()
        self.only_checked = QCheckBox("只看勾选")
        self.only_checked.toggled.connect(self._toggle_only_checked)
        list_heading.addWidget(self.only_checked)
        list_layout.addLayout(list_heading)
        self.table = QTableView()
        self.table.setObjectName("PhotoTable")
        self.table.setAccessibleName("照片列表")
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(52)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 170)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(2, 62)
        self.table.selectionModel().currentRowChanged.connect(self._current_changed)
        self.table.doubleClicked.connect(lambda _: self.open_original())
        list_layout.addWidget(self.table, 1)
        self.empty_label = label("还没有照片\n点击左侧「导入文件夹」开始", "Muted", True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumHeight(130)
        list_layout.addWidget(self.empty_label)
        selection_row = QHBoxLayout()
        self.select_visible_button = button("勾选结果", self.select_visible, tooltip="仅勾选当前搜索结果，保留其他已勾选照片")
        self.clear_checked_button = button("清空勾选", self.clear_checked)
        selection_row.addWidget(self.select_visible_button)
        selection_row.addWidget(self.clear_checked_button)
        selection_row.addStretch()
        list_layout.addLayout(selection_row)
        self.checked_label = label("已勾选 0 张", "Muted")
        list_layout.addWidget(self.checked_label)
        self.ocr_button = button("识别勾选照片", self.start_ocr)
        self.ocr_button.setToolTip("先在识别工具设置中下载模型。再次识别会覆盖勾选照片的旧文字。")
        self.move_button = button("移动导出勾选照片…", self.choose_move)
        list_layout.addWidget(self.ocr_button)
        list_layout.addWidget(self.move_button)
        list_layout.addWidget(label("勾选与预览独立；筛选后勾选仍保留。", "Muted", True))
        self.splitter.addWidget(listing)

        details = QFrame()
        details.setObjectName("Card")
        details.setMinimumWidth(380)
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        photo_heading = QHBoxLayout()
        photo_heading.addWidget(label("照片预览", "Section"))
        photo_heading.addStretch()
        self.open_button = button("打开原图 ↗", self.open_original)
        photo_heading.addWidget(self.open_button)
        detail_layout.addLayout(photo_heading)
        self.filename_label = label("未选择照片", "Muted")
        detail_layout.addWidget(self.filename_label)
        self.image_view = ImageView()
        self.image_view.setMinimumHeight(170)
        detail_layout.addWidget(self.image_view, 3)
        image_toolbar = QHBoxLayout()
        self.image_info = label("", "Muted")
        image_toolbar.addWidget(self.image_info)
        image_toolbar.addStretch()
        image_toolbar.addWidget(button("−", lambda: self.image_view.zoom(1 / 1.2)))
        image_toolbar.addWidget(button("适应", self.image_view.fit_image))
        image_toolbar.addWidget(button("＋", lambda: self.image_view.zoom(1.2)))
        detail_layout.addLayout(image_toolbar)
        text_heading = QHBoxLayout()
        text_heading.addWidget(label("OCR 文字", "Section"))
        self.text_state = label("", "Muted")
        text_heading.addWidget(self.text_state)
        text_heading.addStretch()
        self.copy_button = button("复制", self.copy_text)
        text_heading.addWidget(self.copy_button)
        detail_layout.addLayout(text_heading)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("OcrEditor")
        self.editor.setAccessibleName("OCR文字")
        self.editor.setPlaceholderText("这里显示 OCR.json 中的文字。\n\n在识别工具设置中下载并启用模型，勾选照片后点击识别；也可手动编辑文字。")
        self.editor.textChanged.connect(self._editor_changed)
        detail_layout.addWidget(self.editor, 2)
        footer = QHBoxLayout()
        footer.addWidget(label("保存时移除空格与换行，覆盖本张旧文字。", "Muted", True), 1)
        self.save_button = button("保存文字", self.save_current, "Primary", "保存到当前相册 OCR.json（Ctrl+S）")
        footer.addWidget(self.save_button)
        detail_layout.addLayout(footer)
        self.splitter.addWidget(details)
        self.splitter.setSizes([430, 660])
        workspace.addWidget(self.splitter, 1)
        self.progress = QProgressBar()
        self.progress.hide()
        workspace.addWidget(self.progress)
        self.stop_ocr_button = button("停止识别", self.stop_ocr)
        self.stop_ocr_button.hide()
        workspace.addWidget(self.stop_ocr_button)
        self.storage_label = label("支持 JPG、PNG、WebP、BMP · 仅导入所选文件夹，不含子文件夹", "Muted")
        workspace.addWidget(self.storage_label)
        outer.addLayout(workspace, 1)

    def _schedule_search(self):
        self.search_timer.start()

    def _visible_names(self):
        return [self.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(self.proxy.rowCount())]

    def apply_search(self):
        self.search_timer.stop()
        self.proxy.set_query(self.search.text())
        self._refresh_results()

    def _toggle_only_checked(self, checked):
        self.proxy.set_only_checked(checked)
        self._refresh_results()

    def _refresh_results(self):
        count = self.proxy.rowCount()
        self.results_label.setText(f"照片列表 · {count:,} 张")
        visible_names = set(self._visible_names())
        hidden = len(self.model.checked - visible_names)
        self.checked_label.setText(f"已勾选 {len(self.model.checked)} 张" + (f"（另有 {hidden} 张被筛选隐藏）" if hidden else ""))
        self.empty_label.setVisible(count == 0)
        self.table.setVisible(count > 0)
        self.empty_label.setText("没有匹配的照片\n试试减少关键词，或取消「只看勾选」" if self.album else "还没有照片\n点击左侧「导入文件夹」开始")
        if count and not self.table.currentIndex().isValid():
            self.table.setCurrentIndex(self.proxy.index(0, 0))
        elif not count:
            self._show_photo(None)
        self._update_controls()

    def _current_changed(self, current, previous):
        self._show_photo(current.data(Qt.ItemDataRole.UserRole) if current.isValid() else None)

    def _show_photo(self, name):
        self.current_name = name
        self.image_token += 1
        self.image_pool.clear()
        self.editor.blockSignals(True)
        self.editor.setPlainText(self.drafts.get(name, self.album.index.texts.get(name, "")) if name and self.album else "")
        self.editor.blockSignals(False)
        self.filename_label.setText(name or "未选择照片")
        self.filename_label.setToolTip(name or "")
        self.image_info.setText("")
        if name and self.album:
            self.image_view.message("正在载入照片…")
            try:
                task = ImageTask(self.image_token, self.album.path(name))
                task.signals.loaded.connect(self._image_loaded)
                self.image_pool.start(task)
            except AlbumError as exc:
                self.image_view.message(str(exc))
        else:
            self.image_view.message("选择一张照片\n在这里查看图片预览")
        self._update_text_state()
        self._update_controls()

    def _image_loaded(self, token, image, error, info):
        if token != self.image_token:
            return
        if image.isNull():
            self.image_view.message("无法预览此图片\n文件可能损坏、已移动或格式不受支持")
            self.image_info.setText("预览失败")
            self.image_info.setToolTip(error)
        else:
            self.image_view.show_image(image)
            self.image_info.setText(info)

    def _editor_changed(self):
        if not self.current_name or not self.album:
            return
        current = self.editor.toPlainText()
        stored = self.album.index.texts.get(self.current_name, "")
        if current == stored:
            self.drafts.pop(self.current_name, None)
        else:
            self.drafts[self.current_name] = current
        self._update_text_state()

    def _update_text_state(self):
        if not self.current_name or not self.album:
            self.text_state.setText("")
        elif self.current_name in self.drafts:
            self.text_state.setText("未保存 · 保存后可搜索")
        elif self.current_name not in self.album.index.texts:
            self.text_state.setText("暂无结果")
        else:
            text = self.album.index.texts[self.current_name]
            self.text_state.setText(f"{len(text):,} 字符" if text else "已保存 · 无文字")

    def save_current(self):
        if self.busy or not self.album or not self.current_name:
            return
        name = self.current_name
        try:
            self.album.index.set_text(name, self.editor.toPlainText())
        except AlbumError as exc:
            self._error(str(exc))
            return
        self.drafts.pop(name, None)
        self.model.text_updated(name)
        self.apply_search()
        if self.current_name == name:
            self.editor.blockSignals(True)
            self.editor.setPlainText(self.album.index.texts[name])
            self.editor.blockSignals(False)
            self._update_text_state()
        self._update_metrics()
        self.statusBar().showMessage(f"已保存 {name} 的文字到 OCR.json", 7000)

    def _resolve_drafts(self):
        if not self.drafts:
            return True
        choice = QMessageBox.question(self, "保存未保存的文字？", f"有 {len(self.drafts)} 张照片的文字尚未保存。", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            updated = dict(self.album.index.texts)
            updated.update(self.drafts)
            try:
                self.album.index.write(updated)
            except AlbumError as exc:
                self._error(str(exc))
                return False
            for name in self.drafts:
                self.model.text_updated(name)
        self.drafts.clear()
        if self.current_name:
            self.editor.blockSignals(True)
            self.editor.setPlainText(self.album.index.texts.get(self.current_name, ""))
            self.editor.blockSignals(False)
        self._update_text_state()
        self._update_metrics()
        self.apply_search()
        return True

    def choose_folder(self):
        if self.busy:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择照片文件夹", str(self.album.root) if self.album else "")
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder):
        if self.busy or not self._resolve_drafts():
            return
        self._run_operation(lambda _: Album.open(folder), self._album_loaded, "正在读取文件夹…")

    def reload_album(self):
        if self.album and not self.busy:
            self.load_folder(self.album.root)

    def _album_loaded(self, album):
        same_album = self.album and self.album.root == album.root
        checked = set(self.model.checked) if same_album else set()
        previous = self.current_name if same_album else None
        self.album = album
        if not same_album:
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self.proxy.set_query("")
            self.only_checked.setChecked(False)
        self.current_name = None
        self.model.set_album(album, checked)
        self._update_metrics()
        self._refresh_results()
        if previous:
            for row in range(self.proxy.rowCount()):
                if self.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) == previous:
                    self.table.setCurrentIndex(self.proxy.index(row, 0))
                    break
        self.statusBar().showMessage(f"已载入 {len(album.photos):,} 张照片 · {album.root}")
        if album.warnings:
            self._error("部分文件无法读取：\n" + "\n".join(album.warnings[:10]))

    def _update_metrics(self):
        if not self.album:
            return
        self.album_name.setText(self.album.root.name or str(self.album.root))
        self.album_path.setText(str(self.album.root))
        self.total_metric.setText(f"{len(self.album.photos):,}")
        indexed = sum(photo.name in self.album.index.texts for photo in self.album.photos)
        self.index_metric.setText(f"{indexed:,} 张已保存结果\n{len(self.album.photos) - indexed:,} 张暂无结果")
        suffix = "已载入" if self.album.index.original is not None else "首次保存时自动创建"
        self.storage_label.setText(f"OCR.json · {suffix} · {self.album.root}")
        self.storage_label.setToolTip(str(self.album.index.path))

    def _checked_changed(self):
        self.proxy.invalidateFilter()
        self._refresh_results()

    def select_visible(self):
        self.model.set_checked(self._visible_names(), True)

    def clear_checked(self):
        self.model.set_checked(tuple(self.model.checked), False)

    def copy_text(self):
        QApplication.clipboard().setText(compact_text(self.editor.toPlainText()))
        self.statusBar().showMessage("已复制文字", 3500)

    def open_original(self):
        if self.album and self.current_name and not self.busy:
            try:
                path = self.album.path(self.current_name)
                if not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                    raise AlbumError("无法打开原图，请检查文件是否仍存在。")
            except AlbumError as exc:
                self._error(str(exc))

    def choose_move(self):
        if self.busy or not self.album or not self.model.checked or not self._resolve_drafts():
            return
        parent = QFileDialog.getExistingDirectory(self, "选择导出位置（将在其中新建文件夹）", str(self.album.root.parent))
        if not parent:
            return
        name, accepted = QInputDialog.getText(self, "新建导出文件夹", "文件夹名称", text="导出照片-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        if not accepted:
            return
        name = name.strip()
        if not name or name in (".", "..") or any(char in name for char in '<>:"/\\|?*') or name.endswith("."):
            self._error("请输入有效的文件夹名称，不要包含路径或特殊字符。")
            return
        destination = Path(parent) / name
        if destination.exists():
            self._error("此文件夹已存在，请换一个新名称。不会覆盖已有文件。")
            return
        names = [photo.name for photo in self.album.photos if photo.name in self.model.checked]
        visible_names = set(self._visible_names())
        hidden = sum(item not in visible_names for item in names)
        confirmation = QMessageBox(self)
        confirmation.setWindowTitle("确认移动导出")
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setText(f"将 {len(names)} 张勾选照片移动到新的文件夹？")
        confirmation.setInformativeText(f"目标：{destination}\n\n移动成功后，照片会从原文件夹移除；对应文字会写入目标 OCR.json，并从源索引移除。" + (f"\n其中 {hidden} 张当前被搜索条件隐藏。" if hidden else ""))
        confirmation.setDetailedText("\n".join(names))
        proceed = confirmation.addButton("移动照片", QMessageBox.ButtonRole.AcceptRole)
        cancel = confirmation.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        confirmation.setDefaultButton(cancel)
        confirmation.exec()
        if confirmation.clickedButton() != proceed:
            return
        self._run_operation(lambda progress: move_selected(self.album, names, destination, progress), self._move_done, "正在移动导出…", "移动")

    def _refresh_ocr_config(self):
        self.ocr_choice, self.ocr_ready = None, False
        try:
            self.ocr_choice = load_active(self.runtime_root)
            if self.ocr_choice:
                self.ocr_ready, status = ready_status(self.ocr_choice, self.runtime_root)
                self.tool_status.setText(self.ocr_choice.title + ("\n模型已就绪" if self.ocr_ready else "\n需要部署验证"))
                self.tool_status.setToolTip(status)
            else:
                self.tool_status.setText("尚未配置识别工具\n点击设置选择下载")
        except RuntimeErrorInfo as exc:
            self.tool_status.setText("配置无法读取\n请打开设置重新选择")
            self.tool_status.setToolTip(str(exc))

    def open_ocr_settings(self):
        if self.busy:
            return
        dialog = OcrSettingsDialog(self, self.runtime_root)
        dialog.exec()
        self._refresh_ocr_config()
        self._update_controls()

    def start_ocr(self):
        if self.busy or not self.album or not self.model.checked:
            return
        self._refresh_ocr_config()
        if not self.ocr_ready:
            self.open_ocr_settings()
            return
        if not self._resolve_drafts():
            return
        names = [photo.name for photo in self.album.photos if photo.name in self.model.checked]
        self.ocr_cancel = threading.Event()
        self.stop_ocr_button.setEnabled(True)
        self.stop_ocr_button.show()
        self._run_operation(lambda progress: recognize_album(self.album, names, self.ocr_choice, self.runtime_root, progress, self.ocr_cancel), self._ocr_done, "正在初始化 OCR 模型…", "识别")

    def stop_ocr(self):
        if self.ocr_cancel:
            self.ocr_cancel.set()
            self.stop_ocr_button.setEnabled(False)
            self.statusBar().showMessage("正在停止识别；已保存的结果保留。")

    def _ocr_done(self, report):
        try:
            self._album_loaded(Album.open(self.album.root))
        except Exception as exc:
            self._error(f"结果已写入，但刷新相册失败：{exc}")
        summary = f"{'已停止' if report.cancelled else '识别完成'} · 保存 {len(report.saved)} 张 · 失败 {len(report.errors)} 张 · {report.device or '本地环境'}"
        self.statusBar().showMessage(summary)
        if report.errors:
            self._error(summary + "\n\n" + "\n".join(report.errors[:10]))

    def _move_done(self, report):
        previous_checks = self.model.checked - set(report.moved)
        try:
            album = Album.open(self.album.root)
            self._album_loaded(album)
            self.model.set_checked(report.moved, False)
            self.model.checked.intersection_update(previous_checks)
            self._checked_changed()
        except Exception as exc:
            self._error(f"移动结束，但刷新源文件夹失败：{exc}")
        self.statusBar().showMessage(f"已移动 {len(report.moved)} 张照片到 {report.destination}")
        if report.errors:
            self._error("部分移动未完成；剩余照片未继续移动。\n\n" + "\n".join(report.errors))
        else:
            QMessageBox.information(self, "移动完成", f"已移动 {len(report.moved)} 张照片。\n\n目标：{report.destination}\n对应文字已保存到目标 OCR.json。")

    def _run_operation(self, function, completed, message, kind="处理"):
        if self.busy:
            return
        self.busy = True
        self.operation_kind = kind
        self._update_controls()
        self.progress.setRange(0, 0)
        self.progress.show()
        self.statusBar().showMessage(message)
        operation = Operation(function, self)
        self.operation = operation
        operation.result.connect(completed)
        operation.failed.connect(self._operation_failed)
        operation.progress.connect(self._operation_progress)
        operation.finished.connect(self._operation_finished)
        operation.finished.connect(operation.deleteLater)
        operation.start()

    def _operation_progress(self, position, total, name):
        self.progress.setRange(0, total)
        self.progress.setValue(position)
        self.statusBar().showMessage(f"正在{self.operation_kind} {position}/{total} · {name}")

    def _operation_failed(self, error):
        if self.operation_kind == "识别" and self.album:
            try:
                self._album_loaded(Album.open(self.album.root))
            except Exception:
                pass
        self.statusBar().showMessage("操作未完成 · 原有相册内容仍可查看")
        self._error(error)

    def _operation_finished(self):
        self.busy = False
        self.operation = None
        self.ocr_cancel = None
        self.stop_ocr_button.hide()
        self.progress.hide()
        self._update_controls()

    def _update_controls(self):
        available = bool(self.album) and not self.busy
        selected = available and self.current_name is not None
        for widget in (self.import_button, self.settings_button):
            widget.setEnabled(not self.busy)
        for widget in (self.search, self.search_button, self.reload_button, self.only_checked, self.table):
            widget.setEnabled(available)
        self.select_visible_button.setEnabled(available and self.proxy.rowCount() > 0)
        self.clear_checked_button.setEnabled(available and bool(self.model.checked))
        self.move_button.setEnabled(available and bool(self.model.checked))
        self.ocr_button.setEnabled(available and bool(self.model.checked) and self.ocr_ready)
        for widget in (self.editor, self.copy_button, self.save_button, self.open_button):
            widget.setEnabled(selected)

    def _error(self, message):
        QMessageBox.warning(self, "操作提示", message)

    def closeEvent(self, event):
        if self.busy:
            self.statusBar().showMessage("文件操作尚未完成，请稍候再关闭。")
            event.ignore()
            return
        if not self._resolve_drafts():
            event.ignore()
            return
        self.image_pool.clear()
        self.image_pool.waitForDone()
        event.accept()
