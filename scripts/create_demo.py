"""Generate UI demo cards with MANUALLY supplied text, without running OCR."""

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter


def create_demo(folder: Path):
    folder.mkdir(parents=True, exist_ok=False)
    cards = [
        ("01_东京旅行.png", "#dce7f7", "TOKYO / 2026", "東京の小さな旅", "東京駅 2026年11月 / 予約番号 ABC-123", "東京駅2026年11月予約番号ABC-123"),
        ("02_咖啡小票.png", "#e9e2d8", "KISSA COFFEE", "咖啡与周末", "Invoice 2026 / Latte ￥580 / Total ￥1,160", "KISSACOFFEEInvoice2026Latte￥580Total￥1,160"),
        ("03_展览门票.png", "#dce9df", "ART & EVERYDAY", "日常のかたち", "東京 Art Museum / 2026 / Ticket 50% OFF", "東京ArtMuseum2026Ticket50%OFF"),
        ("04_购物清单.png", "#eee1df", "THE WEEKEND LIST", "购物清单", "牛奶 面包 咖啡 / 预算 ￥200", "购物清单牛奶面包咖啡预算￥200"),
        ("05_车票.png", "#e2e0f1", "NEXT STOP", "京都へ", "京都 2026 / 予約番号 ZX_42 / Seat 08A", "京都2026予約番号ZX_42Seat08A"),
        ("06_尚无文字结果.png", "#dbe9ee", "A QUIET MOMENT", "未保存文字结果", "选择照片后，可以粘贴文字并保存来测试搜索。", None),
    ]
    data = {}
    for filename, background, heading, title, body, text in cards:
        image = QImage(1200, 850, QImage.Format.Format_RGB32)
        image.fill(QColor(background))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(92, 75, 1016, 700, 16, 16)
        painter.setPen(QColor("#64809a"))
        painter.setFont(QFont("Segoe UI", 18, QFont.Weight.DemiBold))
        painter.drawText(QRect(155, 132, 900, 50), Qt.AlignmentFlag.AlignLeft, "PHOTO NOTES  /  " + heading)
        painter.setPen(QColor("#22374b"))
        painter.setFont(QFont("Microsoft YaHei UI", 49, QFont.Weight.Bold))
        painter.drawText(QRect(150, 253, 950, 130), Qt.AlignmentFlag.AlignLeft, title)
        painter.setPen(QColor("#b4c3d2"))
        painter.drawLine(155, 430, 1035, 430)
        painter.setPen(QColor("#516b80"))
        painter.setFont(QFont("Microsoft YaHei UI", 22))
        painter.drawText(QRect(155, 470, 870, 140), Qt.TextFlag.TextWordWrap, body)
        painter.setPen(QColor("#9aa8b5"))
        painter.setFont(QFont("Microsoft YaHei UI", 13))
        painter.drawText(QRect(155, 680, 900, 45), Qt.AlignmentFlag.AlignLeft, "界面演示图片 · 文字为手工预置，未执行 OCR")
        painter.end()
        if not image.save(str(folder / filename)):
            raise OSError(f"无法保存演示图片：{filename}")
        if text is not None:
            data[filename] = text
    (folder / "OCR.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "界面演示_手工预置文字")
    args = parser.parse_args()
    app = QGuiApplication([])
    if os.name == "nt" and not QFontDatabase.families():
        font_folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for font_name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf"):
            QFontDatabase.addApplicationFont(str(font_folder / font_name))
    print(create_demo(args.folder))
