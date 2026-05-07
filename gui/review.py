"""Interactive page-review widget. After scanning, students' answers can be
inspected and corrected by clicking bubbles directly on the page image."""
import logging
from typing import Optional

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from bubblemarking import scanning, scoring
from bubblemarking.dataframes import (
    AnswerKey,
    build_output_df,
    max_total,
    options_to_letters,
    score_scan,
)
from bubblemarking.scanning import (
    ANSWER_KEY_MATRIC,
    MATRIC_LENGTH,
    NUM_OPTIONS,
    UNREAD_MATRIC,
    PageScan,
)


COLOR_SELECTED = (0, 200, 0)
COLOR_UNSELECTED = (180, 180, 180)
COLOR_KEY = (220, 30, 30)
COLOR_LOW_CONF = (240, 180, 0)


def numpy_rgb_to_qpixmap(arr: np.ndarray) -> QtGui.QPixmap:
    if arr is None:
        return QtGui.QPixmap()
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    h, w, ch = arr.shape
    contiguous = np.ascontiguousarray(arr)
    qimg = QtGui.QImage(contiguous.data, w, h, ch * w, QtGui.QImage.Format.Format_RGB888).copy()
    return QtGui.QPixmap.fromImage(qimg)


def recompute_flags(scan: PageScan, low_conf_threshold: float = 0.15):
    """Rebuild the flag list for a scan from its current state. Called after
    edits so that resolved problems clear themselves from the review queue."""
    flags = []
    if scan.unreadable:
        flags.append("unreadable")
        scan.flags = flags
        return
    if scan.matric_string() == UNREAD_MATRIC:
        flags.append("no_matric")
    for q in range(1, scan.num_questions + 1):
        ans = scan.answers.get(q, [])
        if scan.one_answer_only and len(ans) > 1:
            flags.append(f"multi_answer:{q}")
        if not ans:
            flags.append(f"no_answer:{q}")
        if scan.confidence.get(q, 1.0) < low_conf_threshold:
            flags.append(f"low_confidence:{q}")
    scan.flags = flags


def recompute_duplicate_flags(scans):
    """Mark pages whose matric duplicates another page's. Run on the whole
    set whenever any matric changes."""
    counts = {}
    for s in scans:
        m = s.matric_string()
        if m in (UNREAD_MATRIC, ANSWER_KEY_MATRIC):
            continue
        counts[m] = counts.get(m, 0) + 1
    for s in scans:
        s.flags = [f for f in s.flags if not f.startswith("duplicate_matric")]
        m = s.matric_string()
        if counts.get(m, 0) > 1:
            s.flags.append(f"duplicate_matric:{m}")


class PageImageCache:
    """Tiny LRU. Re-renders + re-prepares pages on demand from the PdfDocument."""

    def __init__(self, doc, scale=5.0, maxsize=4, prepare_kwargs=None):
        self._doc = doc
        self._scale = scale
        self._cache = {}
        self._order = []
        self._maxsize = maxsize
        self._prepare_kwargs = prepare_kwargs or {}

    def get(self, page_idx):
        if page_idx in self._cache:
            self._order.remove(page_idx)
            self._order.append(page_idx)
            return self._cache[page_idx]
        raw = scanning.get_image_from_file(self._doc, page_idx, SCALE=self._scale)
        prepared, _ = scanning.prepare_image(raw, **self._prepare_kwargs)
        img = prepared if prepared is not None else raw
        self._cache[page_idx] = img
        self._order.append(page_idx)
        if len(self._order) > self._maxsize:
            evict = self._order.pop(0)
            self._cache.pop(evict, None)
        return img

    def invalidate(self):
        self._cache.clear()
        self._order.clear()


