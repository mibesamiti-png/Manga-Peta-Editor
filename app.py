import sys
import json
import io
import zipfile
import tempfile
import uuid
import hashlib
import shutil
import math
import re
import ast
import os
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageFilter, ImageEnhance, ImageOps

from PySide6.QtCore import Qt, QRectF, QPointF, QBuffer, QByteArray, QIODevice, QSize, QTimer
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QImage, QPainter, QPainterPath,
    QPen, QPixmap, QPolygonF, QTransform, QFontMetricsF, QIcon, QKeySequence
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QAbstractItemView, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QToolBar, QVBoxLayout, QWidget, QFontComboBox, QStackedWidget, QColorDialog, QListWidgetItem,
    QDialog, QScrollArea, QGroupBox, QMenu, QDialogButtonBox, QStyleOptionGraphicsItem, QPlainTextEdit,
    QTabWidget, QTabBar, QToolButton, QSplitter
)

APP_VERSION = "1.8.4-debug"
DEFAULT_CANVAS_W = 1200
DEFAULT_CANVAS_H = 1697
# Backward-compatible defaults; runtime uses editor.canvas_w / editor.canvas_h.
CANVAS_W = DEFAULT_CANVAS_W
CANVAS_H = DEFAULT_CANVAS_H
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

APP_DIR = Path(__file__).resolve().parent
EFFECT_DIR = APP_DIR / "Effect"
PROJECTS_DIR = APP_DIR / "Projects"

def effect_path_to_ref(path):
    """Store Effect-folder assets as portable relative refs where possible."""
    p = Path(path).resolve()
    try:
        rel = p.relative_to(EFFECT_DIR.resolve())
        return "Effect/" + rel.as_posix()
    except Exception:
        return str(p)

def resolve_effect_path(ref):
    if not ref:
        return Path()
    text = str(ref).replace("\\", "/")
    if text.lower().startswith("effect/"):
        return APP_DIR / Path(text)
    p = Path(ref)
    if p.is_absolute():
        return p
    return APP_DIR / p

def composite_effect_overlay(base, overlay_path, settings):
    """Composite an Effect-folder image onto a PIL base image."""
    path = resolve_effect_path(overlay_path)
    if not path.exists():
        return base
    try:
        ov = Image.open(path).convert("RGBA")
    except Exception:
        return base

    base = base.convert("RGBA")
    bw, bh = base.size
    mode = str(settings.get("mode", "fit"))
    opacity = max(0.0, min(1.0, float(settings.get("opacity", 1.0))))

    rotation = float(settings.get("rotation", 0.0))

    if mode == "fit":
        ov = ov.resize((max(1, bw), max(1, bh)), Image.Resampling.LANCZOS)
        if opacity < 0.999:
            alpha = ov.getchannel("A").point(lambda v: int(v * opacity))
            ov.putalpha(alpha)
        if abs(rotation) < 0.001:
            return Image.alpha_composite(base, ov)
        # PIL positive rotation is visually opposite to Qt's y-down scene,
        # so use a negative angle to keep the UI consistent.
        ov = ov.rotate(
            -rotation,
            expand=True,
            resample=Image.Resampling.BICUBIC,
        )
        layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        x = int(round((bw - ov.width) / 2.0))
        y = int(round((bh - ov.height) / 2.0))
        layer.paste(ov, (x, y), ov)
        return Image.alpha_composite(base, layer)

    # Free mode: preserve the effect image aspect ratio.
    ow, oh = max(1, ov.width), max(1, ov.height)
    contain = min(bw / ow, bh / oh)
    scale = max(0.01, float(settings.get("scale", 1.0)))
    dw = max(1, int(round(ow * contain * scale)))
    dh = max(1, int(round(oh * contain * scale)))
    ov = ov.resize((dw, dh), Image.Resampling.LANCZOS)

    if opacity < 0.999:
        alpha = ov.getchannel("A").point(lambda v: int(v * opacity))
        ov.putalpha(alpha)

    if abs(rotation) >= 0.001:
        ov = ov.rotate(
            -rotation,
            expand=True,
            resample=Image.Resampling.BICUBIC,
        )

    offset_x = float(settings.get("offset_x", 0.0))
    offset_y = float(settings.get("offset_y", 0.0))
    x = int(round((bw - dw) / 2.0 + offset_x))
    y = int(round((bh - dh) / 2.0 + offset_y))

    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    layer.paste(ov, (x, y), ov)
    return Image.alpha_composite(base, layer)



def parse_project_version(value):
    """Return a numeric major.minor version from values such as '1.3-debug'."""
    if value is None:
        return 0.0
    text = str(value).strip()
    m = re.match(r"^\s*(\d+)(?:\.(\d+))?", text)
    if not m:
        return 0.0
    major = int(m.group(1))
    minor = int(m.group(2) or 0)
    # Historical migration checks only need coarse ordering.
    return major + minor / 10.0



def pixmap_to_pil(pixmap):
    image = pixmap.toImage()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return Image.open(io.BytesIO(bytes(ba))).convert("RGBA")


def pil_to_pixmap(pil):
    pil = pil.convert("RGBA")
    raw = pil.tobytes("raw", "RGBA")
    qimg = QImage(raw, pil.width, pil.height, QImage.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimg)


def mosaic_region(pil, box, cell=18):
    x0, y0, x1, y1 = [int(v) for v in box]
    x0 = max(0, min(pil.width, x0))
    x1 = max(0, min(pil.width, x1))
    y0 = max(0, min(pil.height, y0))
    y1 = max(0, min(pil.height, y1))
    if x1 <= x0 or y1 <= y0:
        return pil
    crop = pil.crop((x0, y0, x1, y1))
    sw = max(1, crop.width // cell)
    sh = max(1, crop.height // cell)
    small = crop.resize((sw, sh), Image.Resampling.BILINEAR)
    blocky = small.resize(crop.size, Image.Resampling.NEAREST)
    pil.paste(blocky, (x0, y0))
    return pil


class PageListWidget(QListWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def dropEvent(self, event):
        # InternalMove can change currentRow before self.pages is reordered.
        # Block row-change signals during the Qt-side move, then synchronize
        # the model once, atomically.
        previous_block = self.blockSignals(True)
        try:
            super().dropEvent(event)
            current_item = self.currentItem()
            preferred_id = current_item.data(Qt.UserRole) if current_item else None
        finally:
            self.blockSignals(previous_block)
        self.editor.sync_page_order_from_list(preferred_id)


class HistoryScene(QGraphicsScene):
    def __init__(self, editor, *args):
        super().__init__(*args)
        self.editor = editor
        self._press_positions = {}

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._press_positions = {
            item: QPointF(item.pos())
            for item in self.selectedItems()
            if item.parentItem() is None
        }

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        selected = [i for i in self.selectedItems() if i.parentItem() is None]
        moved = False
        for item in selected:
            before = self._press_positions.get(item)
            if before is None:
                continue
            after = item.pos()
            if abs(after.x() - before.x()) > 0.01 or abs(after.y() - before.y()) > 0.01:
                moved = True
                break

        # Important: do not snap on a plain click.
        # This prevents the old "every click slowly shifts things" behaviour.
        if moved:
            self.editor.snap_selected_items()
            self.editor.record_history()
        else:
            self.editor.refresh_layer_list()
        self._press_positions = {}


class ImageItem(QGraphicsPixmapItem):
    HANDLE = 18

    def __init__(self, pixmap, source_path="", original_pixmap=None, effects=None):
        super().__init__(pixmap)
        self.source_path = source_path
        self.original_pixmap = original_pixmap or pixmap
        self.effects = json.loads(json.dumps(effects or []))
        self.effect_uid = uuid.uuid4().hex
        self._resizing = False
        self._resize_start_dist = 1.0
        self._resize_start_scale = 1.0
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsFocusable
        )
        self.setTransformationMode(Qt.SmoothTransformation)
        self.setTransformOriginPoint(0, 0)
        self.setAcceptHoverEvents(True)
        if self.effects:
            self.rebuild_effects()

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.save()
            r = self.boundingRect()
            pen = QPen(QColor("#00c8ff"), 4)
            pen.setCosmetic(True)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(pen)
            painter.drawRect(r)

            # Resize handle remains available, but selection itself is shown by
            # the cyan frame rather than a black square.
            radius = self.HANDLE * 0.48
            painter.setBrush(QColor("#00c8ff"))
            painter.setPen(QPen(QColor("white"), 2))
            painter.drawEllipse(r.bottomRight(), radius, radius)
            painter.restore()

    def _on_resize_handle(self, pos):
        r = self.boundingRect()
        return pos.x() >= r.right()-self.HANDLE*1.7 and pos.y() >= r.bottom()-self.HANDLE*1.7

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isSelected() and self._on_resize_handle(event.pos()):
            self._resizing = True
            origin = self.scenePos()
            dx = event.scenePos().x() - origin.x()
            dy = event.scenePos().y() - origin.y()
            self._resize_start_dist = max(1.0, (dx*dx + dy*dy) ** 0.5)
            self._resize_start_scale = max(0.01, abs(self.scale()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            origin = self.scenePos()
            dx = event.scenePos().x() - origin.x()
            dy = event.scenePos().y() - origin.y()
            dist = max(1.0, (dx*dx + dy*dy) ** 0.5)
            self.setScale(max(0.03, min(20.0, self._resize_start_scale * dist / self._resize_start_dist)))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = False
            if self.scene() and hasattr(self.scene(), "editor"):
                self.scene().editor.record_history()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.scene() and hasattr(self.scene(), "editor"):
            editor = self.scene().editor
            self.setSelected(True)
            editor.open_image_properties(self)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        if not self.scene() or not hasattr(self.scene(), "editor"):
            return super().contextMenuEvent(event)

        editor = self.scene().editor
        if not self.isSelected():
            self.scene().clearSelection()
            self.setSelected(True)

        menu = QMenu()
        prop_action = menu.addAction("画像プロパティ…")
        replace_action = menu.addAction("画像を差し替え…")
        menu.addSeparator()
        mirror_action = menu.addAction("左右反転")
        duplicate_action = menu.addAction("複製")
        menu.addSeparator()
        front_action = menu.addAction("最前面へ")
        back_action = menu.addAction("最背面へ")
        delete_action = menu.addAction("削除")

        chosen = menu.exec(event.screenPos())
        if chosen == prop_action:
            editor.open_image_properties(self)
        elif chosen == replace_action:
            editor.replace_selected_image()
        elif chosen == mirror_action:
            editor.mirror_selected()
        elif chosen == duplicate_action:
            editor.duplicate_selection()
        elif chosen == front_action:
            editor.item_to_front(self)
        elif chosen == back_action:
            editor.item_to_back(self)
        elif chosen == delete_action:
            editor.delete_selected()
        event.accept()

    def rebuild_effects(self):
        if self.original_pixmap.isNull():
            return
        pil = pixmap_to_pil(self.original_pixmap)
        for effect in self.effects:
            kind = effect.get("type")
            if kind == "mosaic":
                rect = effect.get("rect")
                cell = int(effect.get("cell", 18))
                if rect is None:
                    box = (0, 0, pil.width, pil.height)
                else:
                    nx, ny, nw, nh = rect
                    box = (
                        nx * pil.width,
                        ny * pil.height,
                        (nx + nw) * pil.width,
                        (ny + nh) * pil.height,
                    )
                pil = mosaic_region(pil, box, cell)
            elif kind == "grayscale":
                alpha = pil.getchannel("A")
                pil = ImageOps.grayscale(pil).convert("RGBA")
                pil.putalpha(alpha)
            elif kind == "sepia":
                gray = ImageOps.grayscale(pil)
                sep = ImageOps.colorize(gray, "#2b1b10", "#f1d19a")
                alpha = pil.getchannel("A")
                pil = sep.convert("RGBA")
                pil.putalpha(alpha)
            elif kind == "blur":
                pil = pil.filter(ImageFilter.GaussianBlur(radius=float(effect.get("radius", 3.0))))
            elif kind == "sharpen":
                pil = pil.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=3))
            elif kind == "brightness":
                pil = ImageEnhance.Brightness(pil).enhance(float(effect.get("factor", 1.0)))
            elif kind == "contrast":
                pil = ImageEnhance.Contrast(pil).enhance(float(effect.get("factor", 1.0)))
            elif kind == "overlay":
                pil = composite_effect_overlay(
                    pil,
                    effect.get("path", ""),
                    effect,
                )
        self.setPixmap(pil_to_pixmap(pil))

    def add_overlay_effect(self, path, opacity=1.0):
        p = Path(path)
        if not p.exists():
            return False
        self.effects.append({
            "type": "overlay",
            "path": effect_path_to_ref(p),
            "opacity": float(opacity),
            "mode": "fit",
            "scale": 1.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "rotation": 0.0,
        })
        self.rebuild_effects()
        return True

    def add_simple_effect(self, kind, **params):
        self.effects.append({"type": kind, **params})
        self.rebuild_effects()

    def add_mosaic_effect(self, local_rect=None, cell=18):
        if local_rect is None:
            norm = None
        else:
            w = max(1.0, self.original_pixmap.width())
            h = max(1.0, self.original_pixmap.height())
            r = local_rect.normalized().intersected(QRectF(0, 0, w, h))
            if r.width() < 2 or r.height() < 2:
                return False
            norm = [r.x()/w, r.y()/h, r.width()/w, r.height()/h]
        self.effects.append({"type": "mosaic", "rect": norm, "cell": int(cell)})
        self.rebuild_effects()
        return True

    def restore_original(self):
        self.effects.clear()
        self.setPixmap(self.original_pixmap)

    def replace_image(self, path):
        pix = QPixmap(path)
        if pix.isNull():
            return False
        self.source_path = path
        self.original_pixmap = pix
        self.rebuild_effects()
        if not self.effects:
            self.setPixmap(pix)
        return True

    def serialize(self):
        return {
            "type": "image",
            "source_path": self.source_path,
            "x": self.pos().x(),
            "y": self.pos().y(),
            "rotation": self.rotation(),
            "scale": self.scale(),
            "mirror_x": self.transform().m11() < 0,
            "z": self.zValue(),
            "opacity": self.opacity(),
            "effects": self.effects,
            "effect_uid": self.effect_uid,
        }


class ImagePropertiesDialog(QDialog):
    """Numeric properties for a normal page ImageItem."""
    def __init__(self, editor, item):
        super().__init__(editor)
        self.editor = editor
        self.item = item
        self._syncing_size = False

        display_name = Path(item.source_path).name if item.source_path else "画像"
        self.setWindowTitle(f"画像プロパティ - {display_name}")
        self.setMinimumWidth(430)

        root = QVBoxLayout(self)

        info_box = QGroupBox("画像情報")
        info_form = QFormLayout(info_box)

        self.path_edit = QLineEdit(str(item.source_path or ""))
        self.path_edit.setReadOnly(True)

        pix = item.original_pixmap if not item.original_pixmap.isNull() else item.pixmap()
        self.base_w = max(1, int(pix.width()))
        self.base_h = max(1, int(pix.height()))
        self.aspect = self.base_w / max(1.0, float(self.base_h))

        info_form.addRow("ファイル", self.path_edit)
        info_form.addRow("元画像", QLabel(f"{self.base_w} × {self.base_h} px"))
        root.addWidget(info_box)

        geo_box = QGroupBox("配置 / サイズ")
        form = QFormLayout(geo_box)

        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(-100000.0, 100000.0)
        self.x_spin.setDecimals(1)
        self.x_spin.setValue(item.pos().x())
        self.x_spin.setSuffix(" px")

        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(-100000.0, 100000.0)
        self.y_spin.setDecimals(1)
        self.y_spin.setValue(item.pos().y())
        self.y_spin.setSuffix(" px")

        current_scale = max(0.001, abs(float(item.scale())))
        current_w = self.base_w * current_scale
        current_h = self.base_h * current_scale

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(1.0, 100000.0)
        self.width_spin.setDecimals(1)
        self.width_spin.setValue(current_w)
        self.width_spin.setSuffix(" px")

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(1.0, 100000.0)
        self.height_spin.setDecimals(1)
        self.height_spin.setValue(current_h)
        self.height_spin.setSuffix(" px")

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.001, 100.0)
        self.scale_spin.setDecimals(4)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setValue(current_scale)
        self.scale_spin.setSuffix(" ×")

        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(-3600.0, 3600.0)
        self.rotation_spin.setDecimals(1)
        self.rotation_spin.setValue(item.rotation())
        self.rotation_spin.setSuffix("°")

        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setValue(int(round(item.opacity() * 100)))
        self.opacity_spin.setSuffix(" %")

        self.z_spin = QDoubleSpinBox()
        self.z_spin.setRange(-100000.0, 100000.0)
        self.z_spin.setDecimals(1)
        self.z_spin.setValue(item.zValue())

        self.keep_center_check = QCheckBox("サイズ変更時に画像の中心位置を固定")
        self.keep_center_check.setChecked(True)

        form.addRow("X位置", self.x_spin)
        form.addRow("Y位置", self.y_spin)
        form.addRow("幅", self.width_spin)
        form.addRow("高さ", self.height_spin)
        form.addRow("倍率", self.scale_spin)
        form.addRow("回転", self.rotation_spin)
        form.addRow("不透明度", self.opacity_spin)
        form.addRow("重なり順 z", self.z_spin)
        form.addRow("", self.keep_center_check)
        root.addWidget(geo_box)

        self.visual_info = QLabel()
        self.visual_info.setWordWrap(True)
        self.visual_info.setStyleSheet(
            "background:#f5f5f5; border:1px solid #d0d0d0; padding:6px;"
        )
        root.addWidget(self.visual_info)

        hint = QLabel(
            "幅・高さ・倍率は連動します。縦横比は元画像の比率を維持します。\\n"
            "キャンバス上では、選択画像の右下□をドラッグしてもサイズ変更できます。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#555;")
        root.addWidget(hint)

        quick = QHBoxLayout()
        original_btn = QPushButton("原寸 100%")
        fit_btn = QPushButton("ページ内に収める")
        center_btn = QPushButton("ページ中央へ")
        original_btn.clicked.connect(self.set_original_size)
        fit_btn.clicked.connect(self.fit_to_page)
        center_btn.clicked.connect(self.move_to_page_center)
        quick.addWidget(original_btn)
        quick.addWidget(fit_btn)
        quick.addWidget(center_btn)
        root.addLayout(quick)

        self.width_spin.valueChanged.connect(self._width_changed)
        self.height_spin.valueChanged.connect(self._height_changed)
        self.scale_spin.valueChanged.connect(self._scale_changed)
        self.x_spin.valueChanged.connect(self._update_visual_info)
        self.y_spin.valueChanged.connect(self._update_visual_info)
        self.rotation_spin.valueChanged.connect(self._update_visual_info)
        self.opacity_spin.valueChanged.connect(self._update_visual_info)
        self.z_spin.valueChanged.connect(self._update_visual_info)

        self._update_visual_info()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _set_size_controls(self, width, height, scale):
        self._syncing_size = True
        try:
            for w in (self.width_spin, self.height_spin, self.scale_spin):
                w.blockSignals(True)
            self.width_spin.setValue(max(1.0, width))
            self.height_spin.setValue(max(1.0, height))
            self.scale_spin.setValue(max(0.001, scale))
        finally:
            for w in (self.width_spin, self.height_spin, self.scale_spin):
                w.blockSignals(False)
            self._syncing_size = False
        self._update_visual_info()

    def _width_changed(self, value):
        if self._syncing_size:
            return
        scale = max(0.001, float(value) / self.base_w)
        self._set_size_controls(value, self.base_h * scale, scale)

    def _height_changed(self, value):
        if self._syncing_size:
            return
        scale = max(0.001, float(value) / self.base_h)
        self._set_size_controls(self.base_w * scale, value, scale)

    def _scale_changed(self, value):
        if self._syncing_size:
            return
        scale = max(0.001, float(value))
        self._set_size_controls(self.base_w * scale, self.base_h * scale, scale)

    def set_original_size(self):
        self._set_size_controls(self.base_w, self.base_h, 1.0)

    def fit_to_page(self):
        max_w = max(1.0, self.editor.canvas_w * 0.90)
        max_h = max(1.0, self.editor.canvas_h * 0.90)
        scale = min(max_w / self.base_w, max_h / self.base_h)
        self._set_size_controls(self.base_w * scale, self.base_h * scale, scale)

    def _transformed_bbox_size(self):
        """Approximate scene bounding-box size from scale + rotation."""
        w = self.base_w * self.scale_spin.value()
        h = self.base_h * self.scale_spin.value()
        angle = math.radians(self.rotation_spin.value() % 360.0)
        c = abs(math.cos(angle))
        sn = abs(math.sin(angle))
        return (
            w * c + h * sn,
            w * sn + h * c,
        )

    def _update_visual_info(self, *args):
        if not hasattr(self, "visual_info"):
            return
        bbox_w, bbox_h = self._transformed_bbox_size()
        center_x = self.x_spin.value() + (self.base_w * self.scale_spin.value()) / 2.0
        center_y = self.y_spin.value() + (self.base_h * self.scale_spin.value()) / 2.0
        self.visual_info.setText(
            f"見た目の外接サイズ: 約 {bbox_w:.1f} × {bbox_h:.1f} px\n"
            f"画像中心: 約 ({center_x:.1f}, {center_y:.1f})"
        )

    def move_to_page_center(self):
        # Center the image's own center on the page. Rotation happens around the
        # ImageItem center, so this also centers the rotated appearance.
        scale = self.scale_spin.value()
        center_x = self.editor.canvas_w / 2.0
        center_y = self.editor.canvas_h / 2.0
        self.x_spin.setValue(center_x - (self.base_w * scale) / 2.0)
        self.y_spin.setValue(center_y - (self.base_h * scale) / 2.0)
        self._update_visual_info()

    def accept(self):
        item = self.item
        old_center = item.sceneBoundingRect().center()
        original_x = float(item.data(901) if item.data(901) is not None else item.pos().x())
        original_y = float(item.data(902) if item.data(902) is not None else item.pos().y())
        position_changed = (
            abs(self.x_spin.value() - original_x) > 0.01 or
            abs(self.y_spin.value() - original_y) > 0.01
        )

        item.setPos(self.x_spin.value(), self.y_spin.value())
        item.setScale(self.scale_spin.value())
        item.setRotation(self.rotation_spin.value())
        item.setOpacity(self.opacity_spin.value() / 100.0)
        item.setZValue(self.z_spin.value())

        if self.keep_center_check.isChecked() and not position_changed:
            new_center = item.sceneBoundingRect().center()
            delta = old_center - new_center
            item.moveBy(delta.x(), delta.y())

        item.update()
        self.editor.record_history()
        self.editor.refresh_layer_list()
        self.editor.selection_changed()
        super().accept()



class EditableTextItem(QGraphicsTextItem):
    def __init__(self, text="テキスト"):
        super().__init__(text)
        self.outline_color = QColor("white")
        self.outline_width = 0.0
        self.shadow_enabled = False
        self.shadow_color = QColor(0, 0, 0, 150)
        self.shadow_offset = QPointF(5.0, 5.0)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsFocusable
        )
        self.setFont(QFont("Yu Gothic UI", 32))
        self.setDefaultTextColor(QColor("black"))

    def mouseDoubleClickEvent(self, event):
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus(Qt.MouseFocusReason)
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        super().focusOutEvent(event)
        if self.scene() and hasattr(self.scene(), "editor"):
            self.scene().editor.record_history()

    def contextMenuEvent(self, event):
        if self.scene() and hasattr(self.scene(), "editor"):
            editor = self.scene().editor
            self.scene().clearSelection()
            self.setSelected(True)
            menu = QMenu()
            edit_action = menu.addAction("文字を編集…")
            front_action = menu.addAction("最前面へ")
            back_action = menu.addAction("最背面へ")
            action = menu.exec(event.screenPos())
            if action == edit_action:
                editor.edit_text_object(self)
            elif action == front_action:
                editor.item_to_front(self)
            elif action == back_action:
                editor.item_to_back(self)
            event.accept()
            return
        super().contextMenuEvent(event)

    def _text_path(self):
        path = QPainterPath()
        fm = QFontMetricsF(self.font())
        margin = self.document().documentMargin()
        y = margin + fm.ascent()
        for line in (self.toPlainText().split("\n") or [""]):
            path.addText(QPointF(margin, y), self.font(), line)
            y += fm.lineSpacing()
        return path

    def boundingRect(self):
        r = super().boundingRect()
        pad = max(2.0, self.outline_width * 2.0)
        if self.shadow_enabled:
            pad += max(abs(self.shadow_offset.x()), abs(self.shadow_offset.y())) + 2.0
        return r.adjusted(-pad, -pad, pad, pad)

    def paint(self, painter, option, widget=None):
        # While editing, use Qt's native text editor so caret/selection work normally.
        if self.textInteractionFlags() & Qt.TextEditorInteraction:
            super().paint(painter, option, widget)
            return
        path = self._text_path()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if self.shadow_enabled:
            shadow_path = QPainterPath(path)
            shadow_path.translate(self.shadow_offset)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.shadow_color)
            painter.drawPath(shadow_path)
        if self.outline_width > 0:
            painter.setPen(QPen(self.outline_color, self.outline_width * 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(self.defaultTextColor())
            painter.drawPath(path)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.defaultTextColor())
            painter.drawPath(path)
        if self.isSelected():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("black"), 1, Qt.DashLine))
            painter.drawRect(super().boundingRect())
        painter.restore()

    def serialize(self):
        return {
            "type": "text",
            "text": self.toPlainText(),
            "x": self.pos().x(),
            "y": self.pos().y(),
            "rotation": self.rotation(),
            "scale": self.scale(),
            "font_family": self.font().family(),
            "font_size": self.font().pointSize(),
            "bold": self.font().bold(),
            "color": self.defaultTextColor().name(QColor.HexArgb),
            "outline_color": self.outline_color.name(QColor.HexArgb),
            "outline_width": self.outline_width,
            "shadow_enabled": self.shadow_enabled,
            "shadow_color": self.shadow_color.name(QColor.HexArgb),
            "shadow_offset": [self.shadow_offset.x(), self.shadow_offset.y()],
            "z": self.zValue(),
            "opacity": self.opacity(),
        }


class VerticalTextItem(QGraphicsItem):
    """Simple Japanese-style vertical text item.

    Newlines create a new column to the left. This intentionally keeps the
    implementation lightweight; punctuation rotation/tate-chu-yoko can be
    extended later.
    """
    def __init__(self, text="縦書き"):
        super().__init__()
        self.text_value = text
        self.font_value = QFont("Yu Gothic UI", 32)
        self.color = QColor("black")
        self.outline_color = QColor("white")
        self.outline_width = 2.0
        self.char_gap = -2.0
        self.column_gap = 8.0
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsFocusable
        )

    def font(self):
        return self.font_value

    def setFont(self, font):
        self.prepareGeometryChange()
        self.font_value = QFont(font)
        self.update()

    def set_text(self, text):
        self.prepareGeometryChange()
        self.text_value = text
        self.update()

    def _layout_metrics(self):
        fm = QFontMetricsF(self.font_value)
        char_h = max(1.0, fm.height() + self.char_gap)
        col_w = max(1.0, fm.horizontalAdvance("国") + self.column_gap)
        columns = self.text_value.split("\n") or [""]
        max_chars = max(1, max((len(c) for c in columns), default=1))
        return columns, char_h, col_w, max_chars

    def boundingRect(self):
        columns, char_h, col_w, max_chars = self._layout_metrics()
        pad = max(4.0, self.outline_width + 2.0)
        return QRectF(-pad, -pad, len(columns) * col_w + pad*2, max_chars * char_h + pad*2)

    def paint(self, painter, option, widget=None):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font_value)
        columns, char_h, col_w, _ = self._layout_metrics()
        fm = QFontMetricsF(self.font_value)
        ascent = fm.ascent()
        total_w = len(columns) * col_w
        for ci, column in enumerate(columns):
            column_center = total_w - (ci + 0.5) * col_w
            for ri, ch in enumerate(column):
                y = ri * char_h + ascent
                # Center every glyph inside the vertical column. The old code
                # used a left-side baseline and narrow glyphs visibly leaned left.
                glyph_w = fm.horizontalAdvance(ch)
                x = column_center - glyph_w / 2.0
                path = QPainterPath()
                path.addText(QPointF(x, y), self.font_value, ch)
                if self.outline_width > 0:
                    painter.setPen(QPen(self.outline_color, self.outline_width * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    painter.setBrush(self.outline_color)
                    painter.drawPath(path)
                painter.setPen(Qt.NoPen)
                painter.setBrush(self.color)
                painter.drawPath(path)
        if self.isSelected():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("black"), 1, Qt.DashLine))
            painter.drawRect(self.boundingRect())
        painter.restore()

    def mouseDoubleClickEvent(self, event):
        text, ok = QInputDialog.getMultiLineText(None, "縦書き編集", "文字:", self.text_value)
        if ok:
            self.set_text(text)
            if self.scene() and hasattr(self.scene(), "editor"):
                self.scene().editor.record_history()
        event.accept()

    def contextMenuEvent(self, event):
        if self.scene() and hasattr(self.scene(), "editor"):
            editor = self.scene().editor
            self.scene().clearSelection()
            self.setSelected(True)
            menu = QMenu()
            edit_action = menu.addAction("縦書き文字を編集…")
            front_action = menu.addAction("最前面へ")
            back_action = menu.addAction("最背面へ")
            action = menu.exec(event.screenPos())
            if action == edit_action:
                editor.edit_text_object(self)
            elif action == front_action:
                editor.item_to_front(self)
            elif action == back_action:
                editor.item_to_back(self)
            event.accept()
            return
        super().contextMenuEvent(event)

    def serialize(self):
        return {
            "type": "vertical_text",
            "text": self.text_value,
            "x": self.pos().x(), "y": self.pos().y(),
            "rotation": self.rotation(), "scale": self.scale(),
            "font_family": self.font_value.family(),
            "font_size": self.font_value.pointSize(),
            "bold": self.font_value.bold(),
            "color": self.color.name(),
            "outline_color": self.outline_color.name(),
            "outline_width": self.outline_width,
            "char_gap": self.char_gap,
            "column_gap": self.column_gap,
            "z": self.zValue(),
            "opacity": self.opacity(),
        }


class BubbleItem(QGraphicsPathItem):
    HANDLE = 18
    TAIL_HANDLE = 14

    def __init__(self, text="セリフ", width=360, height=190, style="oval", tail_tip=None):
        super().__init__()
        self.bubble_width = width
        self.bubble_height = height
        self.style = style
        self.tail_tip = QPointF(*(tail_tip or [width*0.16, height+70]))
        self._resizing = False
        self._dragging_tail = False
        self._resize_start_dist = 1.0
        self._resize_start_scale = 1.0
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsFocusable
        )
        self.setBrush(QBrush(QColor("white")))
        self.setPen(QPen(QColor("black"), 5))
        self._make_path()

        self.text_item = QGraphicsTextItem(text, self)
        self.text_item.setTextWidth(width - 70)
        self.text_item.setFont(QFont("Yu Gothic UI", 28))
        self.text_item.setDefaultTextColor(QColor("black"))
        self.text_item.setPos(35, 32)
        self.text_item.setTextInteractionFlags(Qt.NoTextInteraction)

    def _make_path(self):
        w, h = self.bubble_width, self.bubble_height
        p = QPainterPath()
        if self.style == "rect":
            p.addRoundedRect(QRectF(0, 0, w, h), 24, 24)
            p.moveTo(w*0.30, h*0.94)
            p.lineTo(self.tail_tip)            p.lineTo(w*0.48, h*0.94)
            p.closeSubpath()
        elif self.style == "thought":
            p.addEllipse(QRectF(0, 0, w, h))
            p.addEllipse(QRectF(w*0.18, h+12, 42, 42))
            p.addEllipse(QRectF(w*0.10, h+58, 25, 25))
        elif self.style == "shout":
            pts = []
            cx, cy = w/2, h/2
            n = 24
            import math
            for i in range(n):
                a = (i / n) * math.tau
                radius = 1.0 if i % 2 == 0 else 0.78
                pts.append(QPointF(cx + (w/2)*radius*math.cos(a), cy + (h/2)*radius*math.sin(a)))
            p.addPolygon(QPolygonF(pts)); p.closeSubpath()
        else:
            p.addEllipse(QRectF(0, 0, w, h))
            p.moveTo(w*0.32, h*0.82)
            p.lineTo(self.tail_tip)
            p.lineTo(w*0.46, h*0.88)
            p.closeSubpath()
        self.setPath(p)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            r = self.boundingRect(); s = self.HANDLE
            painter.save()
            painter.setBrush(QColor("white")); painter.setPen(QPen(QColor("black"), 2))
            painter.drawRect(QRectF(r.right()-s, r.bottom()-s, s, s))
            if self.style in {"oval", "rect"}:
                painter.setBrush(QColor("white"))
                painter.drawEllipse(self.tail_tip, self.TAIL_HANDLE/2, self.TAIL_HANDLE/2)
            painter.restore()

    def _on_resize_handle(self, pos):
        r = self.boundingRect()
        return pos.x() >= r.right()-self.HANDLE*1.7 and pos.y() >= r.bottom()-self.HANDLE*1.7

    def _on_tail_handle(self, pos):
        if self.style not in {"oval", "rect"}:
            return False
        dx, dy = pos.x()-self.tail_tip.x(), pos.y()-self.tail_tip.y()
        return dx*dx + dy*dy <= (self.TAIL_HANDLE*1.6)**2

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isSelected() and self._on_tail_handle(event.pos()):
            self._dragging_tail = True
            event.accept(); return
        if event.button() == Qt.LeftButton and self.isSelected() and self._on_resize_handle(event.pos()):
            self._resizing = True
            origin = self.scenePos(); dx = event.scenePos().x()-origin.x(); dy = event.scenePos().y()-origin.y()
            self._resize_start_dist = max(1.0, (dx*dx+dy*dy)**0.5)
            self._resize_start_scale = max(0.01, abs(self.scale()))
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging_tail:
            self.tail_tip = QPointF(event.pos())
            self._make_path(); self.update(); event.accept(); return
        if self._resizing:
            origin = self.scenePos(); dx = event.scenePos().x()-origin.x(); dy = event.scenePos().y()-origin.y()
            dist = max(1.0, (dx*dx+dy*dy)**0.5)
            self.setScale(max(0.03, min(20.0, self._resize_start_scale*dist/self._resize_start_dist)))
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        edited = self._resizing or self._dragging_tail
        self._resizing = False; self._dragging_tail = False
        if edited and self.scene() and hasattr(self.scene(), "editor"):
            self.scene().editor.record_history()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        text, ok = QInputDialog.getMultiLineText(None, "吹き出し編集", "セリフ:", self.text_item.toPlainText())
        if ok:
            self.text_item.setPlainText(text)
            if self.scene() and hasattr(self.scene(), "editor"):
                self.scene().editor.record_history()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        if self.scene() and hasattr(self.scene(), "editor"):
            editor = self.scene().editor
            self.scene().clearSelection()
            self.setSelected(True)
            menu = QMenu()
            edit_action = menu.addAction("セリフを編集…")
            front_action = menu.addAction("最前面へ")
            back_action = menu.addAction("最背面へ")
            action = menu.exec(event.screenPos())
            if action == edit_action:
                editor.edit_text_object(self)
            elif action == front_action:
                editor.item_to_front(self)
            elif action == back_action:
                editor.item_to_back(self)
            event.accept()
            return
        super().contextMenuEvent(event)

    def serialize(self):
        return {
            "type": "bubble", "text": self.text_item.toPlainText(), "style": self.style,
            "x": self.pos().x(), "y": self.pos().y(), "rotation": self.rotation(), "scale": self.scale(),
            "width": self.bubble_width, "height": self.bubble_height,
            "tail_tip": [self.tail_tip.x(), self.tail_tip.y()],
            "font_family": self.text_item.font().family(), "font_size": self.text_item.font().pointSize(),
            "bold": self.text_item.font().bold(), "color": self.text_item.defaultTextColor().name(QColor.HexArgb),
            "z": self.zValue(), "opacity": self.opacity(),
        }


