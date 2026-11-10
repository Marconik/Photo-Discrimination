"""Support Windows sessions where Qt cannot enumerate the system font registry."""

import os
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


def ensure_fonts():
    if os.name == "nt" and not QFontDatabase.families():
        font_folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf"):
            path = font_folder / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        if "Microsoft YaHei UI" in QFontDatabase.families():
            QApplication.setFont(QFont("Microsoft YaHei UI", 10))
