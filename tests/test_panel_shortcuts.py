"""Focused canvas keyboard shortcut regression tests.

Only uses synthetic scene items. Offscreen Qt is enabled in CI.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsScene, QGraphicsTextItem, QMainWindow

from app import CanvasView


class FakeEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.added = 0
        self.duplicated = 0
        self.view = CanvasView(self)
        self.setCentralWidget(self.view)
        self.view.setScene(QGraphicsScene(self))

    def add_free_panel(self):
        self.added += 1

    def duplicate_selected_panels(self):
        self.duplicated += 1
        return True


class CanvasPanelShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = FakeEditor()

    def tearDown(self):
        self.editor.close()

    def press(self, key, modifiers):
        ev = QKeyEvent(QEvent.KeyPress, key, modifiers, "")
        QApplication.sendEvent(self.editor.view, ev)

    def test_shift_a_calls_add_panel(self):
        self.press(Qt.Key_A, Qt.ShiftModifier)
        self.assertEqual(self.editor.added, 1)
        self.assertEqual(self.editor.duplicated, 0)

    def test_shift_d_calls_duplicate_panel(self):
        self.press(Qt.Key_D, Qt.ShiftModifier)
        self.assertEqual(self.editor.duplicated, 1)
        self.assertEqual(self.editor.added, 0)

    def test_ctrl_shift_a_does_not_add(self):
        self.press(Qt.Key_A, Qt.ControlModifier | Qt.ShiftModifier)
        self.assertEqual(self.editor.added, 0)

    def test_text_editing_does_not_trigger_panel_shortcut(self):
        scene = self.editor.view.scene()
        text = QGraphicsTextItem("test")
        text.setTextInteractionFlags(Qt.TextEditorInteraction)
        text.setFlag(QGraphicsItem.ItemIsFocusable, True)
        scene.addItem(text)
        scene.setFocusItem(text)
        self.assertIs(scene.focusItem(), text)
        self.press(Qt.Key_A, Qt.ShiftModifier)
        self.assertEqual(self.editor.added, 0)
        self.assertEqual(self.editor.duplicated, 0)


if __name__ == "__main__":
    unittest.main()
