"""Run: py -3.10 main.py [optional-album-folder]"""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="本地相册文字搜索")
    parser.add_argument("folder", nargs="?", help="可选：启动时载入的照片文件夹")
    args = parser.parse_args()
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from photo_discrimination.window import MainWindow
    except ImportError as exc:
        raise SystemExit("缺少桌面依赖。请运行：python -m pip install -r requirements.txt\n" + str(exc))
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Photo Discrimination")
    app.setOrganizationName("Marconik")
    window = MainWindow()
    window.show()
    if args.folder:
        QTimer.singleShot(0, lambda: window.load_folder(args.folder))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