class PageImageView(QtWidgets.QGraphicsView):
    """A QGraphicsView that displays a scanned page and lets the user click
    bubbles to toggle them. Emits bubble_clicked with (kind, i1, i2):
        kind == "answer": i1 = question number, i2 = option index
        kind == "matric": i1 = digit value, i2 = position index
    """
    bubble_clicked = QtCore.Signal(str, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: Optional[QtWidgets.QGraphicsPixmapItem] = None
        self._hit_targets = []
        self.setRenderHints(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(40, 40, 40)))

    def set_page(self, image, scan: PageScan, answer_key: Optional[AnswerKey], low_conf_threshold=0.15):
        self._scene.clear()
        self._hit_targets = []
        self._pixmap_item = None
        if image is None:
            placeholder = self._scene.addText("Page could not be processed — geometry unavailable.")
            placeholder.setDefaultTextColor(QtGui.QColor(220, 220, 220))
            return

        composite = self._draw_overlays(image, scan, answer_key, low_conf_threshold)
        pixmap = numpy_rgb_to_qpixmap(composite)
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QtCore.QRectF(pixmap.rect()))
        if scan.bars is not None:
            self._build_hit_targets(scan)
        self.fitInView(self._pixmap_item, QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def _draw_overlays(self, image, scan, answer_key, low_conf_threshold):
        out = image.copy()
        if scan.bars is None:
            return out
        for q in range(1, scan.num_questions + 1):
            selected = set(scan.answers.get(q, []))
            correct = answer_key.correct_for(q) if answer_key else set()
            low_conf = scan.confidence.get(q, 1.0) < low_conf_threshold
            for opt in range(NUM_OPTIONS):
                rect = scan.bubble_rect(q, opt)
                if rect is None:
                    continue
                x1, y1, x2, y2 = rect
                if opt in selected:
                    cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_SELECTED, 4)
                else:
                    cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_UNSELECTED, 1)
                if correct and opt in correct:
                    cv2.rectangle(out, (x1 - 3, y1 - 3), (x2 + 3, y2 + 3), COLOR_KEY, 2)
            if low_conf:
                # outline the row in amber
                xs = [scan.bubble_rect(q, o) for o in range(NUM_OPTIONS)]
                xs = [r for r in xs if r is not None]
                if xs:
                    rx1 = min(r[0] for r in xs) - 6
                    ry1 = min(r[1] for r in xs) - 6
                    rx2 = max(r[2] for r in xs) + 6
                    ry2 = max(r[3] for r in xs) + 6
                    cv2.rectangle(out, (rx1, ry1), (rx2, ry2), COLOR_LOW_CONF, 2)

        for digit in range(10):
            for pos in range(MATRIC_LENGTH):
                rect = scan.matric_bubble_rect(digit, pos)
                if rect is None:
                    continue
                x1, y1, x2, y2 = rect
                if scan.matric_digits[pos] == digit:
                    cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_SELECTED, 4)
                else:
                    cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_UNSELECTED, 1)
        return out

    def _build_hit_targets(self, scan):
        for q in range(1, scan.num_questions + 1):
            for opt in range(NUM_OPTIONS):
                rect = scan.bubble_rect(q, opt)
                if rect is not None:
                    self._hit_targets.append((rect, "answer", q, opt))
        for digit in range(10):
            for pos in range(MATRIC_LENGTH):
                rect = scan.matric_bubble_rect(digit, pos)
                if rect is not None:
                    self._hit_targets.append((rect, "matric", digit, pos))

    def mousePressEvent(self, event):
        if self._pixmap_item is None or event.button() != QtCore.Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        sp = self.mapToScene(event.position().toPoint())
        x, y = sp.x(), sp.y()
        best = None
        best_area = float("inf")
        for rect, kind, i1, i2 in self._hit_targets:
            x1, y1, x2, y2 = rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                area = (x2 - x1) * (y2 - y1)
                if area < best_area:
                    best_area = area
                    best = (kind, i1, i2)
        if best is not None:
            self.bubble_clicked.emit(*best)
            event.accept()
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_0:
            if self._pixmap_item is not None:
                self.fitInView(self._pixmap_item, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            event.accept()
            return
        super().keyPressEvent(event)


class ScoringPanel(QtWidgets.QGroupBox):
    """Strategy chooser + dynamically-rendered options form.

    Emits :pyattr:`changed` whenever the user picks a different strategy
    or edits any option value, so the parent can recompute live totals."""

    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__("Scoring", parent)
        self._strategies = list(scoring.list_builtins())
        self._option_widgets = {}
        self._active = self._strategies[0] if self._strategies else None
        self._build_ui()
        self._populate_options()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Strategy:"))
        self.combo = QtWidgets.QComboBox()
        for s in self._strategies:
            self.combo.addItem(s.NAME, s)
        self.combo.currentIndexChanged.connect(self._on_strategy_changed)
        row.addWidget(self.combo, 1)
        load_btn = QtWidgets.QPushButton("Load custom…")
        load_btn.clicked.connect(self._load_custom)
        row.addWidget(load_btn)
        layout.addLayout(row)

        self.description = QtWidgets.QLabel()
        self.description.setWordWrap(True)
        self.description.setStyleSheet("color: gray;")
        layout.addWidget(self.description)

        self._options_container = QtWidgets.QWidget()
        self.options_form = QtWidgets.QFormLayout(self._options_container)
        self.options_form.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._options_container)

    def strategy(self):
        return self._active

    def options(self) -> dict:
        if self._active is None:
            return {}
        raw = {}
        for name, widget in self._option_widgets.items():
            if isinstance(widget, QtWidgets.QCheckBox):
                raw[name] = widget.isChecked()
            elif isinstance(widget, (QtWidgets.QDoubleSpinBox, QtWidgets.QSpinBox)):
                raw[name] = widget.value()
            else:
                raw[name] = widget.text()
        return scoring.coerce_options(self._active, raw)

    def _on_strategy_changed(self, idx):
        if idx < 0:
            return
        self._active = self.combo.itemData(idx)
        self._populate_options()
        self.changed.emit()

    def _clear_form(self):
        while self.options_form.rowCount():
            self.options_form.removeRow(0)
        self._option_widgets.clear()

    def _populate_options(self):
        self._clear_form()
        if self._active is None:
            self.description.setText("")
            return
        self.description.setText(getattr(self._active, "DESCRIPTION", ""))
        for name, spec in getattr(self._active, "OPTIONS", {}).items():
            t = spec.get("type", str)
            default = spec.get("default")
            if t is bool:
                widget = QtWidgets.QCheckBox()
                widget.setChecked(bool(default))
                widget.toggled.connect(self.changed.emit)
            elif t is int:
                widget = QtWidgets.QSpinBox()
                widget.setRange(-10 ** 6, 10 ** 6)
                widget.setValue(int(default or 0))
                widget.valueChanged.connect(self.changed.emit)
            elif t is float:
                widget = QtWidgets.QDoubleSpinBox()
                widget.setRange(-1e6, 1e6)
                widget.setDecimals(3)
                widget.setSingleStep(0.05)
                widget.setValue(float(default or 0))
                widget.valueChanged.connect(self.changed.emit)
            else:
                widget = QtWidgets.QLineEdit("" if default is None else str(default))
                widget.editingFinished.connect(self.changed.emit)
            tip = spec.get("tooltip")
            if tip:
                widget.setToolTip(tip)
            self.options_form.addRow(QtWidgets.QLabel(spec.get("label", name)), widget)
            self._option_widgets[name] = widget

    def _load_custom(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load scoring strategy", "", "Python files (*.py)"
        )
        if not path:
            return
        try:
            mod = scoring.load_strategy_from_file(path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Load failed", f"Could not load strategy:\n{exc}")
            return
        self._strategies.append(mod)
        self.combo.addItem(getattr(mod, "NAME", path), mod)
        self.combo.setCurrentIndex(self.combo.count() - 1)


class ReviewWidget(QtWidgets.QWidget):
    """The Review tab. Holds the page list, the page view, and the side panel."""

    exported = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scans = []
        self.answer_key: Optional[AnswerKey] = None
        self.image_cache: Optional[PageImageCache] = None
        self.current_scan_index = -1
        self.low_conf_threshold = 0.15
        self._build_ui()

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)

        # Left pane
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["All pages", "Needs review only"])
        self.filter_combo.currentIndexChanged.connect(self._refresh_list)
        ll.addWidget(self.filter_combo)
        self.page_list = QtWidgets.QListWidget()
        self.page_list.currentRowChanged.connect(self._on_list_row_changed)
        ll.addWidget(self.page_list)
        self.export_button = QtWidgets.QPushButton("Export results CSV…")
        self.export_button.clicked.connect(self._on_export)
        self.export_button.setEnabled(False)
        ll.addWidget(self.export_button)
        root.addWidget(left, 1)

        # Center: page view
        self.page_view = PageImageView()
        self.page_view.bubble_clicked.connect(self._on_bubble_clicked)
        root.addWidget(self.page_view, 4)

        # Right pane
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.addWidget(QtWidgets.QLabel("<b>Page</b>"))
        self.page_label = QtWidgets.QLabel("-")
        rl.addWidget(self.page_label)

        rl.addWidget(QtWidgets.QLabel("<b>Matriculation number</b>"))
        self.matric_edit = QtWidgets.QLineEdit()
        self.matric_edit.setMaxLength(8)
        self.matric_edit.setPlaceholderText("8 digits")
        self.matric_edit.editingFinished.connect(self._on_matric_edited)
        rl.addWidget(self.matric_edit)

        rl.addWidget(QtWidgets.QLabel("<b>Confidence (min over questions)</b>"))
        self.confidence_label = QtWidgets.QLabel("-")
        rl.addWidget(self.confidence_label)

        rl.addWidget(QtWidgets.QLabel("<b>Flags</b>"))
        self.flags_view = QtWidgets.QPlainTextEdit()
        self.flags_view.setReadOnly(True)
        self.flags_view.setMaximumHeight(100)
        rl.addWidget(self.flags_view)

        rl.addWidget(QtWidgets.QLabel("<b>Score</b>"))
        self.score_label = QtWidgets.QLabel("-")
        self.score_label.setStyleSheet("font-size: 14pt;")
        rl.addWidget(self.score_label)

        self.scoring_panel = ScoringPanel()
        self.scoring_panel.changed.connect(self._on_scoring_changed)
        rl.addWidget(self.scoring_panel)

        nav = QtWidgets.QHBoxLayout()
        prev_btn = QtWidgets.QPushButton("◀ Prev")
        prev_btn.clicked.connect(lambda: self._navigate(-1))
        next_btn = QtWidgets.QPushButton("Next ▶")
        next_btn.clicked.connect(lambda: self._navigate(1))
        nav.addWidget(prev_btn)
        nav.addWidget(next_btn)
        rl.addLayout(nav)

        next_review = QtWidgets.QPushButton("Next page needing review")
        next_review.clicked.connect(self._jump_to_next_review)
        rl.addWidget(next_review)

        rl.addWidget(QtWidgets.QLabel(
            "<i>Click bubbles to toggle.\nCtrl+wheel zooms; '0' fits page.</i>"
        ))
        rl.addStretch(1)
        root.addWidget(right, 1)

    # ------------------------------------------------------------------ data
    def set_data(self, scans, answer_key, image_cache, low_conf_threshold=0.15):
        self.scans = scans
        self.answer_key = answer_key
        self.image_cache = image_cache
        self.low_conf_threshold = low_conf_threshold
        recompute_duplicate_flags(self.scans)
        self.export_button.setEnabled(answer_key is not None)
        self._refresh_list()
        if self.page_list.count():
            self.page_list.setCurrentRow(0)

    # ------------------------------------------------------------- list view
    def _list_label(self, scan):
        flag_icon = "⚠ " if scan.flags else "✓ "
        suffix = "  (KEY)" if scan.matric_string() == ANSWER_KEY_MATRIC else ""
        return f"{flag_icon}p{scan.page_index + 1}: {scan.matric_string()}{suffix}"

    def _refresh_list(self):
        only_review = self.filter_combo.currentIndex() == 1
        self.page_list.blockSignals(True)
        prev_idx = self.current_scan_index
        self.page_list.clear()
        for idx, s in enumerate(self.scans):
            if only_review and not s.flags:
                continue
            item = QtWidgets.QListWidgetItem(self._list_label(s))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, idx)
            self.page_list.addItem(item)
        self.page_list.blockSignals(False)
        # try restore selection
        if prev_idx >= 0:
            for row in range(self.page_list.count()):
                if self.page_list.item(row).data(QtCore.Qt.ItemDataRole.UserRole) == prev_idx:
                    self.page_list.setCurrentRow(row)
                    return
        if self.page_list.count():
            self.page_list.setCurrentRow(0)

    def _refresh_list_item(self, scan_idx):
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) == scan_idx:
                item.setText(self._list_label(self.scans[scan_idx]))
                return

    def _on_list_row_changed(self, row):
        if row < 0:
            return
        item = self.page_list.item(row)
        if item is None:
            return
        idx = item.data(QtCore.Qt.ItemDataRole.UserRole)
        self._show_page(idx)

    def _navigate(self, delta):
        cur = self.page_list.currentRow()
        n = self.page_list.count()
        if n == 0:
            return
        new = max(0, min(n - 1, cur + delta))
        self.page_list.setCurrentRow(new)

    def _jump_to_next_review(self):
        n = len(self.scans)
        if n == 0:
            return
        start = self.current_scan_index + 1
        for offset in range(n):
            i = (start + offset) % n
            if self.scans[i].flags:
                # find list row holding i
                for row in range(self.page_list.count()):
                    if self.page_list.item(row).data(QtCore.Qt.ItemDataRole.UserRole) == i:
                        self.page_list.setCurrentRow(row)
                        return

    # ------------------------------------------------------------ page view
    def _show_page(self, scan_idx):
        self.current_scan_index = scan_idx
        scan = self.scans[scan_idx]
        img = None
        if not scan.unreadable and self.image_cache is not None:
            try:
                img = self.image_cache.get(scan.page_index)
            except Exception as exc:
                logging.error(f"Failed to load page {scan.page_index}: {exc}")
        self.page_view.set_page(img, scan, self.answer_key, self.low_conf_threshold)
        self.page_label.setText(f"{scan.page_index + 1} of {len(self.scans)}")
        self.matric_edit.blockSignals(True)
        self.matric_edit.setText(scan.matric_string())
        self.matric_edit.blockSignals(False)
        if scan.confidence:
            mn = min(scan.confidence.values())
            self.confidence_label.setText(f"{mn:.2f}")
        else:
            self.confidence_label.setText("-")
        self.flags_view.setPlainText("\n".join(scan.flags) if scan.flags else "(none)")
        self._refresh_score()

    # --------------------------------------------------------------- edits
    def _on_bubble_clicked(self, kind, i1, i2):
        if self.current_scan_index < 0:
            return
        scan = self.scans[self.current_scan_index]
        if kind == "answer":
            scan.toggle_answer(i1, i2)
        else:  # matric
            current = scan.matric_digits[i2]
            scan.set_matric_digit(i2, None if current == i1 else i1)
            recompute_duplicate_flags(self.scans)
        recompute_flags(scan, self.low_conf_threshold)
        self._show_page(self.current_scan_index)
        self._refresh_list_item(self.current_scan_index)

    def _on_scoring_changed(self):
        self._refresh_score()

    def _refresh_score(self):
        if (self.current_scan_index < 0 or self.answer_key is None
                or self.scoring_panel.strategy() is None):
            self.score_label.setText("-")
            return
        scan = self.scans[self.current_scan_index]
        if scan.matric_string() == ANSWER_KEY_MATRIC:
            self.score_label.setText("(answer key)")
            return
        strat = self.scoring_panel.strategy()
        opts = self.scoring_panel.options()
        total = score_scan(scan, self.answer_key, strat, opts, NUM_OPTIONS)
        mx = max_total(self.answer_key, strat, opts, NUM_OPTIONS)
        self.score_label.setText(f"{total:.2f} / {mx:.2f}")

    def _on_matric_edited(self):
        if self.current_scan_index < 0:
            return
        scan = self.scans[self.current_scan_index]
        text = self.matric_edit.text().strip()
        if text == scan.matric_string():
            return
        if len(text) != 8 or not text.isdigit():
            QtWidgets.QMessageBox.warning(
                self, "Invalid", "Matriculation number must be exactly 8 digits."
            )
            self.matric_edit.setText(scan.matric_string())
            return
        scan.matric_digits = [int(c) for c in text]
        recompute_flags(scan, self.low_conf_threshold)
        recompute_duplicate_flags(self.scans)
        # duplicate change can affect siblings — refresh whole list
        self._refresh_list()
        self._show_page(self.current_scan_index)

    # --------------------------------------------------------------- export
    def _on_export(self):
        if not self.answer_key:
            QtWidgets.QMessageBox.warning(self, "No answer key", "Cannot export without an answer key.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export results", "results.csv", "CSV (*.csv)"
        )
        if not path:
            return
        df = build_output_df(
            self.scans,
            self.answer_key,
            strategy=self.scoring_panel.strategy(),
            options=self.scoring_panel.options(),
            num_options=NUM_OPTIONS,
        )
        df.to_csv(path, index=False)
        self.exported.emit(path)
        QtWidgets.QMessageBox.information(
            self, "Exported", f"Saved {len(df)} rows to:\n{path}"
        )
