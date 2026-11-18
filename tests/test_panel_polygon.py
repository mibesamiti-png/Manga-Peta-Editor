"""P3 synthetic tests: arbitrary-vertex panel geometry, E insertion and reload."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt, QEvent
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QGraphicsScene, QMainWindow

from app import CanvasView, MangaEditor, PanelItem
from app import edge_midpoint, valid_polygon


class PanelPolygonGeometryTests(unittest.TestCase):
    def test_midpoint_keeps_order_and_valid_polygon(self):
        square = [(0, 0), (320, 0), (320, 240), (0, 240)]
        x, y = edge_midpoint(square, 0)
        pentagon = [square[0], (x, y), *square[1:]]
        self.assertEqual((x, y), (160.0, 0.0))
        self.assertTrue(valid_polygon(pentagon))

    def test_concave_polygon_is_valid(self):
        concave = [(0, 0), (280, 0), (280, 230), (190, 230),
                   (190, 80), (90, 80), (90, 230), (0, 230)]
        self.assertTrue(valid_polygon(concave))

    def test_crossing_and_nonadjacent_touch_rejected(self):
        self.assertFalse(valid_polygon(
            [(0, 0), (300, 0), (0, 200), (300, 200)]))
        self.assertFalse(valid_polygon(
            [(0, 0), (300, 0), (300, 200), (140, 0), (0, 200)]))

    def test_backtracking_duplicate_and_tiny_rejected(self):
        self.assertFalse(valid_polygon(
            [(0, 0), (200, 0), (120, 0), (300, 200), (0, 200)]))
        self.assertFalse(valid_polygon(
            [(0, 0), (200, 0), (200, 0), (200, 200), (0, 200)]))
        self.assertFalse(valid_polygon(
            [(0, 0), (10, 0), (10, 10), (0, 10)]))

    def test_nonfinite_rejected(self):
        self.assertFalse(valid_polygon(
            [(0, 0), (200, float('nan')), (250, 200), (0, 200)]))

    def test_triangle_and_reversed_winding(self):
        triangle = [(0, 0), (240, 0), (140, 220)]
        self.assertTrue(valid_polygon(triangle))
        self.assertTrue(valid_polygon(list(reversed(triangle))))


class EditorHarness(QMainWindow):
    insert_selected_panel_edge_vertex = MangaEditor.insert_selected_panel_edge_vertex
    _selected_panel_edge_index = MangaEditor._selected_panel_edge_index
    _valid_panel_quad = MangaEditor._valid_panel_quad
    _valid_panel_polygon = staticmethod(MangaEditor._valid_panel_polygon)
    _panel_shape_valid = MangaEditor._panel_shape_valid
    _segments_intersect = staticmethod(MangaEditor._segments_intersect)
    _scene_delta_to_local = MangaEditor._scene_delta_to_local
    deserialize_scene = MangaEditor.deserialize_scene
    split_selected_panel_n = MangaEditor.split_selected_panel_n

    def __init__(self):
        super().__init__()
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("コマ", "panel")
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

    def _resolve_project_path(self, path):
        return path

    def _resolve_loaded_overlay_paths(self, paths):
        return paths

    def add_panel(self):
        panel = PanelItem(QRectF(0, 0, 320, 240))
        self.scene.addItem(panel)
        self.scene.clearSelection()
        panel.setSelected(True)
        panel.enable_freeform()
        return panel


class PanelPolygonIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = EditorHarness()
        self.panel = self.editor.add_panel()

    def tearDown(self):
        self.editor.close()

    def key(self, key, modifiers=Qt.NoModifier):
        self.editor.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, modifiers))

    def select_edge(self, first, second):
        self.panel._selected_corner_indices = {first, second}

    def test_e_adds_one_selected_midpoint_without_changing_outline(self):
        previous = self.panel.corner_points()
        old_path = self.panel.panel_path()
        self.select_edge(0, 1)
        self.key(Qt.Key_E)
        current = self.panel.corner_points()
        self.assertEqual(len(current), 5)
        self.assertEqual(current[1], QPointF(160, 0))
        self.assertEqual(self.panel._selected_corner_indices, {1})
        self.assertTrue(self.editor._valid_panel_polygon(current))
        self.assertEqual(self.editor.history_calls, 1)
        for sample in [QPointF(20,20), QPointF(150,120), QPointF(420,120)]:
            self.assertEqual(old_path.contains(sample), self.panel.panel_path().contains(sample))

    def test_repeated_e_yields_six_points(self):
        self.select_edge(0, 1)
        self.assertTrue(self.editor.insert_selected_panel_edge_vertex())
        self.select_edge(2, 3)
        self.assertTrue(self.editor.insert_selected_panel_edge_vertex())
        self.assertEqual(len(self.panel.corner_points()), 6)
        self.assertEqual(self.editor.history_calls, 2)

    def test_e_does_not_change_shape_if_edge_is_not_selected(self):
        old = self.panel.serialize()
        self.panel._selected_corner_indices = {0}
        self.key(Qt.Key_E)
        self.assertEqual(old, self.panel.serialize())
        self.assertEqual(self.editor.history_calls, 0)

    def test_e_rejects_too_short_edge_safely(self):
        self.panel.prepareGeometryChange()
        self.panel.custom_points = [
            QPointF(0,0),QPointF(20,0),QPointF(320,0),
            QPointF(320,240),QPointF(0,240),
        ]
        self.select_edge(0,1)
        original = self.panel.serialize()
        self.assertFalse(self.editor.insert_selected_panel_edge_vertex())
        self.assertEqual(original, self.panel.serialize())
        self.assertEqual(self.editor.history_calls, 0)

    def test_save_reload_polygon_retains_vertices_and_effect_uid(self):
        self.select_edge(0, 1)
        self.assertTrue(self.editor.insert_selected_panel_edge_vertex())
        snapshot = self.panel.serialize()
        scene = QGraphicsScene(self.editor)
        made = self.editor.deserialize_scene(scene, [snapshot])
        self.assertEqual(len(made), 1)
        child = made[0]
        self.assertEqual(len(child.corner_points()), 5)
        self.assertEqual(child.serialize()["custom_points"], snapshot["custom_points"])
        self.assertEqual(child.serialize()["effect_uid"], snapshot["effect_uid"])

    def test_paste_polygon_has_independent_uid(self):
        self.select_edge(0, 1)
        self.assertTrue(self.editor.insert_selected_panel_edge_vertex())
        snapshot = self.panel.serialize()
        made = self.editor.deserialize_scene(self.editor.scene, [snapshot], QPointF(35,35))
        self.assertEqual(len(made[0].corner_points()), 5)
        self.assertNotEqual(made[0].effect_uid, self.panel.effect_uid)

    def test_g_moves_inserted_vertex_and_rejects_crossing(self):
        self.select_edge(0, 1)
        self.assertTrue(self.editor.insert_selected_panel_edge_vertex())
        self.key(Qt.Key_G)
        self.assertIsNotNone(self.editor.view._panel_grab)
        self.key(Qt.Key_Y)
        self.key(Qt.Key_5)
        self.key(Qt.Key_0)
        polygon = self.panel.corner_points()
        self.assertAlmostEqual(polygon[1].y(), 50)
        self.assertTrue(valid_polygon(polygon))
        self.key(Qt.Key_Return)
        self.assertEqual(self.editor.history_calls, 2)

    def test_g_excessive_move_is_bounded(self):
        self.select_edge(0, 1)
        self.assertTrue(self.editor.insert_selected_panel_edge_vertex())
        self.key(Qt.Key_G)
        self.key(Qt.Key_Y)
        self.key(Qt.Key_9)
        self.key(Qt.Key_9)
        self.key(Qt.Key_9)
        self.assertTrue(valid_polygon(self.panel.corner_points()))
        self.key(Qt.Key_Escape)
        self.assertEqual(self.panel.corner_points()[1], QPointF(160,0))
        self.assertEqual(self.editor.history_calls, 1)

    def test_concat_insert_cannot_be_used_in_text_mode(self):
        self.editor.mode_combo.setCurrentIndex(0)
        self.select_edge(0,1)
        self.editor.view.region_mosaic_mode = True
        old = self.panel.serialize()
        self.key(Qt.Key_E)
        self.assertEqual(self.panel.serialize(),old)

    def test_polygon_render_clips_concave_image(self):
        # Synthetic in-memory pixels only, no external/user image.
        poly = [(0,0),(280,0),(280,230),(190,230),
                (190,80),(90,80),(90,230),(0,230)]
        self.assertTrue(valid_polygon(poly))
        panel = PanelItem(QRectF(0,0,280,230),custom_points=poly)
        pix = QPixmap(280,230)
        pix.fill(QColor(220,30,30))
        panel.image_pixmap = pix
        target = QImage(320,270,QImage.Format_ARGB32_Premultiplied)
        target.fill(Qt.transparent)
        painter = QPainter(target)
        panel.paint(painter,None)
        painter.end()
        self.assertGreater(target.pixelColor(45,155).alpha(),0)
        self.assertEqual(target.pixelColor(140,160).alpha(),0)

    def test_split_core_explicitly_refuses_ngon(self):
        self.select_edge(0,1)
        self.assertTrue(self.editor.insert_selected_panel_edge_vertex())
        with patch("app.QMessageBox.information"):
            self.editor.split_selected_panel_n("vertical",count=2)
        self.assertIs(self.panel.scene(),self.editor.scene)
        self.assertEqual(self.editor.history_calls,1)


if __name__ == "__main__":
    unittest.main()
