"""Focused Ctrl+R panel strip-preview tests using synthetic offscreen data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QEvent
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGraphicsItem, QGraphicsScene,
    QGraphicsTextItem, QMainWindow, QSpinBox,
)

from app import CanvasView, MangaEditor, PanelItem


class SplitEditorHarness(QMainWindow):
    _valid_panel_quad = MangaEditor._valid_panel_quad
    _segments_intersect = staticmethod(MangaEditor._segments_intersect)
    _freeform_strip_cells = MangaEditor._freeform_strip_cells
    _biased_fraction = staticmethod(MangaEditor._biased_fraction)
    _lerp_point = staticmethod(MangaEditor._lerp_point)
    _split_freeform_panel = MangaEditor._split_freeform_panel
    _child_pos_preserving_panel_transform = MangaEditor._child_pos_preserving_panel_transform
    split_selected_panel_n = MangaEditor.split_selected_panel_n

    def __init__(self):
        super().__init__()
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("選択", "select")
        self.panel_split_layout = QComboBox(self)
        self.panel_split_layout.addItem("左右", "vertical")
        self.panel_split_layout.addItem("上下", "horizontal")
        self.panel_split_count_spin = QSpinBox(self)
        self.panel_split_count_spin.setRange(2, 12)
        self.panel_split_count_spin.setValue(2)
        self.panel_split_gap_spin = QSpinBox(self)
        self.panel_split_gap_spin.setRange(0, 100)
        self.panel_split_gap_spin.setValue(24)
        self.panel_split_image_mode = QComboBox(self)
        self.panel_split_image_mode.addItem("自動", "fit_each")
        self.panel_split_image_mode.addItem("維持", "keep_view")
        self.scene = QGraphicsScene(self)
        self.view = CanvasView(self)
        self.setCentralWidget(self.view)
        self.view.setScene(self.scene)
        self.history_calls = 0

    def record_history(self):
        self.history_calls += 1

    def current_scene(self):
        return self.scene

    def selected(self):
        return self.scene.selectedItems()

    def add_panel(self, width=320, height=240):
        panel = PanelItem(QRectF(0, 0, width, height))
        panel.setPos(25, 30)
        self.scene.addItem(panel)
        self.scene.clearSelection()
        panel.setSelected(True)
        return panel


class PanelSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = SplitEditorHarness()
        self.panel = self.editor.add_panel()
        self.view = self.editor.view

    def tearDown(self):
        self.editor.close()

    def press(self, key, modifier=Qt.NoModifier):
        event = QKeyEvent(QEvent.KeyPress, key, modifier)
        self.view.keyPressEvent(event)

    def start(self):
        self.press(Qt.Key_R, Qt.ControlModifier)
        self.assertIsNotNone(self.view._panel_split_preview)

    def selected_panels(self):
        return [i for i in self.editor.scene.items() if isinstance(i, PanelItem)]

    def test_preview_does_not_change_scene_or_history(self):
        before = self.panel.serialize()
        self.start()
        self.view.change_panel_split_count(2)
        self.view.update_panel_split_orientation(
            self.panel.mapToScene(self.view._panel_split_preview["anchor"] + QPointF(0, 40))
        )
        self.assertEqual(self.panel.serialize(), before)
        self.assertEqual(len(self.selected_panels()), 1)
        self.assertEqual(self.editor.history_calls, 0)

    def test_mouse_direction_switches_local_axis(self):
        self.start()
        anchor = self.view._panel_split_preview["anchor"]
        self.view.update_panel_split_orientation(self.panel.mapToScene(anchor + QPointF(45, 3)))
        self.assertEqual(self.view._panel_split_preview["direction"], "vertical")
        self.view.update_panel_split_orientation(self.panel.mapToScene(anchor + QPointF(2, 45)))
        self.assertEqual(self.view._panel_split_preview["direction"], "horizontal")

    def test_wheel_count_updates_and_clamps(self):
        self.start()
        up = QWheelEvent(
            QPointF(20, 20), QPointF(20, 20), QPoint(0, 0), QPoint(0, 120),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
        )
        self.view.wheelEvent(up)
        self.assertEqual(self.view._panel_split_preview["count"], 3)
        self.view.change_panel_split_count(100)
        self.assertEqual(self.view._panel_split_preview["count"], 12)
        self.view.change_panel_split_count(-100)
        self.assertEqual(self.view._panel_split_preview["count"], 2)

    def test_three_way_rect_split_with_exact_gutters_one_history(self):
        self.start()
        self.view.change_panel_split_count(1)
        self.assertTrue(self.view._panel_split_preview_valid())
        self.assertTrue(self.view.finish_panel_split(commit=True))
        self.assertIsNone(self.panel.scene())
        parts = sorted(self.selected_panels(), key=lambda item: item.rect().left())
        self.assertEqual(len(parts), 3)
        self.assertEqual(self.editor.history_calls, 1)
        for a, b in zip(parts, parts[1:]):
            gutter = b.rect().left()-a.rect().right()
            self.assertAlmostEqual(gutter, 24)

    def test_horizontal_split_and_preview_line_geometry(self):
        self.start()
        self.view._panel_split_preview["direction"] = "horizontal"
        self.view.change_panel_split_count(1)
        lines = self.view._split_preview_lines(self.panel, "horizontal", 3)
        self.assertEqual(len(lines), 2)
        self.assertAlmostEqual(lines[0][0].y(), 30+240/3)
        self.assertAlmostEqual(lines[0][1].y(), 30+240/3)
        self.assertTrue(self.view.finish_panel_split(True))
        parts = sorted(self.selected_panels(), key=lambda item: item.rect().top())
        self.assertEqual(len(parts), 3)
        for a, b in zip(parts, parts[1:]):
            self.assertAlmostEqual(b.rect().top()-a.rect().bottom(), 24)

    def test_freeform_quad_split_uses_existing_core(self):
        self.panel.enable_freeform()
        self.panel.custom_points = [
            QPointF(0, 0), QPointF(320, 20), QPointF(290, 240), QPointF(10, 220)
        ]
        self.start()
        self.assertTrue(self.view._panel_split_preview_valid())
        self.assertTrue(self.view.finish_panel_split(True))
        children = self.selected_panels()
        self.assertEqual(len(children), 2)
        self.assertTrue(all(child.custom_points is not None for child in children))
        self.assertEqual(self.editor.history_calls, 1)

    def test_invalid_count_does_not_commit(self):
        self.editor.panel_split_gap_spin.setValue(100)
        self.start()
        self.view.change_panel_split_count(4)
        self.assertFalse(self.view._panel_split_preview_valid())
        self.assertFalse(self.view.finish_panel_split(True))
        self.assertIs(self.panel.scene(), self.editor.scene)
        self.assertEqual(len(self.selected_panels()), 1)
        self.assertEqual(self.editor.history_calls, 0)
        self.assertIsNotNone(self.view._panel_split_preview)

    def test_escape_cancels_without_side_effects(self):
        before = self.panel.serialize()
        self.start()
        self.view.change_panel_split_count(2)
        self.press(Qt.Key_Escape)
        self.assertEqual(before, self.panel.serialize())
        self.assertEqual(self.editor.history_calls, 0)
        self.assertIsNone(self.view._panel_split_preview)

    def test_split_preview_does_not_run_g_or_x(self):
        self.start()
        self.press(Qt.Key_X)
        self.press(Qt.Key_G)
        self.assertIs(self.panel.scene(), self.editor.scene)
        self.assertIsNone(self.view._panel_grab)
        self.assertIsNotNone(self.view._panel_split_preview)

    def test_ctrl_r_ignored_while_text_editing(self):
        text = QGraphicsTextItem("editing")
        text.setTextInteractionFlags(Qt.TextEditorInteraction)
        text.setFlag(QGraphicsItem.ItemIsFocusable, True)
        self.editor.scene.addItem(text)
        self.editor.scene.setFocusItem(text)
        self.assertIs(self.editor.scene.focusItem(), text)
        self.press(Qt.Key_R, Qt.ControlModifier)
        self.assertIsNone(self.view._panel_split_preview)

    def test_no_selected_panel_or_multiple_selection(self):
        self.editor.scene.clearSelection()
        self.press(Qt.Key_R, Qt.ControlModifier)
        self.assertIsNone(self.view._panel_split_preview)
        other = self.editor.add_panel()
        self.panel.setSelected(True)
        self.assertIs(other.scene(), self.editor.scene)
        self.press(Qt.Key_R, Qt.ControlModifier)
        self.assertIsNone(self.view._panel_split_preview)

    def test_scene_switch_cancels_split(self):
        self.start()
        self.view.setScene(QGraphicsScene(self.editor))
        self.assertIsNone(self.view._panel_split_preview)
        self.assertEqual(self.editor.history_calls, 0)

    def test_diagonal_vertex_selection_waits_for_p4(self):
        self.panel.enable_freeform()
        self.panel._selected_corner_indices = {0, 2}
        self.press(Qt.Key_R, Qt.ControlModifier)
        self.assertIsNone(self.view._panel_split_preview)


if __name__ == "__main__":
    unittest.main()
