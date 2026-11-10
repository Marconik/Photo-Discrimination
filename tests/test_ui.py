import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from photo_discrimination.window import MainWindow


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("东京.png", "invoice.png", "unindexed.png"):
            image = QImage(160, 100, QImage.Format.Format_RGB32)
            image.fill(QColor("#e0eaff"))
            image.save(str(self.root / name))
        (self.root / "OCR.json").write_text(json.dumps({"东京.png": "東京 駅 2026", "invoice.png": "Invoice 50% ABC-123"}, ensure_ascii=False), encoding="utf-8")
        self.window = MainWindow(runtime_root=self.root / "runtime")
        self.window.show()
        self.window.load_folder(self.root)
        self.wait_until(lambda: not self.window.busy)

    def tearDown(self):
        self.window.drafts.clear()
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_until(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(10)
        self.fail("UI condition timed out")

    def select(self, name):
        for row in range(self.window.proxy.rowCount()):
            if self.window.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) == name:
                self.window.table.setCurrentIndex(self.window.proxy.index(row, 0))
                self.app.processEvents()
                return
        self.fail(f"Missing UI row: {name}")

    def search(self, query):
        self.window.search.setText(query)
        self.window.apply_search()
        self.app.processEvents()

    def test_import_preview_and_engine_placeholder(self):
        self.assertEqual(self.window.proxy.rowCount(), 3)
        self.select("东京.png")
        self.wait_until(lambda: self.window.image_view.has_image)
        self.assertEqual(self.window.editor.toPlainText(), "東京駅2026")
        self.assertFalse(self.window.ocr_button.isEnabled())
        self.assertTrue(self.window.save_button.isEnabled())

    def test_multiple_keyword_search_and_no_matches(self):
        self.search("東京 2026")
        self.assertEqual(self.window._visible_names(), ["东京.png"])
        self.search("東京 2027")
        self.assertEqual(self.window.proxy.rowCount(), 0)
        self.assertIsNone(self.window.current_name)
        self.assertTrue(self.window.empty_label.isVisible())
        self.assertFalse(self.window.save_button.isEnabled())

    def test_filename_does_not_match_without_ocr(self):
        self.search("unindexed")
        self.assertEqual(self.window.proxy.rowCount(), 0)

    def test_checkbox_click_and_hidden_selection_survive_search(self):
        self.select("东京.png")
        row = self.window.table.currentIndex().row()
        index = self.window.proxy.index(row, 2)
        rectangle = self.window.table.visualRect(index)
        # Native Qt checkbox delegate places its indicator near the left padding.
        QTest.mouseClick(self.window.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=rectangle.topLeft() + QPoint(17, 26))
        self.assertIn("东京.png", self.window.model.checked)
        self.search("invoice")
        self.assertIn("东京.png", self.window.model.checked)
        self.assertIn("隐藏", self.window.checked_label.text())
        self.window.select_visible()
        self.assertEqual(self.window.model.checked, {"东京.png", "invoice.png"})

    def test_save_overwrites_single_result_and_updates_search(self):
        self.select("东京.png")
        self.window.editor.setPlainText("新 结果\nＡＢＣ 42")
        QTest.mouseClick(self.window.save_button, Qt.MouseButton.LeftButton)
        data = json.loads((self.root / "OCR.json").read_text(encoding="utf-8"))
        self.assertEqual(data["东京.png"], "新结果ＡＢＣ42")
        self.assertEqual(data["invoice.png"], "Invoice50%ABC-123")
        self.search("新结果 abc")
        self.assertEqual(self.window._visible_names(), ["东京.png"])

    def test_draft_survives_preview_switch_and_cancel_close(self):
        self.select("东京.png")
        self.window.editor.setPlainText("unsaved draft")
        self.select("invoice.png")
        self.select("东京.png")
        self.assertEqual(self.window.editor.toPlainText(), "unsaved draft")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertNotIn("unsaved", (self.root / "OCR.json").read_text(encoding="utf-8"))

    def test_discard_drafts_restores_saved_text_in_editor(self):
        self.select("东京.png")
        self.window.editor.setPlainText("discard this")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            self.assertTrue(self.window._resolve_drafts())
        self.assertEqual(self.window.editor.toPlainText(), "東京駅2026")
        self.assertEqual(self.window.drafts, {})

    def test_invalid_json_load_does_not_replace_current_album(self):
        invalid = self.root / "bad"
        invalid.mkdir()
        (invalid / "OCR.json").write_text("broken")
        with patch.object(self.window, "_error") as error:
            self.window.load_folder(invalid)
            self.wait_until(lambda: not self.window.busy)
        error.assert_called_once()
        self.assertEqual(self.window.album.root, self.root.resolve())
        self.assertEqual(self.window.proxy.rowCount(), 3)

    def test_only_checked_filter_clears_cleanly(self):
        self.window.model.set_checked(["invoice.png"], True)
        self.window.only_checked.setChecked(True)
        self.assertEqual(self.window._visible_names(), ["invoice.png"])
        self.window.clear_checked()
        self.assertEqual(self.window.proxy.rowCount(), 0)
        self.window.only_checked.setChecked(False)
        self.assertEqual(self.window.proxy.rowCount(), 3)

    def test_move_button_moves_only_checked_photo_and_refreshes(self):
        self.window.model.set_checked(["东京.png"], True)
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.root)), \
             patch.object(QInputDialog, "getText", return_value=("export", True)), \
             patch.object(QMessageBox, "exec", return_value=0), \
             patch.object(QMessageBox, "clickedButton", lambda box: next(item for item in box.buttons() if item.text() == "移动照片")), \
             patch.object(QMessageBox, "information"), \
             patch.object(self.window, "_error") as error:
            QTest.mouseClick(self.window.move_button, Qt.MouseButton.LeftButton)
            self.wait_until(lambda: not self.window.busy)
        error.assert_not_called()
        self.assertFalse((self.root / "东京.png").exists())
        self.assertTrue((self.root / "export" / "东京.png").exists())
        self.assertEqual(json.loads((self.root / "export" / "OCR.json").read_text(encoding="utf-8")), {"东京.png": "東京駅2026"})
        self.assertEqual(self.window.proxy.rowCount(), 2)
        self.assertEqual(self.window.model.checked, set())


if __name__ == "__main__":
    unittest.main()
