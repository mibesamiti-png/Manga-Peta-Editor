"""P5 GUI acceptance automation using real Qt event delivery and synthetic assets.

This is a Windows offscreen/virtual GUI interaction suite, not a human check on
the user's actual PC. The update test alters only a temporary app.py copy.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QMimeData, Qt, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app
from app import MangaEditor, PanelItem, QRectF


class GUIQTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mpe_gui_qa_")
        self.root = Path(self.tmp.name)
        self.red_path = self.root / "synthetic_red.png"
        red = QImage(64, 64, QImage.Format_ARGB32)
        red.fill(QColor(240, 30, 30))
        self.assertTrue(red.save(str(self.red_path)))

        self.editor = MangaEditor()
        self.editor.autosave_timer.stop()
        self.editor.recovery_path = self.root / "recovery.mpe.json"
        self.editor.show()
        QApplication.processEvents()
        self.editor.set_page_size(420, 500)
        self.editor.view.setFocus(Qt.OtherFocusReason)
        self.editor.snap_check.setChecked(False)
        self.editor.corner_snap_check.setChecked(False)
        QApplication.processEvents()
        # Flush the delayed startup recovery probe while using an empty sandbox
        # recovery path, before later synthetic updater events touch it.
        QTest.qWait(300)
        # Unexpected modal dialogs should neither hang headless CI nor be
        # silently ignored. Record them and fail tearDown after cleanup.
        self._dialog_calls = []
        real_message_box = app.QMessageBox

        def unexpected_dialog(*args, **kwargs):
            self._dialog_calls.append(str(args[1]) if len(args) > 1 else "dialog")
            return real_message_box.No

        self._dialog_guard = patch.object(
            app, "QMessageBox",
            SimpleNamespace(
                Yes=real_message_box.Yes, No=real_message_box.No,
                information=unexpected_dialog, warning=unexpected_dialog,
                critical=unexpected_dialog, question=unexpected_dialog,
            ),
        )
        self._dialog_guard.start()

    def tearDown(self):
        self.editor.view.cancel_panel_grab()
        self.editor.view.cancel_panel_split()
        with patch.object(self.editor, "_has_unsaved_changes", return_value=False):
            self.editor.close()
        QApplication.processEvents()
        self._dialog_guard.stop()
        self.tmp.cleanup()
        self.assertEqual(self._dialog_calls, [], "Unexpected GUI dialog in test")

    def add_panel(self, polygon=False, image=True):
        panel = PanelItem(
            QRectF(0, 0, 220, 250),
            image_path=str(self.red_path) if image else "",
            custom_points=[[0, 0], [220, 0], [220, 250], [0, 250]] if polygon else None,
        )
        panel.setPos(40, 50)
        self.editor.current_scene().addItem(panel)
        self.editor.record_history()
        self.editor.view.fitInView(QRectF(0, 0, 420, 500), Qt.KeepAspectRatio)
        QApplication.processEvents()
        return panel

    def point(self, scene_x, scene_y):
        return self.editor.view.mapFromScene(QPointF(scene_x, scene_y))

    def click_panel(self):
        QTest.mouseClick(
            self.editor.view.viewport(), Qt.LeftButton,
            Qt.NoModifier, self.point(140, 120),
        )
        QApplication.processEvents()

    def panels(self):
        return [
            item for item in self.editor.current_scene().items()
            if isinstance(item, PanelItem)
        ]

    def maybe_save_qa_image(self, name, image):
        path = os.environ.get("MPE_GUI_QA_OUTPUT")
        if path:
            destination = Path(path)
            destination.mkdir(parents=True, exist_ok=True)
            self.assertTrue(image.save(str(destination / name)))

    def test_real_mouse_selection_and_g_keyboard_cancel_commit(self):
        panel = self.add_panel()
        self.assertFalse(panel.isSelected())
        self.click_panel()
        self.assertTrue(panel.isSelected())
        view = self.editor.view
        initial_history = self.editor.pages[0]["history_index"]
        QTest.keyClick(view, Qt.Key_G)
        self.assertIsNotNone(view._panel_grab)
        QTest.keyClicks(view, "25")
        self.assertAlmostEqual(panel.pos().x(), 65)
        self.assertAlmostEqual(panel.pos().y(), 75)
        QTest.keyClick(view, Qt.Key_Escape)
        self.assertAlmostEqual(panel.pos().x(), 40)
        self.assertEqual(self.editor.pages[0]["history_index"], initial_history)

        QTest.keyClick(view, Qt.Key_G)
        QTest.keyClick(view, Qt.Key_X)
        QTest.keyClicks(view, "30")
        self.assertAlmostEqual(panel.pos().x(), 70)
        self.assertAlmostEqual(panel.pos().y(), 50)
        # Real mouse click completes the modal grab, rather than direct method call.
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(140, 120))
        self.assertIsNone(view._panel_grab)
        self.assertEqual(self.editor.pages[0]["history_index"], initial_history + 1)
        self.editor.undo()
        self.assertAlmostEqual(self.panels()[0].pos().x(), 40)
        self.editor.redo()
        self.assertAlmostEqual(self.panels()[0].pos().x(), 70)

    def test_ctrl_r_preview_is_visibly_orange_and_mouse_commit(self):
        panel = self.add_panel(image=False)
        self.click_panel()
        view = self.editor.view
        initial_history = self.editor.pages[0]["history_index"]
        self.editor.panel_split_gap_spin.setValue(24)
        QTest.keyClick(view, Qt.Key_R, Qt.ControlModifier)
        self.assertIsNotNone(view._panel_split_preview)
        QApplication.processEvents()
        image = view.viewport().grab().toImage()
        self.assertFalse(image.isNull())
        self.maybe_save_qa_image("split_preview_synthetic.png", image)

        # The orange dashed overlay must exist in the actual rendered widget.
        # A tolerance avoids exact antialiasing differences across Qt builds.
        orange = 0
        for y in range(0, image.height(), 2):
            for x in range(0, image.width(), 2):
                c = image.pixelColor(x, y)
                if c.red() > 220 and 105 < c.green() < 220 and c.blue() < 115:
                    orange += 1
        self.assertGreater(orange, 4, "Ctrl+R preview was not visibly rendered")

        QTest.keyClick(view, Qt.Key_Escape)
        self.assertIsNone(view._panel_split_preview)
        self.assertEqual(len(self.panels()), 1)
        self.assertEqual(self.editor.pages[0]["history_index"], initial_history)
        QTest.keyClick(view, Qt.Key_R, Qt.ControlModifier)
        self.assertIsNotNone(view._panel_split_preview)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(140, 120))
        self.assertEqual(len(self.panels()), 2)
        self.assertEqual(self.editor.pages[0]["history_index"], initial_history + 1)
        self.editor.undo()
        self.assertEqual(len(self.panels()), 1)

    def test_real_shift_edge_e_then_ctrl_vertices_chord(self):
        panel = self.add_panel(polygon=True, image=False)
        self.click_panel()
        self.assertTrue(panel.isSelected())
        panel.set_corner_edit_enabled(True)
        view = self.editor.view
        # Shift+click a visible edge, then E through the keyboard event path.
        QTest.mouseClick(
            view.viewport(), Qt.LeftButton, Qt.ShiftModifier,
            self.point(150, 50),
        )
        self.assertEqual(panel._selected_corner_indices, {0, 1})
        QTest.keyClick(view, Qt.Key_E)
        self.assertEqual(len(panel.corner_points()), 5)
        panel._selected_corner_indices.clear()
        for vertex in (0, 3):
            p = panel.mapToScene(panel.corner_points()[vertex])
            QTest.mouseClick(
                view.viewport(), Qt.LeftButton, Qt.ControlModifier,
                view.mapFromScene(p),
            )
        self.assertEqual(panel._selected_corner_indices, {0, 3})
        self.editor.panel_split_gap_spin.setValue(0)
        QTest.keyClick(view, Qt.Key_R, Qt.ControlModifier)
        self.assertIsNotNone(view._panel_split_preview)
        self.assertEqual(view._panel_split_preview["kind"], "chord")
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, self.point(130, 130))
        self.assertEqual(len(self.panels()), 2)
        self.editor.undo()
        self.assertEqual(len(self.panels()), 1)
        self.assertEqual(len(self.panels()[0].corner_points()), 5)

    def test_image_render_detects_concave_clip_and_effect_pixels(self):
        panel = self.add_panel(polygon=True)
        panel.prepareGeometryChange()
        panel.custom_points = [
            QPointF(0, 0), QPointF(220, 0), QPointF(220, 220),
            QPointF(110, 110), QPointF(0, 220),
        ]
        panel.update()
        scene_image = self.editor.render_scene_to_image(self.editor.current_scene())
        self.maybe_save_qa_image("concave_clip_synthetic.png", scene_image)
        inside = scene_image.pixelColor(90, 100)
        outside = scene_image.pixelColor(150, 230)
        self.assertGreater(inside.red(), 180)
        self.assertLess(inside.green(), 100)
        self.assertGreater(outside.red(), 240)
        self.assertGreater(outside.green(), 240)
        self.assertGreater(outside.blue(), 240)

        # A real Pillow effect must change rendered pixels, not only metadata.
        self.assertTrue(panel.add_image_simple_effect("grayscale"))
        gray = self.editor.render_scene_to_image(self.editor.current_scene())
        corrected = gray.pixelColor(90, 100)
        self.assertLess(abs(corrected.red() - corrected.green()), 4)
        self.assertLess(abs(corrected.green() - corrected.blue()), 4)
        self.maybe_save_qa_image("grayscale_concave_synthetic.png", gray)

    def test_synthetic_py_drop_replaces_only_temporary_install(self):
        # Simulate Qt's drag/drop event to the real window, but patch __file__
        # so handle_update_file can ONLY target our temporary sandbox copy.
        sandbox = self.root / "isolated_install"
        sandbox.mkdir()
        target = sandbox / "app.py"
        original_source = Path(app.__file__).read_text(encoding="utf-8")
        target.write_text(original_source, encoding="utf-8")
        update = self.root / "synthetic_update.py"
        update.write_text(original_source + "\n# synthetic gui qa update marker\n", encoding="utf-8")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(update))])
        timers = []
        with patch.object(app, "__file__", str(target)), patch.object(
            app, "QTimer", SimpleNamespace(singleShot=lambda *args: timers.append(args))
        ):
            enter = QDragEnterEvent(
                QPoint(16, 16), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
            )
            QApplication.sendEvent(self.editor, enter)
            self.assertTrue(enter.isAccepted())
            drop = QDropEvent(
                QPointF(16, 16), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
            )
            QApplication.sendEvent(self.editor, drop)
            self.assertTrue(drop.isAccepted())

        self.assertIn("synthetic gui qa update marker", target.read_text(encoding="utf-8"))
        backups = list((sandbox / "update_backups").glob("app_*.py"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original_source)
        self.assertTrue(any(args[0] == 350 for args in timers))
        # subprocess.Popen and QApplication.quit are deliberately NOT invoked.


if __name__ == "__main__":
    unittest.main()
