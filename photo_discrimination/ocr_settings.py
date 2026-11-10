from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from .ocr_runtime import ROOT, GPU_VARIANTS, MODEL_VARIANTS, OcrChoice, RuntimeErrorInfo, deploy, hardware_info, load_active, ready_status, save_active
from .style import STYLE


class DeploymentThread(QThread):
    log = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, choice, root, parent=None):
        super().__init__(parent)
        self.choice, self.root = choice, root
        self.cancel = threading.Event()

    def run(self):
        try:
            self.log.emit("本机硬件：" + hardware_info())
            reuse = ready_status(self.choice, self.root)[0]
            result = deploy(self.choice, self.root, self.log.emit, self.cancel, reuse_packages=reuse)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class OcrSettingsDialog(QDialog):
    def __init__(self, parent=None, root: Path = ROOT):
        super().__init__(parent)
        self.root = root
        self.job: DeploymentThread | None = None
        self.setWindowTitle("识别工具设置")
        self.resize(820, 730)
        self.setMinimumSize(680, 620)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        title = QLabel("识别工具设置")
        title.setObjectName("Title")
        layout.addWidget(title)
        intro = QLabel("首次使用不包含任何 OCR / VLM 模型或 API 配置。\n选择工具并点击下载后才会联网；下载完成后可在本地识别。")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        form.setVerticalSpacing(14)
        self.tool_combo = QComboBox()
        self.tool_combo.setObjectName("OcrToolChoice")
        self.tool_combo.addItem("请选择识别工具", None)
        self.tool_combo.addItem("OCR · PaddleOCR · CPU", "cpu")
        self.tool_combo.addItem("OCR · PaddleOCR · GPU", "gpu")
        self.gpu_combo = QComboBox()
        self.gpu_combo.setObjectName("GpuVersionChoice")
        self.gpu_combo.addItem("请手动选择 GPU 版本", "")
        for key, text in GPU_VARIANTS.items():
            self.gpu_combo.addItem(text, key)
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("ModelChoice")
        for key, text in MODEL_VARIANTS.items():
            self.model_combo.addItem(text, key)
        form.addRow("识别工具", self.tool_combo)
        form.addRow("GPU 运行版本", self.gpu_combo)
        form.addRow("文字模型", self.model_combo)
        layout.addLayout(form)
        self.version_label = QLabel("固定组件：PaddleOCR 3.3.2 / PaddlePaddle 3.2.2。\nCPU/GPU 是运行环境；检测和文字模型可共用。")
        self.version_label.setWordWrap(True)
        self.version_label.setObjectName("Muted")
        layout.addWidget(self.version_label)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("Pill")
        layout.addWidget(self.status)
        self.description = QLabel()
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        location = QLabel(f"环境：{root / 'runtimes'}\n模型：{root / 'models'}")
        location.setWordWrap(True)
        location.setObjectName("Muted")
        layout.addWidget(location)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setPlaceholderText("点击下载后，此处显示安装、模型下载和推理验证进度。\nGPU 首次部署包含 CUDA 运行库，可能下载数 GB；无需单独安装完整 CUDA 开发工具包。")
        layout.addWidget(self.log_view, 1)
        row = QHBoxLayout()
        self.download_button = QPushButton("下载并部署")
        self.download_button.setObjectName("Primary")
        self.download_button.clicked.connect(self.download)
        self.cancel_button = QPushButton("取消下载")
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.hide()
        row.addWidget(self.download_button)
        row.addWidget(self.cancel_button)
        row.addStretch()
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        self.use_button = QPushButton("使用此配置")
        self.use_button.setObjectName("Primary")
        self.use_button.clicked.connect(self.use_choice)
        row.addWidget(self.close_button)
        row.addWidget(self.use_button)
        layout.addLayout(row)
        for combo in (self.tool_combo, self.gpu_combo, self.model_combo):
            combo.currentIndexChanged.connect(self.update_selection)
        try:
            active = load_active(root)
            if active:
                self.tool_combo.setCurrentIndex(self.tool_combo.findData(active.device))
                self.gpu_combo.setCurrentIndex(self.gpu_combo.findData(active.variant))
                self.model_combo.setCurrentIndex(self.model_combo.findData(active.model))
        except RuntimeErrorInfo as exc:
            self.log_view.appendPlainText(str(exc))
        self.update_selection()

    def choice(self):
        device = self.tool_combo.currentData()
        if device is None:
            raise RuntimeErrorInfo("请选择识别工具；不会自动下载或启用任何模型。")
        choice = OcrChoice(device, self.gpu_combo.currentData() if device == "gpu" else "", self.model_combo.currentData())
        choice.validate()
        return choice

    def update_selection(self):
        busy = self.job is not None
        gpu = self.tool_combo.currentData() == "gpu"
        self.gpu_combo.setEnabled(gpu and not busy)
        self.tool_combo.setEnabled(not busy)
        self.model_combo.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        if busy:
            self.download_button.setEnabled(False)
            self.use_button.setEnabled(False)
            return
        try:
            choice = self.choice()
            ready, status = ready_status(choice, self.root)
            self.status.setText(status)
            self.download_button.setEnabled(True)
            self.download_button.setText("重新验证部署" if ready else "下载并部署")
            self.use_button.setEnabled(ready)
        except RuntimeErrorInfo as exc:
            self.status.setText(str(exc))
            self.download_button.setEnabled(False)
            self.use_button.setEnabled(False)
        size = "约 22 MB" if self.model_combo.currentData() == "mobile" else "约 166 MB"
        self.description.setText(f"模型文件合计 {size}，另需下载独立运行环境。" +
                                 ("\nGPU 版本须与驱动兼容；546.30 驱动建议选择 CUDA 11.8。" if gpu else "\nCPU 环境无需独立显卡。"))

    def download(self):
        if self.job:
            return
        try:
            choice = self.choice()
        except RuntimeErrorInfo as exc:
            self.status.setText(str(exc))
            return
        self.log_view.clear()
        self.log_view.appendPlainText(f"开始部署：{choice.title} · {choice.model}")
        self.progress.show()
        self.cancel_button.show()
        self.cancel_button.setEnabled(True)
        self.job = DeploymentThread(choice, self.root, self)
        self.job.log.connect(self.log_view.appendPlainText)
        self.job.succeeded.connect(lambda _: self.log_view.appendPlainText("验证成功，点击“使用此配置”即可启用。"))
        self.job.failed.connect(self._failed)
        self.job.finished.connect(self._finished)
        self.job.finished.connect(self.job.deleteLater)
        self.update_selection()
        self.job.start()

    def _failed(self, error):
        self.log_view.appendPlainText("部署未完成：\n" + error)

    def _finished(self):
        self.job = None
        self.progress.hide()
        self.cancel_button.hide()
        self.update_selection()

    def cancel_download(self):
        if self.job:
            self.job.cancel.set()
            self.cancel_button.setEnabled(False)
            self.log_view.appendPlainText("正在停止部署，请稍候；不会启用未完成环境。")

    def use_choice(self):
        try:
            save_active(self.choice(), self.root)
        except (RuntimeErrorInfo, OSError) as exc:
            QMessageBox.warning(self, "无法启用", str(exc))
            return
        self.accept()

    def reject(self):
        if self.job:
            self.cancel_download()
            return
        super().reject()

    def closeEvent(self, event):
        if self.job:
            self.cancel_download()
            event.ignore()
        else:
            event.accept()