class PanelItem(QGraphicsRectItem):
    """Comic panel with clipped image, normal resize and four-corner freeform editing."""
    HANDLE = 20
    CORNER_HANDLE = 17
    MIN_PANEL_SIZE = 24.0
    MIN_EDGE_LENGTH = 12.0

    def __init__(self, rect, image_path="", image_zoom=1.0, image_offset=None, skew=0.0, custom_points=None, image_effects=None, panel_texts=None, panel_overlays=None):
        super().__init__(rect)
        self.image_path = image_path or ""
        self.original_image_pixmap = QPixmap(self.image_path) if self.image_path else QPixmap()
        self.image_pixmap = QPixmap(self.original_image_pixmap)
        self.image_effects = json.loads(json.dumps(image_effects or []))
        self.panel_texts = json.loads(json.dumps(panel_texts or []))
        self.panel_overlays = json.loads(json.dumps(panel_overlays or []))
        self.effect_uid = uuid.uuid4().hex
        self.image_zoom = float(image_zoom or 1.0)
        off = image_offset or [0.0, 0.0]
        self.image_offset = QPointF(float(off[0]), float(off[1]))
        self.skew = max(-0.35, min(0.35, float(skew or 0.0)))
        self.custom_points = None
        if custom_points and len(custom_points) == 4:
            try:
                self.custom_points = [QPointF(float(p[0]), float(p[1])) for p in custom_points]
            except Exception:
                self.custom_points = None
        self._panning_image = False
        self._pan_last = QPointF()
        self._resizing = False
        self._resize_origin = QPointF()
        self._resize_rect = QRectF()
        self._resize_custom_points = None
        self._resize_visual_bounds = QRectF()
        self._drag_corner_index = None
        self.corner_edit_enabled = False
        self._selected_corner_indices = set()
        self._drag_corner_start_points = None
        self._drag_corner_start_mouse = QPointF()
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsFocusable)
        self.setBrush(Qt.NoBrush)
        self.setPen(QPen(QColor("black"), 8))
        self.setTransformOriginPoint(self.rect().center())
        self.setAcceptHoverEvents(True)
        if self.image_effects and not self.original_image_pixmap.isNull():
            self.rebuild_image_effects()

    def _default_corner_points(self):
        r = self.rect(); dx = abs(self.skew) * r.width()
        if self.skew > 0:
            return [QPointF(r.left()+dx, r.top()), QPointF(r.right(), r.top()), QPointF(r.right()-dx, r.bottom()), QPointF(r.left(), r.bottom())]
        if self.skew < 0:
            return [QPointF(r.left(), r.top()), QPointF(r.right()-dx, r.top()), QPointF(r.right(), r.bottom()), QPointF(r.left()+dx, r.bottom())]
        return [QPointF(r.topLeft()), QPointF(r.topRight()), QPointF(r.bottomRight()), QPointF(r.bottomLeft())]

    def corner_points(self):
        pts = self.custom_points if self.custom_points is not None else self._default_corner_points()
        return [QPointF(p) for p in pts]

    def panel_path(self):
        path = QPainterPath()
        path.addPolygon(QPolygonF(self.corner_points()))
        path.closeSubpath()
        return path

    def visual_bounds(self):
        return self.panel_path().boundingRect()

    def _resize_handle_center(self):
        return QPointF(self.visual_bounds().bottomRight())

    def sync_transform_origin_to_visual_center(self):
        """Move transform origin to visible panel center without moving the panel on screen."""
        new_origin = QPointF(self.visual_bounds().center())
        old_origin = QPointF(self.transformOriginPoint())
        if (
            abs(new_origin.x()-old_origin.x()) < 0.001
            and abs(new_origin.y()-old_origin.y()) < 0.001
        ):
            return

        # Panels are top-level scene items. Preserve the scene position of the
        # new visual center while changing transformOriginPoint.
        scene_anchor_before = self.mapToScene(new_origin)
        self.setTransformOriginPoint(new_origin)
        scene_anchor_after = self.mapToScene(new_origin)
        delta = scene_anchor_before - scene_anchor_after
        self.setPos(self.pos() + delta)

    def shape(self):
        path = QPainterPath(self.panel_path())
        # Selection handles must be part of the hit-test shape, otherwise the
        # visible circles can be clicked outside the polygon without receiving
        # mouse events.
        if self.isSelected():
            if self.custom_points is not None and self.corner_edit_enabled:
                rr = self.CORNER_HANDLE * 2.2
                for p in self.corner_points():
                    path.addEllipse(QRectF(p.x()-rr, p.y()-rr, rr*2, rr*2))
            else:
                c = self._resize_handle_center()
                hit = self.HANDLE * 1.35
                path.addEllipse(
                    QRectF(
                        c.x()-hit,
                        c.y()-hit,
                        hit*2,
                        hit*2,
                    )
                )
        return path

    def boundingRect(self):
        # For freeform panels self.rect() is legacy/base geometry. Using it
        # here left a giant invisible old selection/update area after shrinking.
        br = self.visual_bounds()
        pad = self.HANDLE * 1.7
        return br.adjusted(-pad, -pad, pad, pad)

    def enable_freeform(self):
        if self.custom_points is None:
            self.prepareGeometryChange()
            # Preserve any legacy skewed appearance as editable corner points,
            # then retire the old skew value so reset really means rectangle.
            self.custom_points = self._default_corner_points()
            self.skew = 0.0
        self.corner_edit_enabled = True
        if not self._selected_corner_indices:
            self._selected_corner_indices = {0}
        self.update()

    def set_corner_edit_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled and self.custom_points is None:
            self.enable_freeform()
            return
        self.corner_edit_enabled = enabled
        if not enabled:
            self._drag_corner_index = None
            self._drag_corner_start_points = None
        self.update()

    def reset_freeform(self):
        if self.custom_points is not None or abs(self.skew) > 1e-9:
            self.prepareGeometryChange()
            self.custom_points = None
            self.skew = 0.0
        self.corner_edit_enabled = False
        self._selected_corner_indices.clear()
        self._drag_corner_index = None
        self._drag_corner_start_points = None
        self.update()

    def set_image(self, path):
        pix = QPixmap(path)
        if pix.isNull():
            return False
        self.image_path = path
        self.original_image_pixmap = pix
        self.image_pixmap = QPixmap(pix)
        self.image_effects = []
        self.image_zoom = 1.0
        self.image_offset = QPointF(0, 0)
        self.update()
        return True

    def rebuild_image_effects(self):
        if self.original_image_pixmap.isNull():
            return
        pil = pixmap_to_pil(self.original_image_pixmap)
        for effect in self.image_effects:
            kind = effect.get("type")
            if kind == "mosaic":
                rect = effect.get("rect")
                cell = int(effect.get("cell", 18))
                if rect is None:
                    box = (0, 0, pil.width, pil.height)
                else:
                    nx, ny, nw, nh = rect
                    box = (nx*pil.width, ny*pil.height, (nx+nw)*pil.width, (ny+nh)*pil.height)
                pil = mosaic_region(pil, box, cell)
            elif kind == "grayscale":
                alpha = pil.getchannel("A")
                pil = ImageOps.grayscale(pil).convert("RGBA")
                pil.putalpha(alpha)
            elif kind == "sepia":
                gray = ImageOps.grayscale(pil)
                sep = ImageOps.colorize(gray, "#2b1b10", "#f1d19a")
                alpha = pil.getchannel("A")
                pil = sep.convert("RGBA"); pil.putalpha(alpha)
            elif kind == "blur":
                pil = pil.filter(ImageFilter.GaussianBlur(radius=float(effect.get("radius", 3.0))))
            elif kind == "sharpen":
                pil = pil.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=3))
            elif kind == "brightness":
                pil = ImageEnhance.Brightness(pil).enhance(float(effect.get("factor", 1.0)))
            elif kind == "contrast":
                pil = ImageEnhance.Contrast(pil).enhance(float(effect.get("factor", 1.0)))
        self.image_pixmap = pil_to_pixmap(pil)
        self.update()

    def add_image_simple_effect(self, kind, **params):
        if self.original_image_pixmap.isNull():
            return False
        self.image_effects.append({"type": kind, **params})
        self.rebuild_image_effects()
        return True

    def add_image_mosaic_effect(self, local_rect=None, cell=18):
        if self.original_image_pixmap.isNull():
            return False
        if local_rect is None:
            norm = None
        else:
            draw = self._image_draw_rect()
            r = local_rect.normalized().intersected(draw)
            if r.width() < 2 or r.height() < 2 or draw.width() <= 0 or draw.height() <= 0:
                return False
            norm = [
                (r.x()-draw.x())/draw.width(),
                (r.y()-draw.y())/draw.height(),
                r.width()/draw.width(),
                r.height()/draw.height(),
            ]
        self.image_effects.append({"type": "mosaic", "rect": norm, "cell": int(cell)})
        self.rebuild_image_effects()
        return True

    def restore_image_effects(self):
        if self.original_image_pixmap.isNull():
            return False
        self.image_effects = []
        self.image_pixmap = QPixmap(self.original_image_pixmap)
        self.update()
        return True

    def add_panel_overlay(self, path, opacity=1.0):
        p = Path(path)
        if not p.exists():
            return False
        self.panel_overlays.append({
            "path": effect_path_to_ref(p),
            "opacity": float(opacity),
            "mode": "fit",
            "scale": 1.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "rotation": 0.0,
        })
        self.update()
        return True

    def clear_panel_overlays(self):
        if not self.panel_overlays:
            return False
        self.panel_overlays = []
        self.update()
        return True

    def reset_image_view(self):
        self.image_zoom = 1.0
        self.image_offset = QPointF(0, 0)
        self.update()

    def _image_draw_rect(self):
        r = self.panel_path().boundingRect()
        if self.image_pixmap.isNull():
            return QRectF()
        pw, ph = max(1, self.image_pixmap.width()), max(1, self.image_pixmap.height())
        cover = max(r.width()/pw, r.height()/ph)
        scale = cover * max(0.05, self.image_zoom)
        dw, dh = pw*scale, ph*scale
        return QRectF(r.center().x()-dw/2+self.image_offset.x(), r.center().y()-dh/2+self.image_offset.y(), dw, dh)

    def _overlay_draw_rect(self, overlay, pix, bounds):
        if str(overlay.get("mode", "fit")) == "fit":
            return QRectF(bounds)
        pw = max(1.0, float(pix.width()))
        ph = max(1.0, float(pix.height()))
        contain = min(bounds.width()/pw, bounds.height()/ph)
        scale = contain * max(0.01, float(overlay.get("scale", 1.0)))
        dw = pw * scale
        dh = ph * scale
        return QRectF(
            bounds.center().x() - dw/2 + float(overlay.get("offset_x", 0.0)),
            bounds.center().y() - dh/2 + float(overlay.get("offset_y", 0.0)),
            dw,
            dh,
        )

    def paint(self, painter, option, widget=None):
        r = self.rect(); path = self.panel_path()
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setClipPath(path)
        if not self.image_pixmap.isNull():
            painter.drawPixmap(self._image_draw_rect(), self.image_pixmap, QRectF(self.image_pixmap.rect()))
        else:
            painter.fillPath(path, QColor(248, 248, 248))
            painter.setPen(QColor(145,145,145))
            painter.setFont(QFont("Yu Gothic UI",18))
            painter.drawText(path.boundingRect(), Qt.AlignCenter, "画像をドロップ\nまたは右クリック→コマ詳細編集")

        # Decorative effect assets are rendered in panel coordinates and are
        # clipped by the panel path. Anything outside a slanted/freeform panel
        # is therefore never visible.
        overlay_bounds = path.boundingRect()
        for overlay in self.panel_overlays:
            overlay_path = resolve_effect_path(overlay.get("path", ""))
            if not overlay_path.exists():
                continue
            pix = QPixmap(str(overlay_path))
            if pix.isNull():
                continue
            painter.save()
            painter.setOpacity(max(0.0, min(1.0, float(overlay.get("opacity", 1.0)))))
            draw_rect = self._overlay_draw_rect(overlay, pix, overlay_bounds)
            angle = float(overlay.get("rotation", 0.0))
            painter.translate(draw_rect.center())
            painter.rotate(angle)
            painter.drawPixmap(
                QRectF(
                    -draw_rect.width()/2.0,
                    -draw_rect.height()/2.0,
                    draw_rect.width(),
                    draw_rect.height(),
                ),
                pix,
                QRectF(pix.rect()),
            )
            painter.restore()

        # panel_texts is kept only for backward-compatible project loading.
        # The panel-local text feature is currently retired and not rendered.

        painter.restore()

        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(self.pen())
        painter.drawPath(path)
        if self.isSelected():
            sel_pen = QPen(QColor("#00c8ff"), 4)
            sel_pen.setCosmetic(True)
            painter.setPen(sel_pen)
            painter.drawPath(path)
            painter.setBrush(QColor("white"))
            painter.setPen(QPen(QColor("#00c8ff"),2))
            if self.custom_points is not None and self.corner_edit_enabled:
                rr = self.CORNER_HANDLE
                pts = self.corner_points()
                edge_pen = QPen(QColor("#ff9f1a"), 6)
                edge_pen.setCosmetic(True)
                painter.setPen(edge_pen)
                for edge_idx in range(4):
                    a_idx = edge_idx
                    b_idx = (edge_idx + 1) % 4
                    if (
                        a_idx in self._selected_corner_indices
                        and b_idx in self._selected_corner_indices
                    ):
                        painter.drawLine(pts[a_idx], pts[b_idx])

                for idx, p in enumerate(pts):
                    if idx in self._selected_corner_indices:
                        painter.setBrush(QColor("#ffb000"))
                        painter.setPen(QPen(QColor("#7a4300"), 2))
                    else:
                        painter.setBrush(QColor("white"))
                        painter.setPen(QPen(QColor("black"), 2))
                    painter.drawEllipse(QRectF(p.x()-rr, p.y()-rr, rr*2, rr*2))
            else:
                radius = self.HANDLE * 0.48
                painter.setBrush(QColor("#00c8ff"))
                painter.setPen(QPen(QColor("white"), 2))
                painter.drawEllipse(
                    self._resize_handle_center(), radius, radius
                )
        painter.restore()

    def _on_resize_handle(self, pos):
        if self.corner_edit_enabled:
            return False
        c = self._resize_handle_center()
        radius = self.HANDLE * 1.35
        return math.hypot(pos.x()-c.x(), pos.y()-c.y()) <= radius

    def _corner_hit(self, pos):
        if self.custom_points is None or not self.corner_edit_enabled:
            return None
        radius = self.CORNER_HANDLE * 2.35
        for idx, p in enumerate(self.corner_points()):
            if ((pos.x()-p.x())**2 + (pos.y()-p.y())**2) ** 0.5 <= radius:
                return idx
        return None

    def _edge_hit(self, pos):
        """Return an edge index when the pointer is close to the visible polygon edge."""
        if self.custom_points is None or not self.corner_edit_enabled:
            return None
        pts = self.corner_points()
        threshold = self.CORNER_HANDLE * 1.15
        best = None
        best_dist = threshold + 1.0
        for idx in range(4):
            a = pts[idx]
            b = pts[(idx+1) % 4]
            vx = b.x() - a.x()
            vy = b.y() - a.y()
            denom = vx*vx + vy*vy
            if denom <= 1e-9:
                continue
            t = ((pos.x()-a.x())*vx + (pos.y()-a.y())*vy) / denom
            # Corners themselves are handled by the normal corner hit.
            if t < 0.08 or t > 0.92:
                continue
            px = a.x() + vx*t
            py = a.y() + vy*t
            dist = math.hypot(pos.x()-px, pos.y()-py)
            if dist < best_dist:
                best_dist = dist
                best = idx
        return best

    def _clamp_corner(self, idx, p):
        """Prevent inversion without anchoring corners to the old rect centre."""
        pts = self.corner_points()
        opposite = pts[(int(idx) + 2) % 4]
        minimum = self.MIN_PANEL_SIZE
        x, y = float(p.x()), float(p.y())

        if idx == 0:      # TL relative to BR
            x = min(x, opposite.x() - minimum)
            y = min(y, opposite.y() - minimum)
        elif idx == 1:    # TR relative to BL
            x = max(x, opposite.x() + minimum)
            y = min(y, opposite.y() - minimum)
        elif idx == 2:    # BR relative to TL
            x = max(x, opposite.x() + minimum)
            y = max(y, opposite.y() + minimum)
        else:             # BL relative to TR
            x = min(x, opposite.x() - minimum)
            y = max(y, opposite.y() + minimum)
        return QPointF(x, y)

    def begin_corner_drag_scene(self, scene_pos, modifiers=Qt.NoModifier, forced_corner=None):
        if self.custom_points is None or not self.corner_edit_enabled:
            return False
        local = self.mapFromScene(scene_pos)
        corner = forced_corner if forced_corner is not None else self._corner_hit(local)
        if corner is None:
            return False
        if not self.scene() or not hasattr(self.scene(), "editor"):
            return False
        return self.scene().editor.begin_corner_group_drag(
            self, corner, QPointF(scene_pos), modifiers
        )

    def update_corner_drag_scene(self, scene_pos):
        if self.scene() and hasattr(self.scene(), "editor"):
            return self.scene().editor.update_corner_group_drag(QPointF(scene_pos))
        return False

    def end_corner_drag_scene(self):
        if self.scene() and hasattr(self.scene(), "editor"):
            return self.scene().editor.end_corner_group_drag()
        return False

    def mouseDoubleClickEvent(self, event):
        if self.scene() and hasattr(self.scene(), "editor"):
            self.scene().editor.open_panel_detail(self)
        event.accept()

    def contextMenuEvent(self, event):
        if not self.scene() or not hasattr(self.scene(), "editor"):
            return super().contextMenuEvent(event)
        editor = self.scene().editor
        menu = QMenu()
        detail = menu.addAction("コマ詳細編集…")
        image = menu.addAction("画像を設定 / 差し替え…")
        menu.addSeparator()
        reset_view = menu.addAction("画像位置・ズームをリセット")
        clear_image = menu.addAction("コマ画像を外す")
        action = menu.exec(event.screenPos())
        if action == detail:
            editor.open_panel_detail(self)
        elif action == image:
            editor.assign_image_to_panel(self)
        elif action == reset_view:
            self.reset_image_view()
            editor.record_history()
        elif action == clear_image:
            self.image_path = ""
            self.original_image_pixmap = QPixmap()
            self.image_pixmap = QPixmap()
            self.image_effects = []
            self.reset_image_view()
            self.update()
            editor.record_history()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isSelected():
            corner = self._corner_hit(event.pos())
            if corner is not None:
                started = self.begin_corner_drag_scene(
                    event.scenePos(), event.modifiers(), corner
                )
                if started:
                    self._drag_corner_index = corner
                self.update()
                event.accept()
                return
            if self._on_resize_handle(event.pos()):
                self._resizing = True
                self._resize_origin = event.pos()
                self._resize_rect = QRectF(self.rect())
                self._resize_custom_points = (
                    [QPointF(p) for p in self.custom_points]
                    if self.custom_points is not None
                    else None
                )
                self._resize_visual_bounds = QRectF(self.visual_bounds())
                event.accept()
                return
        if event.button()==Qt.LeftButton and event.modifiers() & Qt.ShiftModifier and not self.image_pixmap.isNull():
            self._panning_image = True
            self._pan_last = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_corner_index is not None and self.custom_points is not None:
            self.update_corner_drag_scene(event.scenePos())
            event.accept()
            return
        if self._resizing:
            delta = event.pos()-self._resize_origin
            self.prepareGeometryChange()

            if self._resize_custom_points is None:
                r = QRectF(self._resize_rect)
                r.setWidth(
                    max(self.MIN_PANEL_SIZE, r.width()+delta.x())
                )
                r.setHeight(
                    max(self.MIN_PANEL_SIZE, r.height()+delta.y())
                )
                self.setRect(r)
                self.setTransformOriginPoint(r.center())
            else:
                bounds = QRectF(self._resize_visual_bounds)
                old_w = max(1e-6, bounds.width())
                old_h = max(1e-6, bounds.height())
                new_w = max(
                    self.MIN_PANEL_SIZE,
                    old_w + delta.x()
                )
                new_h = max(
                    self.MIN_PANEL_SIZE,
                    old_h + delta.y()
                )
                sx = new_w / old_w
                sy = new_h / old_h
                origin = bounds.topLeft()
                self.custom_points = [
                    QPointF(
                        origin.x() + (p.x()-origin.x())*sx,
                        origin.y() + (p.y()-origin.y())*sy,
                    )
                    for p in self._resize_custom_points
                ]

            self.update()
            event.accept()
            return
        if self._panning_image:
            delta = event.pos()-self._pan_last
            self.image_offset += delta
            self._pan_last = event.pos()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        corner_edited = self._drag_corner_index is not None
        edited = self._resizing or self._panning_image or corner_edited
        if self._panning_image:
            self.unsetCursor()
        if corner_edited:
            self.end_corner_drag_scene()
        elif self._resizing:
            self.sync_transform_origin_to_visual_center()

        self._resizing = False
        self._resize_custom_points = None
        self._resize_visual_bounds = QRectF()
        self._panning_image = False
        self._drag_corner_index = None
        self._drag_corner_start_points = None
        if edited and not corner_edited and self.scene() and hasattr(self.scene(), "editor"):
            self.scene().editor.record_history()
        if edited:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if not self.image_pixmap.isNull() and event.modifiers() & Qt.AltModifier:
            factor = 1.10 if event.delta()>0 else 1/1.10
            self.image_zoom = max(0.1, min(10.0, self.image_zoom*factor))
            self.update()
            if self.scene() and hasattr(self.scene(), "editor"):
                self.scene().editor.record_history()
            event.accept()
            return
        super().wheelEvent(event)

    def serialize(self):
        r = self.rect()
        custom = None
        if self.custom_points is not None:
            custom = [[p.x(), p.y()] for p in self.custom_points]
        return {
            "type":"panel", "x":self.pos().x(), "y":self.pos().y(),
            "rx":r.x(), "ry":r.y(), "w":r.width(), "h":r.height(),
            "rotation": self.rotation(), "scale": self.scale(),
            "z":self.zValue(), "opacity":self.opacity(), "image_path":self.image_path,
            "image_zoom":self.image_zoom,
            "image_offset":[self.image_offset.x(),self.image_offset.y()],
            "image_effects": self.image_effects,
            "panel_texts": self.panel_texts,
            "panel_overlays": self.panel_overlays,
            "effect_uid": self.effect_uid,
            "skew":self.skew, "custom_points":custom
        }


class CanvasView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setBackgroundBrush(QColor("#505050"))
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.region_mosaic_mode = False
        self.region_start = None
        self.region_overlay = None
        self.region_target = None
        self.show_center_guides = True
        self.show_margin_guides = False
        self._group_dragging = False
        self._group_drag_start_scene = QPointF()
        self._group_drag_positions = {}
        self._group_drag_moved = False
        self._forced_corner_drag = False
        self._forced_text_drag_item = None
        self._forced_text_drag_start_scene = QPointF()
        self._forced_text_drag_start_pos = QPointF()
        self._forced_text_drag_moved = False

    def drawBackground(self, painter, rect):
        editor = self.window()
        cw = float(getattr(editor, "canvas_w", CANVAS_W))
        ch = float(getattr(editor, "canvas_h", CANVAS_H))
        painter.fillRect(rect, QColor(55, 55, 59))
        # Page shadow and paper. View-only; export still uses scene background.
        painter.fillRect(QRectF(10, 10, cw, ch), QColor(0, 0, 0, 75))
        painter.fillRect(QRectF(0, 0, cw, ch), QColor("white"))

    def drawForeground(self, painter, rect):
        painter.save()
        editor = self.window()
        cw = float(getattr(editor, "canvas_w", CANVAS_W))
        ch = float(getattr(editor, "canvas_h", CANVAS_H))

        border = QPen(QColor(15, 15, 15, 240), 5)
        border.setCosmetic(True)
        painter.setPen(border)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(0, 0, cw, ch))

        painter.setPen(QColor(245,245,245))
        painter.setFont(QFont("Yu Gothic UI", 12, QFont.Bold))
        page_no = max(1, int(getattr(editor, "current_page_index", -1)) + 1)
        painter.drawText(QPointF(8, -14), f"PAGE {page_no}   {int(cw)} × {int(ch)} px")

        guide_pen = QPen(QColor(30, 120, 220, 170), 1, Qt.DashLine)
        guide_pen.setCosmetic(True)
        painter.setPen(guide_pen)
        if self.show_center_guides:
            painter.drawLine(QPointF(cw/2, 0), QPointF(cw/2, ch))
            painter.drawLine(QPointF(0, ch/2), QPointF(cw, ch/2))
        if self.show_margin_guides:
            margin = min(55.0, cw*0.08, ch*0.08)
            painter.drawRect(QRectF(margin, margin, cw-margin*2, ch-margin*2))

        # Draw selection feedback in the view foreground, above every layer.
        # This remains visible even when a selected panel/image is underneath
        # another object in z-order.
        scene = self.scene()
        if scene:
            select_pen = QPen(QColor("#00c8ff"), 4)
            select_pen.setCosmetic(True)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(select_pen)
            for item in scene.selectedItems():
                if item.parentItem() is not None or not item.isVisible():
                    continue
                if isinstance(item, PanelItem):
                    pts = [item.mapToScene(p) for p in item.corner_points()]
                    if pts:
                        painter.drawPolygon(QPolygonF(pts))
                    if item.corner_edit_enabled and item.custom_points is not None:
                        corner_points = item.corner_points()
                        scene_points = [item.mapToScene(p) for p in corner_points]
                        edge_pen = QPen(QColor("#ff9f1a"), 6)
                        edge_pen.setCosmetic(True)
                        painter.setPen(edge_pen)
                        for edge_idx in range(4):
                            a_idx = edge_idx
                            b_idx = (edge_idx + 1) % 4
                            if (
                                a_idx in item._selected_corner_indices
                                and b_idx in item._selected_corner_indices
                            ):
                                painter.drawLine(scene_points[a_idx], scene_points[b_idx])

                        for idx, local_p in enumerate(corner_points):
                            sp = scene_points[idx]
                            radius = 9.0 / max(0.01, self.transform().m11())
                            if idx in item._selected_corner_indices:
                                painter.setBrush(QColor("#ff9f1a"))
                                painter.setPen(QPen(QColor("#7a4300"), 2))
                            else:
                                painter.setBrush(QColor("white"))
                                painter.setPen(QPen(QColor("#00c8ff"), 2))
                            painter.drawEllipse(sp, radius, radius)
                        painter.setBrush(Qt.NoBrush)
                        painter.setPen(select_pen)
                else:
                    br = item.sceneBoundingRect()
                    painter.drawRect(br)
        painter.restore()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.12 if event.angleDelta().y() > 0 else 1/1.12
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            return super().dropEvent(event)
        main = self.window()

        local_files = [
            u.toLocalFile()
            for u in event.mimeData().urls()
            if u.isLocalFile()
        ]
        py_files = [
            p for p in local_files
            if Path(p).suffix.lower() == ".py"
        ]
        if py_files:
            if len(py_files) == 1 and hasattr(main, "handle_update_file"):
                event.acceptProposedAction()
                main.handle_update_file(py_files[0])
            else:
                QMessageBox.information(
                    main, "アップデート",
                    "更新用.pyは1ファイルだけドラッグしてください。"
                )
                event.acceptProposedAction()
            return

        scene_pos = self.mapToScene(event.position().toPoint())
        offset = 0
        added = False
        image_urls = [u.toLocalFile() for u in event.mimeData().urls()
                      if Path(u.toLocalFile()).suffix.lower() in IMAGE_EXTS]
        panel = None
        if self.scene():
            for candidate in self.scene().items(scene_pos):
                if isinstance(candidate, PanelItem):
                    panel = candidate
                    break
        for idx, p in enumerate(image_urls):
            if idx == 0 and panel is not None:
                if panel.set_image(p):
                    self.scene().clearSelection(); panel.setSelected(True)
                    added = True
            else:
                main.add_image(p, QPointF(scene_pos.x()+offset, scene_pos.y()+offset), record=False)
                offset += 30
                added = True
        if added:
            main.record_history()
        event.acceptProposedAction()

    def start_region_mosaic(self, target):
        self.region_mosaic_mode = True
        self.region_target = target
        self.setCursor(Qt.CrossCursor)
        self.window().statusBar().showMessage("画像の上をドラッグしてモザイク範囲を指定 / Escでキャンセル")

    def cancel_region_mosaic(self):
        if self.region_overlay and self.scene():
            self.scene().removeItem(self.region_overlay)
        self.region_overlay = None
        self.region_start = None
        self.region_target = None
        self.region_mosaic_mode = False
        self.unsetCursor()

    def _corner_handle_at(self, scene_pos):
        if not self.scene():
            return None
        # Selected panels only; handles are an editing affordance and must win
        # even when a higher-z image visually overlaps them.
        panels = [
            i for i in self.scene().selectedItems()
            if isinstance(i, PanelItem) and i.corner_edit_enabled and i.isVisible()
        ]
        for panel in reversed(panels):
            idx = panel._corner_hit(panel.mapFromScene(scene_pos))
            if idx is not None:
                return panel, idx
        return None

    def _edge_handle_at(self, scene_pos):
        if not self.scene():
            return None
        panels = [
            i for i in self.scene().selectedItems()
            if isinstance(i, PanelItem) and i.corner_edit_enabled and i.isVisible()
        ]
        for panel in reversed(panels):
            idx = panel._edge_hit(panel.mapFromScene(scene_pos))
            if idx is not None:
                return panel, idx
        return None

    def _priority_text_at(self, scene_pos):
        if not self.scene():
            return None
        candidates = [
            i for i in self.scene().items(scene_pos)
            if i.parentItem() is None and i.isVisible()
        ]
        # Editing usability takes priority over z-order for text.
        for cls in (EditableTextItem, VerticalTextItem, BubbleItem):
            for item in candidates:
                if isinstance(item, cls):
                    return item
        return None

    def _preferred_target_for_mode(self, scene_pos):
        if not self.scene():
            return None
        editor = self.window()
        mode = editor.mode_combo.currentData() if hasattr(editor, "mode_combo") else "select"
        candidates = [i for i in self.scene().items(scene_pos) if i.parentItem() is None and i.isVisible()]
        if mode == "effect":
            for i in candidates:
                if isinstance(i, ImageItem):                    return i
            for i in candidates:
                if isinstance(i, PanelItem) and i.image_path:
                    return i
        elif mode == "panel":
            for i in candidates:
                if isinstance(i, PanelItem):
                    return i
        elif mode == "text":
            for cls in (EditableTextItem, VerticalTextItem, BubbleItem):
                for i in candidates:
                    if isinstance(i, cls):
                        return i
        elif mode == "select":
            text = self._priority_text_at(scene_pos)
            if text is not None:
                return text
        return None

    def mousePressEvent(self, event):
        if not self.region_mosaic_mode and event.button() == Qt.LeftButton:
            pos = self.mapToScene(event.position().toPoint())
            editor = self.window()
            mode = editor.mode_combo.currentData() if hasattr(editor, "mode_combo") else "select"

            # 1) Shift + edge selects both vertices of that edge.
            # It only changes handle selection, so N-split behavior remains untouched.
            if (
                mode in {"select", "panel"}
                and (event.modifiers() & Qt.ShiftModifier)
            ):
                edge_hit = self._edge_handle_at(pos)
                if edge_hit is not None:
                    panel, edge_idx = edge_hit
                    if hasattr(editor, "toggle_panel_edge_selection"):
                        editor.toggle_panel_edge_selection(panel, edge_idx)
                    event.accept()
                    return

            # 2) Four-corner handles always win over overlapping images.
            if mode in {"select", "panel"}:
                corner_hit = self._corner_handle_at(pos)
                if corner_hit is not None:
                    panel, corner_idx = corner_hit
                    if panel.begin_corner_drag_scene(pos, event.modifiers(), corner_idx):
                        self._forced_corner_drag = True
                    event.accept()
                    return

            # 3) Text is intentionally easier to grab than images behind/in front
            # of it. First click-drag can immediately move the text.
            if mode in {"select", "text"} and not (event.modifiers() & Qt.ControlModifier):
                text_item = self._priority_text_at(pos)
                if text_item is not None and not text_item.isSelected():
                    self.scene().clearSelection()
                    text_item.setSelected(True)
                    self._forced_text_drag_item = text_item
                    self._forced_text_drag_start_scene = QPointF(pos)
                    self._forced_text_drag_start_pos = QPointF(text_item.pos())
                    self._forced_text_drag_moved = False
                    if hasattr(editor, "selection_changed"):
                        editor.selection_changed()
                    event.accept()
                    return

            # Reliable group move in Select/Place mode.
            # Uses absolute positions from the drag start instead of accumulating
            # mouse deltas, so repeated moves do not introduce drift.
            if mode == "select" and not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
                selected = [
                    i for i in self.scene().selectedItems()
                    if i.parentItem() is None
                ] if self.scene() else []
                hit = None
                if self.scene():
                    for candidate in self.scene().items(pos):
                        if candidate.parentItem() is None and candidate in selected:
                            hit = candidate
                            break
                if hit is not None and len(selected) > 1:
                    self._group_dragging = True
                    self._group_drag_moved = False
                    self._group_drag_start_scene = QPointF(pos)
                    self._group_drag_positions = {
                        item: QPointF(item.pos()) for item in selected
                    }
                    self.setCursor(Qt.ClosedHandCursor)
                    event.accept()
                    return

            preferred = self._preferred_target_for_mode(pos)
            if preferred is not None:
                # If the object is already selected, let QGraphicsScene receive the
                # event so dragging, resize handles and four-corner handles work.
                # Ctrl/Shift also go through so multi-selection remains available.
                if preferred.isSelected() or (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
                    return super().mousePressEvent(event)

                # First click in a specialized mode selects its preferred target.
                self.scene().clearSelection()
                preferred.setSelected(True)
                if hasattr(self.window(), "selection_changed"):
                    self.window().selection_changed()
                event.accept()
                return
        if self.region_mosaic_mode and event.button() == Qt.LeftButton:
            pos = self.mapToScene(event.position().toPoint())
            target = self.region_target
            if target and target.scene() == self.scene() and target.contains(target.mapFromScene(pos)):
                self.region_start = pos
                self.region_overlay = QGraphicsRectItem()
                self.region_overlay.setPen(QPen(QColor("red"), 3, Qt.DashLine))
                self.region_overlay.setBrush(QColor(255, 0, 0, 35))
                self.region_overlay.setZValue(999999)
                self.scene().addItem(self.region_overlay)
                event.accept()
                return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        pos = self.mapToScene(event.pos())
        text_item = self._priority_text_at(pos)
        if text_item is not None:
            scene = self.scene()
            if scene:
                scene.clearSelection()
                text_item.setSelected(True)
            editor = self.window()
            if hasattr(editor, "edit_text_object"):
                editor.edit_text_object(text_item)
            event.accept()
            return
        super().contextMenuEvent(event)

    def mouseMoveEvent(self, event):
        if self._forced_corner_drag:
            editor = self.window()
            if hasattr(editor, "update_corner_group_drag"):
                editor.update_corner_group_drag(self.mapToScene(event.position().toPoint()))
            event.accept()
            return

        if self._forced_text_drag_item is not None:
            item = self._forced_text_drag_item
            if item.scene() == self.scene():
                pos = self.mapToScene(event.position().toPoint())
                delta = pos - self._forced_text_drag_start_scene
                if abs(delta.x()) > 0.5 or abs(delta.y()) > 0.5:
                    self._forced_text_drag_moved = True
                item.setPos(self._forced_text_drag_start_pos + delta)
            event.accept()
            return

        if self._group_dragging:
            pos = self.mapToScene(event.position().toPoint())
            delta = pos - self._group_drag_start_scene
            if abs(delta.x()) > 0.5 or abs(delta.y()) > 0.5:
                self._group_drag_moved = True
            for item, original in list(self._group_drag_positions.items()):
                if item.scene() == self.scene():
                    item.setPos(original + delta)
            event.accept()
            return

        if self.region_mosaic_mode and self.region_start is not None and self.region_overlay:
            pos = self.mapToScene(event.position().toPoint())
            self.region_overlay.setRect(QRectF(self.region_start, pos).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._forced_corner_drag:
            self._forced_corner_drag = False
            editor = self.window()
            if hasattr(editor, "end_corner_group_drag"):
                editor.end_corner_group_drag()
            event.accept()
            return

        if self._forced_text_drag_item is not None:
            moved = self._forced_text_drag_moved
            self._forced_text_drag_item = None
            self._forced_text_drag_moved = False
            if moved:
                editor = self.window()
                if hasattr(editor, "snap_selected_items"):
                    editor.snap_selected_items()
                if hasattr(editor, "record_history"):
                    editor.record_history()
            event.accept()
            return

        if self._group_dragging:
            self._group_dragging = False
            self.unsetCursor()
            moved = self._group_drag_moved
            self._group_drag_moved = False
            self._group_drag_positions = {}
            editor = self.window()
            if moved:
                if hasattr(editor, "snap_selected_items"):
                    editor.snap_selected_items()
                if hasattr(editor, "record_history"):
                    editor.record_history()
            else:
                # Plain click on a multi-selection must never cause snapping/drift.
                if hasattr(editor, "refresh_layer_list"):
                    editor.refresh_layer_list()
            event.accept()
            return

        if self.region_mosaic_mode and self.region_start is not None and self.region_overlay:
            end = self.mapToScene(event.position().toPoint())
            scene_rect = QRectF(self.region_start, end).normalized()
            target = self.region_target
            self.scene().removeItem(self.region_overlay)
            self.region_overlay = None
            self.region_start = None
            if target:
                p1 = target.mapFromScene(scene_rect.topLeft())
                p2 = target.mapFromScene(scene_rect.bottomRight())
                local_rect = QRectF(p1, p2).normalized()
                editor = self.window()
                before = (
                    editor._capture_effect_state([target])
                    if hasattr(editor, "_capture_effect_state")
                    else {}
                )
                changed = False
                if isinstance(target, ImageItem):
                    changed = target.add_mosaic_effect(local_rect)
                elif isinstance(target, PanelItem):
                    changed = target.add_image_mosaic_effect(local_rect)
                if changed:
                    if hasattr(editor, "_finish_effect_change"):
                        editor._finish_effect_change(
                            before,
                            "範囲モザイク",
                            [target],
                        )
                    else:
                        editor.record_history()
            self.region_mosaic_mode = False
            self.region_target = None
            self.unsetCursor()
            self.window().statusBar().showMessage("範囲モザイクを適用しました", 3000)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.region_mosaic_mode and event.key() == Qt.Key_Escape:
            self.cancel_region_mosaic()
            self.window().statusBar().showMessage("範囲モザイクをキャンセルしました", 2500)
            return
        super().keyPressEvent(event)


class EffectOverlayPreview(QWidget):
    """Interactive preview for one overlay: drag=move, corner=uniform scale, top handle=rotate."""
    HANDLE_RADIUS = 9.0
    ROTATE_HANDLE_DISTANCE = 34.0

    def __init__(self, dialog, base_pixmap=None, logical_size=None):
        super().__init__(dialog)
        self.dialog = dialog
        self.base_pixmap = QPixmap(base_pixmap) if base_pixmap is not None else QPixmap()
        if logical_size:
            self.logical_w = max(1.0, float(logical_size[0]))
            self.logical_h = max(1.0, float(logical_size[1]))
        elif not self.base_pixmap.isNull():
            self.logical_w = max(1.0, float(self.base_pixmap.width()))
            self.logical_h = max(1.0, float(self.base_pixmap.height()))
        else:
            self.logical_w, self.logical_h = 800.0, 600.0

        self.effect_pixmap = QPixmap(str(resolve_effect_path(dialog.overlay.get("path", ""))))
        self.drag_mode = None
        self.drag_start = QPointF()
        self.start_offset = QPointF()
        self.start_scale = 1.0
        self.start_distance = 1.0

        self.setMinimumSize(520, 340)
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)

    def _target_rect(self):
        margin = 34.0
        available = QRectF(
            margin,
            margin,
            max(1.0, self.width() - margin*2),
            max(1.0, self.height() - margin*2),
        )
        ratio = self.logical_w / max(1.0, self.logical_h)
        if available.width() / max(1.0, available.height()) > ratio:
            h = available.height()
            w = h * ratio
        else:
            w = available.width()
            h = w / max(0.001, ratio)
        return QRectF(
            available.center().x() - w/2,
            available.center().y() - h/2,
            w,
            h,
        )

    def _view_scale(self):
        return self._target_rect().width() / max(1.0, self.logical_w)

    @staticmethod
    def _rotate_point(point, center, angle_deg):
        angle = math.radians(angle_deg)
        dx = point.x() - center.x()
        dy = point.y() - center.y()
        c = math.cos(angle)
        sn = math.sin(angle)
        return QPointF(
            center.x() + dx*c - dy*sn,
            center.y() + dx*sn + dy*c,
        )

    def _geometry(self):
        target = self._target_rect()
        if self.effect_pixmap.isNull():
            return target, QPolygonF(), QPointF(), QPointF()

        overlay = self.dialog.overlay
        mode = str(overlay.get("mode", "fit"))
        if mode == "fit":
            w = target.width()
            h = target.height()
            center = QPointF(target.center())
        else:
            factor = self._view_scale()
            ow = max(1.0, float(self.effect_pixmap.width()))
            oh = max(1.0, float(self.effect_pixmap.height()))
            contain = min(self.logical_w/ow, self.logical_h/oh)
            scale = contain * max(0.01, float(overlay.get("scale", 1.0)))
            w = ow * scale * factor
            h = oh * scale * factor
            center = QPointF(
                target.center().x() + float(overlay.get("offset_x", 0.0))*factor,
                target.center().y() + float(overlay.get("offset_y", 0.0))*factor,
            )

        angle = float(overlay.get("rotation", 0.0))
        rect = QRectF(center.x()-w/2, center.y()-h/2, w, h)
        corners = [
            rect.topLeft(),
            rect.topRight(),
            rect.bottomRight(),
            rect.bottomLeft(),
        ]
        poly = QPolygonF([
            self._rotate_point(p, center, angle) for p in corners
        ])

        scale_handle = poly[2] if len(poly) == 4 else QPointF()
        top_mid = QPointF(
            (poly[0].x()+poly[1].x())/2,
            (poly[0].y()+poly[1].y())/2,
        ) if len(poly) == 4 else QPointF()
        # Outward direction from center through the rotated top midpoint.
        vx = top_mid.x() - center.x()
        vy = top_mid.y() - center.y()
        length = max(0.001, math.hypot(vx, vy))
        rotate_handle = QPointF(
            top_mid.x() + vx/length*self.ROTATE_HANDLE_DISTANCE,
            top_mid.y() + vy/length*self.ROTATE_HANDLE_DISTANCE,
        )
        return rect, poly, scale_handle, rotate_handle

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(38, 38, 42))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        target = self._target_rect()
        painter.fillRect(target, QColor(235,235,235))
        if not self.base_pixmap.isNull():
            painter.drawPixmap(target, self.base_pixmap, QRectF(self.base_pixmap.rect()))

        rect, poly, scale_handle, rotate_handle = self._geometry()
        if not self.effect_pixmap.isNull() and len(poly) == 4:
            painter.save()
            center = poly.boundingRect().center()
            # Use the unrotated rect center rather than bounding-box center.
            center = QRectF(rect).center()
            painter.translate(center)
            painter.rotate(float(self.dialog.overlay.get("rotation", 0.0)))
            painter.setOpacity(max(0.0, min(1.0, float(self.dialog.overlay.get("opacity", 1.0)))))
            painter.drawPixmap(
                QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height()),
                self.effect_pixmap,
                QRectF(self.effect_pixmap.rect()),
            )
            painter.restore()

            pen = QPen(QColor("#00c8ff"), 3)
            pen.setCosmetic(True)
            painter.setOpacity(1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(poly)

            top_mid = QPointF(
                (poly[0].x()+poly[1].x())/2,
                (poly[0].y()+poly[1].y())/2,
            )
            painter.drawLine(top_mid, rotate_handle)

            painter.setBrush(QColor("#00c8ff"))
            painter.setPen(QPen(QColor("white"), 2))
            painter.drawEllipse(scale_handle, self.HANDLE_RADIUS, self.HANDLE_RADIUS)

            painter.setBrush(QColor("#ff9f1a"))
            painter.drawEllipse(rotate_handle, self.HANDLE_RADIUS, self.HANDLE_RADIUS)

        painter.setOpacity(1.0)
        painter.setPen(QColor(220,220,220))
        painter.drawText(
            QRectF(8, self.height()-24, self.width()-16, 18),
            Qt.AlignCenter,
            "ドラッグ=移動　青丸=縦横比を保って拡大縮小　橙丸=回転"
        )
        painter.end()

    def _distance(self, a, b):
        return math.hypot(a.x()-b.x(), a.y()-b.y())

    def _ensure_free_mode(self):
        if self.dialog.mode_combo.currentData() != "free":
            idx = self.dialog.mode_combo.findData("free")
            if idx >= 0:
                self.dialog.mode_combo.setCurrentIndex(idx)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.effect_pixmap.isNull():
            return super().mousePressEvent(event)

        pos = QPointF(event.position())
        rect, poly, scale_handle, rotate_handle = self._geometry()
        center = QRectF(rect).center()

        if self._distance(pos, rotate_handle) <= self.HANDLE_RADIUS*1.8:
            self.drag_mode = "rotate"
            self.drag_start = pos
            self.setCursor(Qt.CrossCursor)
            event.accept()
            return

        if self._distance(pos, scale_handle) <= self.HANDLE_RADIUS*1.8:
            self._ensure_free_mode()
            rect, poly, scale_handle, rotate_handle = self._geometry()
            center = QRectF(rect).center()
            self.drag_mode = "scale"
            self.setCursor(Qt.SizeFDiagCursor)
            self.start_scale = float(self.dialog.overlay.get("scale", 1.0))
            self.start_distance = max(1.0, self._distance(pos, center))
            event.accept()
            return

        if len(poly) == 4 and poly.containsPoint(pos, Qt.OddEvenFill):
            self._ensure_free_mode()
            self.drag_mode = "move"
            self.setCursor(Qt.ClosedHandCursor)
            self.drag_start = pos
            self.start_offset = QPointF(
                float(self.dialog.overlay.get("offset_x", 0.0)),
                float(self.dialog.overlay.get("offset_y", 0.0)),
            )
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.drag_mode:
            return super().mouseMoveEvent(event)

        pos = QPointF(event.position())
        rect, poly, _scale_handle, _rotate_handle = self._geometry()
        center = QRectF(rect).center()

        if self.drag_mode == "move":
            factor = max(0.0001, self._view_scale())
            delta = pos - self.drag_start
            self.dialog.set_from_preview(
                offset_x=self.start_offset.x() + delta.x()/factor,
                offset_y=self.start_offset.y() + delta.y()/factor,
            )

        elif self.drag_mode == "scale":
            current = max(1.0, self._distance(pos, center))
            ratio = current / max(1.0, self.start_distance)
            self.dialog.set_from_preview(
                scale=max(0.05, min(20.0, self.start_scale * ratio))
            )

        elif self.drag_mode == "rotate":
            dx = pos.x() - center.x()
            dy = pos.y() - center.y()
            angle = math.degrees(math.atan2(dy, dx)) + 90.0
            while angle > 180.0:
                angle -= 360.0
            while angle <= -180.0:
                angle += 360.0
            self.dialog.set_from_preview(rotation=angle)

        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.drag_mode:
            self.drag_mode = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)



