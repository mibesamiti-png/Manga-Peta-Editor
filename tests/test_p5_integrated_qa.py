"""P5 end-to-end synthetic Qt checks for save, pages, effects, export, updater.

These tests exercise the real MangaEditor methods in offscreen mode. They do NOT
replace human verification of Windows pointer/focus/layout behavior.
Only in-memory images and temporary files are used; no private manga assets.
"""
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image as PILImage
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import app
from app import MangaEditor, PanelItem


class P5IntegratedQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mpe_p5_synthetic_")
        self.root = Path(self.temp.name)
        self.picture = self.root / "synthetic_red.png"
        self.overlay = self.root / "synthetic_overlay.png"
        red = QImage(64, 64, QImage.Format_ARGB32)
        red.fill(QColor(240, 30, 30))
        self.assertTrue(red.save(str(self.picture)))
        blue = QImage(32, 32, QImage.Format_ARGB32)
        blue.fill(QColor(20, 30, 220, 160))
        self.assertTrue(blue.save(str(self.overlay)))

        self.editor = MangaEditor()
        self.editor.autosave_timer.stop()
        self.editor.recovery_path = self.root / "recovery.mpe.json"
        self.editor.set_page_size(400, 500)
        self.editor.title_edit.setText("P5人工テスト")
        self.editor.episode_edit.setText("1")

    def tearDown(self):
        with patch.object(self.editor, "_has_unsaved_changes", return_value=False):
            self.editor.close()
        self.editor.autosave_timer.stop()
        self.temp.cleanup()

    def panels(self):
        return [
            item for item in self.editor.current_scene().items()
            if isinstance(item, PanelItem)
        ]

    def add_panel(self, with_image=True, polygon=False):
        points = [
            [0, 0], [160, 0], [160, 160], [0, 160],
        ] if polygon else None
        panel = PanelItem(
            QRectF(0, 0, 160, 160),
            image_path=str(self.picture) if with_image else "",
            custom_points=points,
        )
        panel.setPos(40, 35)
        self.editor.current_scene().addItem(panel)
        self.editor.current_scene().clearSelection()
        panel.setSelected(True)
        self.editor.record_history()
        return panel

    def test_page_switch_then_undo_redo_is_page_local(self):
        panel = self.add_panel()
        panel.setPos(60, 55)
        self.editor.record_history()
        self.editor.add_page()
        page2 = self.add_panel(with_image=False)
        page2.setPos(180, 120)
        self.editor.record_history()
        self.editor.page_list.setCurrentRow(0)
        returned = self.panels()
        self.assertEqual(len(returned), 1)
        self.assertAlmostEqual(returned[0].pos().x(), 60)
        self.editor.undo()
        self.assertAlmostEqual(self.panels()[0].pos().x(), 40)
        self.editor.redo()
        self.assertAlmostEqual(self.panels()[0].pos().x(), 60)
        self.editor.page_list.setCurrentRow(1)
        self.assertAlmostEqual(self.panels()[0].pos().x(), 180)

    def test_e_chord_split_undo_redo_and_project_reload(self):
        panel = self.add_panel(polygon=True)
        self.assertTrue(panel.add_image_simple_effect("brightness", factor=0.8))
        self.assertTrue(panel.add_panel_overlay(str(self.overlay), opacity=0.6))
        self.editor.record_history()
        panel.set_corner_edit_enabled(True)
        panel._selected_corner_indices = {0, 1}
        self.assertTrue(self.editor.insert_selected_panel_edge_vertex())
        self.assertEqual(len(panel.corner_points()), 5)
        panel._selected_corner_indices = {0, 3}
        self.editor.panel_split_gap_spin.setValue(0)
        self.assertTrue(self.editor.split_selected_panel_by_chord(panel, (0, 3)))
        children = self.panels()
        self.assertEqual(len(children), 2)
        self.assertEqual(len({p.effect_uid for p in children}), 2)
        self.assertTrue(all(p.image_effects for p in children))
        self.assertTrue(all(p.panel_overlays for p in children))
        self.assertTrue(all(p.image_path for p in children))
        self.editor.undo()
        restored = self.panels()
        self.assertEqual(len(restored), 1)
        self.assertEqual(len(restored[0].corner_points()), 5)
        self.editor.redo()
        self.assertEqual(len(self.panels()), 2)

        target = self.root / "project.mpe.json"
        self.assertTrue(self.editor._save_project_to(target))
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["pages"][0]["items"]), 2)
        self.editor.load_project_data(saved, project_path=str(target))
        self.assertEqual(len(self.panels()), 2)
        self.assertTrue(all(p.image_effects for p in self.panels()))
        self.assertTrue(all(p.panel_overlays for p in self.panels()))
        self.assertTrue(all(Path(p.image_path).is_file() for p in self.panels()))
        self.assertTrue(all(len(p.corner_points()) >= 3 for p in self.panels()))

    def test_legacy_four_vertex_project_still_roundtrips(self):
        panel = self.add_panel(with_image=False, polygon=True)
        data = {
            "version": "v1.8.4-debug",
            "title": "旧形式テスト",
            "episode": "2",
            "canvas_size": [400, 500],
            "pages": [{"name": "旧ページ", "items": [panel.serialize()]}],
        }
        self.editor.load_project_data(data)
        self.assertEqual(len(self.panels()), 1)
        self.assertEqual(len(self.panels()[0].corner_points()), 4)
        self.assertEqual(len(self.editor.project_data()["pages"][0]["items"][0]["custom_points"]), 4)

    def test_effect_only_undo_redo_and_page_independence(self):
        panel = self.add_panel()
        uid = panel.effect_uid
        before = self.editor._capture_effect_state([panel])
        self.assertTrue(panel.add_image_simple_effect("grayscale"))
        self.assertTrue(self.editor._finish_effect_change(before, "合成効果", [panel]))
        self.assertEqual(len(panel.image_effects), 1)

        self.editor.add_page()
        self.assertEqual(self.editor._effect_history_bucket()["undo"], [])
        self.editor.page_list.setCurrentRow(0)
        original = next(p for p in self.panels() if p.effect_uid == uid)
        self.assertEqual(len(original.image_effects), 1)
        self.editor.effect_undo()
        self.assertEqual(len(original.image_effects), 0)
        self.editor.effect_redo()
        self.assertEqual(len(original.image_effects), 1)

    def test_recovery_checkpoint_is_synthetic_and_restorable(self):
        panel = self.add_panel(polygon=True)
        panel.setPos(90, 45)
        self.editor.record_history()
        self.editor.auto_save_recovery()
        self.assertTrue(self.editor.recovery_path.exists())
        data = json.loads(self.editor.recovery_path.read_text(encoding="utf-8"))
        self.assertTrue(data["recovery"])
        self.assertEqual(len(data["pages"]), 1)
        self.editor.load_project_data(data, from_recovery=True)
        self.assertAlmostEqual(self.panels()[0].pos().x(), 90)
        self.assertTrue(self.editor._has_unsaved_changes())

    def test_png_long_png_and_zip_produce_decodable_images(self):
        self.add_panel()
        self.editor.add_page()
        self.add_panel()
        current = self.root / "current.png"
        long = self.root / "long.png"
        archive = self.root / "all.zip"
        with patch.object(app.QFileDialog, "getSaveFileName", return_value=(str(current), "PNG")):
            self.editor.export_current_png()
        with patch.object(app.QFileDialog, "getSaveFileName", return_value=(str(long), "PNG")):
            self.editor.export_long_png()
        with patch.object(app.QFileDialog, "getSaveFileName", return_value=(str(archive), "ZIP")):
            self.editor.export_all_zip()

        with PILImage.open(current) as im:
            self.assertEqual(im.size, (400, 500))
            im.verify()
        with PILImage.open(long) as im:
            self.assertEqual(im.size, (400, 1000))
            im.verify()
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            self.assertEqual(len(names), 2)
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(all(name.lower().endswith(".png") for name in names))
            for name in names:
                with z.open(name) as fh:
                    with PILImage.open(fh) as im:
                        self.assertEqual(im.size, (400, 500))
                        im.verify()

    def test_export_does_not_mutate_edit_history_or_selection(self):
        panel = self.add_panel(with_image=False)
        initial = self.editor.pages[0]["history_index"]
        before = panel.serialize()
        preview = self.editor.render_scene_to_image(self.editor.current_scene())
        self.assertFalse(preview.isNull())
        self.assertTrue(panel.isSelected())
        self.assertEqual(panel.serialize(), before)
        self.assertEqual(self.editor.pages[0]["history_index"], initial)

    def test_updater_validates_single_py_without_replacing_live_file(self):
        version = self.editor._validate_update_file(Path(app.__file__))
        self.assertEqual(version, app.APP_VERSION)
        tiny = self.root / "tiny.py"
        tiny.write_text("print('not an editor')\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.editor._validate_update_file(tiny)
        invalid = self.root / "invalid.py"
        invalid.write_text("x = 1\n" * 2000 + "def broken(:\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.editor._validate_update_file(invalid)
        self.assertFalse(self.editor._is_python_update_file(self.picture))

    def test_narrow_window_can_initialize_and_render_without_crashing(self):
        self.editor.resize(900, 560)
        self.add_panel(polygon=True)
        self.editor.view.viewport().update()
        self.assertGreaterEqual(self.editor.minimumWidth(), 900)
        image = self.editor.render_scene_to_image(self.editor.current_scene())
        self.assertEqual((image.width(), image.height()), (400, 500))


if __name__ == "__main__":
    unittest.main()
