import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from photo_discrimination.album import Album
from photo_discrimination.ocr_runtime import (
    Cancelled, GPU_CUDA, OcrChoice, PADDLE_VERSION, OCR_VERSION, PADDLEX_VERSION,
    RuntimeErrorInfo, atomic_json, load_active, marker_path, ready_status,
    runtime_python, save_active, probe,
)
from photo_discrimination.ocr_service import recognize_album
from photo_discrimination.ocr_settings import OcrSettingsDialog
from photo_discrimination.fonts import ensure_fonts


def fake_ready(root, choice):
    python = runtime_python(choice, root)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"test stub, never executed")
    for model in choice.model_names():
        folder = root / "models" / model
        folder.mkdir(parents=True, exist_ok=True)
        for filename in ("inference.json", "inference.pdiparams", "inference.yml"):
            (folder / filename).write_text("stub")
    atomic_json(marker_path(choice, root), {
        "choice": asdict(choice), "paddle": PADDLE_VERSION,
        "paddleocr": OCR_VERSION, "paddlex": PADDLEX_VERSION, "device": "gpu:0" if choice.device == "gpu" else "cpu",
        "cuda": GPU_CUDA.get(choice.variant),
    })


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_first_run_is_unconfigured_and_writes_nothing(self):
        self.assertIsNone(load_active(self.root))
        self.assertFalse(ready_status(OcrChoice(), self.root)[0])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_gpu_variant_must_be_explicit(self):
        for variant in ("", "cu999", "../outside"):
            with self.subTest(variant=variant), self.assertRaises(RuntimeErrorInfo):
                OcrChoice("gpu", variant).validate()
        OcrChoice("gpu", "cu118").validate()
        with self.assertRaises(RuntimeErrorInfo):
            OcrChoice("cpu", "cu118").validate()

    def test_unverified_download_cannot_be_activated(self):
        choice = OcrChoice("gpu", "cu118")
        runtime_python(choice, self.root).parent.mkdir(parents=True)
        runtime_python(choice, self.root).write_bytes(b"not a real interpreter")
        with self.assertRaises(RuntimeErrorInfo):
            save_active(choice, self.root)
        self.assertIsNone(load_active(self.root))

    def test_ready_configuration_roundtrip_and_missing_weights(self):
        choice = OcrChoice("gpu", "cu118")
        fake_ready(self.root, choice)
        self.assertTrue(ready_status(choice, self.root)[0])
        save_active(choice, self.root)
        self.assertEqual(load_active(self.root), choice)
        (self.root / "models" / choice.model_names()[0] / "inference.pdiparams").unlink()
        self.assertFalse(ready_status(choice, self.root)[0])

    def test_changed_versions_need_revalidation(self):
        choice = OcrChoice()
        fake_ready(self.root, choice)
        marker = marker_path(choice, self.root)
        data = json.loads(marker.read_text())
        data["paddle"] = "old-version"
        atomic_json(marker, data)
        self.assertFalse(ready_status(choice, self.root)[0])

    def test_probe_rejects_wrong_cuda_build(self):
        choice = OcrChoice("gpu", "cu118")
        def worker(command, root, callback, *args, **kwargs):
            callback("PD_EVENT:" + json.dumps({"type": "probe", "ok": True, "device": "gpu:0", "cuda": "12.6", "paddle": PADDLE_VERSION, "paddleocr": OCR_VERSION, "paddlex": PADDLEX_VERSION}))
        with patch("photo_discrimination.ocr_runtime._stream_process", worker):
            with self.assertRaises(RuntimeErrorInfo):
                probe(choice, self.root)


class OcrBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.choice = OcrChoice("gpu", "cu118")
        fake_ready(self.root, self.choice)
        folder = self.root / "album"
        folder.mkdir()
        (folder / "a.png").write_bytes(b"image")
        (folder / "b.png").write_bytes(b"other")
        self.album = Album.open(folder)
        self.album.index.write({"a.png": "old", "b.png": "keep"})

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def event(callback, kind, **values):
        callback("PD_EVENT:" + json.dumps({"type": kind, **values}))

    def test_success_overwrites_only_checked_and_compacts_text(self):
        def worker(command, root, callback, *args, **kwargs):
            self.event(callback, "ready", device="gpu:0")
            self.event(callback, "photo", name="a.png", text="新 文字\nABC 123")
            self.event(callback, "done")
        with patch("photo_discrimination.ocr_service._stream_process", worker):
            report = recognize_album(self.album, ["a.png"], self.choice, self.root)
        self.assertEqual(report.saved, ["a.png"])
        self.assertEqual(report.device, "gpu:0")
        self.assertEqual(self.album.index.texts, {"a.png": "新文字ABC123", "b.png": "keep"})
        self.assertEqual(list((self.root / "cache" / "tasks").iterdir()), [])

    def test_recognition_failure_preserves_old_result(self):
        def worker(command, root, callback, *args, **kwargs):
            self.event(callback, "photo_error", name="a.png", error="decode failure")
        with patch("photo_discrimination.ocr_service._stream_process", worker):
            report = recognize_album(self.album, ["a.png"], self.choice, self.root)
        self.assertEqual(report.saved, [])
        self.assertTrue(report.errors)
        self.assertEqual(self.album.index.texts["a.png"], "old")

    def test_image_changed_during_recognition_does_not_overwrite(self):
        def worker(command, root, callback, *args, **kwargs):
            (self.album.root / "a.png").write_bytes(b"changed file contents")
            self.event(callback, "photo", name="a.png", text="outdated")
        with patch("photo_discrimination.ocr_service._stream_process", worker):
            report = recognize_album(self.album, ["a.png"], self.choice, self.root)
        self.assertEqual(report.saved, [])
        self.assertTrue(report.errors)
        self.assertEqual(self.album.index.texts["a.png"], "old")

    def test_cancel_keeps_results_already_saved(self):
        def worker(command, root, callback, *args, **kwargs):
            self.event(callback, "photo", name="a.png", text="completed")
            raise Cancelled("cancelled")
        with patch("photo_discrimination.ocr_service._stream_process", worker):
            report = recognize_album(self.album, ["a.png", "b.png"], self.choice, self.root)
        self.assertTrue(report.cancelled)
        self.assertEqual(self.album.index.texts, {"a.png": "completed", "b.png": "keep"})

    def test_unknown_worker_photo_is_rejected(self):
        def worker(command, root, callback, *args, **kwargs):
            self.event(callback, "photo", name="../outside.png", text="bad")
        with patch("photo_discrimination.ocr_service._stream_process", worker):
            with self.assertRaises(RuntimeErrorInfo):
                recognize_album(self.album, ["a.png"], self.choice, self.root)
        self.assertEqual(self.album.index.texts["a.png"], "old")


class SettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        ensure_fonts()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dialog = OcrSettingsDialog(root=self.root)

    def tearDown(self):
        self.dialog.close()
        self.temp.cleanup()

    def test_opening_settings_does_not_start_download_or_write_config(self):
        self.assertIsNone(self.dialog.job)
        self.assertIsNone(self.dialog.tool_combo.currentData())
        self.assertFalse(self.dialog.download_button.isEnabled())
        self.assertFalse(self.dialog.use_button.isEnabled())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_gpu_requires_manual_variant_before_download(self):
        self.dialog.tool_combo.setCurrentIndex(self.dialog.tool_combo.findData("gpu"))
        self.assertTrue(self.dialog.gpu_combo.isEnabled())
        self.assertFalse(self.dialog.download_button.isEnabled())
        self.dialog.gpu_combo.setCurrentIndex(self.dialog.gpu_combo.findData("cu118"))
        self.assertTrue(self.dialog.download_button.isEnabled())
        self.assertFalse(self.dialog.use_button.isEnabled())

    def test_cpu_can_be_downloaded_without_cuda_selection(self):
        self.dialog.tool_combo.setCurrentIndex(self.dialog.tool_combo.findData("cpu"))
        self.assertFalse(self.dialog.gpu_combo.isEnabled())
        self.assertTrue(self.dialog.download_button.isEnabled())
        self.assertEqual(self.dialog.choice(), OcrChoice())

    def test_use_downloaded_configuration_is_persisted(self):
        choice = OcrChoice("gpu", "cu118")
        fake_ready(self.root, choice)
        self.dialog.tool_combo.setCurrentIndex(self.dialog.tool_combo.findData("gpu"))
        self.dialog.gpu_combo.setCurrentIndex(self.dialog.gpu_combo.findData("cu118"))
        self.assertTrue(self.dialog.use_button.isEnabled())
        self.dialog.use_choice()
        self.assertEqual(load_active(self.root), choice)

    def wait_job(self):
        end = time.monotonic() + 5
        while self.dialog.job is not None and time.monotonic() < end:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertIsNone(self.dialog.job)

    def test_download_button_runs_selected_profile_and_waits_for_activation(self):
        self.dialog.tool_combo.setCurrentIndex(self.dialog.tool_combo.findData("gpu"))
        self.dialog.gpu_combo.setCurrentIndex(self.dialog.gpu_combo.findData("cu118"))
        self.dialog.show()
        def fake_deploy(choice, root, log, cancel, **kwargs):
            self.assertEqual(choice, OcrChoice("gpu", "cu118"))
            log("test download completed")
            fake_ready(root, choice)
            return choice
        with patch("photo_discrimination.ocr_settings.deploy", fake_deploy), \
             patch("photo_discrimination.ocr_settings.hardware_info", return_value="Test GPU"):
            QTest.mouseClick(self.dialog.download_button, Qt.MouseButton.LeftButton)
            self.wait_job()
        self.assertTrue(self.dialog.use_button.isEnabled())
        self.assertIn("test download completed", self.dialog.log_view.toPlainText())
        self.assertIsNone(load_active(self.root), "A completed download must not silently change the active selection")

    def test_failed_download_cannot_enable_configuration(self):
        self.dialog.tool_combo.setCurrentIndex(self.dialog.tool_combo.findData("cpu"))
        self.dialog.show()
        with patch("photo_discrimination.ocr_settings.deploy", side_effect=RuntimeErrorInfo("network failed")), \
             patch("photo_discrimination.ocr_settings.hardware_info", return_value="Test CPU"):
            QTest.mouseClick(self.dialog.download_button, Qt.MouseButton.LeftButton)
            self.wait_job()
        self.assertFalse(self.dialog.use_button.isEnabled())
        self.assertIn("network failed", self.dialog.log_view.toPlainText())
        self.assertIsNone(load_active(self.root))


if __name__ == "__main__":
    unittest.main()