class EffectOverlayPropertiesDialog(QDialog):
    """Interactive editor for one Effect-folder overlay."""
    def __init__(
        self,
        parent,
        overlay,
        on_change=None,
        title="素材エフェクト調整",
        preview_pixmap=None,
        logical_size=None,
    ):
        super().__init__(parent)
        self.overlay = overlay
        self.original = json.loads(json.dumps(overlay))
        self.on_change = on_change
        self.setWindowTitle(title)
        self.setMinimumSize(620, 620)

        # Backward compatibility for overlays saved before rotation existed.
        self.overlay.setdefault("mode", "fit")
        self.overlay.setdefault("scale", 1.0)
        self.overlay.setdefault("offset_x", 0.0)
        self.overlay.setdefault("offset_y", 0.0)
        self.overlay.setdefault("opacity", 1.0)
        self.overlay.setdefault("rotation", 0.0)

        root = QVBoxLayout(self)
        path = resolve_effect_path(overlay.get("path", ""))
        name = path.name if path.name else str(overlay.get("path", ""))
        label = QLabel(f"素材: {name}")
        label.setWordWrap(True)
        root.addWidget(label)

        self.preview = EffectOverlayPreview(
            self,
            base_pixmap=preview_pixmap,
            logical_size=logical_size,
        )
        root.addWidget(self.preview, 1)

        form = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("全面フィット（現在方式）", "fit")
        self.mode_combo.addItem("自由配置", "free")

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.05, 20.0)
        self.scale_spin.setDecimals(3)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setSuffix(" ×")

        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(-20000, 20000)
        self.x_spin.setDecimals(1)
        self.x_spin.setSuffix(" px")

        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(-20000, 20000)
        self.y_spin.setDecimals(1)
        self.y_spin.setSuffix(" px")

        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(-180.0, 180.0)
        self.rotation_spin.setDecimals(1)
        self.rotation_spin.setSingleStep(1.0)
        self.rotation_spin.setSuffix("°")

        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setSuffix(" %")

        mode = str(overlay.get("mode", "fit"))
        idx = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(max(0, idx))
        self.scale_spin.setValue(float(overlay.get("scale", 1.0)))
        self.x_spin.setValue(float(overlay.get("offset_x", 0.0)))
        self.y_spin.setValue(float(overlay.get("offset_y", 0.0)))
        self.rotation_spin.setValue(float(overlay.get("rotation", 0.0)))
        self.opacity_spin.setValue(int(round(float(overlay.get("opacity", 1.0)) * 100)))

        form.addRow("配置方式", self.mode_combo)
        form.addRow("倍率", self.scale_spin)
        form.addRow("横移動", self.x_spin)
        form.addRow("縦移動", self.y_spin)
        form.addRow("回転", self.rotation_spin)
        form.addRow("不透明度", self.opacity_spin)
        root.addLayout(form)

        hint = QLabel(
            "プレビュー上で素材を直接ドラッグできます。\n"
            "青丸をドラッグ = 縦横比を保った拡大縮小 / 橙丸 = 回転。\n"
            "全面フィット中に移動・拡大縮小すると自動で自由配置へ切り替わります。"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        reset_btn = QPushButton("自由配置を中央・100%・回転0°へ戻す")
        reset_btn.clicked.connect(self.reset_free)
        root.addWidget(reset_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.mode_combo.currentIndexChanged.connect(self.apply_live)
        self.mode_combo.currentIndexChanged.connect(self.update_enabled)
        self.scale_spin.valueChanged.connect(self.apply_live)
        self.x_spin.valueChanged.connect(self.apply_live)
        self.y_spin.valueChanged.connect(self.apply_live)
        self.rotation_spin.valueChanged.connect(self.apply_live)
        self.opacity_spin.valueChanged.connect(self.apply_live)
        self.update_enabled()

    def update_enabled(self, *_args):
        free = self.mode_combo.currentData() == "free"
        self.scale_spin.setEnabled(free)
        self.x_spin.setEnabled(free)
        self.y_spin.setEnabled(free)

    def reset_free(self):
        self.mode_combo.setCurrentIndex(self.mode_combo.findData("free"))
        self.set_from_preview(
            scale=1.0,
            offset_x=0.0,
            offset_y=0.0,
            rotation=0.0,
        )

    def set_from_preview(
        self,
        scale=None,
        offset_x=None,
        offset_y=None,
        rotation=None,
    ):
        controls = [
            self.scale_spin,
            self.x_spin,
            self.y_spin,
            self.rotation_spin,
        ]
        for control in controls:
            control.blockSignals(True)
        try:
            if scale is not None:
                self.scale_spin.setValue(float(scale))
            if offset_x is not None:
                self.x_spin.setValue(float(offset_x))
            if offset_y is not None:
                self.y_spin.setValue(float(offset_y))
            if rotation is not None:
                self.rotation_spin.setValue(float(rotation))
        finally:
            for control in controls:
                control.blockSignals(False)
        self.apply_live()

    def apply_live(self, *_args):
        self.overlay["mode"] = self.mode_combo.currentData()
        self.overlay["scale"] = float(self.scale_spin.value())
        self.overlay["offset_x"] = float(self.x_spin.value())
        self.overlay["offset_y"] = float(self.y_spin.value())
        self.overlay["rotation"] = float(self.rotation_spin.value())
        self.overlay["opacity"] = self.opacity_spin.value() / 100.0
        if self.on_change:
            self.on_change()
        if hasattr(self, "preview"):
            self.preview.update()
        QApplication.processEvents()

    def reject(self):
        self.overlay.clear()
        self.overlay.update(json.loads(json.dumps(self.original)))
        if self.on_change:
            self.on_change()
        super().reject()


class PageSetupDialog(QDialog):
    PRESETS = [
        ("現在の標準 1200 × 1697", 1200, 1697),
        ("A4 150dpi 1240 × 1754", 1240, 1754),
        ("A4 300dpi 2480 × 3508", 2480, 3508),
        ("縦長SNS 1080 × 1920", 1080, 1920),
        ("正方形 1080 × 1080", 1080, 1080),
        ("カスタム", None, None),
    ]

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setWindowTitle("ページサイズ設定")
        self.setMinimumWidth(430)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.preset = QComboBox()
        for label, w, h in self.PRESETS:
            self.preset.addItem(label, (w, h))
        self.width_spin = QSpinBox(); self.width_spin.setRange(320, 8000); self.width_spin.setValue(editor.canvas_w); self.width_spin.setSuffix(" px")
        self.height_spin = QSpinBox(); self.height_spin.setRange(320, 12000); self.height_spin.setValue(editor.canvas_h); self.height_spin.setSuffix(" px")
        self.scale_check = QCheckBox("既存ページの内容も比率に合わせて拡縮")
        form.addRow("プリセット", self.preset)
        form.addRow("幅", self.width_spin)
        form.addRow("高さ", self.height_spin)
        root.addLayout(form)
        root.addWidget(self.scale_check)
        root.addWidget(QLabel("編集キャンバスは常に1ページ単位です。\n見開きは F5 の完成プレビューで確認します。"))
        buttons = QHBoxLayout()
        swap = QPushButton("縦横を入れ替え")
        ok = QPushButton("適用")
        cancel = QPushButton("キャンセル")
        buttons.addWidget(swap); buttons.addStretch(1); buttons.addWidget(ok); buttons.addWidget(cancel)
        root.addLayout(buttons)
        self.preset.currentIndexChanged.connect(self.apply_preset)
        swap.clicked.connect(self.swap_size)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def apply_preset(self):
        data = self.preset.currentData()
        if data and data[0] and data[1]:
            self.width_spin.setValue(int(data[0])); self.height_spin.setValue(int(data[1]))

    def swap_size(self):
        w, h = self.width_spin.value(), self.height_spin.value()
        self.width_spin.setValue(h); self.height_spin.setValue(w)

    def values(self):
        return self.width_spin.value(), self.height_spin.value(), self.scale_check.isChecked()


class PanelDetailPreview(QWidget):
    """Large single-panel preview. Drag image to pan; mouse wheel to zoom."""
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.dragging = False
        self.last_pos = QPointF()
        self.setMinimumSize(560, 520)
        self.setCursor(Qt.OpenHandCursor)

    def mapping(self):
        panel = self.dialog.working_panel
        r = panel.panel_path().boundingRect()
        margin = 35.0
        ww = max(1.0, self.width() - margin*2)
        wh = max(1.0, self.height() - margin*2)
        scale = min(ww/max(1.0, r.width()), wh/max(1.0, r.height()))
        ox = (self.width() - r.width()*scale)/2 - r.left()*scale
        oy = (self.height() - r.height()*scale)/2 - r.top()*scale
        return scale, ox, oy

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(45,45,48))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        scale, ox, oy = self.mapping()
        painter.save()
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        self.dialog.working_panel.paint(painter, QStyleOptionGraphicsItem(), None)
        painter.restore()
        painter.setPen(QColor(235,235,235))
        painter.setFont(QFont("Yu Gothic UI", 10))
        painter.drawText(
            QRectF(10, self.height()-30, self.width()-20, 20),
            Qt.AlignCenter,
            "ドラッグ = コマ画像の表示位置移動　｜　ホイール = コマ画像ズーム"
        )
        painter.end()

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and not self.dialog.working_panel.image_pixmap.isNull()
        ):
            self.dragging = True
            self.last_pos = QPointF(event.position())
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging:
            cur = QPointF(event.position())
            delta = cur - self.last_pos
            self.last_pos = cur
            scale, _ox, _oy = self.mapping()
            if scale > 0:
                self.dialog.working_panel.image_offset += QPointF(
                    delta.x()/scale, delta.y()/scale
                )
                self.update()
                self.dialog.update_status()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragging:
            self.dragging = False
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        panel = self.dialog.working_panel
        if panel.image_pixmap.isNull():
            return super().wheelEvent(event)
        factor = 1.10 if event.angleDelta().y() > 0 else 1/1.10
        panel.image_zoom = max(0.10, min(10.0, panel.image_zoom * factor))
        self.update()
        self.dialog.update_status()
        event.accept()


class PanelDetailDialog(QDialog):
    """Edit the contents of one panel without fighting the page-level canvas."""
    def __init__(self, editor, panel):
        super().__init__(editor)
        self.editor = editor
        self.source_panel = panel
        self.text_color = QColor("#000000")
        self.setWindowTitle("コマ詳細編集")
        self.resize(1120, 740)

        r = QRectF(panel.rect())
        self.working_panel = PanelItem(
            r,
            panel.image_path,
            panel.image_zoom,
            [panel.image_offset.x(), panel.image_offset.y()],
            panel.skew,
            [[p.x(), p.y()] for p in panel.custom_points] if panel.custom_points is not None else None,
            json.loads(json.dumps(panel.image_effects)),
            json.loads(json.dumps(panel.panel_texts)),
            json.loads(json.dumps(panel.panel_overlays)),
        )
        self._local_effect_history = [
            self._snapshot_local_effects()
        ]
        self._local_effect_index = 0

        root = QHBoxLayout(self)
        self.preview = PanelDetailPreview(self)
        root.addWidget(self.preview, 1)

        side = QWidget()
        side.setMinimumWidth(375)
        lay = QVBoxLayout(side)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("background:#eeeeee; padding:6px;")
        lay.addWidget(self.status)

        self.detail_tabs = QTabWidget()
        lay.addWidget(self.detail_tabs, 1)

        # Tab 1: Panel image / material library
        image_tab = QWidget()
        image_lay = QVBoxLayout(image_tab)
        top = QHBoxLayout()
        choose = QPushButton("ファイルから選択")
        choose.clicked.connect(self.choose_file)
        reset = QPushButton("位置/ズームをリセット")
        reset.clicked.connect(self.reset_view)
        top.addWidget(choose)
        top.addWidget(reset)
        image_lay.addLayout(top)
        image_lay.addWidget(QLabel("素材ライブラリ"))
        self.material_list = QListWidget()
        self.material_list.setIconSize(QSize(48,48))
        self.material_list.itemDoubleClicked.connect(self.use_material)
        image_lay.addWidget(self.material_list, 1)
        use_mat = QPushButton("選択素材をこのコマに使う")
        use_mat.clicked.connect(self.use_material)
        image_lay.addWidget(use_mat)
        image_lay.addStretch(1)
        self.detail_tabs.addTab(image_tab, "コマ画像")

        # Tab 2: Images already used in this project
        project_tab = QWidget()
        project_lay = QVBoxLayout(project_tab)
        project_lay.addWidget(QLabel("作品内画像（このページ / 前後ページ / 全ページ）"))
        self.project_page_combo = QComboBox()
        self.project_page_combo.currentIndexChanged.connect(self.populate_project_images)
        project_lay.addWidget(self.project_page_combo)
        self.project_image_list = QListWidget()
        self.project_image_list.setIconSize(QSize(64,64))
        self.project_image_list.itemDoubleClicked.connect(lambda _item: self.use_project_image())
        project_lay.addWidget(self.project_image_list, 1)
        use_project = QPushButton("選択した作品内画像をこのコマに使う")
        use_project.clicked.connect(self.use_project_image)
        project_lay.addWidget(use_project)
        self.detail_tabs.addTab(project_tab, "作品内画像")

        # Tab 3: image correction + Effect-folder overlays
        effect_tab = QWidget()
        effect_lay = QVBoxLayout(effect_tab)

        local_history_row = QHBoxLayout()
        self.local_effect_undo_btn = QPushButton("効果Undo")
        self.local_effect_redo_btn = QPushButton("効果Redo")
        self.local_effect_undo_btn.clicked.connect(self.local_effect_undo)
        self.local_effect_redo_btn.clicked.connect(self.local_effect_redo)
        local_history_row.addWidget(self.local_effect_undo_btn)
        local_history_row.addWidget(self.local_effect_redo_btn)
        effect_lay.addLayout(local_history_row)

        for defs in [
            [("白黒","grayscale"),("セピア","sepia"),("ぼかし","blur"),("シャープ","sharpen")],
            [("明るく","brightness_up"),("暗く","brightness_down"),("コントラスト+","contrast_up"),("全解除","reset")],
        ]:
            row = QHBoxLayout()
            for label, key in defs:
                b = QPushButton(label)
                b.clicked.connect(lambda checked=False, k=key: self.apply_effect(k))
                row.addWidget(b)
            effect_lay.addLayout(row)

        effect_lay.addWidget(QLabel("Effectフォルダの画像エフェクト"))
        effect_asset_row = QHBoxLayout()
        self.effect_category = QComboBox()
        effect_reload = QPushButton("再読込")
        effect_reload.clicked.connect(self.refresh_effect_assets)
        effect_asset_row.addWidget(self.effect_category, 1)
        effect_asset_row.addWidget(effect_reload)
        effect_lay.addLayout(effect_asset_row)

        self.effect_asset_list = QListWidget()
        self.effect_asset_list.setIconSize(QSize(64,64))
        self.effect_asset_list.itemDoubleClicked.connect(lambda _item: self.apply_effect_asset())
        effect_lay.addWidget(self.effect_asset_list, 1)

        effect_apply = QPushButton("選択エフェクト素材をコマへ")
        effect_apply.clicked.connect(self.apply_effect_asset)
        effect_lay.addWidget(effect_apply)

        effect_lay.addWidget(QLabel("適用中の素材エフェクト"))
        self.applied_overlay_list = QListWidget()
        self.applied_overlay_list.itemDoubleClicked.connect(lambda _item: self.edit_applied_overlay())
        effect_lay.addWidget(self.applied_overlay_list)

        applied_btns = QHBoxLayout()
        edit_overlay = QPushButton("調整…")
        edit_overlay.clicked.connect(self.edit_applied_overlay)
        remove_overlay = QPushButton("選択解除")
        remove_overlay.clicked.connect(self.remove_applied_overlay)
        applied_btns.addWidget(edit_overlay)
        applied_btns.addWidget(remove_overlay)
        effect_lay.addLayout(applied_btns)

        clear_overlay = QPushButton("素材エフェクトを全解除")
        clear_overlay.clicked.connect(self.clear_effect_assets)
        effect_lay.addWidget(clear_overlay)
        self.effect_category.currentIndexChanged.connect(self.populate_effect_assets)
        self.detail_tabs.addTab(effect_tab, "エフェクト")

        # コマ内文字は一旦廃止。旧データは互換性のため内部保持のみ。

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)        lay.addWidget(box)
        root.addWidget(side)

        self.refresh_materials()
        self.refresh_project_images()
        self.refresh_effect_assets()
        self.refresh_applied_overlays()
        self.update_status()
        self._update_local_effect_buttons()

    def _snapshot_local_effects(self):
        return {
            "image_effects": json.loads(
                json.dumps(self.working_panel.image_effects)
            ),
            "panel_overlays": json.loads(
                json.dumps(self.working_panel.panel_overlays)
            ),
        }

    def _restore_local_effects(self, snapshot):
        self.working_panel.image_effects = json.loads(
            json.dumps(snapshot.get("image_effects", []))
        )
        self.working_panel.panel_overlays = json.loads(
            json.dumps(snapshot.get("panel_overlays", []))
        )
        self.working_panel.rebuild_image_effects()
        self.working_panel.update()
        self.refresh_applied_overlays()
        self.preview.update()
        self.update_status()

    def _commit_local_effect_history(self):
        snapshot = self._snapshot_local_effects()
        current = self._local_effect_history[self._local_effect_index]
        if snapshot == current:
            self._update_local_effect_buttons()
            return False

        self._local_effect_history = self._local_effect_history[
            :self._local_effect_index + 1
        ]
        self._local_effect_history.append(snapshot)
        if len(self._local_effect_history) > 40:
            self._local_effect_history.pop(0)
        self._local_effect_index = len(self._local_effect_history) - 1
        self._update_local_effect_buttons()
        return True

    def _update_local_effect_buttons(self):
        if not hasattr(self, "local_effect_undo_btn"):
            return
        self.local_effect_undo_btn.setEnabled(
            self._local_effect_index > 0
        )
        self.local_effect_redo_btn.setEnabled(
            self._local_effect_index
            < len(self._local_effect_history) - 1
        )

    def local_effect_undo(self):
        if self._local_effect_index <= 0:
            return
        self._local_effect_index -= 1
        self._restore_local_effects(
            self._local_effect_history[self._local_effect_index]
        )
        self._update_local_effect_buttons()

    def local_effect_redo(self):
        if (
            self._local_effect_index
            >= len(self._local_effect_history) - 1
        ):
            return
        self._local_effect_index += 1
        self._restore_local_effects(
            self._local_effect_history[self._local_effect_index]
        )
        self._update_local_effect_buttons()

    def refresh_materials(self):
        self.material_list.clear()
        for mat in self.editor.materials:
            path = mat.get("path", "")
            name = mat.get("name") or Path(path).stem
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, path)
            if path and Path(path).exists():
                item.setIcon(QIcon(path))
            else:
                item.setText(name + " [見つかりません]")
            self.material_list.addItem(item)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "コマ画像を選択", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if path and self.working_panel.set_image(path):
            self.preview.update()
            self.update_status()

    def use_material(self, item=None):
        if not isinstance(item, QListWidgetItem):
            item = self.material_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        if path and Path(path).exists():
            if self.working_panel.set_image(path):
                self.preview.update()
                self.update_status()
        elif path:
            QMessageBox.warning(self, "素材", "素材ファイルが見つかりません。")

    def refresh_project_images(self):
        self._project_sources = self.editor.project_image_sources()
        self.project_page_combo.blockSignals(True)
        self.project_page_combo.clear()
        self.project_page_combo.addItem("全ページ", -1)
        for idx, page in enumerate(self.editor.pages):
            name = page.get("name", f"ページ {idx+1}")
            self.project_page_combo.addItem(f"{idx+1}: {name}", idx)
        current_combo = max(0, self.editor.current_page_index + 1)
        self.project_page_combo.setCurrentIndex(
            min(current_combo, self.project_page_combo.count()-1)
        )
        self.project_page_combo.blockSignals(False)
        self.populate_project_images()

    def populate_project_images(self, *_args):
        self.project_image_list.clear()
        page_filter = self.project_page_combo.currentData()
        for source in getattr(self, "_project_sources", []):
            if page_filter != -1 and source["page_index"] != page_filter:
                continue
            item = QListWidgetItem(
                f"P{source['page_index']+1}  {source['kind']}: {source['name']}"
            )
            item.setData(Qt.UserRole, source["path"])
            item.setToolTip(source["path"])
            item.setIcon(QIcon(source["path"]))
            self.project_image_list.addItem(item)

    def use_project_image(self):
        item = self.project_image_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        if path and Path(path).exists():
            if self.working_panel.set_image(path):
                self.preview.update()
                self.update_status()
        else:
            QMessageBox.warning(self, "作品内画像", "元画像ファイルが見つかりません。")

    def reset_view(self):
        self.working_panel.reset_image_view()
        self.preview.update()
        self.update_status()

    def apply_effect(self, key):
        p = self.working_panel
        if key == "reset":
            p.restore_image_effects()
        elif key == "brightness_up":
            p.add_image_simple_effect("brightness", factor=1.18)
        elif key == "brightness_down":
            p.add_image_simple_effect("brightness", factor=0.82)
        elif key == "contrast_up":
            p.add_image_simple_effect("contrast", factor=1.20)
        else:
            p.add_image_simple_effect(key)
        self.preview.update()
        self.update_status()
        self._commit_local_effect_history()

    def refresh_effect_assets(self):
        self._effect_assets = self.editor.scan_effect_assets()
        categories = sorted({a["category"] for a in self._effect_assets})
        self.effect_category.blockSignals(True)
        current = self.effect_category.currentText()
        self.effect_category.clear()
        for category in categories:
            self.effect_category.addItem(category)
        if current and current in categories:
            self.effect_category.setCurrentText(current)
        self.effect_category.blockSignals(False)
        self.populate_effect_assets()

    def populate_effect_assets(self, *_args):
        self.effect_asset_list.clear()
        category = self.effect_category.currentText()
        for asset in getattr(self, "_effect_assets", []):
            if asset["category"] != category:
                continue
            item = QListWidgetItem(asset["name"])
            item.setData(Qt.UserRole, asset["path"])
            item.setToolTip(asset["relative"])
            item.setIcon(QIcon(asset["path"]))
            self.effect_asset_list.addItem(item)

    def apply_effect_asset(self):
        item = self.effect_asset_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        if self.working_panel.add_panel_overlay(path):
            self.refresh_applied_overlays()
            self.applied_overlay_list.setCurrentRow(
                max(0, len(self.working_panel.panel_overlays)-1)
            )
            self.preview.update()
            self.update_status()
            self._commit_local_effect_history()

    def refresh_applied_overlays(self):
        self.applied_overlay_list.clear()
        for idx, overlay in enumerate(self.working_panel.panel_overlays):
            path = resolve_effect_path(overlay.get("path", ""))
            mode = "全面" if overlay.get("mode", "fit") == "fit" else "自由"
            self.applied_overlay_list.addItem(
                f"{idx+1}. [{mode}] {path.name or overlay.get('path','')}"
            )

    def edit_applied_overlay(self):
        row = self.applied_overlay_list.currentRow()
        if row < 0 or row >= len(self.working_panel.panel_overlays):
            return
        overlay = self.working_panel.panel_overlays[row]

        def changed():
            self.preview.update()
            self.refresh_applied_overlays()
            self.applied_overlay_list.setCurrentRow(row)
            self.update_status()

        bounds = self.working_panel.panel_path().boundingRect()
        result = EffectOverlayPropertiesDialog(
            self,
            overlay,
            changed,
            "コマ素材エフェクト調整",
            preview_pixmap=self.working_panel.image_pixmap,
            logical_size=(bounds.width(), bounds.height()),
        ).exec()
        if result:
            self._commit_local_effect_history()

    def remove_applied_overlay(self):
        row = self.applied_overlay_list.currentRow()
        if row < 0 or row >= len(self.working_panel.panel_overlays):
            return
        self.working_panel.panel_overlays.pop(row)
        self.refresh_applied_overlays()
        self.preview.update()
        self.update_status()
        self._commit_local_effect_history()

    def clear_effect_assets(self):
        if self.working_panel.clear_panel_overlays():
            self.refresh_applied_overlays()
            self.preview.update()
            self.update_status()
            self._commit_local_effect_history()

    def refresh_text_list(self):
        old = self.text_list.currentRow()
        self.text_list.blockSignals(True)
        self.text_list.clear()
        for idx, entry in enumerate(self.working_panel.panel_texts):
            txt = str(entry.get("text","")).replace("\\n"," ")
            self.text_list.addItem(f"{idx+1}. {txt[:30]}")
        self.text_list.blockSignals(False)
        if self.working_panel.panel_texts:
            self.text_list.setCurrentRow(max(0, min(old, len(self.working_panel.panel_texts)-1)))

    def add_panel_text(self):
        text, ok = QInputDialog.getMultiLineText(self, "コマ内文字", "文字:", "テキスト")
        if not ok or not text.strip():
            return
        family = (
            self.editor.font_combo.currentFont().family()
            if hasattr(self.editor, "font_combo") else "Yu Gothic UI"
        )
        self.working_panel.panel_texts.append({
            "text": text,
            "x": 0.5,
            "y": 0.5,
            "font_family": family,
            "font_size": 32,
            "bold": False,
            "color": "#000000",
            "opacity": 1.0,
        })
        self.refresh_text_list()
        self.text_list.setCurrentRow(len(self.working_panel.panel_texts)-1)
        self.preview.update()
        self.update_status()

    def edit_panel_text(self):
        row = self.text_list.currentRow()
        if row < 0 or row >= len(self.working_panel.panel_texts):
            return
        entry = self.working_panel.panel_texts[row]
        text, ok = QInputDialog.getMultiLineText(
            self, "文章編集", "文字:", str(entry.get("text",""))
        )
        if ok:
            entry["text"] = text
            self.refresh_text_list()
            self.text_list.setCurrentRow(row)
            self.preview.update()

    def delete_panel_text(self):
        row = self.text_list.currentRow()
        if row < 0 or row >= len(self.working_panel.panel_texts):
            return
        self.working_panel.panel_texts.pop(row)
        self.refresh_text_list()
        self.preview.update()
        self.update_status()

    def load_text_controls(self, row):
        if row < 0 or row >= len(self.working_panel.panel_texts):
            return
        e = self.working_panel.panel_texts[row]
        widgets = [self.font_combo, self.font_size, self.bold, self.text_x, self.text_y, self.text_opacity]
        for w in widgets:
            w.blockSignals(True)
        self.font_combo.setCurrentFont(QFont(e.get("font_family", "Yu Gothic UI")))
        self.font_size.setValue(int(e.get("font_size", 32)))
        self.bold.setChecked(bool(e.get("bold", False)))
        self.text_x.setValue(int(round(float(e.get("x",0.5))*100)))
        self.text_y.setValue(int(round(float(e.get("y",0.5))*100)))
        self.text_opacity.setValue(int(round(float(e.get("opacity",1.0))*100)))
        self.text_color = QColor(e.get("color", "#000000"))
        for w in widgets:
            w.blockSignals(False)

    def apply_text_controls(self, *args):
        row = self.text_list.currentRow()
        if row < 0 or row >= len(self.working_panel.panel_texts):
            return
        e = self.working_panel.panel_texts[row]
        e["font_family"] = self.font_combo.currentFont().family()
        e["font_size"] = self.font_size.value()
        e["bold"] = self.bold.isChecked()
        e["x"] = self.text_x.value()/100.0
        e["y"] = self.text_y.value()/100.0
        e["opacity"] = self.text_opacity.value()/100.0
        e["color"] = self.text_color.name()
        self.preview.update()

    def choose_text_color(self):
        c = QColorDialog.getColor(self.text_color, self, "文字色")
        if c.isValid():
            self.text_color = c
            self.apply_text_controls()

    def update_status(self):
        p = self.working_panel
        name = Path(p.image_path).name if p.image_path else "画像なし"
        self.status.setText(
            f"画像: {name}\\n"
            f"ズーム: {p.image_zoom:.2f}x / オフセット: "
            f"({p.image_offset.x():.0f}, {p.image_offset.y():.0f})\\n"
            f"画像補正: {len(p.image_effects)} / 素材エフェクト: {len(p.panel_overlays)}"
        )

    def accept(self):
        src = self.source_panel
        work = self.working_panel
        effect_before = self.editor._capture_effect_state([src])
        src.image_path = work.image_path
        src.original_image_pixmap = QPixmap(work.original_image_pixmap)
        src.image_pixmap = QPixmap(work.image_pixmap)
        src.image_effects = json.loads(json.dumps(work.image_effects))
        src.panel_overlays = json.loads(json.dumps(work.panel_overlays))
        src.image_zoom = work.image_zoom
        src.image_offset = QPointF(work.image_offset)
        src.panel_texts = json.loads(json.dumps(work.panel_texts))
        src.update()

        effect_after = self.editor._capture_effect_state([src])
        self.editor._push_effect_history(
            effect_before,
            effect_after,
            "コマ詳細エフェクト編集",
        )
        self.editor.record_history()
        super().accept()


class PagePreviewDialog(QDialog):
    """Finished-page preview with page turning and horizontal N-page viewing."""
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.page_index = max(0, editor.current_page_index)
        self.setWindowTitle("完成図プレビュー")
        self.resize(1180, 820)

        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.prev_btn = QPushButton("◀ 前へ")
        self.next_btn = QPushButton("次へ ▶")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("1ページ", 1)
        self.mode_combo.addItem("見開き 2ページ", 2)
        self.mode_combo.addItem("Nページ横並び", -1)
        self.n_spin = QSpinBox(); self.n_spin.setRange(1, 12); self.n_spin.setValue(4); self.n_spin.setPrefix("N = ")
        self.info = QLabel()
        self.jump_btn = QPushButton("このページを編集")
        self.close_btn = QPushButton("閉じる")
        controls.addWidget(self.prev_btn); controls.addWidget(self.next_btn); controls.addSpacing(12)
        controls.addWidget(QLabel("表示:")); controls.addWidget(self.mode_combo); controls.addWidget(self.n_spin)
        controls.addStretch(1); controls.addWidget(self.info); controls.addWidget(self.jump_btn); controls.addWidget(self.close_btn)
        root.addLayout(controls)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setAlignment(Qt.AlignCenter)
        self.preview_host = QWidget(); self.preview_lay = QHBoxLayout(self.preview_host)
        self.preview_lay.setAlignment(Qt.AlignCenter); self.preview_lay.setSpacing(24)
        self.scroll.setWidget(self.preview_host); root.addWidget(self.scroll, 1)

        self.prev_btn.clicked.connect(self.prev_pages); self.next_btn.clicked.connect(self.next_pages)
        self.mode_combo.currentIndexChanged.connect(self.refresh); self.n_spin.valueChanged.connect(self.refresh)
        self.jump_btn.clicked.connect(self.jump_to_page); self.close_btn.clicked.connect(self.close)
        self.refresh()

    def page_count(self):
        value = int(self.mode_combo.currentData())
        return self.n_spin.value() if value < 0 else value

    def _clear_preview(self):
        while self.preview_lay.count():
            item = self.preview_lay.takeAt(0)
            widget = item.widget()
            if widget: widget.deleteLater()

    def refresh(self):
        self._clear_preview()
        total = len(self.editor.pages)
        if total <= 0:
            self.info.setText("ページなし"); return
        count = max(1, self.page_count())
        self.page_index = max(0, min(self.page_index, total - 1))
        end = min(total, self.page_index + count)
        visible = max(1, end - self.page_index)
        viewport_w = max(500, self.scroll.viewport().width() - 60)
        viewport_h = max(500, self.scroll.viewport().height() - 70)
        each_w = max(150, int((viewport_w - 24 * (visible - 1)) / visible))
        each_h = max(220, viewport_h - 50)
        cw, ch = self.editor.canvas_w, self.editor.canvas_h
        scale = min(each_w / cw, each_h / ch, 1.0)
        target_w = max(1, int(cw * scale)); target_h = max(1, int(ch * scale))
        for idx in range(self.page_index, end):
            page = self.editor.pages[idx]
            image = self.editor.render_scene_to_image(page["scene"])
            pix = QPixmap.fromImage(image).scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            box = QWidget(); lay = QVBoxLayout(box); lay.setContentsMargins(0,0,0,0)
            label = QLabel(); label.setPixmap(pix); label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("background: #666; border: 1px solid #888;")
            caption = QLabel(f"{idx+1:03d}  {page['name']}"); caption.setAlignment(Qt.AlignCenter)
            lay.addWidget(label,1); lay.addWidget(caption); self.preview_lay.addWidget(box)
        self.info.setText(f"{self.page_index+1}–{end} / {total}")
        self.prev_btn.setEnabled(self.page_index > 0); self.next_btn.setEnabled(end < total)
        self.n_spin.setEnabled(int(self.mode_combo.currentData()) < 0)

    def prev_pages(self):
        self.page_index = max(0, self.page_index - self.page_count()); self.refresh()

    def next_pages(self):
        total = len(self.editor.pages)
        if total: self.page_index = min(total - 1, self.page_index + self.page_count())
        self.refresh()

    def jump_to_page(self):
        if self.editor.pages: self.editor.page_list.setCurrentRow(self.page_index)
        self.close()


class MangaEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Manga Peta Editor v{APP_VERSION}")
        self.resize(1520, 940)
        self.setMinimumSize(900, 560)
        self.setAcceptDrops(True)
        try:
            EFFECT_DIR.mkdir(parents=True, exist_ok=True)
            PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.canvas_w = DEFAULT_CANVAS_W
        self.canvas_h = DEFAULT_CANVAS_H
        self.project_path = None
        self.pages = []
        self.current_page_index = -1
        self.suspend_history = False
        self.clipboard_data = []
        self.text_style_clipboard = None
        self.user_settings_path = Path(__file__).with_name("user_settings.json")
        self.favorite_fonts = []
        self.materials = []
        self.active_project_dir = None
        self.active_assets_dir = None
        self._saved_content_signature = None
        self._restarting_for_update = False
        self.effect_history_by_page = {}
        self.recovery_path = Path(tempfile.gettempdir()) / "manga_peta_editor_recovery.mpe.json"
        self._load_user_settings()
        self._build_ui()
        self.new_project(force=True)

        QTimer.singleShot(0, self._fit_window_to_screen)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(90_000)
        self.autosave_timer.timeout.connect(self.auto_save_recovery)
        self.autosave_timer.start()
        QTimer.singleShot(250, self.offer_startup_recovery)

    def _load_user_settings(self):
        try:
            if self.user_settings_path.exists():
                data = json.loads(self.user_settings_path.read_text(encoding="utf-8"))
                favs = data.get("favorite_fonts", [])
                if isinstance(favs, list):
                    self.favorite_fonts = [str(x) for x in favs if str(x).strip()]
                mats = data.get("materials", [])
                if isinstance(mats, list):
                    self.materials = [m for m in mats if isinstance(m, dict) and m.get("path")]
        except Exception:
            self.favorite_fonts = []

    def _save_user_settings(self):
        try:
            self.user_settings_path.write_text(
                json.dumps({"favorite_fonts": self.favorite_fonts, "materials": self.materials}, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            self.statusBar().showMessage(f"お気に入りフォントの保存に失敗: {e}", 4000)

    def _fit_window_to_screen(self):
        """Fit the main window inside the usable screen area."""
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        width = min(1520, max(900, int(geo.width() * 0.96)))
        height = min(940, max(560, int(geo.height() * 0.94)))
        width = min(width, geo.width())
        height = min(height, geo.height())
        x = geo.x() + max(0, (geo.width() - width) // 2)
        y = geo.y() + max(0, (geo.height() - height) // 2)
        self.setGeometry(x, y, width, height)

    def _is_python_update_file(self, path):
        try:
            return Path(path).is_file() and Path(path).suffix.lower() == ".py"
        except Exception:
            return False

    def _validate_update_file(self, path):
        """Validate the update file without executing it."""
        path = Path(path)
        if not self._is_python_update_file(path):
            raise ValueError("Pythonファイル（.py）ではありません。")

        try:
            source = path.read_text(encoding="utf-8-sig")
        except Exception as e:
            raise ValueError(f"更新ファイルを読めません: {e}") from e

        if len(source) < 10_000:
            raise ValueError("更新ファイルが小さすぎます。漫画エディタ本体の.pyではない可能性があります。")

        try:
            tree = ast.parse(source, filename=str(path))
            compile(source, str(path), "exec")
        except SyntaxError as e:
            raise ValueError(
                f"Python構文エラーがあります。\\n{e.msg}  (line {e.lineno})"
            ) from e

        class_names = {
            node.name for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        if "MangaEditor" not in class_names:
            raise ValueError("MangaEditorクラスが無いため更新ファイルとして扱えません。")

        version = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "APP_VERSION"
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        version = node.value.value
                        break
            if version is not None:
                break

        if not version:
            raise ValueError("APP_VERSIONが見つかりません。")

        has_main_function = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "main"
            for node in tree.body
        )
        if not has_main_function:
            raise ValueError("main()が見つからないため更新ファイルとして扱えません。")

        return version

    def handle_update_file(self, path):
        """Replace app.py with a dropped update and restart."""
        source_path = Path(path)
        try:
            new_version = self._validate_update_file(source_path)
        except Exception as e:
            QMessageBox.warning(
                self,
                "アップデート",
                "更新ファイルの検証に失敗しました。\n\n"
                f"{e}\n\n"
                "このメッセージが出た場合は、更新用.pyのバージョンと"
                "エディタ本体のバージョンを一緒に教えてください。"
            )
            return False

        target = Path(__file__).resolve()
        try:
            if source_path.resolve() == target:
                self.statusBar().showMessage("現在のapp.py自身なので更新は不要です", 3500)
                return False
        except Exception:
            pass

        backup_dir = target.parent / "update_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe_current = re.sub(r"[^0-9A-Za-z._-]+", "_", APP_VERSION)
        backup = backup_dir / f"app_{safe_current}_{stamp}.py"
        temp_target = target.with_name(".app_update_pending.py")

        try:
            # Preserve only genuinely unsaved work before update restart.
            if self._has_unsaved_changes():
                self.auto_save_recovery()
            else:
                self.clear_recovery_file()

            shutil.copy2(target, backup)
            shutil.copy2(source_path, temp_target)
            self._validate_update_file(temp_target)
            os.replace(temp_target, target)
        except Exception as e:
            try:
                if temp_target.exists():
                    temp_target.unlink()
            except Exception:
                pass
            QMessageBox.critical(
                self,
                "アップデート失敗",
                f"更新ファイルを適用できませんでした。\\n\\n{e}\\n\\n"
                "現在のapp.pyは変更していません。"
            )
            return False

        self.statusBar().showMessage(
            f"v{new_version} を適用しました。再起動します…", 5000
        )
        QApplication.processEvents()
        QTimer.singleShot(350, self._restart_after_update)
        return True

    def _restart_after_update(self):
        target = Path(__file__).resolve()
        try:
            env = os.environ.copy()
            subprocess.Popen(
                [sys.executable, str(target)] + list(sys.argv[1:]),
                cwd=str(target.parent),
                env=env,
            )
            self._restarting_for_update = True
            QApplication.quit()
        except Exception as e:
            QMessageBox.critical(
                self,
                "再起動失敗",
                "更新自体は適用済みですが、自動再起動に失敗しました。\\n"
                "いったんアプリを閉じて、いつものBATから起動してください。\\n\\n"
                + str(e)
            )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            py_files = [p for p in paths if self._is_python_update_file(p)]
            if len(py_files) == 1:
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            py_files = [p for p in paths if self._is_python_update_file(p)]
            if len(py_files) == 1:
                event.acceptProposedAction()
                self.handle_update_file(py_files[0])
                return
            if len(py_files) > 1:
                QMessageBox.information(
                    self, "アップデート",
                    "更新用.pyは1ファイルだけドラッグしてください。"
                )
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def _refresh_favorite_font_list(self):
        if not hasattr(self, "favorite_font_list"):
            return
        self.favorite_font_list.clear()
        for family in self.favorite_fonts:
            self.favorite_font_list.addItem(family)

    def _refresh_material_list(self):
        if not hasattr(self, "material_list"):
            return
        query = self.material_search.text().strip().lower() if hasattr(self, "material_search") else ""
        self.material_list.clear()
        for mat in self.materials:
            name = mat.get("name") or Path(mat.get("path", "")).name
            path = mat.get("path", "")
            if query and query not in name.lower() and query not in path.lower():
                continue
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, path)
            if Path(path).exists():
                item.setIcon(QIcon(path))
            else:
                item.setText(name + "  [見つかりません]")
            item.setToolTip(path)
            self.material_list.addItem(item)

    def add_material_paths(self, paths):
        existing = {m.get("path") for m in self.materials}
        added = 0
        for path in paths:
            path = str(Path(path))
            if Path(path).suffix.lower() not in IMAGE_EXTS or path in existing:
                continue
            self.materials.append({"name": Path(path).stem, "path": path})
            existing.add(path); added += 1
        if added:
            self._save_user_settings(); self._refresh_material_list()
            self.statusBar().showMessage(f"素材を{added}件登録しました", 2200)

    def register_material_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "素材画像を登録", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if paths:
            self.add_material_paths(paths)

    def register_selected_as_material(self):
        paths = []
        for item in self.selected():
            if isinstance(item, ImageItem) and item.source_path:
                paths.append(item.source_path)
            elif isinstance(item, PanelItem) and item.image_path:
                paths.append(item.image_path)
        if not paths:
            QMessageBox.information(self, "素材登録", "画像または画像入りコマを選択してください。")
            return
        self.add_material_paths(paths)

    def remove_selected_material(self):
        item = self.material_list.currentItem() if hasattr(self, "material_list") else None
        if not item: return
        path = item.data(Qt.UserRole)
        self.materials = [m for m in self.materials if m.get("path") != path]
        self._save_user_settings(); self._refresh_material_list()

    def insert_selected_material(self, item=None):
        row_item = item if hasattr(item, "data") else (self.material_list.currentItem() if hasattr(self, "material_list") else None)
        if not row_item: return
        path = row_item.data(Qt.UserRole)
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "素材", "素材ファイルが見つかりません。")
            return
        self.add_image(path)

    def change_mode(self, index):
        if hasattr(self, "mode_stack"):
            self.mode_stack.setCurrentIndex(index)
        labels = ["選択 / 配置モード", "コマ割りモード", "エフェクトモード", "文字編集モード", "素材ライブラリ", "レイヤーモード"]
        if hasattr(self, "mode_title") and 0 <= index < len(labels):
            self.mode_title.setText(labels[index])
        tips = [
            "空白ドラッグで範囲選択 / 複数選択したまままとめて移動",
            "Nコマ生成・N分割・四隅編集",
            "選択画像へのエフェクト・モザイク",
            "フォント・文字サイズ・お気に入り・スタイルコピー",
            "よく使う画像素材を登録してダブルクリックで配置",
            "前後関係・表示状態・オブジェクト選択を一覧で管理",
        ]
        if 0 <= index < len(tips):
            self.statusBar().showMessage(tips[index], 2500)
        if index == 5:
            self.refresh_layer_list()

    def _text_font(self, item):
        if isinstance(item, EditableTextItem):
            return QFont(item.font())
        if isinstance(item, VerticalTextItem):
            return QFont(item.font())
        if isinstance(item, BubbleItem):
            return QFont(item.text_item.font())
        return None

    def _set_item_font(self, item, font):
        font = QFont(font)
        if isinstance(item, (EditableTextItem, VerticalTextItem)):
            item.setFont(font)
            return True
        if isinstance(item, BubbleItem):
            item.text_item.setFont(font)
            return True
        return False

    def current_text_style(self):
        items = self.selected()
        for item in items:
            font = self._text_font(item)
            if font is None:
                continue
            style = {
                "font_family": font.family(),
                "font_size": font.pointSize(),
                "bold": font.bold(),
            }
            if isinstance(item, EditableTextItem):
                style["color"] = item.defaultTextColor().name(QColor.HexArgb)
                style["outline_color"] = item.outline_color.name(QColor.HexArgb)
                style["outline_width"] = item.outline_width
                style["shadow_enabled"] = item.shadow_enabled
                style["shadow_color"] = item.shadow_color.name(QColor.HexArgb)
                style["shadow_offset"] = [item.shadow_offset.x(), item.shadow_offset.y()]
            elif isinstance(item, VerticalTextItem):
                style["color"] = item.color.name()
                style["outline_color"] = item.outline_color.name()
                style["outline_width"] = item.outline_width
            elif isinstance(item, BubbleItem):
                style["color"] = item.text_item.defaultTextColor().name()
            return style
        return None

    def apply_text_style_to_item(self, item, style):
        if not style:
            return False
        font = self._text_font(item)
        if font is None:
            return False
        family = style.get("font_family")
        if family:
            font.setFamily(family)
        if style.get("font_size"):
            font.setPointSize(int(style["font_size"]))
        font.setBold(bool(style.get("bold", False)))
        self._set_item_font(item, font)
        color = QColor(style.get("color", "#000000"))
        if isinstance(item, EditableTextItem):
            item.setDefaultTextColor(color)
            if "outline_color" in style: item.outline_color = QColor(style["outline_color"])
            if "outline_width" in style: item.outline_width = float(style["outline_width"])
            if "shadow_enabled" in style: item.shadow_enabled = bool(style["shadow_enabled"])
            if "shadow_color" in style: item.shadow_color = QColor(style["shadow_color"])
            if "shadow_offset" in style:
                off = style["shadow_offset"]; item.shadow_offset = QPointF(float(off[0]), float(off[1]))
            item.prepareGeometryChange(); item.update()
        elif isinstance(item, VerticalTextItem):
            item.color = color
            if "outline_color" in style:
                item.outline_color = QColor(style["outline_color"])
            if "outline_width" in style:
                item.outline_width = float(style["outline_width"])
            item.update()
        elif isinstance(item, BubbleItem):
            item.text_item.setDefaultTextColor(color)
        return True

    def ui_text_style(self):
        font = self.font_combo.currentFont() if hasattr(self, "font_combo") else QFont("Yu Gothic UI")
        return {
            "font_family": font.family(),
            "font_size": self.font_spin.value() if hasattr(self, "font_spin") else 32,
            "bold": self.bold_check.isChecked() if hasattr(self, "bold_check") else False,
            "color": "#000000",
            "outline_color": "#ffffff",
            "outline_width": self.outline_spin.value() if hasattr(self, "outline_spin") else 0.0,
            "shadow_enabled": self.shadow_check.isChecked() if hasattr(self, "shadow_check") else False,
            "shadow_color": "#80000000",
            "shadow_offset": [self.shadow_x_spin.value(), self.shadow_y_spin.value()] if hasattr(self, "shadow_x_spin") else [5.0, 5.0],
        }

    def set_selected_font_family(self, font):
        changed = False
        for item in self.selected():
            current = self._text_font(item)
            if current is not None:
                current.setFamily(font.family())
                self._set_item_font(item, current)
                changed = True
        if changed:
            self.statusBar().showMessage(f"フォント: {font.family()}", 1800)

    def add_favorite_font(self):
        family = self.font_combo.currentFont().family()
        if family and family not in self.favorite_fonts:
            self.favorite_fonts.append(family)
            self._refresh_favorite_font_list()
            self._save_user_settings()
            self.statusBar().showMessage(f"★ {family} をお気に入りに追加しました", 2500)

    def remove_favorite_font(self):
        item = self.favorite_font_list.currentItem()
        if not item:
            return
        family = item.text()
        if family in self.favorite_fonts:
            self.favorite_fonts.remove(family)
            self._refresh_favorite_font_list()
            self._save_user_settings()
            self.statusBar().showMessage(f"{family} をお気に入りから外しました", 2500)

    def apply_favorite_font(self, item=None):
        row_item = item if hasattr(item, "text") else self.favorite_font_list.currentItem()
        if not row_item:
            return
        family = row_item.text()
        font = QFont(family, self.font_spin.value())
        self.font_combo.setCurrentFont(font)
        self.set_selected_font_family(font)
        if self.selected():
            self.record_history()

    def copy_font_info_to_clipboard(self):
        style = self.current_text_style()
        if not style:
            QMessageBox.information(self, "フォント情報", "文字・縦書き・吹き出しを1つ選択してください。")
            return
        text = json.dumps(style, ensure_ascii=False, indent=2)
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("フォント情報をクリップボードへコピーしました", 2500)

    def copy_text_style(self):
        style = self.current_text_style()
        if not style:
            QMessageBox.information(self, "文字スタイル", "コピー元の文字・縦書き・吹き出しを1つ選択してください。")
            return
        self.text_style_clipboard = json.loads(json.dumps(style))
        self.statusBar().showMessage("文字スタイルをコピーしました", 2200)

    def paste_text_style(self):
        if not self.text_style_clipboard:
            QMessageBox.information(self, "文字スタイル", "先に文字スタイルをコピーしてください。")
            return
        changed = False
        for item in self.selected():
            changed = self.apply_text_style_to_item(item, self.text_style_clipboard) or changed
        if changed:
            self.record_history()
            self.selection_changed()
            self.statusBar().showMessage("文字スタイルを貼り付けました", 2200)
        else:
            QMessageBox.information(self, "文字スタイル", "貼り付け先の文字・縦書き・吹き出しを選択してください。")

    def _build_ui(self):
        self.toolbar = QToolBar("Main")
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)

        def add_action(text, slot, shortcut=None):
            act = QAction(text, self)
            if shortcut:
                if isinstance(shortcut, (list, tuple)):
                    act.setShortcuts([QKeySequence(x) for x in shortcut])
                else:                    act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            self.toolbar.addAction(act)
            return act

        def add_menu_action(menu, text, slot, shortcut=None):
            act = QAction(text, self)
            if shortcut:
                if isinstance(shortcut, (list, tuple)):
                    act.setShortcuts([QKeySequence(x) for x in shortcut])
                else:
                    act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            menu.addAction(act)
            self.addAction(act)
            return act

        add_action("新規", self.new_project, "Ctrl+N")
        add_action("開く", self.open_project, "Ctrl+O")

        save_button = QToolButton()
        save_button.setText("保存 ▼")
        save_button.setPopupMode(QToolButton.InstantPopup)
        save_menu = QMenu(save_button)
        add_menu_action(save_menu, "上書き保存", self.save_project, "Ctrl+S")
        add_menu_action(save_menu, "名前を付けて保存", self.save_project_as, "Ctrl+Shift+S")
        add_menu_action(save_menu, "日付保存", self.save_project_dated)
        save_menu.addSeparator()
        add_menu_action(save_menu, "素材を内包", self.collect_project_assets)
        save_button.setMenu(save_menu)
        self.toolbar.addWidget(save_button)

        output_button = QToolButton()
        output_button.setText("出力 ▼")
        output_button.setPopupMode(QToolButton.InstantPopup)
        output_menu = QMenu(output_button)
        add_menu_action(output_menu, "現在ページPNG", self.export_current_png)
        add_menu_action(output_menu, "縦長PNG", self.export_long_png)
        add_menu_action(output_menu, "全ページZIP", self.export_all_zip)
        output_menu.addSeparator()
        add_menu_action(output_menu, "完成プレビュー", self.open_page_preview, "F5")
        output_button.setMenu(output_menu)
        self.toolbar.addWidget(output_button)

        add_action("ページ設定", self.open_page_setup)
        add_action("1ページ表示", self.fit_current_page)
        add_action("◀ページ", self.prev_page, "Alt+Left")
        add_action("ページ▶", self.next_page, "Alt+Right")
        self.toolbar.addSeparator()
        add_action("元に戻す", self.undo, "Ctrl+Z")
        add_action("やり直す", self.redo, ["Ctrl+Y", "Ctrl+Shift+Z"])
        self.toolbar.addSeparator()
        # Keep a hidden combo as the internal mode model so existing mode logic
        # stays compatible. The visible mode selector is now a tab bar above
        # the property panel.
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("選択 / 配置", "select")
        self.mode_combo.addItem("コマ割り", "panel")
        self.mode_combo.addItem("エフェクト", "effect")
        self.mode_combo.addItem("文字編集", "text")
        self.mode_combo.addItem("素材", "material")
        self.mode_combo.addItem("レイヤー", "layer")
        self.mode_combo.hide()
        for idx, shortcut in enumerate(["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6"]):
            act = QAction(self)
            act.setShortcut(shortcut)
            act.triggered.connect(lambda checked=False, i=idx: self.mode_combo.setCurrentIndex(i))
            self.addAction(act)
        self.toolbar.addSeparator()
        add_action("画像追加", self.pick_image)
        add_action("画像差替", self.replace_selected_image)
        add_action("プロパティ", self.open_selected_properties)
        add_action("文字", self.add_text)
        add_action("縦書き", self.add_vertical_text)
        add_action("吹き出し", self.add_bubble)
        add_action("範囲モザイク", self.start_region_mosaic)
        self.toolbar.addSeparator()
        add_action("診断情報", self.show_diagnostics)
        add_action("デバッグリスト", self.show_debug_checklist)
        self.toolbar.addSeparator()
        update_hint = QLabel("更新: .pyを画面へD&D")
        update_hint.setToolTip(
            "今後送られてくる更新用.pyをエディタ画面へドラッグすると、自動適用して再起動します"
        )
        update_hint.setStyleSheet(
            "padding: 3px 7px; background: #eaf4ff; border: 1px solid #9ec9f5;"
        )
        self.toolbar.addWidget(update_hint)

        content = QWidget()
        content_lay = QHBoxLayout(content)
        content_lay.setContentsMargins(4, 4, 4, 4)
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(7)
        content_lay.addWidget(self.main_splitter, 1)

        left = QWidget()
        left.setFixedWidth(235)
        left_lay = QVBoxLayout(left)
        left_lay.addWidget(QLabel("ページ（ドラッグで並び替え）"))
        self.page_info_label = QLabel("1ページ編集")
        self.page_info_label.setStyleSheet("font-weight: bold;")
        left_lay.addWidget(self.page_info_label)
        self.page_list = PageListWidget(self)
        self.page_list.setIconSize(QSize(96, 136))
        self.page_list.setSpacing(8)
        self.page_list.setMinimumWidth(215)
        self.page_list.setMinimumHeight(190)
        self.page_list.currentRowChanged.connect(self.switch_page)
        left_lay.addWidget(self.page_list)

        r1 = QHBoxLayout()
        for text, slot in [("追加", self.add_page), ("複製", self.duplicate_page)]:
            b = QPushButton(text); b.clicked.connect(slot); r1.addWidget(b)
        left_lay.addLayout(r1)
        r2 = QHBoxLayout()
        for text, slot in [("名前", self.rename_page), ("削除", self.delete_page)]:
            b = QPushButton(text); b.clicked.connect(slot); r2.addWidget(b)
        left_lay.addLayout(r2)

        left_lay.addWidget(QLabel("このページのレイヤー（常時表示）"))
        self.quick_layer_count = QLabel("0個")
        left_lay.addWidget(self.quick_layer_count)
        self.quick_layer_list = QListWidget()
        self.quick_layer_list.setMinimumHeight(110)
        self.quick_layer_list.setMaximumHeight(210)
        self.quick_layer_list.currentRowChanged.connect(
            lambda _row: self.select_layer_from_list(self.quick_layer_list)
        )
        self.quick_layer_list.itemDoubleClicked.connect(
            lambda _item: self.edit_layer_from_list(self.quick_layer_list)
        )
        self.quick_layer_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.quick_layer_list.customContextMenuRequested.connect(
            lambda pos: self.show_layer_context_menu(self.quick_layer_list, pos)
        )
        left_lay.addWidget(self.quick_layer_list)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(250)
        left_scroll.setMaximumWidth(270)
        left_scroll.setStyleSheet("QScrollArea { border: none; }")
        left_scroll.setWidget(left)
        self.main_splitter.addWidget(left_scroll)

        self.view = CanvasView(self)
        self.corner_tab_action = QAction(self.view)
        self.corner_tab_action.setShortcut(
            QKeySequence(Qt.Key_Tab)
        )
        self.corner_tab_action.setShortcutContext(
            Qt.WidgetWithChildrenShortcut
        )
        self.corner_tab_action.triggered.connect(
            self.toggle_corner_edit_shortcut
        )
        self.view.addAction(self.corner_tab_action)
        self.main_splitter.addWidget(self.view)

        right = QWidget()
        right.setMinimumWidth(310)
        right.setMaximumWidth(520)
        right_lay = QVBoxLayout(right)

        self.mode_tabs = QTabBar()
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.setUsesScrollButtons(True)
        for label in ("選択", "コマ", "効果", "文字", "素材", "レイヤー"):
            self.mode_tabs.addTab(label)
        self.mode_tabs.currentChanged.connect(self.mode_combo.setCurrentIndex)
        self.mode_combo.currentIndexChanged.connect(
            lambda idx: self.mode_tabs.setCurrentIndex(idx)
            if self.mode_tabs.currentIndex() != idx else None
        )
        right_lay.addWidget(self.mode_tabs)

        self.mode_title = QLabel("選択 / 配置モード")
        self.mode_title.setStyleSheet("font-weight: bold; font-size: 15px;")
        right_lay.addWidget(self.mode_title)
        right_lay.addWidget(QLabel("モードごとに必要な操作だけを表示します"))
        self.selection_info_label = QLabel("選択: なし")
        self.selection_info_label.setWordWrap(True)
        self.selection_info_label.setStyleSheet("background:#eeeeee; padding:5px;")
        right_lay.addWidget(self.selection_info_label)

        common_form = QFormLayout()
        self.scale_spin = QDoubleSpinBox(); self.scale_spin.setRange(0.03, 20.0); self.scale_spin.setSingleStep(0.05); self.scale_spin.setValue(1)
        self.rot_spin = QDoubleSpinBox(); self.rot_spin.setRange(-360, 360); self.rot_spin.setSingleStep(1)
        self.opacity_spin = QSpinBox(); self.opacity_spin.setRange(0, 100); self.opacity_spin.setValue(100); self.opacity_spin.setSuffix(" %")
        self.scale_spin.valueChanged.connect(self.set_selected_scale)
        self.rot_spin.valueChanged.connect(self.set_selected_rotation)
        self.opacity_spin.valueChanged.connect(self.set_selected_opacity)
        self.scale_spin.editingFinished.connect(self.record_history)
        self.rot_spin.editingFinished.connect(self.record_history)
        self.opacity_spin.editingFinished.connect(self.record_history)
        common_form.addRow("倍率", self.scale_spin)
        common_form.addRow("回転", self.rot_spin)
        common_form.addRow("不透明度", self.opacity_spin)
        right_lay.addLayout(common_form)

        self.mode_stack = QStackedWidget()
        right_lay.addWidget(self.mode_stack, 1)

        def add_scrollable_mode_page(page):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll.setMinimumWidth(335)
            scroll.setStyleSheet("QScrollArea { border: none; }")
            scroll.setWidget(page)
            self.mode_stack.addWidget(scroll)
            return scroll

        # 0: 選択 / 配置
        select_page = QWidget(); select_lay = QVBoxLayout(select_page)
        select_lay.addWidget(QLabel("画像・オブジェクト"))
        for text, slot in [
            ("画像を追加", self.pick_image),
            ("画像プロパティ…", self.open_selected_properties),
            ("画像差し替え", self.replace_selected_image),
            ("左右反転", self.mirror_selected),
            ("複製 (Ctrl+D)", self.duplicate_selection),
            ("最前面", lambda: self.change_z(100)),
            ("最背面", lambda: self.change_z(-100)),
            ("削除", self.delete_selected),
        ]:
            b = QPushButton(text); b.clicked.connect(slot); select_lay.addWidget(b)
        select_lay.addWidget(QLabel(
            "画像サイズ: 選択画像の右下□をドラッグ。\n"
            "数値指定したい場合は「画像プロパティ…」または画像を右クリック。\n"
            "透過PNG / WebPのアルファも保持します。"
        ))

        guide_box = QGroupBox("ガイド / スナップ")
        guide_lay = QFormLayout(guide_box)
        self.center_guide_check = QCheckBox(); self.center_guide_check.setChecked(True)
        self.margin_guide_check = QCheckBox(); self.margin_guide_check.setChecked(False)
        self.snap_check = QCheckBox(); self.snap_check.setChecked(True)
        self.snap_threshold_spin = QSpinBox(); self.snap_threshold_spin.setRange(2, 80); self.snap_threshold_spin.setValue(14); self.snap_threshold_spin.setSuffix(" px")
        self.center_guide_check.stateChanged.connect(self.update_guide_settings)
        self.margin_guide_check.stateChanged.connect(self.update_guide_settings)
        guide_lay.addRow("中央ガイド", self.center_guide_check)
        guide_lay.addRow("余白ガイド", self.margin_guide_check)
        guide_lay.addRow("スナップ", self.snap_check)
        guide_detail = QPushButton("吸着・間隔の詳細設定…")
        guide_detail.clicked.connect(self.open_layout_detail_settings)
        guide_lay.addRow(guide_detail)
        select_lay.addWidget(guide_box)
        select_lay.addStretch(1)
        add_scrollable_mode_page(select_page)

        # 1: コマ割り
        panel_page = QWidget(); panel_lay = QVBoxLayout(panel_page)

        nbox = QGroupBox("Nコマ自動生成")
        nlay = QFormLayout(nbox)
        self.n_panel_spin = QSpinBox()
        self.n_panel_spin.setRange(1, 24)
        self.n_panel_spin.setValue(4)
        self.n_panel_layout = QComboBox()
        self.n_panel_layout.addItem("上下に並べる", "vertical")
        self.n_panel_layout.addItem("左右に並べる", "horizontal")
        self.n_panel_layout.addItem("自動グリッド", "grid")
        nlay.addRow("コマ数", self.n_panel_spin)
        nlay.addRow("配置", self.n_panel_layout)
        make_n = QPushButton("Nコマを生成")
        make_n.clicked.connect(self.apply_n_panel_template_from_ui)
        nlay.addRow(make_n)
        panel_lay.addWidget(nbox)

        panel_lay.addWidget(QLabel("選択中のコマを編集"))
        free_panel = QPushButton("空のコマを1つ追加")
        free_panel.clicked.connect(self.add_free_panel)
        panel_lay.addWidget(free_panel)

        corner_box = QGroupBox("四隅編集")
        corner_lay = QVBoxLayout(corner_box)
        cornerrow = QHBoxLayout()
        self.corner_edit_button = QPushButton("四隅編集 (Tab)")
        self.corner_edit_button.setCheckable(True)
        corner_reset = QPushButton("四隅をリセット")
        self.corner_edit_button.toggled.connect(self.toggle_selected_panel_freeform_edit)
        corner_reset.clicked.connect(self.reset_selected_panel_freeform)
        cornerrow.addWidget(self.corner_edit_button)
        cornerrow.addWidget(corner_reset)
        corner_lay.addLayout(cornerrow)
        self.corner_snap_check = QCheckBox("隣接コマの角・辺へ自動吸着")
        self.corner_snap_check.setChecked(True)
        self.corner_snap_spin = QSpinBox()
        self.corner_snap_spin.setRange(2, 80)
        self.corner_snap_spin.setValue(18)
        self.corner_snap_spin.setSuffix(" px")
        self.corner_gap_spin = QSpinBox()
        self.corner_gap_spin.setRange(0, 120)
        self.corner_gap_spin.setValue(24)
        self.corner_gap_spin.setSuffix(" px")
        self.page_outer_gap_spin = QSpinBox()
        self.page_outer_gap_spin.setRange(0, 300)
        self.page_outer_gap_spin.setValue(24)
        self.page_outer_gap_spin.setSuffix(" px")
        cform = QFormLayout()
        cform.addRow(self.corner_snap_check)
        self.parallel_snap_check = QCheckBox("四隅編集中に辺の平行を自動吸着")
        self.parallel_snap_check.setChecked(True)
        self.parallel_snap_angle_spin = QDoubleSpinBox()
        self.parallel_snap_angle_spin.setRange(0.5, 20.0)
        self.parallel_snap_angle_spin.setValue(5.0)
        self.parallel_snap_angle_spin.setSuffix("°")
        cform.addRow(self.parallel_snap_check)
        corner_detail = QPushButton("四隅吸着の詳細設定…")
        corner_detail.clicked.connect(self.open_layout_detail_settings)
        cform.addRow(corner_detail)
        corner_lay.addLayout(cform)
        corner_lay.addWidget(QLabel(
            "コマを選択 → Tabで四隅編集ON/OFF → ○をドラッグ。\n"
            "Ctrl+○クリック=複数頂点 / Shift+辺クリック=辺の2頂点を選択。\n"
            "選択中の頂点・辺はオレンジ。"
        ))
        panel_lay.addWidget(corner_box)

        nsplit_box = QGroupBox("選択コマをN分割")
        nsplit_lay = QFormLayout(nsplit_box)
        self.panel_split_count_spin = QSpinBox()
        self.panel_split_count_spin.setRange(2, 12)
        self.panel_split_count_spin.setValue(2)
        self.panel_split_layout = QComboBox()
        self.panel_split_layout.addItem("左右方向に等分", "vertical")
        self.panel_split_layout.addItem("上下方向に等分", "horizontal")
        self.panel_split_layout.addItem("グリッド分割", "grid")
        self.panel_split_gap_spin = QSpinBox()
        self.panel_split_gap_spin.setRange(0, 100)
        self.panel_split_gap_spin.setValue(24)
        self.panel_split_gap_spin.setSuffix(" px")
        nsplit_lay.addRow("分割数", self.panel_split_count_spin)
        nsplit_lay.addRow("分割方法", self.panel_split_layout)
        self.panel_split_image_mode = QComboBox()
        self.panel_split_image_mode.addItem("各コマへ自動フィット", "fit_each")
        self.panel_split_image_mode.addItem("元画像のサイズ・位置を維持", "keep_view")
        nsplit_lay.addRow("画像の引継ぎ", self.panel_split_image_mode)
        split_exec = QPushButton("選択コマをN分割")
        split_exec.clicked.connect(self.split_selected_panel_n_from_ui)
        nsplit_lay.addRow(split_exec)
        panel_lay.addWidget(nsplit_box)

        advanced_box = QGroupBox("高度分割（Shift+辺選択）")
        advanced_lay = QFormLayout(advanced_box)
        self.advanced_split_count_spin = QSpinBox()
        self.advanced_split_count_spin.setRange(2, 12)
        self.advanced_split_count_spin.setValue(2)
        self.advanced_split_gap_spin = QSpinBox()
        self.advanced_split_gap_spin.setRange(0, 100)
        self.advanced_split_gap_spin.setValue(24)
        self.advanced_split_gap_spin.setSuffix(" px")
        self.advanced_split_bias_spin = QSpinBox()
        self.advanced_split_bias_spin.setRange(-40, 40)
        self.advanced_split_bias_spin.setValue(0)
        self.advanced_split_bias_spin.setSuffix(" %")
        advanced_lay.addRow("分割数", self.advanced_split_count_spin)
        advanced_lay.addRow("分割線スライド", self.advanced_split_bias_spin)
        split_detail_btn = QPushButton("分割の隙間など詳細設定…")
        split_detail_btn.clicked.connect(self.open_layout_detail_settings)
        advanced_lay.addRow(split_detail_btn)
        advanced_exec = QPushButton("選択した辺に沿って高度分割")
        advanced_exec.clicked.connect(self.advanced_split_selected_panel)
        advanced_lay.addRow(advanced_exec)
        advanced_help = QLabel(
            "Shift+辺クリックで辺を選択 → 実行。\n"
            "選択辺と平行な分割線で分割し、画像の見え方は維持します。\n"
            "スライドで分割線を片側へ寄せられます。"
        )
        advanced_help.setWordWrap(True)
        advanced_lay.addRow(advanced_help)
        panel_lay.addWidget(advanced_box)

        panel_lay.addWidget(QLabel(
            "コマ詳細・画像設定・位置リセット・画像解除は\n"
            "コマを右クリック。詳細編集はダブルクリックでも開けます。"
        ))

        panel_lay.addWidget(QLabel(
            "範囲選択: 選択/配置モードで空白からドラッグ\n"
            "複数選択後、そのうち1つをドラッグするとまとめて移動\n"
            "Ctrl+クリックで選択を追加/解除"
        ))
        panel_lay.addStretch(1)
        add_scrollable_mode_page(panel_page)

        # 2: エフェクト
        effect_page = QWidget(); effect_lay = QVBoxLayout(effect_page)
        effect_lay.addWidget(QLabel("エフェクトモードでは画像をクリックすると直接選択します。\nコマ内画像もコマをクリックして編集できます。"))
        effect_form = QFormLayout()
        self.image_effect = QComboBox()
        for label, key in [("白黒","grayscale"),("セピア","sepia"),("ぼかし","blur"),("シャープ","sharpen"),("明るく","bright"),("暗く","dark"),("コントラスト+","contrast_up"),("コントラスト-","contrast_down")]:
            self.image_effect.addItem(label, key)
        effect_form.addRow("画像エフェクト", self.image_effect)
        effect_lay.addLayout(effect_form)

        effect_history_row = QHBoxLayout()
        self.effect_undo_btn = QPushButton("効果Undo")
        self.effect_redo_btn = QPushButton("効果Redo")
        self.effect_undo_btn.clicked.connect(self.effect_undo)
        self.effect_redo_btn.clicked.connect(self.effect_redo)
        effect_history_row.addWidget(self.effect_undo_btn)
        effect_history_row.addWidget(self.effect_redo_btn)
        effect_lay.addLayout(effect_history_row)

        effect_history_hint = QLabel(
            "エフェクト専用履歴: Ctrl+Alt+Z / Ctrl+Alt+Y\n"
            "通常のCtrl+Zとは別に、画像補正・モザイク・Effect素材だけを戻します。"
        )
        effect_history_hint.setWordWrap(True)
        effect_lay.addWidget(effect_history_hint)

        self.effect_undo_action = QAction(self)
        self.effect_undo_action.setShortcut(QKeySequence("Ctrl+Alt+Z"))
        self.effect_undo_action.triggered.connect(self.effect_undo)
        self.addAction(self.effect_undo_action)

        self.effect_redo_action = QAction(self)
        self.effect_redo_action.setShortcut(QKeySequence("Ctrl+Alt+Y"))
        self.effect_redo_action.triggered.connect(self.effect_redo)
        self.addAction(self.effect_redo_action)

        effect_lay.addWidget(QLabel("Effectフォルダの画像エフェクト"))
        asset_row = QHBoxLayout()
        self.effect_category_combo = QComboBox()
        self.effect_reload_btn = QPushButton("再読込")
        self.effect_reload_btn.clicked.connect(self.refresh_effect_asset_browser)
        asset_row.addWidget(self.effect_category_combo, 1)
        asset_row.addWidget(self.effect_reload_btn)
        effect_lay.addLayout(asset_row)

        self.effect_asset_list = QListWidget()
        self.effect_asset_list.setIconSize(QSize(72,72))
        self.effect_asset_list.setMinimumHeight(170)
        self.effect_asset_list.itemDoubleClicked.connect(
            lambda _item: self.apply_selected_effect_asset()
        )
        effect_lay.addWidget(self.effect_asset_list)
        self.effect_category_combo.currentIndexChanged.connect(
            self.populate_effect_asset_list
        )
        effect_asset_apply = QPushButton("選択素材エフェクトを適用")
        effect_asset_apply.clicked.connect(self.apply_selected_effect_asset)
        effect_lay.addWidget(effect_asset_apply)

        overlay_manage = QHBoxLayout()
        overlay_edit = QPushButton("適用済みを調整…")
        overlay_edit.clicked.connect(self.edit_selected_overlay_effect)
        overlay_remove = QPushButton("適用済みを1つ削除")
        overlay_remove.clicked.connect(self.remove_selected_overlay_effect)
        overlay_manage.addWidget(overlay_edit)
        overlay_manage.addWidget(overlay_remove)
        effect_lay.addLayout(overlay_manage)

        open_effect_folder = QPushButton("Effectフォルダを開く")
        open_effect_folder.clicked.connect(self.open_effect_folder)
        effect_lay.addWidget(open_effect_folder)

        for text, slot in [
            ("選択画像 / コマ内画像へ適用", self.apply_selected_image_effect),
            ("画像全体モザイク", self.mosaic_selected),
            ("範囲モザイク", self.start_region_mosaic),
            ("エフェクトを全解除", self.restore_selected_image),
        ]:
            b = QPushButton(text); b.clicked.connect(slot); effect_lay.addWidget(b)
        effect_lay.addStretch(1)
        add_scrollable_mode_page(effect_page)
        QTimer.singleShot(0, self.refresh_effect_asset_browser)
        QTimer.singleShot(0, self._update_effect_history_buttons)

        # 3: 文字編集
        text_page = QWidget(); text_lay = QVBoxLayout(text_page)
        font_form = QFormLayout()
        self.font_combo = QFontComboBox()
        self.font_spin = QSpinBox(); self.font_spin.setRange(6, 240); self.font_spin.setValue(32)
        self.bold_check = QCheckBox()
        self.outline_spin = QDoubleSpinBox(); self.outline_spin.setRange(0.0, 20.0); self.outline_spin.setSingleStep(0.5); self.outline_spin.setValue(2.0)
        self.bubble_style = QComboBox()
        self.bubble_style.addItem("通常", "oval")
        self.bubble_style.addItem("四角", "rect")
        self.bubble_style.addItem("心の声", "thought")
        self.bubble_style.addItem("叫び", "shout")
        font_form.addRow("フォント", self.font_combo)
        font_form.addRow("文字サイズ", self.font_spin)
        font_form.addRow("太字", self.bold_check)
        font_form.addRow("文字の縁幅", self.outline_spin)
        self.vertical_gap_spin = QDoubleSpinBox()
        self.vertical_gap_spin.setRange(-20.0, 40.0)
        self.vertical_gap_spin.setSingleStep(1.0)
        self.vertical_gap_spin.setValue(-2.0)
        self.vertical_gap_spin.setSuffix(" px")
        font_form.addRow("縦書き文字間隔", self.vertical_gap_spin)
        self.shadow_check = QCheckBox()
        self.shadow_x_spin = QDoubleSpinBox(); self.shadow_x_spin.setRange(-50, 50); self.shadow_x_spin.setValue(5); self.shadow_x_spin.setSingleStep(1)
        self.shadow_y_spin = QDoubleSpinBox(); self.shadow_y_spin.setRange(-50, 50); self.shadow_y_spin.setValue(5); self.shadow_y_spin.setSingleStep(1)
        font_form.addRow("吹き出し", self.bubble_style)
        font_form.addRow("文字影", self.shadow_check)
        font_form.addRow("影 X", self.shadow_x_spin)
        font_form.addRow("影 Y", self.shadow_y_spin)
        text_lay.addLayout(font_form)

        self.font_combo.currentFontChanged.connect(self.set_selected_font_family)
        self.font_combo.currentFontChanged.connect(lambda _font: self.record_history())
        self.font_spin.valueChanged.connect(self.set_selected_font_size)
        self.bold_check.stateChanged.connect(self.set_selected_bold)
        self.outline_spin.valueChanged.connect(self.set_selected_outline_width)
        self.vertical_gap_spin.valueChanged.connect(self.set_selected_vertical_gap)
        self.shadow_check.stateChanged.connect(self.set_selected_shadow)
        self.shadow_x_spin.valueChanged.connect(self.set_selected_shadow)
        self.shadow_y_spin.valueChanged.connect(self.set_selected_shadow)
        self.font_spin.editingFinished.connect(self.record_history)
        self.outline_spin.editingFinished.connect(self.record_history)
        self.vertical_gap_spin.editingFinished.connect(self.record_history)
        self.shadow_x_spin.editingFinished.connect(self.record_history)
        self.shadow_y_spin.editingFinished.connect(self.record_history)
        self.shadow_check.clicked.connect(self.record_history)
        self.bold_check.clicked.connect(self.record_history)

        addrow = QHBoxLayout()
        for text, slot in [("横書き", self.add_text), ("縦書き", self.add_vertical_text), ("吹き出し", self.add_bubble)]:
            b = QPushButton(text); b.clicked.connect(slot); addrow.addWidget(b)
        text_lay.addLayout(addrow)
        colorrow = QHBoxLayout()
        text_color = QPushButton("文字色"); text_color.clicked.connect(self.choose_text_color)
        outline_color = QPushButton("縁取り色"); outline_color.clicked.connect(self.choose_outline_color)
        colorrow.addWidget(text_color); colorrow.addWidget(outline_color)
        text_lay.addLayout(colorrow)

        text_lay.addWidget(QLabel("★ お気に入りフォント"))
        self.favorite_font_list = QListWidget()
        self.favorite_font_list.setMaximumHeight(120)
        self.favorite_font_list.itemDoubleClicked.connect(lambda item: self.apply_favorite_font(item))
        text_lay.addWidget(self.favorite_font_list)
        favrow = QHBoxLayout()
        fav_add = QPushButton("★追加"); fav_add.clicked.connect(self.add_favorite_font)
        fav_del = QPushButton("★削除"); fav_del.clicked.connect(self.remove_favorite_font)
        fav_apply = QPushButton("適用"); fav_apply.clicked.connect(self.apply_favorite_font)
        for b in [fav_add, fav_del, fav_apply]: favrow.addWidget(b)
        text_lay.addLayout(favrow)

        text_lay.addWidget(QLabel("文字スタイル再利用"))
        for text, slot in [
            ("フォント情報をコピー", self.copy_font_info_to_clipboard),
            ("文字スタイルをコピー", self.copy_text_style),
            ("文字スタイルを貼り付け", self.paste_text_style),
        ]:
            b = QPushButton(text); b.clicked.connect(slot); text_lay.addWidget(b)
        text_lay.addWidget(QLabel("スタイルコピー対象: フォント / サイズ / 太字 / 文字色 / 縁取り。\n横書きでは影設定も含みます。"))
        text_lay.addStretch(1)
        add_scrollable_mode_page(text_page)

        # 4: 素材ライブラリ
        material_page = QWidget(); material_lay = QVBoxLayout(material_page)
        material_lay.addWidget(QLabel("よく使う画像を登録して再利用"))
        self.material_search = QLineEdit(); self.material_search.setPlaceholderText("素材を検索")
        self.material_search.textChanged.connect(self._refresh_material_list)
        material_lay.addWidget(self.material_search)
        self.material_list = QListWidget(); self.material_list.setIconSize(QSize(56, 56))
        self.material_list.itemDoubleClicked.connect(lambda item: self.insert_selected_material(item))
        material_lay.addWidget(self.material_list, 1)
        for text, slot in [("画像ファイルを登録", self.register_material_files), ("選択中の画像を登録", self.register_selected_as_material), ("選択素材を配置", self.insert_selected_material), ("素材登録を削除", self.remove_selected_material)]:
            b=QPushButton(text); b.clicked.connect(slot); material_lay.addWidget(b)
        material_lay.addWidget(QLabel("素材は元ファイルへの参照です。\nダブルクリックですぐキャンバスへ配置できます。"))
        add_scrollable_mode_page(material_page)

        # 5: レイヤー
        layer_page = QWidget(); layer_lay = QVBoxLayout(layer_page)
        layer_lay.addWidget(QLabel("ページ内オブジェクト＝このページのレイヤー（上ほど前面）"))
        self.layer_count_label = QLabel("0個")
        layer_lay.addWidget(self.layer_count_label)
        add_layer_row = QHBoxLayout()
        for text, slot in [("＋画像", self.pick_image), ("＋文字", self.add_text), ("＋コマ", self.add_free_panel)]:
            b = QPushButton(text); b.clicked.connect(slot); add_layer_row.addWidget(b)
        layer_lay.addLayout(add_layer_row)
        self.layer_list = QListWidget()
        self.layer_list.currentRowChanged.connect(
            lambda _row: self.select_layer_from_list(self.layer_list)
        )
        self.layer_list.itemDoubleClicked.connect(
            lambda _item: self.edit_layer_from_list(self.layer_list)
        )
        self.layer_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.layer_list.customContextMenuRequested.connect(
            lambda pos: self.show_layer_context_menu(self.layer_list, pos)
        )
        layer_lay.addWidget(self.layer_list, 1)
        layerrow1 = QHBoxLayout()
        for text, slot in [("↑ 上へ", lambda: self.move_layer_z(1)), ("↓ 下へ", lambda: self.move_layer_z(-1))]:
            b = QPushButton(text); b.clicked.connect(slot); layerrow1.addWidget(b)
        layer_lay.addLayout(layerrow1)
        layerrow2 = QHBoxLayout()
        for text, slot in [("最前面", self.layer_to_front), ("最背面", self.layer_to_back)]:
            b = QPushButton(text); b.clicked.connect(slot); layerrow2.addWidget(b)
        layer_lay.addLayout(layerrow2)
        layer_visible = QPushButton("表示 / 非表示を切替"); layer_visible.clicked.connect(self.toggle_layer_visibility)
        layer_select = QPushButton("選択してキャンバスへ"); layer_select.clicked.connect(self.select_layer_from_list)
        layer_refresh = QPushButton("一覧を更新"); layer_refresh.clicked.connect(self.refresh_layer_list)
        layer_lay.addWidget(layer_visible); layer_lay.addWidget(layer_select); layer_lay.addWidget(layer_refresh)
        layer_lay.addWidget(QLabel("画像・文字・吹き出し・コマは追加するたび自動でここへ増えます。\nページを切り替えると、そのページ専用の一覧に切り替わります。"))
        add_scrollable_mode_page(layer_page)

        self._refresh_favorite_font_list()
        self._refresh_material_list()
        self.mode_combo.currentIndexChanged.connect(self.change_mode)
        self.main_splitter.addWidget(right)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        QTimer.singleShot(
            0,
            lambda: self.main_splitter.setSizes([255, 980, 365])
        )

        bottom = QWidget()
        bottom_lay = QHBoxLayout(bottom)
        self.title_edit = QLineEdit(); self.title_edit.setPlaceholderText("作品タイトル")
        self.episode_edit = QLineEdit(); self.episode_edit.setPlaceholderText("話数 / サブタイトル")
        bottom_lay.addWidget(QLabel("タイトル")); bottom_lay.addWidget(self.title_edit, 2)
        bottom_lay.addWidget(QLabel("話")); bottom_lay.addWidget(self.episode_edit, 1)

        wrap = QWidget()
        wrap_lay = QVBoxLayout(wrap)
        wrap_lay.setContentsMargins(0, 0, 0, 0)
        wrap_lay.addWidget(content, 1)
        wrap_lay.addWidget(bottom)
        self.setCentralWidget(wrap)
        self.statusBar().showMessage("準備完了")

    def create_scene(self):
        scene = HistoryScene(self, 0, 0, self.canvas_w, self.canvas_h)
        scene.setBackgroundBrush(QColor("white"))
        scene.selectionChanged.connect(self.selection_changed)
        return scene

    def update_guide_settings(self):
        if not hasattr(self, "view"):
            return
        self.view.show_center_guides = bool(self.center_guide_check.isChecked()) if hasattr(self, "center_guide_check") else True
        self.view.show_margin_guides = bool(self.margin_guide_check.isChecked()) if hasattr(self, "margin_guide_check") else False
        self.view.viewport().update()

    def _snap_scene_rect(self, item):
        """Geometry used for snapping, excluding editing-only handle padding."""
        if isinstance(item, PanelItem):
            pts = [item.mapToScene(p) for p in item.corner_points()]
            if pts:
                return QPolygonF(pts).boundingRect()
        return item.sceneBoundingRect()

    def open_layout_detail_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("配置・吸着・分割 詳細設定")
        dlg.setMinimumWidth(420)
        root = QVBoxLayout(dlg)

        tabs = QTabWidget()
        root.addWidget(tabs)

        snap_tab = QWidget()
        snap_form = QFormLayout(snap_tab)
        object_threshold = QSpinBox()
        object_threshold.setRange(2, 80)
        object_threshold.setValue(self.snap_threshold_spin.value())
        object_threshold.setSuffix(" px")
        corner_threshold = QSpinBox()
        corner_threshold.setRange(2, 80)
        corner_threshold.setValue(self.corner_snap_spin.value())
        corner_threshold.setSuffix(" px")
        panel_gap = QSpinBox()
        panel_gap.setRange(0, 120)
        panel_gap.setValue(self.corner_gap_spin.value())
        panel_gap.setSuffix(" px")
        page_gap = QSpinBox()
        page_gap.setRange(0, 300)
        page_gap.setValue(self.page_outer_gap_spin.value())
        page_gap.setSuffix(" px")
        angle = QDoubleSpinBox()
        angle.setRange(0.5, 20.0)
        angle.setValue(self.parallel_snap_angle_spin.value())
        angle.setSuffix("°")
        snap_form.addRow("選択/配置 吸着距離", object_threshold)
        snap_form.addRow("四隅編集 吸着距離", corner_threshold)
        snap_form.addRow("コマ同士の間隔", panel_gap)
        snap_form.addRow("ページ外枠間隔", page_gap)
        snap_form.addRow("平行吸着角度", angle)
        tabs.addTab(snap_tab, "吸着")

        split_tab = QWidget()
        split_form = QFormLayout(split_tab)
        normal_gap = QSpinBox()
        normal_gap.setRange(0, 100)
        normal_gap.setValue(self.panel_split_gap_spin.value())
        normal_gap.setSuffix(" px")
        advanced_gap = QSpinBox()
        advanced_gap.setRange(0, 100)
        advanced_gap.setValue(self.advanced_split_gap_spin.value())
        advanced_gap.setSuffix(" px")
        split_form.addRow("通常N分割の隙間", normal_gap)
        split_form.addRow("高度分割の隙間", advanced_gap)
        tabs.addTab(split_tab, "分割")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        root.addWidget(buttons)
        buttons.rejected.connect(dlg.reject)

        def apply_values():
            self.snap_threshold_spin.setValue(object_threshold.value())
            self.corner_snap_spin.setValue(corner_threshold.value())
            self.corner_gap_spin.setValue(panel_gap.value())
            self.page_outer_gap_spin.setValue(page_gap.value())
            self.parallel_snap_angle_spin.setValue(angle.value())
            self.panel_split_gap_spin.setValue(normal_gap.value())
            self.advanced_split_gap_spin.setValue(advanced_gap.value())
            dlg.accept()

        buttons.accepted.connect(apply_values)
        dlg.exec()

    def show_layer_context_menu(self, source, pos):
        item = source.itemAt(pos)
        if item is None:
            return
        source.setCurrentItem(item)
        obj = self._current_layer_object(source)
        if obj is None:
            return

        menu = QMenu(source)
        detail = menu.addAction("詳細データを表示…")
        edit = menu.addAction("編集")
        visible = menu.addAction("表示 / 非表示")
        chosen = menu.exec(source.mapToGlobal(pos))
        if chosen == detail:
            self.show_layer_data_preview(obj)
        elif chosen == edit:
            self.edit_layer_from_list(source)
        elif chosen == visible:
            self.toggle_layer_visibility(source)

    def show_layer_data_preview(self, obj):
        dlg = QDialog(self)
        dlg.setWindowTitle("レイヤー詳細データ")
        dlg.resize(720, 620)
        root = QVBoxLayout(dlg)

        header = QLabel(self._layer_label(obj))
        header.setStyleSheet("font-weight:bold; font-size:14px;")
        header.setWordWrap(True)
        root.addWidget(header)

        preview = QLabel()
        preview.setAlignment(Qt.AlignCenter)
        preview.setMinimumHeight(180)
        preview.setStyleSheet(
            "background:#202124; border:1px solid #666; color:white; padding:8px;"
        )

        pix = QPixmap()
        if isinstance(obj, ImageItem):
            pix = obj.pixmap()
        elif isinstance(obj, PanelItem) and not obj.image_pixmap.isNull():
            pix = obj.image_pixmap

        if not pix.isNull():
            preview.setPixmap(
                pix.scaled(
                    420, 220,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        elif isinstance(obj, EditableTextItem):
            preview.setText(obj.toPlainText())
        elif isinstance(obj, VerticalTextItem):
            preview.setText(obj.text_value)
        elif isinstance(obj, BubbleItem):
            preview.setText(obj.text_item.toPlainText())
        else:
            preview.setText("画像プレビューなし")
        root.addWidget(preview)

        payload = {}
        if hasattr(obj, "serialize"):
            try:
                payload = obj.serialize()
            except Exception as e:
                payload = {"serialize_error": str(e)}
        payload["_runtime"] = {
            "class": obj.__class__.__name__,
            "visible": bool(obj.isVisible()),
            "selected": bool(obj.isSelected()),
            "scene_pos": [obj.scenePos().x(), obj.scenePos().y()],
            "scene_bounding_rect": [
                obj.sceneBoundingRect().x(),
                obj.sceneBoundingRect().y(),
                obj.sceneBoundingRect().width(),
                obj.sceneBoundingRect().height(),
            ],
        }

        if isinstance(obj, PanelItem):
            vb = obj.visual_bounds()
            hc = obj._resize_handle_center()
            payload["_panel_geometry_help"] = {
                "cyan_handle_name": "右下リサイズハンドル",
                "cyan_handle_visual_constant": "PanelItem.HANDLE",
                "cyan_handle_size_value": PanelItem.HANDLE,
                "normal_size_saved_as": ["w", "h"],
                "transform_scale_saved_as": "scale",
                "freeform_shape_saved_as": "custom_points",
                "corner_edit_enabled_runtime_only": bool(
                    obj.corner_edit_enabled
                ),
                "minimum_panel_size_px": PanelItem.MIN_PANEL_SIZE,
                "visual_bounds_local": [
                    vb.x(), vb.y(), vb.width(), vb.height()
                ],
                "resize_handle_center_local": [
                    hc.x(), hc.y()
                ],
                "note": (
                    "四隅編集ON中はcyan handleではなくcustom_pointsを編集。"
                    "TabでOFFにするとcyan handleで自由四隅形状全体を再リサイズ可能。"
                ),
            }

        data_edit = QPlainTextEdit()
        data_edit.setReadOnly(True)
        data_edit.setPlainText(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )
        root.addWidget(data_edit, 1)

        copy_btn = QPushButton("詳細データをコピー")
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(data_edit.toPlainText())
        )
        root.addWidget(copy_btn)
        close = QPushButton("閉じる")
        close.clicked.connect(dlg.accept)
        root.addWidget(close)
        dlg.exec()

    def snap_selected_items(self):
        """Snap a single item or a multi-selection as one rigid group."""
        if not hasattr(self, "snap_check") or not self.snap_check.isChecked():
            return
        scene = self.current_scene()
        if not scene:
            return
        selected = [
            i for i in scene.selectedItems()
            if i.parentItem() is None and i.isVisible()
        ]
        if not selected:
            return

        threshold = (
            float(self.snap_threshold_spin.value())
            if hasattr(self, "snap_threshold_spin")
            else 14.0
        )
        margin = min(55.0, self.canvas_w*0.08, self.canvas_h*0.08)

        selected_set = set(selected)
        selected_rects = [self._snap_scene_rect(i) for i in selected]

        group_rect = QRectF(selected_rects[0])
        for br in selected_rects[1:]:
            group_rect = group_rect.united(br)

        # Every item edge/centre can act as a sensor while the whole selection
        # receives the same dx/dy. This restores the "easy to catch" feeling
        # for single and multi-object placement.
        anchors_x = []
        anchors_y = []
        for br in selected_rects + [group_rect]:
            anchors_x.extend([br.left(), br.center().x(), br.right()])
            anchors_y.extend([br.top(), br.center().y(), br.bottom()])

        targets_x = [
            0.0, margin, self.canvas_w/2.0,
            self.canvas_w-margin, float(self.canvas_w)
        ]
        targets_y = [
            0.0, margin, self.canvas_h/2.0,
            self.canvas_h-margin, float(self.canvas_h)
        ]

        for other in scene.items():
            if (
                other.parentItem() is not None
                or other in selected_set
                or not other.isVisible()
            ):
                continue
            br = self._snap_scene_rect(other)
            targets_x.extend([br.left(), br.center().x(), br.right()])
            targets_y.extend([br.top(), br.center().y(), br.bottom()])

        dx_candidates = [
            tx - ax
            for tx in targets_x
            for ax in anchors_x
            if abs(tx-ax) <= threshold
        ]
        dy_candidates = [
            ty - ay
            for ty in targets_y
            for ay in anchors_y
            if abs(ty-ay) <= threshold
        ]

        dx = min(dx_candidates, key=abs) if dx_candidates else 0.0
        dy = min(dy_candidates, key=abs) if dy_candidates else 0.0

        if abs(dx) < 0.05:
            dx = 0.0
        if abs(dy) < 0.05:
            dy = 0.0

        if dx or dy:
            for item in selected:
                item.moveBy(dx, dy)
            self.statusBar().showMessage(
                f"吸着: X {dx:+.1f}px / Y {dy:+.1f}px",
                1400
            )

    def project_image_sources(self):
        """Return source image files used anywhere in the current project."""
        self._sync_page_items(self.current_page_index)
        sources = []
        for page_index, page in enumerate(self.pages):
            seen = set()
            for entry in page.get("items", []):
                kind = None
                raw = ""
                if entry.get("type") == "image":
                    raw = entry.get("source_path", "")
                    kind = "画像"
                elif entry.get("type") == "panel" and entry.get("image_path"):
                    raw = entry.get("image_path", "")
                    kind = "コマ画像"
                if not raw:
                    continue
                resolved = self._resolve_project_path(raw)
                if not resolved:
                    continue
                path = str(Path(resolved))
                if path in seen or not Path(path).exists():
                    continue
                seen.add(path)
                sources.append({
                    "page_index": page_index,
                    "page_name": page.get("name", f"ページ {page_index+1}"),
                    "kind": kind,
                    "path": path,
                    "name": Path(path).name,
                })
        return sources

    def scan_effect_assets(self):
        try:
            EFFECT_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        assets = []
        if not EFFECT_DIR.exists():
            return assets
        for path in sorted(EFFECT_DIR.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                rel = path.relative_to(EFFECT_DIR)
                parent = rel.parent.as_posix()
            except Exception:
                rel = Path(path.name)
                parent = "."
            category = "直下" if parent in {"", "."} else parent
            assets.append({
                "name": path.stem,
                "path": str(path),
                "relative": "Effect/" + rel.as_posix(),
                "category": category,
            })
        return assets

    def refresh_effect_asset_browser(self):
        if not hasattr(self, "effect_category_combo"):
            return        self.effect_assets_cache = self.scan_effect_assets()
        categories = sorted({a["category"] for a in self.effect_assets_cache})
        old_category = self.effect_category_combo.currentText()
        self.effect_category_combo.blockSignals(True)
        self.effect_category_combo.clear()
        for category in categories:
            self.effect_category_combo.addItem(category)
        if old_category in categories:
            self.effect_category_combo.setCurrentText(old_category)
        self.effect_category_combo.blockSignals(False)
        self.populate_effect_asset_list()

    def populate_effect_asset_list(self, *_args):
        if not hasattr(self, "effect_asset_list"):
            return
        self.effect_asset_list.clear()
        category = self.effect_category_combo.currentText() if hasattr(self, "effect_category_combo") else ""
        for asset in getattr(self, "effect_assets_cache", []):
            if asset["category"] != category:
                continue
            item = QListWidgetItem(asset["name"])
            item.setData(Qt.UserRole, asset["path"])
            item.setToolTip(asset["relative"])
            item.setIcon(QIcon(asset["path"]))
            self.effect_asset_list.addItem(item)

    def open_effect_folder(self):
        try:
            EFFECT_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(str(EFFECT_DIR))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(EFFECT_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(EFFECT_DIR)])
        except Exception as e:
            QMessageBox.warning(self, "Effectフォルダ", str(e))

    def apply_selected_effect_asset(self):
        if not hasattr(self, "effect_asset_list"):
            return
        row = self.effect_asset_list.currentItem()
        if not row:
            QMessageBox.information(
                self, "画像エフェクト",
                "Effectフォルダ内のエフェクト素材を選択してください。"
            )
            return
        path = row.data(Qt.UserRole)
        targets = self._selected_image_targets()
        if not targets:
            QMessageBox.information(
                self, "画像エフェクト",
                "通常画像または画像入りコマを選択してください。"
            )
            return
        before = self._capture_effect_state(targets)
        changed = False
        for target in targets:
            if isinstance(target, ImageItem):
                changed = target.add_overlay_effect(path) or changed
            elif isinstance(target, PanelItem):
                changed = target.add_panel_overlay(path) or changed
        if changed:
            self._finish_effect_change(
                before,
                f"素材エフェクト追加: {Path(path).name}",
                targets,
            )
            self.statusBar().showMessage(
                f"素材エフェクトを適用: {Path(path).name}", 2500
            )

    def _choose_overlay_entry(self, target, action_title):
        if isinstance(target, ImageItem):
            entries = [
                effect for effect in target.effects
                if effect.get("type") == "overlay"
            ]
        elif isinstance(target, PanelItem):
            entries = list(target.panel_overlays)
        else:
            entries = []

        if not entries:
            QMessageBox.information(
                self, action_title, "この対象には素材エフェクトがありません。"
            )
            return None

        if len(entries) == 1:
            return entries[0]

        labels = []
        for idx, overlay in enumerate(entries):
            path = resolve_effect_path(overlay.get("path", ""))
            mode = "全面" if overlay.get("mode", "fit") == "fit" else "自由"
            labels.append(f"{idx+1}. [{mode}] {path.name or overlay.get('path','')}")
        picked, ok = QInputDialog.getItem(
            self, action_title, "素材エフェクト:", labels, len(labels)-1, False
        )
        if not ok:
            return None
        try:
            return entries[labels.index(picked)]
        except Exception:
            return None

    def edit_selected_overlay_effect(self):
        targets = self._selected_image_targets()
        if len(targets) != 1:
            QMessageBox.information(
                self, "素材エフェクト調整",
                "通常画像またはコマを1つだけ選択してください。"
            )
            return
        target = targets[0]
        before = self._capture_effect_state([target])
        overlay = self._choose_overlay_entry(target, "素材エフェクト調整")
        if overlay is None:
            return

        def changed():
            if isinstance(target, ImageItem):
                target.rebuild_effects()
            else:
                target.update()
            self.view.viewport().update()

        if isinstance(target, ImageItem):
            preview_pixmap = target.original_pixmap
            logical_size = (
                max(1, target.original_pixmap.width()),
                max(1, target.original_pixmap.height()),
            )
        else:
            preview_pixmap = target.image_pixmap
            bounds = target.panel_path().boundingRect()
            logical_size = (bounds.width(), bounds.height())

        dlg = EffectOverlayPropertiesDialog(
            self,
            overlay,
            changed,
            "素材エフェクト調整",
            preview_pixmap=preview_pixmap,
            logical_size=logical_size,
        )
        if dlg.exec():
            self._finish_effect_change(
                before,
                "素材エフェクト調整",
                [target],
            )

    def remove_selected_overlay_effect(self):
        targets = self._selected_image_targets()
        if len(targets) != 1:
            QMessageBox.information(
                self, "素材エフェクト削除",
                "通常画像またはコマを1つだけ選択してください。"
            )
            return
        target = targets[0]
        before = self._capture_effect_state([target])
        overlay = self._choose_overlay_entry(target, "素材エフェクト削除")
        if overlay is None:
            return

        if isinstance(target, ImageItem):
            try:
                target.effects.remove(overlay)
            except ValueError:
                return
            target.rebuild_effects()
        else:
            try:
                target.panel_overlays.remove(overlay)
            except ValueError:
                return
            target.update()
        self._finish_effect_change(
            before,
            "素材エフェクト削除",
            [target],
        )

    def edit_text_object(self, item):
        if isinstance(item, EditableTextItem):
            text, ok = QInputDialog.getMultiLineText(
                self, "文字編集", "文字:", item.toPlainText()
            )
            if ok:
                item.setPlainText(text)
                self.record_history()
        elif isinstance(item, VerticalTextItem):
            text, ok = QInputDialog.getMultiLineText(
                self, "縦書き編集", "文字:", item.text_value
            )
            if ok:
                item.set_text(text)
                self.record_history()
        elif isinstance(item, BubbleItem):
            text, ok = QInputDialog.getMultiLineText(
                self, "吹き出し編集", "セリフ:", item.text_item.toPlainText()
            )
            if ok:
                item.text_item.setPlainText(text)
                self.record_history()

    def clear_corner_handle_selection(self):
        scene = self.current_scene()
        if not scene:
            return
        for item in scene.items():
            if isinstance(item, PanelItem):
                item._selected_corner_indices.clear()
                item.update()
        self.view.viewport().update()

    def toggle_panel_edge_selection(self, panel, edge_index):
        if (
            not isinstance(panel, PanelItem)
            or panel.custom_points is None
            or not panel.corner_edit_enabled
        ):
            return
        a = int(edge_index) % 4
        b = (a + 1) % 4
        both_selected = (
            a in panel._selected_corner_indices
            and b in panel._selected_corner_indices
        )
        if both_selected:
            panel._selected_corner_indices.discard(a)
            panel._selected_corner_indices.discard(b)
        else:
            panel._selected_corner_indices.add(a)
            panel._selected_corner_indices.add(b)
        panel.update()
        self.view.viewport().update()

    def begin_corner_group_drag(self, source_panel, corner_index, scene_pos, modifiers=Qt.NoModifier):
        if source_panel.custom_points is None or not source_panel.corner_edit_enabled:
            return False

        # Ctrl-click is for building a multi-panel/multi-vertex selection.
        if modifiers & Qt.ControlModifier:
            if corner_index in source_panel._selected_corner_indices:
                source_panel._selected_corner_indices.discard(corner_index)
            else:
                source_panel._selected_corner_indices.add(corner_index)
            source_panel.update()
            self.view.viewport().update()
            return False

        if corner_index not in source_panel._selected_corner_indices:
            self.clear_corner_handle_selection()
            source_panel._selected_corner_indices = {corner_index}

        selected_panels = [
            p for p in self.selected()
            if isinstance(p, PanelItem) and p.corner_edit_enabled and p.custom_points is not None
        ]
        if source_panel not in selected_panels:
            selected_panels.append(source_panel)

        # Only panels that actually have one or more selected vertices participate.
        groups = []
        for panel in selected_panels:
            indices = set(panel._selected_corner_indices)
            if not indices:
                continue
            groups.append({
                "panel": panel,
                "indices": indices,
                "points": [QPointF(p) for p in panel.corner_points()],
            })

        if not groups:
            source_panel._selected_corner_indices = {corner_index}
            groups = [{
                "panel": source_panel,
                "indices": {corner_index},
                "points": [QPointF(p) for p in source_panel.corner_points()],
            }]

        source_points = [QPointF(p) for p in source_panel.corner_points()]
        moving_panels = tuple(group["panel"] for group in groups)
        self._corner_group_drag_state = {
            "source": source_panel,
            "source_index": corner_index,
            "start_scene": QPointF(scene_pos),
            "source_start_point": QPointF(source_points[corner_index]),
            "source_start_scene_point": source_panel.mapToScene(source_points[corner_index]),
            "groups": groups,
            "moving_panels": moving_panels,
            "moved": False,
        }
        return True

    def _scene_delta_to_local(self, panel, delta_scene):
        base_scene = panel.mapToScene(QPointF(0, 0))
        return (
            panel.mapFromScene(base_scene + delta_scene)
            - panel.mapFromScene(base_scene)
        )

    def _parallel_snap_corner_local(self, panel, idx, proposed, start_points):
        if (
            not hasattr(self, "parallel_snap_check")
            or not self.parallel_snap_check.isChecked()
        ):
            return QPointF(proposed)

        pts = [QPointF(p) for p in start_points]
        i = int(idx) % 4
        prev_i = (i - 1) % 4
        next_i = (i + 1) % 4
        opp_i = (i + 2) % 4

        candidates = []

        # Edge i->next parallel to opposite edge prev->opp.
        d1 = pts[opp_i] - pts[prev_i]
        if math.hypot(d1.x(), d1.y()) > 1e-6:
            candidates.append((pts[next_i], d1))

        # Edge prev->i parallel to opposite edge opp->next.
        d2 = pts[next_i] - pts[opp_i]
        if math.hypot(d2.x(), d2.y()) > 1e-6:
            candidates.append((pts[prev_i], d2))

        best = QPointF(proposed)
        best_scene_dist = float("inf")
        scene_proposed = panel.mapToScene(proposed)
        threshold = (
            float(self.corner_snap_spin.value())
            if hasattr(self, "corner_snap_spin")
            else 18.0
        )

        for origin, direction in candidates:
            denom = direction.x()*direction.x() + direction.y()*direction.y()
            if denom <= 1e-9:
                continue
            t = (
                (proposed.x()-origin.x())*direction.x()
                + (proposed.y()-origin.y())*direction.y()
            ) / denom
            projected = QPointF(
                origin.x() + direction.x()*t,
                origin.y() + direction.y()*t,
            )
            scene_projected = panel.mapToScene(projected)
            dist = math.hypot(
                scene_projected.x()-scene_proposed.x(),
                scene_projected.y()-scene_proposed.y(),
            )
            if dist < best_scene_dist and dist <= threshold:
                best_scene_dist = dist
                best = projected

        return best

    @staticmethod
    def _segments_intersect(a, b, c, d):
        def cross(p, q, r):
            return (
                (q.x()-p.x())*(r.y()-p.y())
                - (q.y()-p.y())*(r.x()-p.x())
            )
        ab_c = cross(a, b, c)
        ab_d = cross(a, b, d)
        cd_a = cross(c, d, a)
        cd_b = cross(c, d, b)
        return (
            ab_c * ab_d < -1e-7
            and cd_a * cd_b < -1e-7
        )

    def _valid_panel_quad(self, points):
        if len(points) != 4:
            return False

        for i in range(4):
            a = points[i]
            b = points[(i+1) % 4]
            if (
                math.hypot(b.x()-a.x(), b.y()-a.y())
                < PanelItem.MIN_EDGE_LENGTH
            ):
                return False

        if self._segments_intersect(
            points[0], points[1], points[2], points[3]
        ):
            return False
        if self._segments_intersect(
            points[1], points[2], points[3], points[0]
        ):
            return False

        area2 = 0.0
        signs = []
        for i in range(4):
            a = points[i]
            b = points[(i+1) % 4]
            c = points[(i+2) % 4]
            area2 += a.x()*b.y() - b.x()*a.y()
            turn = (
                (b.x()-a.x())*(c.y()-b.y())
                - (b.y()-a.y())*(c.x()-b.x())
            )
            if abs(turn) > 1e-6:
                signs.append(1 if turn > 0 else -1)

        if abs(area2) < PanelItem.MIN_PANEL_SIZE**2:
            return False
        if signs and not all(v == signs[0] for v in signs):
            return False
        return True

    def _rigid_clamp_local_delta(self, panel, indices, start_points, delta):
        """Rigidly move selected vertices; only shorten motion if the quad would invert."""
        indices = set(indices)
        if len(indices) < 2:
            return QPointF(delta)

        def candidate(factor):
            pts = [QPointF(p) for p in start_points]
            step = QPointF(delta.x()*factor, delta.y()*factor)
            for idx in indices:
                pts[idx] = pts[idx] + step
            return pts

        if self._valid_panel_quad(candidate(1.0)):
            return QPointF(delta)

        low, high = 0.0, 1.0
        for _ in range(20):
            mid = (low + high) / 2.0
            if self._valid_panel_quad(candidate(mid)):
                low = mid
            else:
                high = mid
        return QPointF(delta.x()*low, delta.y()*low)

    def _cluster_snap_corrections(self, corrections, threshold):
        """Pick the correction supported by the most handles, not merely the first/nearest."""
        if not corrections:
            return QPointF(0, 0)

        # Axis-like corrections can coexist (e.g. one point catches left edge,
        # another catches top edge), so solve X/Y independently first.
        x_like = [
            c for c in corrections
            if abs(c.x()) > 0.05 and abs(c.y()) <= max(1.0, abs(c.x())*0.18)
        ]
        y_like = [
            c for c in corrections
            if abs(c.y()) > 0.05 and abs(c.x()) <= max(1.0, abs(c.y())*0.18)
        ]
        dx = min((c.x() for c in x_like), key=abs) if x_like else 0.0
        dy = min((c.y() for c in y_like), key=abs) if y_like else 0.0
        axis_support = (1 if x_like else 0) + (1 if y_like else 0)

        # Slanted-edge corrections should be almost the same vector when
        # several selected vertices can satisfy their targets simultaneously.
        radius = max(3.0, threshold * 0.35)
        best_cluster = []
        for seed in corrections:
            cluster = [
                c for c in corrections
                if math.hypot(c.x()-seed.x(), c.y()-seed.y()) <= radius
            ]
            if (
                len(cluster) > len(best_cluster)
                or (
                    len(cluster) == len(best_cluster)
                    and cluster
                    and sum(math.hypot(c.x(), c.y()) for c in cluster)
                    < sum(math.hypot(c.x(), c.y()) for c in best_cluster)
                )
            ):
                best_cluster = cluster

        if len(best_cluster) >= 2 and len(best_cluster) > axis_support:
            return QPointF(
                sum(c.x() for c in best_cluster)/len(best_cluster),
                sum(c.y() for c in best_cluster)/len(best_cluster),
            )

        if axis_support:
            return QPointF(dx, dy)

        return min(
            corrections,
            key=lambda c: math.hypot(c.x(), c.y())
        )

    def _best_group_corner_delta(self, state, raw_scene_delta):
        """Allow several selected handles to participate in snapping at once."""
        exclude = state.get("moving_panels", ())
        threshold = (
            float(self.corner_snap_spin.value())
            if hasattr(self, "corner_snap_spin")
            else 18.0
        )
        corrections = []

        for group in state["groups"]:
            panel = group["panel"]
            start_points = group["points"]
            local_delta = self._scene_delta_to_local(panel, raw_scene_delta)
            if len(group["indices"]) >= 2:
                local_delta = self._rigid_clamp_local_delta(
                    panel, group["indices"], start_points, local_delta
                )

            for idx in group["indices"]:
                proposed = start_points[idx] + local_delta
                if len(group["indices"]) == 1:
                    proposed = panel._clamp_corner(idx, proposed)
                    proposed = self._parallel_snap_corner_local(
                        panel, idx, proposed, start_points
                    )

                proposed_scene = panel.mapToScene(proposed)
                snapped = self.snap_panel_corner(
                    panel,
                    idx,
                    proposed,
                    exclude_panels=exclude,
                )
                snapped_scene = panel.mapToScene(snapped)
                correction = snapped_scene - proposed_scene
                distance = math.hypot(correction.x(), correction.y())
                if 0.05 < distance <= threshold + 0.5:
                    corrections.append(correction)

        correction = self._cluster_snap_corrections(
            corrections, threshold
        )
        return raw_scene_delta + correction

    def update_corner_group_drag(self, scene_pos):
        state = getattr(self, "_corner_group_drag_state", None)
        if not state:
            return False

        raw_scene_delta = QPointF(scene_pos) - state["start_scene"]
        snapped_scene_delta = self._best_group_corner_delta(
            state, raw_scene_delta
        )

        if (
            abs(snapped_scene_delta.x()) > 0.05
            or abs(snapped_scene_delta.y()) > 0.05
        ):
            state["moved"] = True

        for group in state["groups"]:
            panel = group["panel"]
            if panel.scene() != self.current_scene():
                continue
            start_points = group["points"]
            local_delta = self._scene_delta_to_local(
                panel, snapped_scene_delta
            )
            if len(group["indices"]) >= 2:
                # A selected edge / arbitrary pair is a rigid translation by
                # default: no endpoint can be clamped differently and shear it.
                local_delta = self._rigid_clamp_local_delta(
                    panel, group["indices"], start_points, local_delta
                )

            panel.prepareGeometryChange()
            new_points = [QPointF(p) for p in start_points]
            for idx in group["indices"]:
                proposed = start_points[idx] + local_delta
                if len(group["indices"]) == 1:
                    proposed = self._parallel_snap_corner_local(
                        panel, idx, proposed, start_points
                    )
                    proposed = panel._clamp_corner(idx, proposed)
                new_points[idx] = proposed
            panel.custom_points = new_points
            panel.update()

        self.view.viewport().update()
        return True

    def end_corner_group_drag(self):
        state = getattr(self, "_corner_group_drag_state", None)
        self._corner_group_drag_state = None
        if state and state.get("moved"):
            for group in state.get("groups", []):
                panel = group.get("panel")
                if isinstance(panel, PanelItem):
                    panel.sync_transform_origin_to_visual_center()
            self.record_history()
            return True
        return False

    @staticmethod
    def _closest_point_on_segment(point, a, b):
        vx = b.x() - a.x()
        vy = b.y() - a.y()
        denom = vx*vx + vy*vy
        if denom <= 1e-12:
            return QPointF(a), 0.0
        t = ((point.x()-a.x())*vx + (point.y()-a.y())*vy) / denom
        t = max(0.0, min(1.0, t))
        return QPointF(a.x()+vx*t, a.y()+vy*t), t

    def snap_panel_corner(self, panel, corner_index, local_point, exclude_panels=None):
        """Snap to real polygon edges while ignoring panels moving in the same handle group."""
        if not hasattr(self, "corner_snap_check") or not self.corner_snap_check.isChecked():
            return local_point
        scene = self.current_scene()
        if not scene or panel.scene() != scene:
            return local_point

        threshold = float(self.corner_snap_spin.value()) if hasattr(self, "corner_snap_spin") else 18.0
        gutter = float(self.corner_gap_spin.value()) if hasattr(self, "corner_gap_spin") else 0.0
        scene_point = panel.mapToScene(local_point)

        best_target = None
        best_distance = threshold + 1.0
        panel_scene_pts = [panel.mapToScene(p) for p in panel.corner_points()]
        panel_center = QPolygonF(panel_scene_pts).boundingRect().center()
        excluded_ids = {id(p) for p in (exclude_panels or ())}

        for other in scene.items():
            if (
                other is panel
                or id(other) in excluded_ids
                or other.parentItem() is not None
                or not isinstance(other, PanelItem)
                or not other.isVisible()
            ):
                continue

            pts = [other.mapToScene(p) for p in other.corner_points()]
            if len(pts) != 4:
                continue
            other_center = QPolygonF(pts).boundingRect().center()

            for i in range(4):
                a = pts[i]
                b = pts[(i+1) % 4]
                projection, _t = self._closest_point_on_segment(scene_point, a, b)

                ex = b.x() - a.x()
                ey = b.y() - a.y()
                length = math.hypot(ex, ey)
                if length <= 1e-9:
                    continue

                # Unit normal to the actual slanted edge.
                nx = -ey / length
                ny = ex / length
                toward_panel = panel_center - other_center
                if nx*toward_panel.x() + ny*toward_panel.y() < 0:
                    nx, ny = -nx, -ny

                target = QPointF(
                    projection.x() + nx * gutter,
                    projection.y() + ny * gutter,
                )
                dist = math.hypot(
                    target.x() - scene_point.x(),
                    target.y() - scene_point.y(),
                )
                if dist < best_distance:
                    best_distance = dist
                    best_target = target

        # Page outer frame uses its own inset margin, just like panel gutters.
        page_gap = (
            float(self.page_outer_gap_spin.value())
            if hasattr(self, "page_outer_gap_spin")
            else 0.0
        )
        page_gap_x = min(max(0.0, page_gap), float(self.canvas_w)/2.0)
        page_gap_y = min(max(0.0, page_gap), float(self.canvas_h)/2.0)
        canvas_targets = [
            QPointF(page_gap_x, scene_point.y()),
            QPointF(float(self.canvas_w) - page_gap_x, scene_point.y()),
            QPointF(scene_point.x(), page_gap_y),
            QPointF(scene_point.x(), float(self.canvas_h) - page_gap_y),
        ]
        for target in canvas_targets:
            dist = math.hypot(target.x()-scene_point.x(), target.y()-scene_point.y())
            if dist < best_distance:
                best_distance = dist
                best_target = target

        if best_target is not None and best_distance <= threshold:
            return panel.mapFromScene(best_target)
        return local_point

    def _new_page_dict(self, name, scene=None, initial_items=None):
        page_id = str(uuid.uuid4())
        scene = scene or self.create_scene()
        initial = initial_items if initial_items is not None else []
        initial_copy = json.loads(json.dumps(initial))
        return {
            "id": page_id,
            "name": name,
            "scene": scene,
            "items": initial_copy,
            "history": [json.loads(json.dumps(initial_copy))],
            "history_index": 0,
        }

    def render_scene_thumbnail(self, scene, width=96, height=136):
        selected = list(scene.selectedItems())
        previous_block = scene.blockSignals(True)
        try:
            scene.clearSelection()
            image = QImage(width, height, QImage.Format_ARGB32)
            image.fill(QColor("white"))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            scene.render(
                painter,
                QRectF(0, 0, width, height),
                QRectF(0, 0, self.canvas_w, self.canvas_h),
            )
            painter.end()
            for obj in selected:
                if obj.scene() == scene:
                    obj.setSelected(True)
            return QIcon(QPixmap.fromImage(image))
        finally:
            scene.blockSignals(previous_block)

    def _refresh_page_list_labels(self):
        mapping = {p["id"]: p for p in self.pages}
        for i in range(self.page_list.count()):
            item = self.page_list.item(i)
            page = mapping.get(item.data(Qt.UserRole))
            if page:
                item.setText(f"{i+1:03d}  {page['name']}")

    def refresh_page_thumbnail(self, index=None):
        if index is None:
            index = self.current_page_index
        if index is None or index < 0 or index >= len(self.pages):
            return
        page = self.pages[index]
        for i in range(self.page_list.count()):
            item = self.page_list.item(i)
            if item.data(Qt.UserRole) == page["id"]:
                item.setIcon(self.render_scene_thumbnail(page["scene"]))
                break

    def refresh_all_page_thumbnails(self):
        mapping = {p["id"]: p for p in self.pages}
        for i in range(self.page_list.count()):
            item = self.page_list.item(i)
            page = mapping.get(item.data(Qt.UserRole))
            if page:
                item.setIcon(self.render_scene_thumbnail(page["scene"]))
        self._refresh_page_list_labels()

    def _add_page_list_item(self, page):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, page["id"])
        item.setIcon(self.render_scene_thumbnail(page["scene"]))
        self.page_list.addItem(item)
        self._refresh_page_list_labels()

    def update_page_info(self):
        if hasattr(self, "page_info_label"):
            n = self.current_page_index + 1 if self.current_page_index >= 0 else 0
            self.page_info_label.setText(
                f"【1ページ編集】 {n}/{len(self.pages)}   ページ範囲: {self.canvas_w} × {self.canvas_h}px"
            )

    def fit_current_page(self):
        if self.current_scene():
            self.view.fitInView(QRectF(0, 0, self.canvas_w, self.canvas_h), Qt.KeepAspectRatio)
            self.statusBar().showMessage("現在の1ページ全体を表示しました", 1800)

    def open_page_setup(self):
        dlg = PageSetupDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            w, h, scale_contents = dlg.values()
            self.set_page_size(w, h, scale_contents)

    def set_page_size(self, width, height, scale_contents=False):
        width, height = int(width), int(height)
        self._sync_page_items(self.current_page_index)
        old_w, old_h = self.canvas_w, self.canvas_h
        if width == old_w and height == old_h:
            return
        sx, sy = width/max(1.0, old_w), height/max(1.0, old_h)
        uniform = min(sx, sy)
        self.suspend_history = True
        try:
            for page in self.pages:
                scene = page["scene"]
                if scale_contents:
                    for item in [i for i in scene.items() if i.parentItem() is None]:
                        if isinstance(item, PanelItem):
                            r = item.rect()
                            item.prepareGeometryChange()
                            item.setRect(QRectF(r.x()*sx, r.y()*sy, r.width()*sx, r.height()*sy))
                            item.setPos(item.pos().x()*sx, item.pos().y()*sy)
                            if item.custom_points is not None:
                                item.custom_points = [
                                    QPointF(p.x()*sx, p.y()*sy)
                                    for p in item.custom_points
                                ]
                            item.image_offset = QPointF(
                                item.image_offset.x()*sx,
                                item.image_offset.y()*sy,
                            )
                            item.sync_transform_origin_to_visual_center()
                            item.update()
                        else:
                            item.setPos(item.pos().x()*sx, item.pos().y()*sy)
                            item.setScale(item.scale()*uniform)
                scene.setSceneRect(0, 0, width, height)
            self.canvas_w, self.canvas_h = width, height
            # Reset history baselines because coordinates changed project-wide.
            for page in self.pages:
                snap = self.serialize_scene(page["scene"])
                page["items"] = json.loads(json.dumps(snap))
                page["history"] = [json.loads(json.dumps(snap))]
                page["history_index"] = 0
        finally:
            self.suspend_history = False
        self.refresh_all_page_thumbnails()
        pad = max(80.0, min(self.canvas_w, self.canvas_h) * 0.08)
        self.view.setSceneRect(-pad, -pad, self.canvas_w + pad*2, self.canvas_h + pad*2)
        self.fit_current_page()
        self.update_page_info()
        self.view.viewport().update()
        self.statusBar().showMessage(f"ページサイズを {width} × {height}px に変更しました", 3500)

    def _sync_page_items(self, index):
        """Synchronize a page's authoritative items from its live scene without touching Undo history."""
        if index < 0 or index >= len(self.pages):
            return []
        page = self.pages[index]
        snapshot = self.serialize_scene(page["scene"])
        page["items"] = json.loads(json.dumps(snapshot))
        return snapshot

    def _commit_page_snapshot(self, index):
        if self.suspend_history or index < 0 or index >= len(self.pages):
            return
        page = self.pages[index]
        snapshot = self.serialize_scene(page["scene"])
        page["items"] = json.loads(json.dumps(snapshot))
        if not page["history"]:
            page["history"] = [json.loads(json.dumps(snapshot))]
            page["history_index"] = 0
            return
        current = page["history"][page["history_index"]]
        if snapshot == current:
            return
        page["history"] = page["history"][:page["history_index"]+1]
        page["history"].append(json.loads(json.dumps(snapshot)))
        if len(page["history"]) > 60:
            page["history"].pop(0)
        page["history_index"] = len(page["history"])-1

    def _content_signature(self):
        pages = []
        for page in self.pages:
            pages.append({
                "name": page.get("name", ""),
                "items": self.serialize_scene(page["scene"]),
            })
        payload = {
            "title": self.title_edit.text() if hasattr(self, "title_edit") else "",
            "episode": self.episode_edit.text() if hasattr(self, "episode_edit") else "",
            "canvas_size": [self.canvas_w, self.canvas_h],
            "pages": pages,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _mark_saved_content(self):
        self._saved_content_signature = self._content_signature()

    def _has_unsaved_changes(self):
        if self._saved_content_signature is None:
            return bool(self.pages)
        try:
            return self._content_signature() != self._saved_content_signature
        except Exception:
            return True

    def _confirm_unsaved_action(self, action_text):
        if not self._has_unsaved_changes():
            return True

        box = QMessageBox(self)
        box.setWindowTitle("未保存の変更")
        box.setText(
            f"未保存の変更があります。\n{action_text}前に保存しますか？"
        )
        box.setInformativeText(
            "「保存しない」を選ぶと、現在の未保存編集は破棄されます。"
        )
        save_btn = box.addButton("保存", QMessageBox.AcceptRole)
        discard_btn = box.addButton("保存しない", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("キャンセル", QMessageBox.RejectRole)
        box.setDefaultButton(save_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is cancel_btn:
            return False
        if clicked is discard_btn:
            return True
        if clicked is save_btn:
            return bool(self.save_project())
        return False

    def new_project(self, force=False):
        if not force and not self._confirm_unsaved_action("新規作成する"):
            return
        self.view.cancel_region_mosaic()
        self.canvas_w = DEFAULT_CANVAS_W
        self.canvas_h = DEFAULT_CANVAS_H
        self.page_list.blockSignals(True)
        self.page_list.clear()
        self.page_list.blockSignals(False)
        self.pages = []
        self.effect_history_by_page = {}
        self.project_path = None
        self.active_project_dir = None
        self.active_assets_dir = None
        self.title_edit.clear(); self.episode_edit.clear()
        self.current_page_index = -1
        self.add_page()
        self.update_page_info()
        self._mark_saved_content()

    def _next_default_page_name(self):
        max_no = 0
        pattern = re.compile(r"^ページ\s*(\d+)$")
        for page in self.pages:
            m = pattern.match(str(page.get("name", "")).strip())
            if m:
                max_no = max(max_no, int(m.group(1)))
        return f"ページ {max_no + 1}"

    def add_page(self):
        page = self._new_page_dict(self._next_default_page_name())
        self.pages.append(page)
        self._add_page_list_item(page)
        self.page_list.setCurrentRow(len(self.pages)-1)
        self.refresh_page_thumbnail(len(self.pages)-1)
        self.update_page_info()

    def duplicate_page(self):
        if self.current_page_index < 0:
            return
        self._sync_page_items(self.current_page_index)
        src = self.pages[self.current_page_index]
        data = json.loads(json.dumps(src.get("items", [])))
        scene = self.create_scene()
        self.deserialize_scene(scene, data)
        page = self._new_page_dict(src["name"] + " コピー", scene, data)
        self.pages.append(page)
        self._add_page_list_item(page)
        self.page_list.setCurrentRow(len(self.pages)-1)
        self.refresh_page_thumbnail(len(self.pages)-1)
        self.update_page_info()

    def delete_page(self):
        if len(self.pages) <= 1:
            QMessageBox.information(self, "ページ", "最低1ページ必要です。")
            return
        self._sync_page_items(self.current_page_index)
        row = self.page_list.currentRow()
        if row < 0:
            return

        removed_page_id = self.pages[row].get("id")

        previous_block = self.page_list.blockSignals(True)
        try:
            self.pages.pop(row)
            self.page_list.takeItem(row)
            new_row = min(row, len(self.pages)-1)
            self._refresh_page_list_labels()            self.page_list.setCurrentRow(new_row)
        finally:
            self.page_list.blockSignals(previous_block)

        if removed_page_id:
            self.effect_history_by_page.pop(removed_page_id, None)

        self.current_page_index = -1
        self.switch_page(new_row)
        self.update_page_info()

    def rename_page(self):
        row = self.page_list.currentRow()
        if row < 0:
            return
        current = self.pages[row]["name"]
        text, ok = QInputDialog.getText(self, "ページ名", "名前:", text=current)
        if ok and text.strip():
            self.pages[row]["name"] = text.strip()
            self._refresh_page_list_labels()
            self.refresh_page_thumbnail(row)
            self.update_page_info()
            self.statusBar().showMessage(
                f"ページ名を変更: {text.strip()}",
                1800,
            )

    def sync_page_order_from_list(self, preferred_page_id=None):
        # Commit the current scene BEFORE changing list/model ordering.
        old_index = self.current_page_index
        self._sync_page_items(old_index)

        mapping = {p["id"]: p for p in self.pages}
        ordered = []
        for i in range(self.page_list.count()):
            pid = self.page_list.item(i).data(Qt.UserRole)
            if pid in mapping:
                ordered.append(mapping[pid])

        if len(ordered) != len(self.pages):
            self.statusBar().showMessage("ページ並び替えの同期に失敗しました", 3500)
            return

        self.pages = ordered

        if preferred_page_id is None:
            current_item = self.page_list.currentItem()
            preferred_page_id = current_item.data(Qt.UserRole) if current_item else None

        new_index = 0
        if preferred_page_id:
            for i, page in enumerate(self.pages):
                if page["id"] == preferred_page_id:
                    new_index = i
                    break

        self.current_page_index = -1
        self._refresh_page_list_labels()

        previous_block = self.page_list.blockSignals(True)
        try:
            self.page_list.setCurrentRow(new_index)
        finally:
            self.page_list.blockSignals(previous_block)

        self.switch_page(new_index)

    def _rebuild_page_scene_from_items(self, index):
        if index < 0 or index >= len(self.pages):
            return
        page = self.pages[index]
        scene = page["scene"]
        snapshot = json.loads(json.dumps(page.get("items", [])))
        self.suspend_history = True
        previous_block = scene.blockSignals(True)
        try:
            scene.clear()
            self.deserialize_scene(scene, snapshot)
            scene.setSceneRect(0, 0, self.canvas_w, self.canvas_h)
        finally:
            scene.blockSignals(previous_block)
            self.suspend_history = False

    def switch_page(self, row):
        if row < 0 or row >= len(self.pages):
            return

        # Redundant same-row calls must never rebuild a live page from an older snapshot.
        if (
            row == self.current_page_index
            and self.view.scene() is self.pages[row]["scene"]
        ):
            self.refresh_layer_list()
            self.update_page_info()
            return

        previous = self.current_page_index
        if previous != row:
            self._commit_page_snapshot(previous)
        self.current_page_index = row
        self.view.cancel_region_mosaic()
        # Rebuild from the page's own authoritative snapshot. This prevents
        # page switches/deletions/reorders from accidentally showing stale scene data.
        self._rebuild_page_scene_from_items(row)
        self.view.setScene(self.pages[row]["scene"])
        if hasattr(self, "corner_edit_button"):
            self.corner_edit_button.blockSignals(True)
            self.corner_edit_button.setChecked(False)
            self.corner_edit_button.blockSignals(False)
        pad = max(80.0, min(self.canvas_w, self.canvas_h) * 0.08)
        self.view.setSceneRect(-pad, -pad, self.canvas_w + pad*2, self.canvas_h + pad*2)
        self.fit_current_page()
        self.refresh_page_thumbnail(row)
        self.refresh_layer_list()
        self.update_page_info()
        self._update_effect_history_buttons()
        self.statusBar().showMessage(f"{row+1:03d} / {len(self.pages):03d}  {self.pages[row]['name']}  |  このページの内容は保持されています")

    def prev_page(self):
        if not self.pages: return
        self.page_list.setCurrentRow(max(0, self.page_list.currentRow() - 1))

    def next_page(self):
        if not self.pages: return
        self.page_list.setCurrentRow(min(len(self.pages) - 1, self.page_list.currentRow() + 1))

    def current_scene(self):
        if self.current_page_index < 0 or self.current_page_index >= len(self.pages):
            return None
        return self.pages[self.current_page_index]["scene"]

    def record_history(self):
        if self.suspend_history or self.current_page_index < 0:
            return
        self._commit_page_snapshot(self.current_page_index)
        self.refresh_page_thumbnail(self.current_page_index)
        self.refresh_layer_list()
        self.update_page_info()

    def _restore_history_snapshot(self, snapshot):
        scene = self.current_scene()
        if not scene:
            return
        self.suspend_history = True
        previous_block = scene.blockSignals(True)
        try:
            scene.clear()
            self.deserialize_scene(scene, snapshot)
        finally:
            scene.blockSignals(previous_block)
            self.suspend_history = False
        if 0 <= self.current_page_index < len(self.pages):
            self.pages[self.current_page_index]["items"] = json.loads(json.dumps(snapshot))
        self.refresh_page_thumbnail(self.current_page_index)
        self.refresh_layer_list()
        self.selection_changed()
        self.update_page_info()
        self.view.viewport().update()

    def undo(self):
        if self.current_page_index < 0:
            return

        # Capture live changes that may not have emitted editingFinished yet
        # (spin boxes, text editor focus, etc.) before stepping backward.
        self._commit_page_snapshot(self.current_page_index)

        page = self.pages[self.current_page_index]
        if page["history_index"] <= 0:
            self.statusBar().showMessage(
                "これ以上戻せません",
                1200,
            )
            return

        page["history_index"] -= 1
        self._restore_history_snapshot(
            page["history"][page["history_index"]]
        )
        self.statusBar().showMessage("元に戻しました", 1500)

    def redo(self):
        if self.current_page_index < 0:
            return

        # If the live scene was edited after Undo without yet being committed,
        # committing it here correctly invalidates the old redo branch.
        self._commit_page_snapshot(self.current_page_index)

        page = self.pages[self.current_page_index]
        if page["history_index"] >= len(page["history"])-1:
            self.statusBar().showMessage(
                "これ以上やり直せません",
                1200,
            )
            return

        page["history_index"] += 1
        self._restore_history_snapshot(
            page["history"][page["history_index"]]
        )
        self.statusBar().showMessage("やり直しました", 1500)

    def pick_image(self):
        p, _ = QFileDialog.getOpenFileName(self, "画像を追加", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if p:
            self.add_image(p)

    def add_image(self, path, pos=None, record=True):
        pix = QPixmap(path)
        if pix.isNull():
            QMessageBox.warning(self, "画像", "画像を読み込めませんでした。")
            return None
        item = ImageItem(pix, path, pix)
        if pix.width() > 700:
            item.setScale(700 / pix.width())
        item.setPos(pos or QPointF(100, 100))
        item.setZValue(10)
        self.current_scene().addItem(item)
        self.current_scene().clearSelection(); item.setSelected(True)
        if record:
            self.record_history()
        return item

    def add_text(self):
        item = EditableTextItem("ダブルクリックで編集")
        if hasattr(self, "font_combo"):
            self.apply_text_style_to_item(item, self.ui_text_style())
        item.setPos(160, 160)
        item.setZValue(100)
        self.current_scene().addItem(item)
        self.current_scene().clearSelection(); item.setSelected(True)
        self.record_history()

    def add_vertical_text(self):
        item = VerticalTextItem("縦書き")
        if hasattr(self, "font_combo"):
            self.apply_text_style_to_item(item, self.ui_text_style())
        item.setPos(220, 160)
        item.setZValue(100)
        self.current_scene().addItem(item)
        self.current_scene().clearSelection(); item.setSelected(True)
        self.record_history()

    def add_bubble(self):
        style = self.bubble_style.currentData() if hasattr(self, "bubble_style") else "oval"
        item = BubbleItem("ダブルクリックで編集", style=style)
        if hasattr(self, "font_combo"):
            self.apply_text_style_to_item(item, self.ui_text_style())
        item.setPos(180, 180)
        item.setZValue(100)
        self.current_scene().addItem(item)
        self.current_scene().clearSelection(); item.setSelected(True)
        self.record_history()

    def open_image_properties(self, item=None):
        if item is None:
            images = [obj for obj in self.selected() if isinstance(obj, ImageItem)]
            if len(images) != 1:
                QMessageBox.information(
                    self,
                    "画像プロパティ",
                    "通常画像を1枚だけ選択してください。\n"
                    "コマ内画像は「コマ詳細編集」から調整できます。"
                )
                return
            item = images[0]

        if not isinstance(item, ImageItem):
            return

        # Store opening position only for this dialog instance's center-preserve logic.
        item.setData(901, float(item.pos().x()))
        item.setData(902, float(item.pos().y()))
        dlg = ImagePropertiesDialog(self, item)
        dlg.exec()
        item.setData(901, None)
        item.setData(902, None)

    def open_selected_properties(self):
        images = [obj for obj in self.selected() if isinstance(obj, ImageItem)]
        if len(images) == 1:
            self.open_image_properties(images[0])
            return
        if len(self.selected()) == 1 and isinstance(self.selected()[0], PanelItem):
            self.open_panel_detail(self.selected()[0])
            return
        QMessageBox.information(
            self,
            "プロパティ",
            "画像を1枚選択すると画像プロパティを開けます。\n"
            "コマを1つ選択した場合はコマ詳細編集を開けます。"
        )

    def replace_selected_image(self):
        imgs = [i for i in self.selected() if isinstance(i, ImageItem)]
        if len(imgs) != 1:
            QMessageBox.information(self, "画像差し替え", "差し替える画像を1つ選択してください。")
            return
        p, _ = QFileDialog.getOpenFileName(self, "画像を差し替え", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if p and imgs[0].replace_image(p):
            self.record_history()
            self.statusBar().showMessage("位置・倍率・回転・モザイク設定を維持して差し替えました", 3500)

    def open_panel_detail(self, panel):
        if not isinstance(panel, PanelItem):
            return
        dlg = PanelDetailDialog(self, panel)
        dlg.exec()
        self.refresh_layer_list()
        self.refresh_page_thumbnail(self.current_page_index)

    def assign_image_to_panel(self, panel):
        if not isinstance(panel, PanelItem):
            return
        p, _ = QFileDialog.getOpenFileName(self, "コマに画像を入れる", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if p and panel.set_image(p):
            self.current_scene().clearSelection(); panel.setSelected(True)
            self.record_history()
            self.statusBar().showMessage("コマに画像を設定しました。Shift+ドラッグで中身移動 / Alt+ホイールで中身ズーム", 5000)

    def assign_image_to_selected_panel(self):
        panels = [i for i in self.selected() if isinstance(i, PanelItem)]
        if len(panels) != 1:
            QMessageBox.information(self, "コマ画像", "画像を入れるコマを1つ選択してください。")
            return
        self.assign_image_to_panel(panels[0])

    def reset_selected_panel_image(self):
        panels = [i for i in self.selected() if isinstance(i, PanelItem)]
        if not panels:
            return
        for panel in panels:
            panel.reset_image_view()
        self.record_history()

    def clear_selected_panel_image(self):
        panels = [i for i in self.selected() if isinstance(i, PanelItem)]
        if not panels:
            return
        changed = False
        for panel in panels:
            if panel.image_path:
                panel.image_path = ""
                panel.original_image_pixmap = QPixmap()
                panel.image_pixmap = QPixmap()
                panel.image_effects = []
                panel.reset_image_view()
                changed = True
        if changed:
            self.record_history()

    def add_free_panel(self):
        scene = self.current_scene()
        if not scene: return
        item = PanelItem(QRectF(160, 180, min(700, self.canvas_w*0.65), min(520, self.canvas_h*0.42)))
        item.setZValue(0); scene.addItem(item); scene.clearSelection(); item.setSelected(True); self.record_history()

    def toggle_corner_edit_shortcut(self):
        panels = [
            i for i in self.selected()
            if isinstance(i, PanelItem)
        ]
        if not panels:
            self.statusBar().showMessage(
                "Tab: 四隅編集するコマを選択してください",
                1800,
            )
            return

        target = not all(
            p.corner_edit_enabled for p in panels
        )
        created_shape = False
        for panel in panels:
            before = panel.custom_points is not None
            panel.set_corner_edit_enabled(target)
            created_shape = created_shape or (
                target and not before
            )

        if created_shape:
            self.record_history()

        if hasattr(self, "corner_edit_button"):
            self.corner_edit_button.blockSignals(True)
            self.corner_edit_button.setChecked(target)
            self.corner_edit_button.blockSignals(False)

        self.view.viewport().update()
        self.statusBar().showMessage(
            "Tab → 四隅編集ON"
            if target
            else "Tab → 四隅編集OFF",
            1800,
        )

    def enable_selected_panel_freeform(self):
        # Backward-compatible helper: enabling also turns handle editing on.
        panels = [i for i in self.selected() if isinstance(i, PanelItem)]
        if not panels:
            QMessageBox.information(self, "四隅編集", "コマを選択してください。")
            return
        for panel in panels:
            panel.enable_freeform()
        self.record_history()
        self.statusBar().showMessage("四隅編集ON: Ctrl+○クリックで複数ハンドル選択できます", 3500)

    def toggle_selected_panel_freeform_edit(self, checked):
        panels = [i for i in self.selected() if isinstance(i, PanelItem)]
        if not panels:
            if checked and hasattr(self, "corner_edit_button"):
                self.corner_edit_button.blockSignals(True)
                self.corner_edit_button.setChecked(False)
                self.corner_edit_button.blockSignals(False)
            if checked:
                QMessageBox.information(self, "四隅編集", "コマを選択してください。")
            return
        changed = False
        for panel in panels:
            before_shape = panel.custom_points is not None
            panel.set_corner_edit_enabled(checked)
            changed = changed or (checked and not before_shape)
        if changed:
            self.record_history()
        self.statusBar().showMessage("四隅編集ON" if checked else "四隅編集OFF", 1800)

    def reset_selected_panel_freeform(self):
        panels = [i for i in self.selected() if isinstance(i, PanelItem)]
        if not panels:
            return
        for panel in panels:
            panel.reset_freeform()
        if hasattr(self, "corner_edit_button"):
            self.corner_edit_button.blockSignals(True)
            self.corner_edit_button.setChecked(False)
            self.corner_edit_button.blockSignals(False)
        self.record_history()
        self.statusBar().showMessage("コマの四隅形状をリセットしました", 2200)

    def open_selected_panel_detail(self):
        panels = [i for i in self.selected() if isinstance(i, PanelItem)]
        if len(panels) != 1:
            QMessageBox.information(self, "コマ詳細編集", "編集するコマを1つ選択してください。")
            return
        self.open_panel_detail(panels[0])

    @staticmethod
    def _lerp_point(a, b, t):
        return QPointF(
            a.x() + (b.x() - a.x()) * float(t),
            a.y() + (b.y() - a.y()) * float(t),
        )

    def _selected_panel_edge_index(self, panel):
        indices = set(getattr(panel, "_selected_corner_indices", set()))
        if len(indices) != 2:
            return None
        for edge_idx in range(4):
            if indices == {edge_idx, (edge_idx + 1) % 4}:
                return edge_idx
        return None

    @staticmethod
    def _biased_fraction(x, bias_percent):
        x = max(0.0, min(1.0, float(x)))
        if x <= 0.0 or x >= 1.0:
            return x
        # Positive bias pushes split lines toward the selected edge;
        # negative bias pushes them toward the opposite edge.
        gamma = math.exp(float(bias_percent) / 55.0)
        return x ** gamma

    def _freeform_strip_cells(
        self,
        points,
        orientation,
        count,
        gap,
        bias_percent=0,
    ):
        """Split a 4-point quad into strips using bilinear edge interpolation."""
        pts = [QPointF(p) for p in points]
        count = max(2, int(count))

        if orientation == "horizontal":
            edge_a = QPointF((pts[0].x()+pts[1].x())/2, (pts[0].y()+pts[1].y())/2)
            edge_b = QPointF((pts[3].x()+pts[2].x())/2, (pts[3].y()+pts[2].y())/2)
        else:
            edge_a = QPointF((pts[0].x()+pts[3].x())/2, (pts[0].y()+pts[3].y())/2)
            edge_b = QPointF((pts[1].x()+pts[2].x())/2, (pts[1].y()+pts[2].y())/2)

        depth = max(1.0, math.hypot(edge_b.x()-edge_a.x(), edge_b.y()-edge_a.y()))
        gap_t = min(0.20, max(0.0, float(gap) / depth))

        boundaries = [0.0]
        for i in range(1, count):
            boundaries.append(self._biased_fraction(i / count, bias_percent))
        boundaries.append(1.0)
        boundaries = sorted(boundaries)

        cells = []
        for i in range(count):
            t0 = boundaries[i] + (gap_t/2.0 if i > 0 else 0.0)
            t1 = boundaries[i+1] - (gap_t/2.0 if i < count-1 else 0.0)
            if t1 - t0 < 0.015:
                return []

            if orientation == "horizontal":
                lt0 = self._lerp_point(pts[0], pts[3], t0)
                rt0 = self._lerp_point(pts[1], pts[2], t0)
                rt1 = self._lerp_point(pts[1], pts[2], t1)
                lt1 = self._lerp_point(pts[0], pts[3], t1)
                cells.append([lt0, rt0, rt1, lt1])
            else:
                tt0 = self._lerp_point(pts[0], pts[1], t0)
                bt0 = self._lerp_point(pts[3], pts[2], t0)
                bt1 = self._lerp_point(pts[3], pts[2], t1)
                tt1 = self._lerp_point(pts[0], pts[1], t1)
                cells.append([tt0, tt1, bt1, bt0])
        return cells

    def _freeform_grid_cells(self, points, count, gap):
        pts = [QPointF(p) for p in points]
        cols = max(1, min(count, math.ceil(math.sqrt(count))))
        rows = math.ceil(count / cols)

        top_w = math.hypot(pts[1].x()-pts[0].x(), pts[1].y()-pts[0].y())
        bottom_w = math.hypot(pts[2].x()-pts[3].x(), pts[2].y()-pts[3].y())
        left_h = math.hypot(pts[3].x()-pts[0].x(), pts[3].y()-pts[0].y())
        right_h = math.hypot(pts[2].x()-pts[1].x(), pts[2].y()-pts[1].y())
        width = max(1.0, (top_w + bottom_w)/2.0)
        height = max(1.0, (left_h + right_h)/2.0)
        gap_u = min(0.15, max(0.0, float(gap)/width))
        gap_v = min(0.15, max(0.0, float(gap)/height))

        def quad_point(u, v):
            top = self._lerp_point(pts[0], pts[1], u)
            bottom = self._lerp_point(pts[3], pts[2], u)
            return self._lerp_point(top, bottom, v)

        cells = []
        placed = 0
        for row in range(rows):
            remaining = count - placed
            items_in_row = min(cols, remaining)
            if items_in_row <= 0:
                break
            v0 = row/rows + (gap_v/2 if row > 0 else 0)
            v1 = (row+1)/rows - (gap_v/2 if row < rows-1 else 0)
            for col in range(items_in_row):
                u0 = col/items_in_row + (gap_u/2 if col > 0 else 0)
                u1 = (col+1)/items_in_row - (gap_u/2 if col < items_in_row-1 else 0)
                if u1-u0 < 0.02 or v1-v0 < 0.02:
                    return []
                cells.append([
                    quad_point(u0, v0),
                    quad_point(u1, v0),
                    quad_point(u1, v1),
                    quad_point(u0, v1),
                ])
                placed += 1
        return cells

    def _split_freeform_panel(
        self,
        panel,
        custom_cells,
        label,
        image_mode="keep_view",
    ):
        if not custom_cells:
            QMessageBox.information(self, "コマ分割", "この設定ではコマが小さくなりすぎます。")
            return False
        scene = self.current_scene()
        if scene is None:
            return False

        parent_draw = panel._image_draw_rect() if panel.image_path else QRectF()
        children = []
        base_rect = QRectF(panel.rect())

        for points in custom_cells:
            custom = [[p.x(), p.y()] for p in points]
            child = PanelItem(
                base_rect,
                panel.image_path,
                panel.image_zoom,
                [panel.image_offset.x(), panel.image_offset.y()],
                0.0,
                custom,
                panel.image_effects,
                panel.panel_texts,
                panel.panel_overlays,
            )
            child.setTransformOriginPoint(panel.transformOriginPoint())
            child.setPos(panel.pos())
            child.setRotation(panel.rotation())
            child.setScale(panel.scale())
            child.setZValue(panel.zValue())
            child.setOpacity(panel.opacity())
            child.sync_transform_origin_to_visual_center()

            if panel.image_path:
                if image_mode == "fit_each":
                    child.image_zoom = 1.0
                    child.image_offset = QPointF(0, 0)
                elif not panel.image_pixmap.isNull() and not parent_draw.isNull():
                    bounds = child.panel_path().boundingRect()
                    pw = max(1.0, float(panel.image_pixmap.width()))
                    ph = max(1.0, float(panel.image_pixmap.height()))
                    child_cover = max(bounds.width()/pw, bounds.height()/ph)
                    desired_scale = parent_draw.width()/pw
                    child.image_zoom = max(
                        0.05,
                        desired_scale / max(1e-9, child_cover),
                    )
                    child.image_offset = parent_draw.center() - bounds.center()

            scene.addItem(child)
            children.append(child)

        scene.removeItem(panel)
        scene.clearSelection()
        for child in children:
            child.setSelected(True)
        self.record_history()
        self.statusBar().showMessage(label, 3500)
        return True

    def advanced_split_selected_panel(self):
        panels = [i for i in self.selected() if isinstance(i, PanelItem)]
        if len(panels) != 1:
            QMessageBox.information(self, "高度分割", "高度分割するコマを1つ選択してください。")
            return

        panel = panels[0]
        if panel.custom_points is None:
            panel.enable_freeform()
        edge_idx = self._selected_panel_edge_index(panel)
        if edge_idx is None:
            QMessageBox.information(
                self,
                "高度分割",
                "Shift+辺クリックで分割方向に使う辺を選択してください。"
            )
            return

        count = self.advanced_split_count_spin.value()
        gap = self.advanced_split_gap_spin.value()
        bias = self.advanced_split_bias_spin.value()
        orientation = "horizontal" if edge_idx in (0, 2) else "vertical"
        cells = self._freeform_strip_cells(
            panel.corner_points(),
            orientation,
            count,
            gap,
            bias_percent=bias,
        )
        self._split_freeform_panel(
            panel,
            cells,
            f"高度分割: {count}分割 / 選択辺に平行 / 画像サイズ維持",
            image_mode="keep_view",
        )

    def _child_pos_preserving_panel_transform(self, panel, child_rect):
        """Position a child rect so it matches the parent's transformed geometry exactly."""
        parent_origin = QPointF(panel.transformOriginPoint())
        child_origin = QPointF(child_rect.center())
        delta = parent_origin - child_origin

        angle = math.radians(float(panel.rotation()))
        scale = float(panel.scale())
        cs = math.cos(angle) * scale
        sn = math.sin(angle) * scale
        mapped = QPointF(
            cs * delta.x() - sn * delta.y(),
            sn * delta.x() + cs * delta.y(),
        )
        return QPointF(panel.pos()) + delta - mapped

    def split_selected_panel_n_from_ui(self):
        direction = self.panel_split_layout.currentData() if hasattr(self, "panel_split_layout") else "vertical"
        count = self.panel_split_count_spin.value() if hasattr(self, "panel_split_count_spin") else 2
        self.split_selected_panel_n(direction, count=count)

    def split_selected_panel(self, direction):
        self.split_selected_panel_n(direction, count=2, use_ratio_for_two=True)

    def split_selected_panel_n(self, direction, count=None, use_ratio_for_two=False):
        panels = [i for i in self.selected() if isinstance(i, PanelItem)]
        if len(panels) != 1:
            QMessageBox.information(self, "コマ分割", "分割するコマを1つ選択してください。")
            return

        panel = panels[0]
        scene = self.current_scene()
        if scene is None:
            return

        if count is None:
            count = self.panel_split_count_spin.value() if hasattr(self, "panel_split_count_spin") else 2
        count = max(2, min(12, int(count)))
        r = QRectF(panel.rect())
        gap = float(self.panel_split_gap_spin.value()) if hasattr(self, "panel_split_gap_spin") else 24.0
        min_size = 48.0
        rects = []

        # Freeform quads can now be N-split directly. Shift-edge selection does
        # not disable the ordinary N-split controls.
        if panel.custom_points is not None or abs(panel.skew) > 1e-9:
            points = panel.corner_points()
            if direction == "grid":
                cells = self._freeform_grid_cells(points, count, gap)
                label = f"{count}分割（自由四隅グリッド）"
            elif direction == "vertical":
                cells = self._freeform_strip_cells(
                    points, "vertical", count, gap, bias_percent=0
                )
                label = f"{count}分割（自由四隅・左右方向）"
            else:
                cells = self._freeform_strip_cells(
                    points, "horizontal", count, gap, bias_percent=0
                )
                label = f"{count}分割（自由四隅・上下方向）"
            image_mode = (
                self.panel_split_image_mode.currentData()
                if hasattr(self, "panel_split_image_mode")
                else "fit_each"
            )
            self._split_freeform_panel(
                panel,
                cells,
                f"{label}しました。画像引継ぎ: "
                + ("自動フィット" if image_mode == "fit_each" else "元の見え方を維持"),
                image_mode=image_mode,
            )
            return

        if direction == "grid":
            cols = max(1, min(count, math.ceil(math.sqrt(count))))
            rows = math.ceil(count / cols)
            usable_h = r.height() - gap * (rows - 1)
            if usable_h <= 0:
                QMessageBox.information(self, "コマ分割", "すき間が大きすぎます。")
                return
            cell_h = usable_h / rows
            placed = 0
            for row in range(rows):
                remaining = count - placed
                items_in_row = min(cols, remaining)
                usable_w = r.width() - gap * (items_in_row - 1)
                cell_w = usable_w / items_in_row
                if cell_w < min_size or cell_h < min_size:
                    QMessageBox.information(self, "コマ分割", "コマが小さくなりすぎます。")
                    return
                for col in range(items_in_row):
                    rects.append(QRectF(
                        r.left() + col*(cell_w+gap),
                        r.top() + row*(cell_h+gap),
                        cell_w,
                        cell_h,
                    ))
                    placed += 1
            label = f"{count}分割（グリッド）"

        elif direction == "vertical":
            usable = r.width() - gap*(count-1)
            if usable <= 0:
                QMessageBox.information(self, "コマ分割", "すき間が大きすぎます。")
                return
            size = usable / count
            if size < min_size:
                QMessageBox.information(self, "コマ分割", "コマが小さくなりすぎます。")
                return
            x = r.left()
            for _ in range(count):
                rects.append(QRectF(x, r.top(), size, r.height()))
                x += size + gap
            label = f"{count}分割（左右方向）"

        else:
            usable = r.height() - gap*(count-1)
            if usable <= 0:
                QMessageBox.information(self, "コマ分割", "すき間が大きすぎます。")
                return
            size = usable / count
            if size < min_size:
                QMessageBox.information(self, "コマ分割", "コマが小さくなりすぎます。")
                return
            y = r.top()
            for _ in range(count):
                rects.append(QRectF(r.left(), y, r.width(), size))
                y += size + gap
            label = f"{count}分割（上下方向）"

        image_mode = self.panel_split_image_mode.currentData() if hasattr(self, "panel_split_image_mode") else "fit_each"
        parent_draw = panel._image_draw_rect() if panel.image_path else QRectF()
        children = []
        for child_rect in rects:
            child_zoom = panel.image_zoom
            child_offset = QPointF(panel.image_offset)
            if panel.image_path:
                if image_mode == "fit_each":
                    child_zoom = 1.0
                    child_offset = QPointF(0, 0)
                elif not panel.image_pixmap.isNull() and not parent_draw.isNull():
                    pw = max(1.0, float(panel.image_pixmap.width()))
                    ph = max(1.0, float(panel.image_pixmap.height()))
                    child_cover = max(child_rect.width()/pw, child_rect.height()/ph)
                    desired_scale = parent_draw.width()/pw if pw > 0 else child_cover
                    child_zoom = max(0.05, desired_scale/max(1e-9, child_cover))
                    child_offset = parent_draw.center() - child_rect.center()
            child = PanelItem(
                child_rect,
                panel.image_path,
                child_zoom,
                [child_offset.x(), child_offset.y()],
                0.0,
                None,
                panel.image_effects,
                panel.panel_texts,
                panel.panel_overlays,
            )
            child.setTransformOriginPoint(child.rect().center())
            child.setPos(self._child_pos_preserving_panel_transform(panel, child_rect))
            child.setRotation(panel.rotation())
            child.setScale(panel.scale())
            child.setZValue(panel.zValue())
            child.setOpacity(panel.opacity())
            scene.addItem(child)
            children.append(child)

        scene.removeItem(panel)
        scene.clearSelection()
        for child in children:
            child.setSelected(True)
        self.record_history()
        self.statusBar().showMessage(
            f"{label}しました。画像引継ぎ: {'自動フィット' if image_mode == 'fit_each' else '元の見え方を維持'}",
            3200
        )

    def set_selected_panel_skew(self, value):
        changed=False
        for item in self.selected():
            if isinstance(item, PanelItem):
                item.skew=max(-0.35,min(0.35,float(value))); item.update(); changed=True
        if changed: self.statusBar().showMessage(f"コマ斜め率: {value:.2f}", 1300)

    def set_panel_skew_and_record(self, value):
        if hasattr(self, "panel_skew_spin"):
            self.panel_skew_spin.setValue(value)
        self.set_selected_panel_skew(value); self.record_history()

    def clear_panels(self):
        scene = self.current_scene()
        for item in list(scene.items()):
            if isinstance(item, PanelItem):
                scene.removeItem(item)

    def apply_n_panel_template_from_ui(self):
        count = self.n_panel_spin.value() if hasattr(self, "n_panel_spin") else 4
        layout = self.n_panel_layout.currentData() if hasattr(self, "n_panel_layout") else "vertical"
        self.apply_n_panel_template(count, layout)

    def _n_panel_rects(self, count, layout):
        count = max(1, int(count))
        margin, gap = 55, 24
        W, H = self.canvas_w - margin * 2, self.canvas_h - margin * 2
        min_size = 24.0
        rects = []

        if W < min_size or H < min_size:
            return []

        if layout == "horizontal":
            w = (W - gap * (count - 1)) / count
            if w < min_size:
                return []
            for i in range(count):
                rects.append(QRectF(margin + i * (w + gap), margin, w, H))
            return rects

        if layout == "vertical":
            h = (H - gap * (count - 1)) / count
            if h < min_size:
                return []
            for i in range(count):
                rects.append(QRectF(margin, margin + i * (h + gap), W, h))
            return rects

        cols = max(1, min(count, math.ceil(math.sqrt(count))))
        rows = math.ceil(count / cols)
        h = (H - gap * (rows - 1)) / rows
        if h < min_size:
            return []

        placed = 0
        for row in range(rows):
            remaining = count - placed
            items_in_row = min(cols, remaining)
            w = (W - gap * (items_in_row - 1)) / items_in_row
            if w < min_size:
                return []
            for col in range(items_in_row):
                rects.append(
                    QRectF(
                        margin + col * (w + gap),
                        margin + row * (h + gap),
                        w,
                        h,
                    )
                )
                placed += 1
        return rects

    def apply_n_panel_template(self, count, layout="vertical"):
        scene = self.current_scene()
        if not scene:
            return

        rects = self._n_panel_rects(count, layout)
        if not rects:
            QMessageBox.information(
                self,
                "Nコマ自動生成",
                "現在のページサイズ・コマ数・すき間では、コマが小さくなりすぎます。\n"
                "コマ数を減らすか、ページサイズを大きくしてください。"
            )
            return

        existing_panels = [
            i for i in scene.items()
            if i.parentItem() is None and isinstance(i, PanelItem)
        ]
        if existing_panels:
            important = sum(
                1 for p in existing_panels
                if p.image_path or p.panel_overlays or p.custom_points is not None
            )
            detail = (
                f"現在 {len(existing_panels)} 個のコマがあります。\n"
                f"そのうち内容あり/自由形状は {important} 個です。\n\n"
                "Nコマ自動生成を実行すると既存コマは置き換えられます。続けますか？"
            )
            answer = QMessageBox.question(
                self, "既存コマを置き換え", detail,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                return

        self.clear_panels()
        scene.clearSelection()
        made = []
        for rect in rects:
            item = PanelItem(rect); item.setZValue(0); scene.addItem(item); item.setSelected(True); made.append(item)
        self.record_history()
        self.refresh_layer_list()
        labels = {"vertical":"縦並び","horizontal":"横並び","grid":"自動グリッド"}
        self.statusBar().showMessage(f"{count}コマ / {labels.get(layout, layout)} を生成しました", 2500)

    def open_page_preview(self):
        self._prepare_pages_for_render()
        PagePreviewDialog(self).exec()

    def apply_panel_template(self, kind):
        scene = self.current_scene()
        self.clear_panels()
        margin, gap = 55, 24
        W, H = self.canvas_w-margin*2, self.canvas_h-margin*2
        rects = []
        if kind == "2h":
            h = (H-gap)/2
            rects = [QRectF(margin, margin, W, h), QRectF(margin, margin+h+gap, W, h)]
        elif kind == "4v":
            h = (H-gap*3)/4
            rects = [QRectF(margin, margin+i*(h+gap), W, h) for i in range(4)]
        elif kind == "2x2":
            w, h = (W-gap)/2, (H-gap)/2
            rects = [
                QRectF(margin, margin, w, h), QRectF(margin+w+gap, margin, w, h),
                QRectF(margin, margin+h+gap, w, h), QRectF(margin+w+gap, margin+h+gap, w, h),
            ]
        scene.clearSelection()
        for r in rects:
            item = PanelItem(r); item.setZValue(0); scene.addItem(item); item.setSelected(True)
        self.record_history()
        self.refresh_layer_list()

    def selected(self):
        scene = self.current_scene()
        return scene.selectedItems() if scene else []

    def selection_changed(self):
        items = self.selected()
        if not items:
            if hasattr(self, "selection_info_label"):                self.selection_info_label.setText("選択: なし")
            if hasattr(self, "corner_edit_button"):
                self.corner_edit_button.blockSignals(True)
                self.corner_edit_button.setChecked(False)
                self.corner_edit_button.blockSignals(False)
            # Keep list UI and canvas selection consistent.
            self.sync_layer_list_selection()
            return
        item = items[0]
        if hasattr(self, "selection_info_label"):
            self.selection_info_label.setText("選択: " + self._layer_label(item).replace("●  ", "").replace("○  ", ""))
        widgets = [self.scale_spin, self.rot_spin, self.opacity_spin, self.font_spin, self.bold_check, self.outline_spin, self.shadow_check, self.shadow_x_spin, self.shadow_y_spin]
        if hasattr(self, "vertical_gap_spin"):
            widgets.append(self.vertical_gap_spin)
        if hasattr(self, "font_combo"):
            widgets.append(self.font_combo)
        for w in widgets:
            w.blockSignals(True)
        self.scale_spin.setValue(max(0.03, abs(item.scale())))
        self.rot_spin.setValue(item.rotation())
        self.opacity_spin.setValue(int(round(item.opacity() * 100)))
        self.opacity_spin.setEnabled(not isinstance(item, PanelItem))
        if isinstance(item, (EditableTextItem, VerticalTextItem)):
            self.font_spin.setValue(item.font().pointSize()); self.bold_check.setChecked(item.font().bold())
            self.font_combo.setCurrentFont(item.font())
            self.outline_spin.setValue(item.outline_width)
            if isinstance(item, EditableTextItem):
                self.shadow_check.setChecked(item.shadow_enabled); self.shadow_x_spin.setValue(item.shadow_offset.x()); self.shadow_y_spin.setValue(item.shadow_offset.y())
            else:
                self.shadow_check.setChecked(False)
                if hasattr(self, "vertical_gap_spin"):
                    self.vertical_gap_spin.setValue(item.char_gap)
        elif isinstance(item, BubbleItem):
            self.font_spin.setValue(item.text_item.font().pointSize()); self.bold_check.setChecked(item.text_item.font().bold())
            self.font_combo.setCurrentFont(item.text_item.font())
            self.outline_spin.setValue(0.0)
        elif isinstance(item, PanelItem):
            if item.custom_points is not None:
                self.statusBar().showMessage("自由コマ: 四隅の○をドラッグ / Shift+ドラッグで画像移動 / Alt+ホイールで画像ズーム")
            elif item.image_path:
                self.statusBar().showMessage("コマ画像: Shift+ドラッグで中身移動 / Alt+ホイールで中身ズーム / ダブルクリックで差し替え")
            else:
                self.statusBar().showMessage("コマ: 画像をドロップ、またはダブルクリックで画像を設定")
        for w in widgets:
            w.blockSignals(False)
        if hasattr(self, "corner_edit_button"):
            self.corner_edit_button.blockSignals(True)
            self.corner_edit_button.setChecked(isinstance(item, PanelItem) and item.corner_edit_enabled)
            self.corner_edit_button.blockSignals(False)
        self.sync_layer_list_selection()

    def _layer_label(self, item):
        visible = "●" if item.isVisible() else "○"
        if isinstance(item, ImageItem):
            name = Path(item.source_path).name if item.source_path else "画像"
            kind = f"画像: {name}"
        elif isinstance(item, EditableTextItem):
            text = item.toPlainText().replace("\n", " ")[:28]
            kind = f"文字: {text}"
        elif isinstance(item, VerticalTextItem):
            text = item.text_value.replace("\n", " ")[:28]
            kind = f"縦書き: {text}"
        elif isinstance(item, BubbleItem):
            text = item.text_item.toPlainText().replace("\n", " ")[:24]
            kind = f"吹き出し: {text}"
        elif isinstance(item, PanelItem):
            if item.image_path:
                pname = Path(item.image_path).name
                kind = (
                    ("コマ画像（自由四隅）: " if item.custom_points is not None else "コマ画像: ")
                    + pname
                )
            else:
                kind = "コマ（自由四隅）" if item.custom_points is not None else "コマ"
        else:
            kind = item.__class__.__name__
        return f"{visible}  z={item.zValue():g}  {kind}"

    def refresh_layer_list(self):
        scene = self.current_scene()
        self.layer_item_refs = (
            [i for i in scene.items(Qt.DescendingOrder) if i.parentItem() is None]
            if scene else []
        )

        for name in ("layer_list", "quick_layer_list"):
            widget = getattr(self, name, None)
            if widget is None:
                continue
            widget.blockSignals(True)
            widget.clear()
            for obj in self.layer_item_refs:
                widget.addItem(self._layer_label(obj))
            widget.blockSignals(False)

        count = len(self.layer_item_refs)
        if hasattr(self, "layer_count_label"):
            self.layer_count_label.setText(f"このページ: {count}個")
        if hasattr(self, "quick_layer_count"):
            self.quick_layer_count.setText(f"{count}個")
        self.sync_layer_list_selection()

    def sync_layer_list_selection(self):
        if not hasattr(self, "layer_item_refs"):
            return
        selected = self.selected()
        row = -1
        if selected:
            try:
                row = self.layer_item_refs.index(selected[0])
            except ValueError:
                row = -1
        for name in ("layer_list", "quick_layer_list"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.blockSignals(True)
                widget.setCurrentRow(row)
                widget.blockSignals(False)

    def _current_layer_object(self, source=None):
        if not hasattr(self, "layer_item_refs"):
            return None
        if source is None:
            source = getattr(self, "layer_list", None)
            if source is None or source.currentRow() < 0:
                source = getattr(self, "quick_layer_list", None)
        if source is None:
            return None
        row = source.currentRow()
        if row < 0 or row >= len(self.layer_item_refs):
            return None
        obj = self.layer_item_refs[row]
        return obj if obj.scene() == self.current_scene() else None

    def select_layer_from_list(self, source=None):
        obj = self._current_layer_object(source)
        scene = self.current_scene()
        if obj is None or scene is None:
            return
        scene.clearSelection()
        if obj.isVisible():
            obj.setSelected(True)
            self.view.ensureVisible(obj, 80, 80)
            self.sync_layer_list_selection()
        else:
            # Hidden objects cannot become a QGraphicsScene selection. Keep the
            # list row focused so the visibility button can restore them.
            try:
                row = self.layer_item_refs.index(obj)
            except ValueError:
                row = -1
            if row >= 0:
                for name in ("layer_list", "quick_layer_list"):
                    widget = getattr(self, name, None)
                    if widget is not None:
                        widget.blockSignals(True); widget.setCurrentRow(row); widget.blockSignals(False)

    def edit_layer_from_list(self, source=None):
        obj = self._current_layer_object(source)
        if obj is None:
            return
        self.select_layer_from_list(source)

        if isinstance(obj, EditableTextItem):
            text, ok = QInputDialog.getMultiLineText(
                self, "文字編集", "文字:", obj.toPlainText()
            )
            if ok:
                obj.setPlainText(text)
                self.record_history()
        elif isinstance(obj, VerticalTextItem):
            text, ok = QInputDialog.getMultiLineText(
                self, "縦書き編集", "文字:", obj.text_value
            )
            if ok:
                obj.text_value = text
                obj.prepareGeometryChange()
                obj.update()
                self.record_history()
        elif isinstance(obj, BubbleItem):
            text, ok = QInputDialog.getMultiLineText(
                self, "吹き出し編集", "セリフ:", obj.text_item.toPlainText()
            )
            if ok:
                obj.text_item.setPlainText(text)
                self.record_history()
        elif isinstance(obj, PanelItem):
            self.open_panel_detail(obj)

    def toggle_layer_visibility(self, source=None):
        obj = self._current_layer_object(source)
        if obj is None:
            return
        try:
            keep_row = self.layer_item_refs.index(obj)
        except (ValueError, AttributeError):
            keep_row = -1
        will_show = not obj.isVisible()
        if not will_show:
            obj.setSelected(False)
        obj.setVisible(will_show)
        self.record_history()
        self.refresh_layer_list()
        # Keep the hidden row focused so the same button can restore it.
        if 0 <= keep_row < len(getattr(self, "layer_item_refs", [])):
            for name in ("layer_list", "quick_layer_list"):
                widget = getattr(self, name, None)
                if widget is not None:
                    widget.blockSignals(True)
                    widget.setCurrentRow(keep_row)
                    widget.blockSignals(False)

    def move_layer_z(self, delta):
        obj = self._current_layer_object()
        if obj is None:
            return
        obj.setZValue(obj.zValue() + float(delta))
        self.record_history()
        self.refresh_layer_list()

    def layer_to_front(self):
        self.item_to_front(self._current_layer_object())

    def layer_to_back(self):
        self.item_to_back(self._current_layer_object())

    def set_selected_vertical_gap(self, value):
        changed = False
        for item in self.selected():
            if isinstance(item, VerticalTextItem):
                item.prepareGeometryChange()
                item.char_gap = float(value)
                item.update()
                changed = True
        if changed:
            self.statusBar().showMessage(f"縦書き文字間隔: {value:.1f}px", 1200)

    def set_selected_scale(self, value):
        for item in self.selected():
            item.setScale(value)

    def set_selected_rotation(self, value):
        for item in self.selected():
            item.setRotation(value)

    def set_selected_opacity(self, value):
        opacity = max(0.0, min(1.0, float(value) / 100.0))
        changed = False
        for item in self.selected():
            if isinstance(item, (ImageItem, EditableTextItem, VerticalTextItem, BubbleItem)):
                item.setOpacity(opacity); changed = True
        if changed:
            self.statusBar().showMessage(f"不透明度: {int(value)}%", 1200)

    def set_selected_font_size(self, value):
        for item in self.selected():
            if isinstance(item, (EditableTextItem, VerticalTextItem)):
                f = item.font(); f.setPointSize(value); item.setFont(f)
            elif isinstance(item, BubbleItem):
                f = item.text_item.font(); f.setPointSize(value); item.text_item.setFont(f)

    def set_selected_outline_width(self, value):
        changed = False
        for item in self.selected():
            if isinstance(item, (EditableTextItem, VerticalTextItem)):
                item.outline_width = float(value); item.prepareGeometryChange(); item.update(); changed = True
        if changed: self.statusBar().showMessage(f"文字の縁幅: {value:.1f}", 1500)

    def set_selected_shadow(self, *_args):
        changed=False
        for item in self.selected():
            if isinstance(item, EditableTextItem):
                item.shadow_enabled = self.shadow_check.isChecked()
                item.shadow_offset = QPointF(self.shadow_x_spin.value(), self.shadow_y_spin.value())
                item.prepareGeometryChange(); item.update(); changed=True
        if changed: self.statusBar().showMessage("横書き文字の影設定を変更しました", 1200)

    def choose_text_color(self):
        current = QColor("#000000")
        for item in self.selected():
            if isinstance(item, EditableTextItem): current = item.defaultTextColor(); break
            if isinstance(item, VerticalTextItem): current = item.color; break
            if isinstance(item, BubbleItem): current = item.text_item.defaultTextColor(); break
        color = QColorDialog.getColor(current, self, "文字色")
        if not color.isValid(): return
        changed = False
        for item in self.selected():
            if isinstance(item, EditableTextItem): item.setDefaultTextColor(color); changed = True
            elif isinstance(item, VerticalTextItem): item.color = color; item.update(); changed = True
            elif isinstance(item, BubbleItem): item.text_item.setDefaultTextColor(color); changed = True
        if changed: self.record_history()

    def choose_outline_color(self):
        texts = [i for i in self.selected() if isinstance(i, (EditableTextItem, VerticalTextItem))]
        if not texts:
            QMessageBox.information(self, "縁取り", "横書きまたは縦書き文字を選択してください。")
            return
        color = QColorDialog.getColor(texts[0].outline_color, self, "縁取り色")
        if not color.isValid(): return
        for item in texts:
            item.outline_color = color; item.update()
        self.record_history()

    def set_selected_bold(self, state):
        checked = bool(state)
        for item in self.selected():
            if isinstance(item, (EditableTextItem, VerticalTextItem)):
                f = item.font(); f.setBold(checked); item.setFont(f)
            elif isinstance(item, BubbleItem):
                f = item.text_item.font(); f.setBold(checked); item.text_item.setFont(f)

    def mirror_selected(self):
        changed = False
        for item in self.selected():
            if isinstance(item, ImageItem):
                t = item.transform(); t.scale(-1, 1); item.setTransform(t); changed = True
        if changed:
            self.record_history()

    def _effect_history_bucket(self):
        if self.current_page_index < 0 or self.current_page_index >= len(self.pages):
            return None
        page_id = self.pages[self.current_page_index].get("id")
        if not page_id:
            return None
        return self.effect_history_by_page.setdefault(
            page_id,
            {"undo": [], "redo": []},
        )

    def _effect_state_for_item(self, item):
        if isinstance(item, ImageItem):
            return {
                "kind": "image",
                "effects": json.loads(json.dumps(item.effects)),
            }
        if isinstance(item, PanelItem):
            return {
                "kind": "panel",
                "image_effects": json.loads(json.dumps(item.image_effects)),
                "panel_overlays": json.loads(json.dumps(item.panel_overlays)),
            }
        return None

    def _capture_effect_state(self, targets=None):
        scene = self.current_scene()
        if scene is None:
            return {}

        if targets is None:
            targets = [
                item for item in scene.items()
                if item.parentItem() is None
                and isinstance(item, (ImageItem, PanelItem))
            ]

        snapshot = {}
        for item in targets:
            if (
                not isinstance(item, (ImageItem, PanelItem))
                or item.scene() is not scene
            ):
                continue
            uid = str(getattr(item, "effect_uid", "") or "")
            if not uid:
                item.effect_uid = uuid.uuid4().hex
                uid = item.effect_uid
            state = self._effect_state_for_item(item)
            if state is not None:
                snapshot[uid] = state
        return snapshot

    def _effect_item_map(self):
        scene = self.current_scene()
        if scene is None:
            return {}
        result = {}
        for item in scene.items():
            if (
                item.parentItem() is None
                and isinstance(item, (ImageItem, PanelItem))
            ):
                uid = str(getattr(item, "effect_uid", "") or "")
                if uid:
                    result[uid] = item
        return result

    def _apply_effect_state(self, snapshot):
        item_map = self._effect_item_map()
        applied = 0

        for uid, state in (snapshot or {}).items():
            item = item_map.get(uid)
            if item is None:
                continue

            kind = state.get("kind")
            if isinstance(item, ImageItem) and kind == "image":
                item.effects = json.loads(
                    json.dumps(state.get("effects", []))
                )
                item.rebuild_effects()
                applied += 1

            elif isinstance(item, PanelItem) and kind == "panel":
                item.image_effects = json.loads(
                    json.dumps(state.get("image_effects", []))
                )
                item.panel_overlays = json.loads(
                    json.dumps(state.get("panel_overlays", []))
                )
                item.rebuild_image_effects()
                item.update()
                applied += 1

        self.view.viewport().update()
        return applied

    def _push_effect_history(self, before, after, label):
        if before == after:
            self._update_effect_history_buttons()
            return False

        bucket = self._effect_history_bucket()
        if bucket is None:
            return False

        bucket["undo"].append({
            "before": json.loads(json.dumps(before)),
            "after": json.loads(json.dumps(after)),
            "label": str(label),
        })
        if len(bucket["undo"]) > 50:
            bucket["undo"].pop(0)
        bucket["redo"].clear()
        self._update_effect_history_buttons()
        return True

    def _finish_effect_change(self, before, label, targets=None):
        after = self._capture_effect_state(targets)
        if not self._push_effect_history(before, after, label):
            return False
        self.record_history()
        return True

    def _update_effect_history_buttons(self):
        bucket = self._effect_history_bucket()
        undo_count = len(bucket["undo"]) if bucket else 0
        redo_count = len(bucket["redo"]) if bucket else 0

        if hasattr(self, "effect_undo_btn"):
            self.effect_undo_btn.setEnabled(undo_count > 0)
            self.effect_undo_btn.setText(
                f"効果Undo ({undo_count})"
                if undo_count else "効果Undo"
            )
        if hasattr(self, "effect_redo_btn"):
            self.effect_redo_btn.setEnabled(redo_count > 0)
            self.effect_redo_btn.setText(
                f"効果Redo ({redo_count})"
                if redo_count else "効果Redo"
            )

    def effect_undo(self):
        bucket = self._effect_history_bucket()
        if not bucket or not bucket["undo"]:
            self.statusBar().showMessage(
                "戻せるエフェクト操作はありません",
                1500,
            )
            self._update_effect_history_buttons()
            return

        while bucket["undo"]:
            entry = bucket["undo"].pop()
            applied = self._apply_effect_state(entry["before"])
            if applied:
                bucket["redo"].append(entry)
                self.record_history()
                self.statusBar().showMessage(
                    f"効果Undo: {entry['label']}",
                    1800,
                )
                self._update_effect_history_buttons()
                return

        self.statusBar().showMessage(
            "効果Undoの対象レイヤーが見つかりませんでした",
            1800,
        )
        self._update_effect_history_buttons()

    def effect_redo(self):
        bucket = self._effect_history_bucket()
        if not bucket or not bucket["redo"]:
            self.statusBar().showMessage(
                "やり直せるエフェクト操作はありません",
                1500,
            )
            self._update_effect_history_buttons()
            return

        while bucket["redo"]:
            entry = bucket["redo"].pop()
            applied = self._apply_effect_state(entry["after"])
            if applied:
                bucket["undo"].append(entry)
                self.record_history()
                self.statusBar().showMessage(
                    f"効果Redo: {entry['label']}",
                    1800,
                )
                self._update_effect_history_buttons()
                return

        self.statusBar().showMessage(
            "効果Redoの対象レイヤーが見つかりませんでした",
            1800,
        )
        self._update_effect_history_buttons()

    def _selected_image_targets(self):
        return [i for i in self.selected() if isinstance(i, ImageItem) or (isinstance(i, PanelItem) and i.image_path)]

    def apply_selected_image_effect(self):
        targets = self._selected_image_targets()
        if not targets:
            QMessageBox.information(self, "画像エフェクト", "画像または画像入りコマを選択してください。\nエフェクトモードでは画像部分をクリックすると選択できます。")
            return
        before = self._capture_effect_state(targets)
        key = self.image_effect.currentData()
        for item in targets:
            def add(kind, **kw):
                return item.add_simple_effect(kind, **kw) if isinstance(item, ImageItem) else item.add_image_simple_effect(kind, **kw)
            if key == "bright": add("brightness", factor=1.18)
            elif key == "dark": add("brightness", factor=0.82)
            elif key == "contrast_up": add("contrast", factor=1.20)
            elif key == "contrast_down": add("contrast", factor=0.82)
            elif key == "blur": add("blur", radius=3.0)
            else: add(key)
        self._finish_effect_change(
            before,
            "画像エフェクト適用",
            targets,
        )

    def mosaic_selected(self):
        targets = self._selected_image_targets()
        before = self._capture_effect_state(targets)
        changed = False
        for item in targets:
            if isinstance(item, ImageItem):
                changed = item.add_mosaic_effect(None) or changed
            else:
                changed = item.add_image_mosaic_effect(None) or changed
        if changed:
            self._finish_effect_change(
                before,
                "画像全体モザイク",
                targets,
            )

    def start_region_mosaic(self):
        targets = self._selected_image_targets()
        if len(targets) != 1:
            QMessageBox.information(self, "範囲モザイク", "画像または画像入りコマを1つ選択してください。")
            return
        self.view.start_region_mosaic(targets[0])

    def restore_selected_image(self):
        targets = self._selected_image_targets()
        before = self._capture_effect_state(targets)
        changed = False
        for item in targets:
            if isinstance(item, ImageItem) and item.effects:
                item.restore_original(); changed = True
            elif isinstance(item, PanelItem):
                if item.image_effects:
                    changed = item.restore_image_effects() or changed
                if item.panel_overlays:
                    changed = item.clear_panel_overlays() or changed
        if changed:
            self._finish_effect_change(
                before,
                "エフェクト全解除",
                targets,
            )

    def item_to_front(self, obj):
        scene = self.current_scene()
        if (
            obj is None
            or scene is None
            or obj.scene() is not scene
        ):
            return
        others = [
            i.zValue()
            for i in scene.items()
            if i.parentItem() is None and i is not obj
        ]
        obj.setZValue((max(others) if others else 0.0) + 10.0)
        self.record_history()
        self.refresh_layer_list()

    def item_to_back(self, obj):
        scene = self.current_scene()
        if (
            obj is None
            or scene is None
            or obj.scene() is not scene
        ):
            return
        others = [
            i.zValue()
            for i in scene.items()
            if i.parentItem() is None and i is not obj
        ]
        obj.setZValue((min(others) if others else 0.0) - 10.0)
        self.record_history()
        self.refresh_layer_list()

    def change_z(self, delta):
        if not self.selected():
            return
        for item in self.selected():
            item.setZValue(item.zValue()+delta)
        self.record_history()
        self.refresh_layer_list()

    def delete_selected(self):
        scene = self.current_scene()
        if not scene:
            return
        items = [i for i in self.selected() if i.parentItem() is None]
        if not items:
            return
        for item in items:
            scene.removeItem(item)
        self.record_history()
        self.refresh_layer_list()

    def copy_selection(self):
        self.clipboard_data = json.loads(json.dumps([
            i.serialize()
            for i in self.selected()
            if i.parentItem() is None and hasattr(i, "serialize")
        ]))
        if self.clipboard_data:
            self.statusBar().showMessage(f"{len(self.clipboard_data)}個コピーしました", 1500)

    def paste_selection(self):
        if not self.clipboard_data:
            return
        scene = self.current_scene()
        scene.clearSelection()
        made = self.deserialize_scene(scene, self.clipboard_data, QPointF(35, 35))
        for item in made:
            item.setSelected(True)
        self.record_history()

    def duplicate_selection(self):
        self.copy_selection(); self.paste_selection()

    def show_debug_checklist(self):
        text = """Manga Peta Editor v1.8.4-debug 現役デバッグリスト

※ 「0で合格済み・注釈なし」の過去項目は整理して削除しました。
※ ここには未確認、問題報告があったもの、今回追加したものだけを残しています。

【四隅編集 / 吸着】
□ 複数コマ・複数ハンドルで、異なる頂点が別々の吸着候補へ近づいた時に1点だけが不自然に優先されない
□ X方向とY方向で別々のハンドルが吸着できる場合、両方を同時に満たす
□ 2点以上を選択してドラッグすると、選択点同士の形を崩さず剛体的に平行移動する
□ Shift+辺選択した辺をドラッグしても角度が変わらず平行移動する
□ 単一点を動かし、隣辺が対面辺と平行になる位置へ近づくと平行吸着する
□ 掴んでいない選択ハンドルがページ外枠/非選択コマへ近づいてもグループ全体が吸着する

【選択 / 配置】
□ 選択/配置モードで画像・文字・コマを移動するとページ端/中央/他レイヤーへ吸着する
□ 複数選択でも相対位置を変えずに吸着する
□ 「吸着・間隔の詳細設定」で選択/配置の吸着距離を変更すると実際の吸着距離も変わる

【N分割 / 高度分割】
□ 自由四隅コマで通常N分割が引き続き動く
□ Shift+辺選択中でも通常N分割が動く
□ 高度分割の分割線スライドが動く
□ 高度分割後も元画像の見え方が大きく変わらない
□ 通常N分割/高度分割の「隙間」は詳細設定から変更できる

【エフェクト】
□ コマ詳細→エフェクトタブでEffect素材の追加・選択・調整ができる
□ 適用済みエフェクトをダブルクリック/「調整…」で同じコマ詳細ワークフロー内から開ける
□ エフェクト調整で画像ドラッグ=移動、青丸=縦横比固定拡縮、橙丸=回転ができる
□ エフェクトの自由配置+回転が保存→再読込後も残る
□ コマエフェクトが回転/移動してもコマ外はクリップされる

【UI整理】
□ 左・中央プレビュー・右プロパティの境界をドラッグして中央プレビュー位置/幅を調節できる
□ モード切替タブが右プロパティ欄上部にあり、Ctrl+1〜6とも同期する
□ コマ詳細が「コマ画像 / 作品内画像 / エフェクト」のタブで切替できる
□ 保存操作が上部の「保存」メニューにまとまっている
□ PNG/ZIP/完成プレビューが上部の「出力」メニューにまとまっている
□ 吸着距離・コマ間隔・外枠間隔・分割隙間が詳細設定へまとまっている

【レイヤー詳細】
□ 左のレイヤーを右クリック→「詳細データを表示」でプレビューが開く
□ 画像レイヤーは画像サムネイルとファイル/位置/倍率/回転/エフェクト等のJSONが見える
□ 文字/縦書き/吹き出しは本文とフォント等のデータが見える
□ コマは画像・四隅・エフェクト等のデータが見える
□ 詳細データをクリップボードへコピーできる

【PNG / ZIP】
□ PNG初期名が「漫画タイトル_001_ページ名.png」の形式になる
□ 2ページ目なら「漫画タイトル_002_ページ名.png」になる
□ ZIP内の各PNGも同じページ番号付き形式になる
□ PNG/ZIP出力直前の編集が欠落しない

【プロジェクト保存】
□ 初回Ctrl+Sで「名前を付けて保存」が1回だけ開き、既定場所がアプリ直下 Projects/漫画タイトル になる
□ 保存後のCtrl+Sはダイアログを出さず同じ.mpe.jsonへ上書きする
□ 「名前を付けて保存」は1つの保存ダイアログだけで別名保存できる
□ 名前を付けて保存後は、その新しいファイルが以後Ctrl+Sの上書き先になる
□ 「日付保存」はダイアログなしで現在プロジェクトフォルダ（未保存ならProjects/タイトル）へ保存する
□ 日付保存後もCtrl+Sの上書き先はメインプロジェクトのまま

【以前から未確認の回帰項目】
□ 元画像を移動した後も、保存済みassetsからページ往復/Undo/Redoで画像が復元される
□ 保存/自動復旧/完成プレビュー/PNG・ZIP出力だけではUndo履歴が増えない
□ 1366×768程度でも起動時に下端が画面外へ出ない
□ 無関係な.pyをD&Dしてもapp.pyを壊さず拒否する
□ 画像プロパティの「中心位置固定」「原寸/ページ内/中央」「保存→再読込」が正常

【v1.8.1追加監査: コマサイズ / Tab四隅編集】
□ コマを選択してキャンバス上でTab → 四隅編集ONになる
□ もう一度Tab → 四隅編集OFFになる
□ 複数コマ選択時もTabでまとめてON/OFFできる
□ 四隅編集ONでは右下シアン丸ではなく4頂点ハンドルを編集する
□ 四隅編集OFF後、右下シアン丸が自由四隅形状の実際の右下へ表示される
□ 四隅編集OFF後のシアン丸で自由四隅形状全体をリサイズできる
□ 通常コマを100px未満まで縮小でき、24px付近まで縮小できる
□ 画像入りコマでも100px制限に引っかからない
□ 四隅編集でも古いrect中心に縛られず小さくできる
□ 複数頂点移動時も古いrect中心の固定制限で急に止まらない
□ レイヤー詳細にcyan handle / w,h / scale / custom_pointsの説明が表示される
□ 自由四隅を小さくした後も古い大きなrectが選択範囲として残らない

【v1.8.2追加監査: 静的監査で見つけたバグ】
□ 右下シアン丸の当たり判定が大きすぎず、丸の近くにある画像/文字/コマも選択しやすい
□ 自由四隅を変形した後、回転/倍率変更が古いrect中心ではなく見た目中心を軸に動く
□ 自由四隅をシアン丸でリサイズ後も回転中心が見た目中心になる
□ 自由四隅→保存→再読込後も回転中心が見た目中心になる
□ レイヤー左側一覧を右クリック→表示/非表示で、右クリックしたそのレイヤーが切り替わる
□ Effect素材を使った作品を保存すると、タイトル_assets/effectsへEffect画像も内包される
□ 元のEffectフォルダ素材を一時的に移動しても、保存済みプロジェクトを再読込するとエフェクトが表示される
□ 通常画像のEffect素材・コマEffect素材の両方が内包される
□ 更新用.pyをD&Dする直前に未保存編集があっても、自動再起動後の復旧データが消えない
□ 更新再起動後に復旧を選ぶと更新直前のページ内容へ戻れる
□ 未保存状態でウィンドウを閉じると「保存 / 保存しない / キャンセル」が出る
□ 未保存状態で新規作成・別プロジェクトを開く時も確認が出る
□ 保存済みで変更が無い時は終了確認が出ない
□ 日付保存だけした状態では、メインプロジェクト未保存変更の扱いが勝手に消えない

【v1.8.3追加監査: 追加静的監査】
□ 保存済みで変更なしの状態で90秒以上待っても不要な復旧ファイルが残らない
□ 保存済みで変更なし→更新D&D→再起動しても「復旧しますか？」が不要に出ない
□ 未保存変更あり→更新D&Dでは従来どおり復旧データが残る
□ 画像を右クリック→最前面へ で、z=1000等のレイヤーがあっても本当に最前面になる
□ 画像を右クリック→最背面へ も本当に最背面になる
□ 文字/縦書き/吹き出しの右クリック最前面・最背面がその対象自身へ確実に効く
□ 分割した複数コマのうち1コマだけ画像補正を変更しても別コマへ内部値が波及しない
□ コピー後に元レイヤーのエフェクトを変更してから貼り付けても、コピー時点の状態で貼られる
□ ページ名変更直後に左のページ表示・ページ情報・サムネイルが更新される
□ Effect素材が欠損しているプロジェクトを開くと欠損パスが警告される
□ 漫画タイトル/ページ名がCON・AUX・非常に長い文字列でも保存名生成で失敗しにくい

【v1.8.4追加監査: エフェクト専用Undo / Undo回帰】
□ エフェクトモードの「効果Undo」で直前の画像補正だけ戻り、位置・文字・コマ形状は戻らない
□ 「効果Redo」で戻したエフェクトだけ再適用できる
□ Ctrl+Alt+Z / Ctrl+Alt+Yでも効果Undo/Redoできる
□ 白黒→ぼかし→Effect素材追加の順に操作し、効果Undoを3回するとその3操作だけ逆順に戻る
□ 通常画像とコマ画像を同時選択してエフェクト適用→効果Undoで両方戻る
□ 範囲モザイクも効果Undo/Redo対象になる
□ Effect素材の移動・倍率・回転調整も効果Undo/Redo対象になる
□ Effect素材削除/全解除も効果Undoで復元できる
□ ページを切り替えると、そのページ固有の効果Undo件数が表示される
□ コピー&ペーストした画像は元画像と別の効果履歴IDを持ち、片方の効果Undoが他方へ誤適用されない
□ コマ詳細エフェクトタブ内の効果Undo/Redoで、OK前に補正・Effect素材操作を戻せる
□ コマ詳細をOKした後、その変更をメイン側の効果Undoでも戻せる
□ 倍率/回転の数値欄を変更してeditingFinished前にCtrl+Zしても、その最新変更が1段目として戻る
□ Undo後に未確定の新しい編集をしてCtrl+Yすると、古いRedo分岐へ勝手に戻らない
□ Undo/Redo後にレイヤー数・ページ情報・サムネイル表示が古いまま残らない

【後回し】
・吹き出しの種類をさらに増やす

不具合が出たら:
1. 何を押した直後か
2. 選択していたもの
3. ページ番号
4. 「診断情報」の内容
5. Traceback全文（あれば）
"""
        dlg = QDialog(self)
        dlg.setWindowTitle("デバッグ確認リスト")
        dlg.resize(760, 780)
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        lay.addWidget(edit, 1)
        copy_btn = QPushButton("リストをクリップボードへコピー")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(text))
        lay.addWidget(copy_btn)
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)
        dlg.exec()

    def show_diagnostics(self):
        scene = self.current_scene()
        objs = [i for i in scene.items() if i.parentItem() is None] if scene else []
        counts = {
            "画像": sum(isinstance(i, ImageItem) for i in objs),
            "コマ": sum(isinstance(i, PanelItem) for i in objs),
            "文字": sum(isinstance(i, (EditableTextItem, VerticalTextItem)) for i in objs),
            "吹き出し": sum(isinstance(i, BubbleItem) for i in objs),
        }
        lines = [
            f"Manga Peta Editor {APP_VERSION}",
            f"Version parser test: {parse_project_version(APP_VERSION):.1f}",
            f"ページサイズ: {self.canvas_w} x {self.canvas_h}",
            f"ページ数: {len(self.pages)} / 現在: {self.current_page_index+1}",
            f"現在ページのオブジェクト: {len(objs)}",
            *(f"  {k}: {v}" for k, v in counts.items()),
            f"選択数: {len(self.selected())}",
            f"モード: {self.mode_combo.currentText() if hasattr(self, 'mode_combo') else '-'}",
        ]
        selected_panels = [
            i for i in self.selected()
            if isinstance(i, PanelItem)
        ]
        if len(selected_panels) == 1:
            p = selected_panels[0]
            vb = p.visual_bounds()
            lines.extend([
                f"選択コマ rect(w,h): {p.rect().width():.1f}, {p.rect().height():.1f}",
                f"選択コマ visual(w,h): {vb.width():.1f}, {vb.height():.1f}",
                f"選択コマ scale: {p.scale():.3f}",
                f"選択コマ custom_points: {'あり' if p.custom_points is not None else 'なし'}",
                f"選択コマ corner_edit: {p.corner_edit_enabled}",
            ])
        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "診断情報（クリップボードへコピー済み）", text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.delete_selected(); return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_C:
            self.copy_selection(); return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_V:
            self.paste_selection(); return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_D:
            self.duplicate_selection(); return
        super().keyPressEvent(event)

    def _project_asset_dir_name(self, project_path=None):
        project_path = Path(project_path or self.project_path or "project.mpe.json")
        title = ""
        if hasattr(self, "title_edit"):
            title = self.title_edit.text().strip()
        if not title:
            title = project_path.parent.name or project_path.stem
        safe = self._safe_filename_component(title, "project")
        return safe + "_assets"

    def _resolve_project_path(self, raw_path):
        if not raw_path:
            return ""
        p = Path(str(raw_path))
        if p.is_absolute() and p.exists():
            return str(p)

        if self.active_project_dir:
            candidate = (Path(self.active_project_dir) / p).resolve()
            if candidate.exists():
                return str(candidate)

            # A project folder may have been renamed or moved. Try the basename.
            basename = p.name
            search_roots = []
            if self.active_assets_dir:
                search_roots.append(Path(self.active_assets_dir))
            search_roots.append(Path(self.active_project_dir))
            for root in search_roots:
                if root.exists():
                    try:
                        for found in root.rglob(basename):
                            if found.is_file():
                                return str(found.resolve())
                    except Exception:
                        pass

        return str(p)

    def _asset_hash(self, path):
        h = hashlib.sha1()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _copy_asset_to_project(self, source_path, assets_dir):
        if not source_path:
            return "", None
        source = Path(self._resolve_project_path(source_path))
        if not source.exists() or not source.is_file():
            return str(source_path), None

        assets_dir.mkdir(parents=True, exist_ok=True)
        try:
            digest = self._asset_hash(source)[:12]
        except Exception:
            digest = hashlib.sha1(
                str(source.resolve()).encode("utf-8", "ignore")
            ).hexdigest()[:12]

        # Reuse any already-copied file with the same content hash,
        # even if its original filename was different.
        existing = next(iter(assets_dir.glob(f"{digest}_*")), None)
        if existing and existing.is_file():
            return existing.name, str(existing.resolve())

        safe_name = "".join(
            c if c.isalnum() or c in "-_ ." else "_" for c in source.name
        )
        dest = assets_dir / f"{digest}_{safe_name}"
        if not dest.exists():
            shutil.copy2(source, dest)
        return dest.name, str(dest.resolve())

    def _resolve_overlay_asset_ref(self, ref):
        if not ref:
            return ""
        raw = str(ref)
        normalized = raw.replace("\\", "/")
        if normalized.lower().startswith("effect/"):
            return str(resolve_effect_path(raw))
        p = Path(raw)
        if p.is_absolute():
            return str(p)
        return str(Path(self._resolve_project_path(raw)))

    def _bundle_overlay_list(self, overlays, assets_dir, assets_name, mapping, missing):
        if not overlays:
            return
        effect_dir = assets_dir / "effects"
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            raw = str(overlay.get("path", "") or "")
            if not raw:
                continue

            resolved = self._resolve_overlay_asset_ref(raw)
            rel_name, absolute_dest = self._copy_asset_to_project(
                resolved,
                effect_dir,
            )
            if absolute_dest:
                portable = (
                    Path(assets_name) / "effects" / rel_name
                ).as_posix()
                overlay["path"] = portable
                mapping[raw] = absolute_dest
                mapping[resolved] = absolute_dest
            else:
                missing.append(raw)

    def _bundle_project_assets(self, data, target):
        target = Path(target)
        assets_name = self._project_asset_dir_name(target)
        assets_dir = target.parent / assets_name
        mapping = {}
        missing = []

        copied = json.loads(json.dumps(data))
        for page in copied.get("pages", []):
            for item in page.get("items", []):
                key = None
                if item.get("type") == "image":
                    key = "source_path"
                elif item.get("type") == "panel":
                    key = "image_path"
                if not key or not item.get(key):
                    continue
                original = str(item.get(key))
                rel_name, absolute_dest = self._copy_asset_to_project(
                    original, assets_dir
                )
                if absolute_dest:
                    item[key] = str(Path(assets_name) / rel_name).replace("\\", "/")
                    # Keep both the serialized path and its currently-resolved
                    # absolute path as lookup keys. Loaded projects often have
                    # relative paths in page snapshots but absolute paths in live scenes.
                    mapping[original] = absolute_dest
                    try:
                        resolved_original = str(Path(self._resolve_project_path(original)))
                        mapping[resolved_original] = absolute_dest
                    except Exception:
                        pass
                else:
                    missing.append(original)

                # Continue below so Effect overlay assets are bundled as well.

            for item in page.get("items", []):
                if item.get("type") == "image":
                    overlay_effects = [
                        e for e in item.get("effects", [])
                        if isinstance(e, dict) and e.get("type") == "overlay"
                    ]
                    self._bundle_overlay_list(
                        overlay_effects,
                        assets_dir,
                        assets_name,
                        mapping,
                        missing,
                    )
                elif item.get("type") == "panel":
                    image_overlay_effects = [
                        e for e in item.get("image_effects", [])
                        if isinstance(e, dict) and e.get("type") == "overlay"
                    ]
                    self._bundle_overlay_list(
                        image_overlay_effects,
                        assets_dir,
                        assets_name,
                        mapping,
                        missing,
                    )
                    self._bundle_overlay_list(
                        item.get("panel_overlays", []),
                        assets_dir,
                        assets_name,
                        mapping,
                        missing,
                    )

        copied["asset_dir"] = assets_name
        copied["portable_assets"] = True
        return copied, mapping, sorted(set(missing)), assets_dir

    def _lookup_rebound_asset(self, path_value, mapping):
        if not path_value or not mapping:
            return None
        raw = str(path_value)
        if raw in mapping:
            return mapping[raw]
        try:
            resolved = str(Path(self._resolve_project_path(raw)))
            if resolved in mapping:
                return mapping[resolved]
        except Exception:
            pass
        return None

    def _rebind_overlay_paths(self, overlays, mapping):
        if not overlays:
            return
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            raw = str(overlay.get("path", "") or "")
            if not raw:
                continue
            rebound = mapping.get(raw)
            if not rebound:
                resolved = self._resolve_overlay_asset_ref(raw)
                rebound = mapping.get(resolved)
            if rebound:
                overlay["path"] = rebound

    def _rebind_snapshot_asset_paths(self, items, mapping):
        """Rewrite image paths inside serialized page/history payloads in-place."""
        if not items or not mapping:
            return
        for entry in items:
            key = None
            if entry.get("type") == "image":
                key = "source_path"
            elif entry.get("type") == "panel":
                key = "image_path"
            if not key:
                continue
            current = entry.get(key, "")
            rebound = self._lookup_rebound_asset(current, mapping)
            if rebound:
                entry[key] = rebound

            if entry.get("type") == "image":
                self._rebind_overlay_paths(
                    [
                        e for e in entry.get("effects", [])
                        if isinstance(e, dict) and e.get("type") == "overlay"
                    ],
                    mapping,
                )
            elif entry.get("type") == "panel":
                self._rebind_overlay_paths(
                    [
                        e for e in entry.get("image_effects", [])
                        if isinstance(e, dict) and e.get("type") == "overlay"
                    ],
                    mapping,
                )
                self._rebind_overlay_paths(
                    entry.get("panel_overlays", []),
                    mapping,
                )

    def _rebind_runtime_asset_paths(self, mapping):
        if not mapping:
            return

        for page in self.pages:
            # 1) Live QGraphicsItems.
            for item in page["scene"].items():
                if isinstance(item, ImageItem):
                    new = self._lookup_rebound_asset(item.source_path, mapping)
                    if new:
                        item.source_path = new
                    self._rebind_overlay_paths(
                        [
                            e for e in item.effects
                            if isinstance(e, dict) and e.get("type") == "overlay"
                        ],
                        mapping,
                    )
                    item.rebuild_effects()
                elif isinstance(item, PanelItem):
                    new = self._lookup_rebound_asset(item.image_path, mapping)
                    if new:
                        item.image_path = new
                    self._rebind_overlay_paths(
                        [
                            e for e in item.image_effects
                            if isinstance(e, dict) and e.get("type") == "overlay"
                        ],
                        mapping,
                    )
                    self._rebind_overlay_paths(
                        item.panel_overlays,
                        mapping,
                    )
                    item.update()

            # 2) Authoritative page snapshot used on page switches.
            self._rebind_snapshot_asset_paths(page.get("items", []), mapping)

            # 3) Every Undo/Redo snapshot. Otherwise Undo after save could
            # resurrect a path to the user's original external image.
            for snapshot in page.get("history", []):
                self._rebind_snapshot_asset_paths(snapshot, mapping)


    def collect_project_assets(self):
        if not self.project_path:
            self.save_project_as()
            return
        try:
            target = Path(self.project_path)
            portable, mapping, missing, assets_dir = self._bundle_project_assets(
                self.project_data(), target
            )
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(
                json.dumps(portable, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp.replace(target)

            self.active_project_dir = target.parent
            self.active_assets_dir = assets_dir
            self._rebind_runtime_asset_paths(mapping)
            self._sync_page_items(self.current_page_index)
            self._mark_saved_content()
            self.clear_recovery_file()
            self.statusBar().showMessage(
                f"素材を内包しました: {assets_dir.name}", 5000
            )
            if missing:
                QMessageBox.warning(
                    self,
                    "素材を内包",
                    "一部の元画像が見つからなかったため内包できませんでした。\n\n"
                    + "\n".join(missing[:12])
                    + ("\n…" if len(missing) > 12 else "")
                )
        except Exception as e:
            QMessageBox.critical(
                self, "素材を内包", f"素材の内包に失敗しました。\n{e}"
            )

    def recovery_data(self):
        data = self.project_data()
        data["recovery"] = True
        data["recovery_project_path"] = self.project_path or ""
        data["recovery_active_project_dir"] = str(self.active_project_dir or "")
        return data

    def auto_save_recovery(self):
        if not self.pages:
            return
        if not self._has_unsaved_changes():
            # A recovery file for an already-saved/clean document only causes
            # false recovery prompts after a crash or an update.
            self.clear_recovery_file()
            return
        try:
            tmp = self.recovery_path.with_suffix(
                self.recovery_path.suffix + ".tmp"
            )
            tmp.write_text(
                json.dumps(self.recovery_data(), ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp.replace(self.recovery_path)
            self.statusBar().showMessage(
                "自動復旧データを保存しました", 1800
            )
        except Exception as e:
            self.statusBar().showMessage(
                f"自動復旧データの保存に失敗: {e}", 3500
            )

    def clear_recovery_file(self):
        try:
            if self.recovery_path.exists():
                self.recovery_path.unlink()
        except Exception:
            pass

    def offer_startup_recovery(self):
        if not self.recovery_path.exists():
            return
        try:
            data = json.loads(
                self.recovery_path.read_text(encoding="utf-8")
            )
        except Exception:
            return

        answer = QMessageBox.question(
            self,
            "自動復旧",
            "前回の編集途中データが見つかりました。\n復旧しますか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            project_path = data.get("recovery_project_path") or None
            self.load_project_data(
                data, project_path=project_path, from_recovery=True
            )
        else:
            self.clear_recovery_file()

    def serialize_scene(self, scene):
        data = []
        for item in scene.items(Qt.AscendingOrder):
            if item.parentItem() is not None:
                continue
            if hasattr(item, "serialize"):
                payload = item.serialize()
                payload["visible"] = item.isVisible()
                data.append(payload)
        return data

    def _resolve_loaded_overlay_paths(self, overlays):
        result = json.loads(json.dumps(overlays or []))
        for overlay in result:
            if not isinstance(overlay, dict):
                continue
            raw = str(overlay.get("path", "") or "")
            if raw:
                overlay["path"] = self._resolve_overlay_asset_ref(raw)
        return result

    def deserialize_scene(self, scene, items, offset=None):
        is_paste = offset is not None
        offset = offset or QPointF(0, 0)
        created = []
        for d in items:
            t = d.get("type")
            item = None
            if t == "image":
                p = self._resolve_project_path(d.get("source_path", ""))
                pix = QPixmap(p)
                if pix.isNull():
                    continue
                effects = json.loads(json.dumps(d.get("effects", [])))
                for effect in effects:
                    if (
                        isinstance(effect, dict)
                        and effect.get("type") == "overlay"
                        and effect.get("path")
                    ):
                        effect["path"] = self._resolve_overlay_asset_ref(
                            effect.get("path")
                        )
                item = ImageItem(pix, p, pix, effects)
                item.setRotation(d.get("rotation", 0)); item.setScale(d.get("scale", 1))
                if d.get("mirror_x"):
                    tr = QTransform(); tr.scale(-1, 1); item.setTransform(tr)
            elif t == "text":
                item = EditableTextItem(d.get("text", ""))
                item.setRotation(d.get("rotation", 0)); item.setScale(d.get("scale", 1))
                f = item.font(); f.setFamily(d.get("font_family", f.family())); f.setPointSize(d.get("font_size", 32)); f.setBold(d.get("bold", False)); item.setFont(f)
                item.setDefaultTextColor(QColor(d.get("color", "#000000")))
                item.outline_color = QColor(d.get("outline_color", "#ffffff")); item.outline_width = float(d.get("outline_width", 0.0))
                item.shadow_enabled = bool(d.get("shadow_enabled", False)); item.shadow_color = QColor(d.get("shadow_color", "#80000000"))
                off = d.get("shadow_offset", [5.0, 5.0]); item.shadow_offset = QPointF(float(off[0]), float(off[1]))
            elif t == "vertical_text":
                item = VerticalTextItem(d.get("text", ""))
                item.setRotation(d.get("rotation", 0)); item.setScale(d.get("scale", 1))
                f = item.font(); f.setFamily(d.get("font_family", f.family())); f.setPointSize(d.get("font_size", 32)); f.setBold(d.get("bold", False)); item.setFont(f)
                item.color = QColor(d.get("color", "#000000"))
                item.outline_color = QColor(d.get("outline_color", "#ffffff"))
                item.outline_width = float(d.get("outline_width", 2.0))
                item.char_gap = float(d.get("char_gap", -2.0))
                item.column_gap = float(d.get("column_gap", 8.0))
            elif t == "bubble":
                item = BubbleItem(d.get("text", ""), d.get("width", 360), d.get("height", 190), d.get("style", "oval"), d.get("tail_tip"))
                item.setRotation(d.get("rotation", 0)); item.setScale(d.get("scale", 1))
                f = item.text_item.font(); f.setFamily(d.get("font_family", f.family())); f.setPointSize(d.get("font_size", 28)); f.setBold(d.get("bold", False)); item.text_item.setFont(f)
                item.text_item.setDefaultTextColor(QColor(d.get("color", "#000000")))
            elif t == "panel":
                panel_path = self._resolve_project_path(d.get("image_path", ""))
                image_effects = json.loads(
                    json.dumps(d.get("image_effects", []))
                )
                for effect in image_effects:
                    if (
                        isinstance(effect, dict)
                        and effect.get("type") == "overlay"
                        and effect.get("path")
                    ):
                        effect["path"] = self._resolve_overlay_asset_ref(
                            effect.get("path")
                        )
                panel_overlays = self._resolve_loaded_overlay_paths(
                    d.get("panel_overlays", [])
                )
                item = PanelItem(
                    QRectF(
                        d.get("rx", 0),
                        d.get("ry", 0),
                        d.get("w", 100),
                        d.get("h", 100),
                    ),
                    panel_path,
                    d.get("image_zoom", 1.0),
                    d.get("image_offset", [0, 0]),
                    d.get("skew", 0.0),
                    d.get("custom_points"),
                    image_effects,
                    d.get("panel_texts", []),
                    panel_overlays,
                )
                item.setTransformOriginPoint(
                    item.visual_bounds().center()
                    if item.custom_points is not None
                    else item.rect().center()
                )
                item.setRotation(float(d.get("rotation", 0.0)))
                item.setScale(float(d.get("scale", 1.0)))
            if item is not None:
                if isinstance(item, (ImageItem, PanelItem)):
                    if is_paste:
                        item.effect_uid = uuid.uuid4().hex
                    else:
                        item.effect_uid = str(
                            d.get("effect_uid") or item.effect_uid
                        )

                item.setPos(d.get("x", 0)+offset.x(), d.get("y", 0)+offset.y())
                item.setZValue(d.get("z", 0))
                item.setOpacity(float(d.get("opacity", 1.0)))
                item.setVisible(bool(d.get("visible", True)))
                scene.addItem(item)
                created.append(item)
        return created

    def project_data(self):
        self._sync_page_items(self.current_page_index)
        return {
            "version": APP_VERSION,
            "title": self.title_edit.text(),
            "episode": self.episode_edit.text(),
            "canvas_size": [self.canvas_w, self.canvas_h],
            "pages": [
                {"name": page["name"], "items": json.loads(json.dumps(page.get("items", [])))}
                for page in self.pages
            ]
        }

    def _project_title_folder_name(self):
        return self._safe_filename_component(
            self.title_edit.text(), "manga_project"
        )

    def _default_project_folder(self):
        folder = PROJECTS_DIR / self._project_title_folder_name()
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _choose_project_parent_and_name(self):
        folder = self._default_project_folder()
        default_file = folder / f"{self._project_title_folder_name()}.mpe.json"
        p, _ = QFileDialog.getSaveFileName(
            self,
            "名前を付けて保存",
            str(default_file),
            "Manga Peta Project (*.mpe.json);;JSON (*.json)",
        )
        if not p:
            return None
        target = Path(p)
        text = str(target)
        if not text.lower().endswith(".mpe.json"):
            if target.suffix.lower() == ".json":
                target = target.with_name(target.stem + ".mpe.json")
            else:
                target = Path(text + ".mpe.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _save_project_to(self, target, adopt_path=True, clear_recovery=True):
        target = Path(target)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)

            portable, mapping, missing, assets_dir = self._bundle_project_assets(
                self.project_data(), target
            )

            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(
                json.dumps(portable, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp.replace(target)

            if adopt_path:
                self.project_path = str(target)
                self.active_project_dir = target.parent
                self.active_assets_dir = assets_dir
                self._rebind_runtime_asset_paths(mapping)
                self._sync_page_items(self.current_page_index)
                self._mark_saved_content()

            if clear_recovery:
                self.clear_recovery_file()

            self.statusBar().showMessage(
                f"保存しました（素材内包済み）: {target}",
                5000
            )

            if missing:
                QMessageBox.warning(
                    self,
                    "保存完了 / 一部素材未内包",
                    "プロジェクト自体は保存しましたが、"
                    "見つからない画像がありました。\\n"
                    "その画像は元の参照のままです。\\n\\n"
                    + "\\n".join(missing[:12])
                    + ("\\n…" if len(missing) > 12 else "")
                )
            return True
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", str(e))
            return False

    def save_project(self):
        """Ctrl+S: overwrite the current project; first save falls back to Save As."""
        if not self.project_path:
            return self.save_project_as()
        return self._save_project_to(
            Path(self.project_path),
            adopt_path=True,
            clear_recovery=True,
        )

    def save_project_as(self):
        """Create/use a manga-title folder and adopt the new file as the active project."""
        target = self._choose_project_parent_and_name()
        if not target:
            return False
        return self._save_project_to(
            target,
            adopt_path=True,
            clear_recovery=True,
        )

    def save_project_dated(self):
        """Create a timestamped snapshot without changing Ctrl+S overwrite target."""
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        if self.project_path:
            title_folder = Path(self.project_path).parent
        else:
            title_folder = self._default_project_folder()

        base = self._project_title_folder_name()
        target = title_folder / f"{base}_{stamp}.mpe.json"
        return self._save_project_to(
            target,
            adopt_path=False,
            clear_recovery=False,
        )

    def _missing_overlay_refs_in_item(self, entry):
        refs = []
        overlay_lists = []
        if entry.get("type") == "image":
            overlay_lists.append([
                e for e in entry.get("effects", [])
                if isinstance(e, dict) and e.get("type") == "overlay"
            ])
        elif entry.get("type") == "panel":
            overlay_lists.append([
                e for e in entry.get("image_effects", [])
                if isinstance(e, dict) and e.get("type") == "overlay"
            ])
            overlay_lists.append(entry.get("panel_overlays", []))

        for overlays in overlay_lists:
            for overlay in overlays or []:
                if not isinstance(overlay, dict):
                    continue
                raw = str(overlay.get("path", "") or "")
                if not raw:
                    continue
                resolved = self._resolve_overlay_asset_ref(raw)
                if not resolved or not Path(resolved).exists():
                    refs.append(raw)
        return refs

    def load_project_data(self, data, project_path=None, from_recovery=False):
        self.view.cancel_region_mosaic()
        self.suspend_history = True
        self.current_page_index = -1
        self.pages = []
        self.page_list.blockSignals(True)
        self.page_list.clear()
        self.page_list.blockSignals(False)

        if project_path:
            self.project_path = str(project_path)
            self.active_project_dir = Path(project_path).parent
        else:
            self.project_path = None
            recovery_dir = data.get("recovery_active_project_dir", "")
            self.active_project_dir = (
                Path(recovery_dir) if recovery_dir else None
            )

        asset_dir_name = data.get("asset_dir", "")
        self.active_assets_dir = (
            Path(self.active_project_dir) / asset_dir_name
            if self.active_project_dir and asset_dir_name
            else None
        )

        self.title_edit.setText(data.get("title", ""))
        self.episode_edit.setText(data.get("episode", ""))
        size = data.get("canvas_size", [DEFAULT_CANVAS_W, DEFAULT_CANVAS_H])
        try:
            self.canvas_w = max(320, int(size[0]))
            self.canvas_h = max(320, int(size[1]))
        except Exception:
            self.canvas_w, self.canvas_h = DEFAULT_CANVAS_W, DEFAULT_CANVAS_H

        source_version = parse_project_version(data.get("version", 0))

        missing_paths = []

        for pd in data.get("pages", []):
            items = json.loads(json.dumps(pd.get("items", [])))

            if source_version < 0.3:
                for entry in items:
                    if entry.get("type") == "panel":
                        entry["z"] = -100

            for entry in items:
                raw = ""
                if entry.get("type") == "image":
                    raw = entry.get("source_path", "")
                elif entry.get("type") == "panel":
                    raw = entry.get("image_path", "")
                if raw:
                    resolved = self._resolve_project_path(raw)
                    if not Path(resolved).exists():
                        missing_paths.append(str(raw))

                missing_paths.extend(
                    self._missing_overlay_refs_in_item(entry)
                )

            scene = self.create_scene()
            self.deserialize_scene(scene, items)
            page = self._new_page_dict(
                pd.get("name", "ページ"), scene, items
            )
            self.pages.append(page)
            self._add_page_list_item(page)

        self.suspend_history = False

        if not self.pages:
            self.add_page()
        else:
            self.refresh_all_page_thumbnails()
            self.page_list.setCurrentRow(0)
            self.update_page_info()

        if missing_paths:
            unique = sorted(set(missing_paths))
            QMessageBox.warning(
                self,
                "見つからない画像",
                "一部の画像 / Effect素材が見つかりませんでした。\n"
                "見つかった素材は保存時にassetsへ内包されます。\n\n"
                + "\n".join(unique[:12])
                + ("\n…" if len(unique) > 12 else "")
            )

        if from_recovery:
            self._saved_content_signature = None
            self.statusBar().showMessage(
                "復旧データを読み込みました。保存をおすすめします。",
                5000
            )
        else:
            self._mark_saved_content()

    def open_project(self):
        p, _ = QFileDialog.getOpenFileName(
            self,
            "プロジェクトを開く",
            "",
            "Manga Peta Project (*.mpe.json);;JSON (*.json)"
        )
        if not p:
            return
        if not self._confirm_unsaved_action("別のプロジェクトを開く"):
            return

        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.critical(self, "読込エラー", str(e))
            return

        self.load_project_data(data, project_path=p)

    def _prepare_pages_for_render(self):
        """Sync the live page and rebuild non-current pages without changing Undo history."""
        self._sync_page_items(self.current_page_index)
        for index in range(len(self.pages)):
            if index != self.current_page_index:
                self._rebuild_page_scene_from_items(index)

    def render_scene_to_image(self, scene):
        selected = list(scene.selectedItems())
        previous_block = scene.blockSignals(True)
        try:
            scene.clearSelection()
            image = QImage(self.canvas_w, self.canvas_h, QImage.Format_ARGB32)
            image.fill(QColor("white"))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            scene.render(
                painter,
                QRectF(0, 0, self.canvas_w, self.canvas_h),
                QRectF(0, 0, self.canvas_w, self.canvas_h),
            )
            painter.end()
            for item in selected:
                if item.scene() == scene:
                    item.setSelected(True)
            return image
        finally:
            scene.blockSignals(previous_block)

    @staticmethod
    def _safe_filename_component(text, fallback="untitled"):
        value = str(text or "").strip()
        value = re.sub(r'[<>:"/\\|?*\\x00-\\x1f]', "_", value)
        value = value.rstrip(" .")
        value = value or fallback

        # Windows device names are invalid even when an extension is added.
        reserved = {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if value.upper() in reserved:
            value = "_" + value

        # Avoid path/file failures from extremely long manga/page titles.
        if len(value) > 90:
            value = value[:90].rstrip(" ._") or fallback
        return value

    def _page_export_filename(self, page, index=None):
        title = self._safe_filename_component(
            self.title_edit.text(), "manga"
        )
        page_number = (int(index) + 1) if index is not None else 1
        page_name = self._safe_filename_component(
            page.get("name", ""), f"page_{page_number:03d}"
        )
        return f"{title}_{page_number:03d}_{page_name}.png"

    def export_current_png(self):
        scene = self.current_scene()
        if not scene:
            return
        default_name = self._page_export_filename(
            self.pages[self.current_page_index],
            self.current_page_index,
        )
        p, _ = QFileDialog.getSaveFileName(
            self, "PNG出力", default_name, "PNG (*.png)"
        )
        if not p:
            return
        if not p.lower().endswith(".png"):
            p += ".png"
        self.render_scene_to_image(scene).save(p)
        self.statusBar().showMessage(f"PNG出力: {p}", 4000)

    def export_long_png(self):
        if not self.pages:
            return
        self._prepare_pages_for_render()
        default_name = (self.title_edit.text().strip() or "manga") + "_long.png"
        p, _ = QFileDialog.getSaveFileName(self, "縦長PNG出力", default_name, "PNG (*.png)")
        if not p:
            return
        if not p.lower().endswith(".png"):
            p += ".png"
        gap = 0
        total_h = len(self.pages) * self.canvas_h + max(0, len(self.pages)-1) * gap
        if total_h > 30000:
            answer = QMessageBox.question(
                self, "大きな画像", f"高さ {total_h}px の画像になります。続行しますか？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                return
        image = QImage(self.canvas_w, total_h, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        y = 0
        for page in self.pages:
            rendered = self.render_scene_to_image(page["scene"])
            painter.drawImage(0, y, rendered)
            y += self.canvas_h + gap
        painter.end()
        image.save(p)
        self.statusBar().showMessage(f"縦長PNG出力: {p}", 5000)

    def export_all_zip(self):
        self._prepare_pages_for_render()
        zip_default = self._safe_filename_component(self.title_edit.text(), "manga") + "_pages.zip"
        p, _ = QFileDialog.getSaveFileName(self, "ZIP出力", zip_default, "ZIP (*.zip)")
        if not p:
            return
        if not p.lower().endswith(".zip"):
            p += ".zip"
        with tempfile.TemporaryDirectory() as td:
            files = []
            used_names = set()
            for i, page in enumerate(self.pages, start=1):
                base_name = self._page_export_filename(page, i-1)
                candidate = base_name
                suffix = 2
                while candidate.lower() in used_names:
                    stem = Path(base_name).stem
                    candidate = f"{stem}_{suffix}.png"
                    suffix += 1
                used_names.add(candidate.lower())
                fp = Path(td) / candidate
                self.render_scene_to_image(page["scene"]).save(str(fp))
                files.append(fp)
            with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
                for fp in files:
                    z.write(fp, fp.name)
        self.statusBar().showMessage(f"ZIP出力: {p}", 5000)



    def closeEvent(self, event):
        if self._restarting_for_update:
            # handle_update_file() already wrote a fresh recovery checkpoint.
            event.accept()
            return

        if not self._confirm_unsaved_action("終了する"):
            event.ignore()
            return

        # User explicitly saved or chose to discard.
        self.clear_recovery_file()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Manga Peta Editor")
    w = MangaEditor(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()