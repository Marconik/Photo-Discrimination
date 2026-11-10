import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photo_discrimination.album import Album, AlbumError, JsonIndex, compact_text, keywords, matches, move_selected


class AlbumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "相册 空格"
        self.root.mkdir()
        (self.root / "东京.JPG").write_bytes(b"first-photo")
        (self.root / "receipt.png").write_bytes(b"second-photo")

    def tearDown(self):
        self.temp.cleanup()

    def write_index(self, data):
        (self.root / "OCR.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_import_is_read_only_and_does_not_scan_subfolders(self):
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "hidden.jpg").write_bytes(b"hidden")
        (self.root / "notes.txt").write_text("note")
        album = Album.open(self.root)
        self.assertEqual({p.name for p in album.photos}, {"东京.JPG", "receipt.png"})
        self.assertFalse((self.root / "OCR.json").exists())

    def test_save_compacts_whitespace_and_overwrites_only_one_entry(self):
        self.write_index({"东京.JPG": "旧文字", "receipt.png": "Keep", "missing.jpg": "保留"})
        album = Album.open(self.root)
        album.index.set_text("东京.JPG", "東 京\nABC\t123\u3000%")
        saved = json.loads((self.root / "OCR.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, {"东京.JPG": "東京ABC123%", "receipt.png": "Keep", "missing.jpg": "保留"})

    def test_empty_result_is_distinct_from_missing_result(self):
        album = Album.open(self.root)
        album.index.set_text("东京.JPG", " \n\t")
        self.assertIn("东京.JPG", album.index.texts)
        self.assertEqual(album.index.texts["东京.JPG"], "")
        self.assertNotIn("receipt.png", album.index.texts)

    def test_load_bom_and_unicode(self):
        (self.root / "OCR.json").write_text('{"东京.JPG": "予約 番号１２３"}', encoding="utf-8-sig")
        album = Album.open(self.root)
        self.assertEqual(album.index.texts["东京.JPG"], "予約番号１２３")

    def test_invalid_documents_are_preserved(self):
        invalid = ('{ broken', '[]', '{"x": 123}', '{"x":"a", "x":"b"}', '{"photos":{"x":"a"}}')
        path = self.root / "OCR.json"
        for raw in invalid:
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(AlbumError):
                    Album.open(self.root)
                self.assertEqual(path.read_text(encoding="utf-8"), raw)

    def test_external_update_is_not_overwritten(self):
        self.write_index({"东京.JPG": "before"})
        album = Album.open(self.root)
        self.write_index({"东京.JPG": "external"})
        with self.assertRaises(AlbumError):
            album.index.set_text("东京.JPG", "overwrite")
        self.assertEqual(json.loads((self.root / "OCR.json").read_text(encoding="utf-8"))["东京.JPG"], "external")

    def test_new_external_index_is_not_overwritten(self):
        album = Album.open(self.root)
        self.write_index({"东京.JPG": "new external"})
        with self.assertRaises(AlbumError):
            album.index.set_text("东京.JPG", "overwrite")

    def test_atomic_save_failure_keeps_existing_json(self):
        self.write_index({"东京.JPG": "original"})
        album = Album.open(self.root)
        old = (self.root / "OCR.json").read_bytes()
        with patch("photo_discrimination.album.os.replace", side_effect=PermissionError("read only")):
            with self.assertRaises(AlbumError):
                album.index.set_text("东京.JPG", "changed")
        self.assertEqual((self.root / "OCR.json").read_bytes(), old)
        self.assertEqual(album.index.texts["东京.JPG"], "original")
        self.assertEqual(list(self.root.glob(".OCR-*.tmp")), [])

    def test_move_transfers_selected_files_and_their_index(self):
        self.write_index({"东京.JPG": "東京 2026", "receipt.png": "Invoice", "missing.jpg": "orphan"})
        album = Album.open(self.root)
        destination = self.root.parent / "导出"
        report = move_selected(album, ["东京.JPG"], destination)
        self.assertEqual(report.moved, ["东京.JPG"])
        self.assertEqual(report.errors, [])
        self.assertFalse((self.root / "东京.JPG").exists())
        self.assertEqual((destination / "东京.JPG").read_bytes(), b"first-photo")
        self.assertTrue((self.root / "receipt.png").exists())
        self.assertEqual(JsonIndex.open(destination).texts, {"东京.JPG": "東京2026"})
        self.assertEqual(JsonIndex.open(self.root).texts, {"receipt.png": "Invoice", "missing.jpg": "orphan"})

    def test_unindexed_move_remains_unindexed(self):
        album = Album.open(self.root)
        destination = self.root.parent / "export"
        report = move_selected(album, ["东京.JPG", "receipt.png"], destination)
        self.assertEqual(len(report.moved), 2)
        self.assertEqual(JsonIndex.open(destination).texts, {})

    def test_existing_destination_is_untouched(self):
        album = Album.open(self.root)
        destination = self.root.parent / "existing"
        destination.mkdir()
        (destination / "东京.JPG").write_bytes(b"must-keep")
        with self.assertRaises(AlbumError):
            move_selected(album, ["东京.JPG"], destination)
        self.assertEqual((destination / "东京.JPG").read_bytes(), b"must-keep")
        self.assertTrue((self.root / "东京.JPG").exists())

    def test_invalid_selection_does_not_move_anything(self):
        album = Album.open(self.root)
        with self.assertRaises(AlbumError):
            move_selected(album, ["东京.JPG", "../outside.jpg"], self.root.parent / "export")
        self.assertTrue((self.root / "东京.JPG").exists())
        self.assertFalse((self.root.parent / "export").exists())

    def test_copy_failure_keeps_source_and_cleans_partial_copy(self):
        self.write_index({"东京.JPG": "Keep"})
        album = Album.open(self.root)
        destination = self.root.parent / "export"
        with patch("photo_discrimination.album.shutil.copyfileobj", side_effect=OSError("disk full")):
            report = move_selected(album, ["东京.JPG"], destination)
        self.assertEqual(report.moved, [])
        self.assertTrue(report.errors)
        self.assertTrue((self.root / "东京.JPG").exists())
        self.assertFalse((destination / "东京.JPG").exists())
        self.assertEqual(JsonIndex.open(self.root).texts["东京.JPG"], "Keep")

    def test_destination_index_failure_keeps_source(self):
        self.write_index({"东京.JPG": "Keep"})
        album = Album.open(self.root)
        destination = self.root.parent / "export"
        original_write = JsonIndex.write

        def fail_destination(index, data):
            if index.path.parent == destination and data:
                raise AlbumError("target is read only")
            return original_write(index, data)

        with patch.object(JsonIndex, "write", fail_destination):
            report = move_selected(album, ["东京.JPG"], destination)
        self.assertEqual(report.moved, [])
        self.assertTrue((self.root / "东京.JPG").exists())
        self.assertEqual(JsonIndex.open(self.root).texts["东京.JPG"], "Keep")

    def test_source_index_failure_after_move_preserves_destination_text(self):
        self.write_index({"东京.JPG": "Important"})
        album = Album.open(self.root)
        destination = self.root.parent / "export"
        original_write = JsonIndex.write

        def fail_source_update(index, data):
            if index.path.parent == self.root and "东京.JPG" not in data:
                raise AlbumError("source save failed")
            return original_write(index, data)

        with patch.object(JsonIndex, "write", fail_source_update):
            report = move_selected(album, ["东京.JPG", "receipt.png"], destination)
        self.assertEqual(report.moved, ["东京.JPG"])
        self.assertTrue(report.errors)
        self.assertEqual(JsonIndex.open(destination).texts["东京.JPG"], "Important")
        self.assertTrue((destination / "东京.JPG").exists())
        self.assertTrue((self.root / "receipt.png").exists())

    def test_conflicting_source_edit_stops_before_source_delete(self):
        self.write_index({"东京.JPG": "before"})
        album = Album.open(self.root)
        destination = self.root.parent / "export"
        original_copy = __import__("shutil").copyfileobj

        def copy_and_change(*args, **kwargs):
            original_copy(*args, **kwargs)
            self.write_index({"东京.JPG": "external"})

        with patch("photo_discrimination.album.shutil.copyfileobj", copy_and_change):
            report = move_selected(album, ["东京.JPG"], destination)
        self.assertEqual(report.moved, [])
        self.assertTrue((self.root / "东京.JPG").exists())
        self.assertEqual(JsonIndex.open(self.root).texts["东京.JPG"], "external")


class SearchTests(unittest.TestCase):
    def test_keywords_use_unicode_whitespace(self):
        self.assertEqual(keywords(" 東京\t2026\nＡＢＣ\u3000東京 "), ("東京", "2026", "abc"))

    def test_all_keywords_required(self):
        text = "東京駅Invoice2026"
        self.assertTrue(matches(text, keywords("東京 2026")))
        self.assertFalse(matches(text, keywords("東京 2027")))

    def test_single_char_and_symbols_are_literal(self):
        text = "发票ABC-123金额￥200O'Neil_50%"
        for query in ("票", "发票", "￥", "%", "_", "O'Neil", "ABC-123"):
            with self.subTest(query=query):
                self.assertTrue(matches(text, keywords(query)))
        self.assertFalse(matches(text, keywords(".*")))

    def test_case_and_width_without_mutating_stored_characters(self):
        self.assertTrue(matches("ＩＮＶＯＩＣＥ１２３", keywords("invoice 123")))
        self.assertEqual(compact_text("ＡＢＣ １２３\n東京"), "ＡＢＣ１２３東京")

    def test_blank_query_matches_all(self):
        self.assertTrue(matches("", keywords(" \n\t")))


if __name__ == "__main__":
    unittest.main()
