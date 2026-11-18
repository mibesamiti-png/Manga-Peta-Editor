"""P4 synthetic tests for vertex-chord cuts, gutter and Ctrl+R preview."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QPointF, QRectF, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGraphicsScene, QMainWindow, QSpinBox,
)
from app import (
    CanvasView, MangaEditor, PanelItem, split_polygon_chord,
    valid_polygon, _cross,
)


def area(points):
    p = [(v.x(), v.y()) if hasattr(v, "x") else v for v in points]
    return abs(sum(p[i][0]*p[(i+1) % len(p)][1] -
                   p[(i+1) % len(p)][0]*p[i][1]
                   for i in range(len(p))) / 2.0)


class ChordGeometryTests(unittest.TestCase):
    def test_rectangle_diagonal_with_no_gap_partitions_area(self):
        rect = [(0,0),(320,0),(320,240),(0,240)]
        children = split_polygon_chord(rect, 0, 2, 0)
        self.assertEqual(len(children), 2)
        self.assertTrue(all(valid_polygon(c) for c in children))
        self.assertAlmostEqual(sum(area(c) for c in children), area(rect))

    def test_gap_offsets_each_cut_edge_by_half_distance(self):
        pts = [(0,0),(320,0),(320,240),(0,240)]
        children = split_polygon_chord(pts, 0, 2, 24)
        self.assertEqual(len(children), 2)
        a, b = pts[0], pts[2]
        length = math.hypot(b[0]-a[0],b[1]-a[1])
        offset = []
        for child in children:
            distances = [_cross(a,b,v) / length for v in
                         (child[0], child[-1])]
            self.assertAlmostEqual(distances[0], distances[1], places=5)
            offset.append(distances[0])
        self.assertAlmostEqual(abs(offset[0]-offset[1]), 24, places=5)
        self.assertLess(sum(area(c) for c in children),area(pts))

    def test_odd_sided_panel_cut(self):
        pts = [(0,0),(160,0),(320,0),(320,240),(0,240)]
        children = split_polygon_chord(pts,0,3,24)
        self.assertEqual(len(children),2)
        self.assertTrue(all(valid_polygon(x) for x in children))
        self.assertGreaterEqual(len(children[0]),3)

    def test_reversed_winding_support(self):
        pts = [(0,0),(320,0),(320,240),(0,240)]
        children = split_polygon_chord(list(reversed(pts)),0,2,24)
        self.assertEqual(len(children),2)

    def test_adjacent_duplicate_and_out_of_bounds_are_rejected(self):
        pts = [(0,0),(320,0),(320,240),(0,240)]
        for pair in [(0,0),(0,1),(0,3),(0,4),(-1,2)]:
            with self.subTest(pair=pair):
                self.assertEqual(split_polygon_chord(pts,*pair,24),[])

    def test_concave_valid_inside_chord_can_keep_gutter(self):
        pts = [(0,0),(300,0),(300,200),(200,200),
               (200,120),(100,120),(100,200),(0,200)]
        self.assertTrue(valid_polygon(pts))
        parts = split_polygon_chord(pts,0,5,24)
        self.assertEqual(len(parts),2)
        self.assertTrue(all(valid_polygon(c) for c in parts))

    def test_concave_chord_outside_polygon_is_rejected(self):
        pts = [(0,0),(300,0),(300,200),(200,200),
               (200,120),(100,120),(100,200),(0,200)]
        # Horizontal chord across missing interior notch.
        self.assertEqual(split_polygon_chord(pts,7,3,0),[])
        self.assertEqual(split_polygon_chord(pts,7,3,24),[])

    def test_gutter_too_large_is_rejected_without_fallback(self):
        pts = [(0,0),(320,0),(320,240),(0,240)]
        self.assertEqual(split_polygon_chord(pts,0,2,1000),[])
        self.assertEqual(split_polygon_chord(pts,0,2,-1),[])

    def test_chord_touching_nonadjacent_boundary_is_rejected(self):
        # Chord 0->2 passes exactly through the notch vertex 3.
        pts = [(0,0),(320,0),(320,240),(160,120),(0,240)]
        self.assertTrue(valid_polygon(pts))
        self.assertEqual(split_polygon_chord(pts,0,2,0),[])

    def test_geometry_input_is_not_mutated(self):
        pts = [(0,0),(320,0),(320,240),(0,240)]
        snapshot = list(pts)
        self.assertEqual(len(split_polygon_chord(pts,0,2,24)),2)
        self.assertEqual(pts,snapshot)


class ChordEditorHarness(QMainWindow):
    _valid_panel_quad = MangaEditor._valid_panel_quad
    _valid_panel_polygon = staticmethod(MangaEditor._valid_panel_polygon)
    _panel_shape_valid = MangaEditor._panel_shape_valid
    _segments_intersect = staticmethod(MangaEditor._segments_intersect)
    _split_freeform_panel = MangaEditor._split_freeform_panel
    split_selected_panel_by_chord = MangaEditor.split_selected_panel_by_chord
    _selected_panel_edge_index = MangaEditor._selected_panel_edge_index

    def __init__(self):
        super().__init__()
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("選択","select")
        self.panel_split_gap_spin = QSpinBox(self)
        self.panel_split_gap_spin.setRange(0,100)
        self.panel_split_gap_spin.setValue(24)
        self.panel_split_image_mode = QComboBox(self)
        self.panel_split_image_mode.addItem("維持","keep_view")
        self.panel_split_image_mode.addItem("フィット","fit_each")
        self.panel_split_layout = QComboBox(self)
        self.panel_split_layout.addItem("左右","vertical")
        self.panel_split_layout.addItem("上下","horizontal")
        self.panel_split_count_spin = QSpinBox(self)
        self.panel_split_count_spin.setRange(2,12)
        self.panel_split_count_spin.setValue(2)
        self.scene = QGraphicsScene(self)
        self.view = CanvasView(self)
        self.setCentralWidget(self.view)
        self.view.setScene(self.scene)
        self.history_calls = 0

    def current_scene(self):
        return self.scene

    def selected(self):
        return self.scene.selectedItems()

    def record_history(self):
        self.history_calls += 1

    def add_panel(self, pts=None, image_path=""):
        item = PanelItem(QRectF(0,0,320,240),image_path=image_path,custom_points=pts)
        item.setPos(QPointF(25,35))
        self.scene.addItem(item)
        self.scene.clearSelection()
        item.setSelected(True)
        item.set_corner_edit_enabled(True)
        return item


class ChordEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = ChordEditorHarness()
        self.view = self.editor.view
        self.panel = self.editor.add_panel()
        self.panel._selected_corner_indices = {0,2}

    def tearDown(self):
        self.editor.close()

    def key(self,key,mods=Qt.NoModifier):
        self.view.keyPressEvent(QKeyEvent(QEvent.KeyPress,key,mods))

    def start(self):
        self.key(Qt.Key_R,Qt.ControlModifier)
        self.assertIsNotNone(self.view._panel_split_preview)
        self.assertEqual(self.view._panel_split_preview["kind"],"chord")

    def panels(self):
        return [p for p in self.editor.scene.items() if isinstance(p,PanelItem)]

    def test_selected_quad_diagonal_starts_chord_not_auto_split(self):
        before = self.panel.serialize()
        self.start()
        self.assertTrue(self.view._panel_split_preview_valid())
        self.assertEqual(self.panel.serialize(),before)
        self.assertEqual(self.editor.history_calls,0)

    def test_chord_wheel_and_mouse_do_not_change_selected_cut(self):
        self.start()
        self.view.change_panel_split_count(10)
        self.view.update_panel_split_orientation(QPointF(200,120))
        state = self.view._panel_split_preview
        self.assertEqual(state["vertices"],(0,2))
        self.assertNotIn("count",state)
        self.assertNotIn("direction",state)

    def test_click_commit_yields_two_children_and_one_history(self):
        self.start()
        self.assertTrue(self.view.finish_panel_split(True))
        self.assertIsNone(self.panel.scene())
        children = self.panels()
        self.assertEqual(len(children),2)
        self.assertEqual(self.editor.history_calls,1)
        self.assertTrue(all(valid_polygon(c.corner_points()) for c in children))
        self.assertTrue(all(c.isSelected() for c in children))
        for child in children:
            self.assertEqual(child.pos().x(),25)
            self.assertEqual(child.pos().y(),35)

    def test_chord_can_split_rotated_and_scaled_panel(self):
        self.panel.setRotation(27)
        self.panel.setScale(1.3)
        self.start()
        self.assertTrue(self.view.finish_panel_split(True))
        children = self.panels()
        self.assertEqual(len(children),2)
        for item in children:
            self.assertAlmostEqual(item.rotation(),27)
            self.assertAlmostEqual(item.scale(),1.3)
        self.assertEqual(self.editor.history_calls,1)

    def test_zero_gap_preserves_all_parent_area(self):
        self.editor.panel_split_gap_spin.setValue(0)
        parent_area = area(self.panel.corner_points())
        self.start()
        self.assertTrue(self.view.finish_panel_split(True))
        self.assertAlmostEqual(
            sum(area(c.corner_points()) for c in self.panels()),parent_area)

    def test_escape_and_x_during_preview_are_non_destructive(self):
        old = self.panel.serialize()
        self.start()
        self.key(Qt.Key_X)
        self.key(Qt.Key_G)
        self.assertEqual(self.panel.serialize(),old)
        self.assertEqual(len(self.panels()),1)
        self.key(Qt.Key_Escape)
        self.assertIsNone(self.view._panel_split_preview)
        self.assertEqual(self.panel.serialize(),old)
        self.assertEqual(self.editor.history_calls,0)

    def test_invalid_chord_keeps_preview_and_original_panel(self):
        pts = [(0,0),(300,0),(300,200),(200,200),
               (200,120),(100,120),(100,200),(0,200)]
        self.editor.scene.removeItem(self.panel)
        self.panel = self.editor.add_panel(pts)
        self.panel._selected_corner_indices = {3,7}
        old = self.panel.serialize()
        self.start()
        self.assertFalse(self.view._panel_split_preview_valid())
        self.assertFalse(self.view.finish_panel_split(True))
        self.assertEqual(old,self.panel.serialize())
        self.assertEqual(self.editor.history_calls,0)
        self.assertIsNotNone(self.view._panel_split_preview)
        self.key(Qt.Key_Escape)

    def test_large_gap_rejects_commit_with_no_history(self):
        self.editor.panel_split_gap_spin.setValue(100)
        self.panel.prepareGeometryChange()
        self.panel.custom_points = [
            QPointF(0,0),QPointF(75,0),QPointF(75,75),QPointF(0,75)
        ]
        self.start()
        self.assertFalse(self.view._panel_split_preview_valid())
        self.assertFalse(self.view.finish_panel_split(True))
        self.assertEqual(len(self.panels()),1)
        self.assertEqual(self.editor.history_calls,0)

    def test_odd_ngon_chord_and_reload_preserves_child_geometry(self):
        pts=[(0,0),(160,0),(320,0),(320,240),(0,240)]
        self.editor.scene.removeItem(self.panel)
        self.panel = self.editor.add_panel(pts)
        self.panel._selected_corner_indices = {0,3}
        self.start()
        self.assertTrue(self.view.finish_panel_split(True))
        self.assertEqual(len(self.panels()),2)
        for child in self.panels():
            serialized=child.serialize()
            reloaded=PanelItem(
                child.rect(),custom_points=serialized["custom_points"]
            )
            self.assertEqual(
                reloaded.serialize()["custom_points"],serialized["custom_points"]
            )

    def test_concave_polygon_cut_succeeds(self):
        pts=[(0,0),(300,0),(300,200),(200,200),
             (200,120),(100,120),(100,200),(0,200)]
        self.editor.scene.removeItem(self.panel)
        self.panel=self.editor.add_panel(pts)
        self.panel._selected_corner_indices={0,5}
        self.start()
        self.assertTrue(self.view._panel_split_preview_valid())
        self.assertTrue(self.view.finish_panel_split(True))
        self.assertEqual(len(self.panels()),2)
        self.assertTrue(all(valid_polygon(c.corner_points()) for c in self.panels()))

    def test_scene_switch_cancels_with_no_history(self):
        self.start()
        self.view.setScene(QGraphicsScene(self.editor))
        self.assertIsNone(self.view._panel_split_preview)
        self.assertEqual(self.editor.history_calls,0)
        self.assertIs(self.panel.scene(),self.editor.scene)


if __name__=="__main__":
    unittest.main()
