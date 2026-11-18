"""Regression tests for B1 panel G move and modal X handling.

Use synthetic QGraphics items only; the GUI is run in offscreen mode.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGraphicsItem, QGraphicsScene,
    QGraphicsTextItem, QMainWindow,
)

from app import CanvasView, MangaEditor, PanelItem


class EditorHarness(QMainWindow):
    delete_selected_panels = MangaEditor.delete_selected_panels

    def __init__(self):
        super().__init__()
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("選択", "select")
        self.scene = QGraphicsScene(self)
        self.view = CanvasView(self)
        self.setCentralWidget(self.view)
        self.view.setScene(self.scene)
        self.history_calls = 0

    def record_history(self):
        self.history_calls += 1

    def refresh_layer_list(self):
        pass

    def current_scene(self):
        return self.scene

    def selected(self):
        return self.scene.selectedItems()

    def add_panel(self, x=20.0, y=30.0):
        panel = PanelItem(QRectF(0, 0, 120, 110))
        panel.setPos(x, y)
        self.scene.addItem(panel)
        panel.setSelected(True)
        return panel


class PanelGrabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = EditorHarness()
        self.panel = self.editor.add_panel()

    def tearDown(self):
        self.editor.close()

    def key(self, key, mods=Qt.NoModifier):
        e = QKeyEvent(QEvent.KeyPress, key, mods)
        self.editor.view.keyPressEvent(e)

    def grab(self):
        self.key(Qt.Key_G)
        self.assertIsNotNone(self.editor.view._panel_grab)

    def assert_panel_at(self, x, y):
        self.assertAlmostEqual(self.panel.pos().x(), x)
        self.assertAlmostEqual(self.panel.pos().y(), y)

    def test_g_xy_numeric_enter_one_history(self):
        self.grab()
        self.key(Qt.Key_2)
        self.key(Qt.Key_5)
        self.assert_panel_at(45, 55)
        self.key(Qt.Key_Return)
        self.assertIsNone(self.editor.view._panel_grab)
        self.assertEqual(self.editor.history_calls, 1)

    def test_g_x_axis_numeric_and_x_never_deletes(self):
        self.grab()
        self.key(Qt.Key_X)
        self.assertIs(self.panel.scene(), self.editor.scene)
        self.key(Qt.Key_3)
        self.key(Qt.Key_0)
        self.assert_panel_at(50, 30)
        self.key(Qt.Key_Enter)
        self.assertEqual(self.editor.history_calls, 1)

    def test_g_y_axis_numeric_negative_prefix(self):
        self.grab()
        self.key(Qt.Key_Y)
        self.key(Qt.Key_Minus)
        self.key(Qt.Key_1)
        self.key(Qt.Key_5)
        self.assert_panel_at(20, 15)
        self.key(Qt.Key_Return)

    def test_g_numeric_negative_suffix_toggles(self):
        self.grab()
        self.key(Qt.Key_2)
        self.key(Qt.Key_5)
        self.key(Qt.Key_Minus)
        self.assert_panel_at(-5, 5)
        self.key(Qt.Key_Minus)
        self.assert_panel_at(45, 55)
        self.key(Qt.Key_Return)

    def test_mouse_delta_axis_and_numeric_override(self):
        self.grab()
        state = self.editor.view._panel_grab
        state["mouse_delta"] = QPointF(17, -8)
        self.editor.view._apply_panel_grab()
        self.assert_panel_at(37, 22)
        self.key(Qt.Key_Y)
        self.assert_panel_at(20, 22)
        self.key(Qt.Key_4)
        self.assert_panel_at(20, 34)
        state["mouse_delta"] = QPointF(999, 999)
        self.editor.view._apply_panel_grab()
        self.assert_panel_at(20, 34)
        self.key(Qt.Key_Escape)
        self.assert_panel_at(20, 30)
        self.assertEqual(self.editor.history_calls, 0)

    def test_cancel_does_not_record_history(self):
        self.grab()
        self.key(Qt.Key_5)
        self.key(Qt.Key_Escape)
        self.assert_panel_at(20, 30)
        self.assertEqual(self.editor.history_calls, 0)

    def test_zero_move_does_not_record_history(self):
        self.grab()
        self.key(Qt.Key_0)
        self.key(Qt.Key_Return)
        self.assertEqual(self.editor.history_calls, 0)

    def test_multiple_panels_move_as_group(self):
        other = self.editor.add_panel(170, 180)
        self.grab()
        self.key(Qt.Key_1)
        self.key(Qt.Key_0)
        self.assert_panel_at(30, 40)
        self.assertAlmostEqual(other.pos().x(), 180)
        self.assertAlmostEqual(other.pos().y(), 190)
        self.key(Qt.Key_Return)
        self.assertEqual(self.editor.history_calls, 1)

    def test_idle_x_deletes_only_panels(self):
        text = QGraphicsTextItem("keep")
        self.editor.scene.addItem(text)
        text.setSelected(True)
        self.key(Qt.Key_X)
        self.assertIsNone(self.panel.scene())
        self.assertIs(text.scene(), self.editor.scene)
        self.assertEqual(self.editor.history_calls, 1)

    def test_text_focus_does_not_begin_g_or_delete(self):
        text = QGraphicsTextItem("text")
        text.setTextInteractionFlags(Qt.TextEditorInteraction)
        text.setFlag(QGraphicsItem.ItemIsFocusable, True)
        self.editor.scene.addItem(text)
        self.editor.scene.setFocusItem(text)
        self.assertIs(self.editor.scene.focusItem(), text)
        self.key(Qt.Key_G)
        self.assertIsNone(self.editor.view._panel_grab)
        self.key(Qt.Key_X)
        self.assertIs(self.panel.scene(), self.editor.scene)

    def test_corner_edit_refuses_whole_panel_grab(self):
        self.panel.set_corner_edit_enabled(True)
        self.key(Qt.Key_G)
        self.assertIsNone(self.editor.view._panel_grab)
        self.assert_panel_at(20, 30)

    def test_page_scene_change_cancels_preview(self):
        self.grab()
        self.key(Qt.Key_5)
        self.editor.view.setScene(QGraphicsScene(self.editor))
        self.assert_panel_at(20, 30)
        self.assertIsNone(self.editor.view._panel_grab)
        self.assertEqual(self.editor.history_calls, 0)


if __name__ == "__main__":
    unittest.main()
