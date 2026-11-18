"""Offscreen regressions for G-move snapping without numeric/axis drift."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGraphicsScene,
    QMainWindow, QSpinBox,
)

from app import CanvasView, MangaEditor, PanelItem


class SnapHarness(QMainWindow):
    _scene_delta_to_local = MangaEditor._scene_delta_to_local
    _valid_panel_quad = MangaEditor._valid_panel_quad
    _valid_panel_polygon = staticmethod(MangaEditor._valid_panel_polygon)
    _panel_shape_valid = MangaEditor._panel_shape_valid
    _segments_intersect = staticmethod(MangaEditor._segments_intersect)
    _snap_scene_rect = MangaEditor._snap_scene_rect
    _group_snap_correction = MangaEditor._group_snap_correction
    snap_selected_items = MangaEditor.snap_selected_items
    _parallel_snap_corner_local = MangaEditor._parallel_snap_corner_local
    _rigid_clamp_local_delta = MangaEditor._rigid_clamp_local_delta
    _cluster_snap_corrections = MangaEditor._cluster_snap_corrections
    _best_group_corner_delta = MangaEditor._best_group_corner_delta
    _closest_point_on_segment = staticmethod(MangaEditor._closest_point_on_segment)
    snap_panel_corner = MangaEditor.snap_panel_corner

    def __init__(self):
        super().__init__()
        self.canvas_w, self.canvas_h = 600, 800
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("選択", "select")
        self.scene = QGraphicsScene(self)
        self.view = CanvasView(self)
        self.setCentralWidget(self.view)
        self.view.setScene(self.scene)

        self.snap_check = QCheckBox(self)
        self.snap_check.setChecked(True)
        self.snap_threshold_spin = QSpinBox(self)
        self.snap_threshold_spin.setValue(14)
        self.corner_snap_check = QCheckBox(self)
        self.corner_snap_check.setChecked(True)
        self.corner_snap_spin = QSpinBox(self)
        self.corner_snap_spin.setValue(18)
        self.corner_gap_spin = QSpinBox(self)
        self.corner_gap_spin.setValue(0)
        self.page_outer_gap_spin = QSpinBox(self)
        self.page_outer_gap_spin.setValue(0)
        self.history_calls = 0

    def current_scene(self):
        return self.scene

    def record_history(self):
        self.history_calls += 1

    def refresh_layer_list(self):
        pass

    def add_panel(self, x, y, selected=False):
        panel = PanelItem(QRectF(0, 0, 120, 110))
        panel.setPos(x, y)
        self.scene.addItem(panel)
        panel.setSelected(selected)
        return panel


class PanelGrabSnapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = SnapHarness()
        self.panel = self.editor.add_panel(20, 30, selected=True)
        self.target = self.editor.add_panel(165, 50)

    def tearDown(self):
        self.editor.close()

    def key(self, key):
        event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier)
        self.editor.view.keyPressEvent(event)

    def grab_with_mouse(self, dx, dy=0):
        self.key(Qt.Key_G)
        self.assertIsNotNone(self.editor.view._panel_grab)
        state = self.editor.view._panel_grab
        state["mouse_delta"] = QPointF(dx, dy)
        self.editor.view._apply_panel_grab()
        return state

    def test_whole_panel_snaps_like_pointer_release(self):
        self.panel.setPos(43, 30)
        self.editor.snap_selected_items()
        pointer_x = self.panel.pos().x()
        self.assertAlmostEqual(pointer_x, 45)
        self.panel.setPos(20, 30)
        state = self.grab_with_mouse(23)
        self.assertAlmostEqual(self.panel.pos().x(), pointer_x)
        self.assertAlmostEqual(state["delta"].x(), 25)
        self.key(Qt.Key_Return)
        self.assertEqual(self.editor.history_calls, 1)

    def test_numeric_g_x_is_exact_even_near_target(self):
        self.key(Qt.Key_G)
        self.key(Qt.Key_X)
        self.key(Qt.Key_2)
        self.key(Qt.Key_3)
        self.assertAlmostEqual(self.panel.pos().x(), 43)
        self.assertAlmostEqual(self.panel.pos().y(), 30)
        self.key(Qt.Key_Return)
        self.assertEqual(self.editor.history_calls, 1)

    def test_mouse_axis_constraint_does_not_snap_other_axis(self):
        self.key(Qt.Key_G)
        self.key(Qt.Key_X)
        state = self.editor.view._panel_grab
        state["mouse_delta"] = QPointF(23, 9)
        self.editor.view._apply_panel_grab()
        self.assertAlmostEqual(self.panel.pos().x(), 45)
        self.assertAlmostEqual(self.panel.pos().y(), 30)

    def test_group_snap_moves_all_panels_by_same_delta(self):
        second = self.editor.add_panel(210, 220, selected=True)
        self.target.setPos(355, 180)
        self.grab_with_mouse(23)
        self.assertAlmostEqual(self.panel.pos().x(), 45)
        self.assertAlmostEqual(second.pos().x(), 235)
        self.assertAlmostEqual(second.pos().y(), 220)
        self.key(Qt.Key_Return)
        self.assertEqual(self.editor.history_calls, 1)

    def test_disabled_object_snap_keeps_raw_mouse_delta(self):
        self.editor.snap_check.setChecked(False)
        self.grab_with_mouse(23)
        self.assertAlmostEqual(self.panel.pos().x(), 43)

    def test_same_mouse_preview_does_not_accumulate_snap(self):
        self.grab_with_mouse(23)
        for _ in range(4):
            self.editor.view._apply_panel_grab()
            self.assertAlmostEqual(self.panel.pos().x(), 45)
        self.key(Qt.Key_Escape)
        self.assertAlmostEqual(self.panel.pos().x(), 20)
        self.assertEqual(self.editor.history_calls, 0)

    def test_stationary_grab_never_snaps(self):
        self.target.setPos(142, 50)  # Only 2px from the initial right edge.
        self.grab_with_mouse(0)
        self.assertAlmostEqual(self.panel.pos().x(), 20)
        self.key(Qt.Key_Return)
        self.assertEqual(self.editor.history_calls, 0)

    def test_single_handle_uses_existing_corner_snap(self):
        self.target.setPos(165, 30)
        self.panel.set_corner_edit_enabled(True)
        self.panel._selected_corner_indices = {1}
        original = self.panel.corner_points()
        self.grab_with_mouse(22)
        moved = self.panel.corner_points()
        self.assertAlmostEqual(self.panel.mapToScene(moved[1]).x(), 165)
        self.assertEqual(moved[0], original[0])
        self.key(Qt.Key_Escape)
        self.assertEqual(self.panel.corner_points(), original)
        self.assertEqual(self.editor.history_calls, 0)

    def test_handle_numeric_input_is_not_snapped(self):
        self.target.setPos(165, 30)
        self.panel.set_corner_edit_enabled(True)
        self.panel._selected_corner_indices = {1}
        original = self.panel.corner_points()
        self.key(Qt.Key_G)
        self.key(Qt.Key_X)
        self.key(Qt.Key_2)
        self.key(Qt.Key_2)
        moved = self.panel.corner_points()
        self.assertAlmostEqual(moved[1].x(), original[1].x() + 22)
        self.key(Qt.Key_Return)
        self.assertEqual(self.editor.history_calls, 1)


if __name__ == "__main__":
    unittest.main()
