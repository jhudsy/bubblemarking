"""Interactive page-review widget. After scanning, students' answers can be
inspected and corrected by clicking bubbles directly on the page image."""
import hashlib
import json
import logging
import pathlib
import sys
from typing import Optional

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets


# Platform-appropriate label for the zoom modifier — Cmd on macOS, Ctrl
# elsewhere. Used in tooltip / hint text shown to the user.
ZOOM_KEY_LABEL = "⌘" if sys.platform == "darwin" else "Ctrl"

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
COLOR_NO_ANSWER = (50, 130, 230)
COLOR_SKIP_BADGE = (200, 60, 60)


def numpy_rgb_to_qpixmap(arr: np.ndarray) -> QtGui.QPixmap:
    if arr is None:
        return QtGui.QPixmap()
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    h, w, ch = arr.shape
    contiguous = np.ascontiguousarray(arr)
    qimg = QtGui.QImage(contiguous.data, w, h, ch * w, QtGui.QImage.Format.Format_RGB888).copy()
    return QtGui.QPixmap.fromImage(qimg)


def recompute_flags(scan: PageScan, low_conf_threshold: float = 0.15,
                    answer_key=None):
    """Rebuild the flag list for a scan from its current state. Called after
    edits so that resolved problems clear themselves from the review queue.

    With cohort calibration, ``confidence`` is the bubble margin from the
    decision boundary, normalised by half the filled/blank spread. Values
    near 0 mean a bubble landed on the boundary; values ≥ 1 mean it's at
    or past one of the medians.

    When an ``answer_key`` is supplied, questions that the key marks as
    in-scope (i.e. have a correct answer) but where the student selected
    nothing get a ``no_answer:N`` flag — those are likely "the student
    forgot" rather than "intentionally blank." Out-of-scope questions
    (the form has 120 rows, the exam may use only 30) are not flagged."""
    flags = []
    if scan.unreadable:
        flags.append("unreadable")
        scan.flags = flags
        return
    if scan.matric_string() == UNREAD_MATRIC:
        flags.append("no_matric")
    is_answer_key_page = scan.matric_string() == ANSWER_KEY_MATRIC
    for q in range(1, scan.num_questions + 1):
        ans = scan.answers.get(q, [])
        if scan.one_answer_only and len(ans) > 1:
            flags.append(f"multi_answer:{q}")
        if scan.confidence.get(q, 1.0) < low_conf_threshold:
            flags.append(f"low_confidence:{q}")
        if (answer_key is not None and not is_answer_key_page
                and not ans and answer_key.correct_for(q)):
            flags.append(f"no_answer:{q}")
    scan.flags = flags


def _format_q_list(qs):
    qs = sorted(qs)
    if len(qs) <= 8:
        return ", ".join(str(q) for q in qs)
    return ", ".join(str(q) for q in qs[:6]) + f" and {len(qs) - 6} more"


def friendly_issue_summary(scan):
    """Translate raw flag strings into a list of human-readable issue lines.
    Empty list means the page has no problems to surface."""
    if not scan.flags:
        return []
    low_conf = {int(f.split(":", 1)[1]) for f in scan.flags
                if f.startswith("low_confidence:")}
    multi = {int(f.split(":", 1)[1]) for f in scan.flags
             if f.startswith("multi_answer:")}
    no_answer = {int(f.split(":", 1)[1]) for f in scan.flags
                 if f.startswith("no_answer:")}
    duplicates = [f.split(":", 1)[1] for f in scan.flags
                  if f.startswith("duplicate_matric:")]

    lines = []
    if "unreadable" in scan.flags:
        lines.append("Page geometry could not be detected — manual entry only.")
    if "no_matric" in scan.flags:
        lines.append("Matriculation number could not be read.")
    for matric in duplicates:
        lines.append(f"Same matric ({matric}) appears on another page.")
    if no_answer:
        qs = _format_q_list(no_answer)
        lines.append(f"Missing answer (key expects one): question {qs}." if len(no_answer) == 1
                     else f"Missing answer (key expects one): questions {qs}.")
    if low_conf:
        qs = _format_q_list(low_conf)
        lines.append(f"Possibly unclear answer: question {qs}." if len(low_conf) == 1
                     else f"Possibly unclear answers: questions {qs}.")
    if multi:
        qs = _format_q_list(multi)
        lines.append(f"More than one bubble selected: question {qs}." if len(multi) == 1
                     else f"More than one bubble selected: questions {qs}.")
    return lines


class UndoEntry:
    """One reversible action on a single PageScan."""
    __slots__ = ("scan_idx", "description", "before", "after")

    def __init__(self, scan_idx, description, before, after):
        self.scan_idx = scan_idx
        self.description = description
        self.before = before
        self.after = after


class UndoManager:
    """Two-stack undo/redo. Each entry is a snapshot pair for one scan."""

    def __init__(self, max_depth=200):
        self._undo = []
        self._redo = []
        self._max = max_depth

    def push(self, entry: UndoEntry):
        self._undo.append(entry)
        if len(self._undo) > self._max:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return None
        e = self._undo.pop()
        self._redo.append(e)
        return e

    def redo(self):
        if not self._redo:
            return None
        e = self._redo.pop()
        self._undo.append(e)
        return e

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def clear(self):
        self._undo.clear()
        self._redo.clear()


def scan_snapshot(scan):
    """Capture the user-editable parts of a PageScan for undo / persistence."""
    return {
        "matric_digits": list(scan.matric_digits),
        "answers": {q: list(a) for q, a in scan.answers.items()},
        "skip_from_export": bool(getattr(scan, "skip_from_export", False)),
    }


def restore_scan_snapshot(scan, snap):
    scan.matric_digits = list(snap["matric_digits"])
    scan.answers = {q: list(a) for q, a in snap["answers"].items()}
    scan.skip_from_export = bool(snap.get("skip_from_export", False))


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


SELECTED_THICKNESS = 7
KEY_THICKNESS = 5
LOW_CONF_THICKNESS = 5

DEFAULT_PAGE_SIZE = (2977, 4209)  # A4 portrait at SCALE=5.0
PAGE_GAP = 60
LOAD_WINDOW = 2  # pages on either side of the active page kept fully rendered


class PageBlock:
    """One page slot in the multi-page scroll. Starts as a cheap placeholder;
    swapped to a real rendered pixmap when it's near the viewport."""
    __slots__ = ("scan_index", "page_index", "scan", "bounds",
                 "pixmap_item", "is_loaded")

    def __init__(self, scan_index, scan, bounds, pixmap_item):
        self.scan_index = scan_index
        self.page_index = scan.page_index
        self.scan = scan
        self.bounds = bounds  # QRectF in scene coords
        self.pixmap_item = pixmap_item
        self.is_loaded = False


class PageImageView(QtWidgets.QGraphicsView):
    """A QGraphicsView that lays out every page in the cohort vertically and
    streams real page renderings into the visible window. Clicking a bubble
    on any page emits ``bubble_clicked(scan_index, kind, i1, i2)``; scrolling
    so a different page becomes the centre emits ``active_page_changed``.

    Page renderings are loaded lazily for ±LOAD_WINDOW pages around the
    active page. Pages outside that window revert to grey placeholders so
    memory stays bounded."""
    bubble_clicked = QtCore.Signal(int, str, int, int)
    active_page_changed = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self._blocks: list = []
        self._answer_key: Optional[AnswerKey] = None
        self._image_cache: Optional[PageImageCache] = None
        self._low_conf_threshold = 0.15
        self._page_w = DEFAULT_PAGE_SIZE[0]
        self._page_h = DEFAULT_PAGE_SIZE[1]
        self._active_index = -1
        self._suppress_active_signal = False
        self._show_correct_answers = True
        self._last_tooltip_key = None
        self.setMouseTracking(True)
        self.setRenderHints(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor(40, 40, 40)))
        self.grabGesture(QtCore.Qt.GestureType.PinchGesture)
        # Debounce scroll-driven recomputes so smooth scrolling doesn't
        # hammer the lazy-load logic.
        self._scroll_timer = QtCore.QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(40)
        self._scroll_timer.timeout.connect(self._on_scroll_settled)
        self.verticalScrollBar().valueChanged.connect(
            lambda *_: self._scroll_timer.start()
        )

    # ---------------------------------------------------- scene construction
    def set_pages(self, scans, answer_key, image_cache, low_conf_threshold=0.15):
        """Rebuild the scene from a list of :class:`PageScan` objects."""
        self._scene.clear()
        self._blocks = []
        self._answer_key = answer_key
        self._image_cache = image_cache
        self._low_conf_threshold = low_conf_threshold
        if not scans:
            placeholder = self._scene.addText("No pages found in this PDF.")
            placeholder.setDefaultTextColor(QtGui.QColor(220, 220, 220))
            return

        # Resolve a canonical page size from the first available rendered
        # page; all pages from one PDF render at the same dimensions.
        size = self._resolve_page_size(scans, image_cache)
        self._page_w, self._page_h = size

        y = 0.0
        for idx, scan in enumerate(scans):
            bounds = QtCore.QRectF(0.0, y, float(self._page_w), float(self._page_h))
            placeholder = self._make_placeholder_pixmap(self._page_w, self._page_h, scan)
            item = self._scene.addPixmap(placeholder)
            item.setPos(0.0, y)
            self._blocks.append(PageBlock(idx, scan, bounds, item))
            y += self._page_h + PAGE_GAP

        self._scene.setSceneRect(QtCore.QRectF(0, 0, self._page_w, max(0, y - PAGE_GAP)))
        self._active_index = 0
        self._update_loaded_window()
        # Defer the initial fit until after the viewport gets its real size.
        QtCore.QTimer.singleShot(0, lambda: self._fit_block(0))

    def _resolve_page_size(self, scans, image_cache):
        for s in scans:
            if s.unreadable or image_cache is None:
                continue
            try:
                img = image_cache.get(s.page_index)
                if img is not None and img.shape[0] > 0:
                    return img.shape[1], img.shape[0]
            except Exception:
                continue
        return DEFAULT_PAGE_SIZE

    # ------------------------------------------------- placeholders / loaded
    def _make_placeholder_pixmap(self, w, h, scan: PageScan):
        pix = QtGui.QPixmap(w, h)
        pix.fill(QtGui.QColor(60, 60, 60))
        painter = QtGui.QPainter(pix)
        painter.setPen(QtGui.QColor(180, 180, 180))
        font = painter.font()
        font.setPointSize(48)
        painter.setFont(font)
        if scan.unreadable:
            text = f"Page {scan.page_index + 1}\nGeometry could not be detected"
        else:
            text = f"Page {scan.page_index + 1}"
        painter.drawText(pix.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return pix

    def _load_block(self, block: PageBlock):
        if block.scan.unreadable or self._image_cache is None:
            return
        try:
            img = self._image_cache.get(block.page_index)
        except Exception as exc:
            logging.error(f"Failed to render page {block.page_index + 1}: {exc}")
            return
        if img is None:
            return
        composite = self._draw_overlays(img, block.scan, self._answer_key,
                                        self._low_conf_threshold)
        block.pixmap_item.setPixmap(numpy_rgb_to_qpixmap(composite))
        block.is_loaded = True

    def _unload_block(self, block: PageBlock):
        block.pixmap_item.setPixmap(
            self._make_placeholder_pixmap(int(block.bounds.width()),
                                          int(block.bounds.height()), block.scan)
        )
        block.is_loaded = False

    def _update_loaded_window(self):
        if not self._blocks:
            return
        target = set(range(max(0, self._active_index - LOAD_WINDOW),
                           min(len(self._blocks), self._active_index + LOAD_WINDOW + 1)))
        for i, block in enumerate(self._blocks):
            if i in target and not block.is_loaded:
                self._load_block(block)
            elif i not in target and block.is_loaded:
                self._unload_block(block)

    # ------------------------------------------------------------ external API
    def scroll_to_page(self, scan_index: int, fit: bool = False):
        """Programmatic scroll. With ``fit=True`` also resets zoom to fit
        the page; otherwise keeps the current zoom level."""
        if not (0 <= scan_index < len(self._blocks)):
            return
        block = self._blocks[scan_index]
        self._active_index = scan_index
        self._update_loaded_window()
        if fit:
            self._fit_block(scan_index)
        else:
            self.centerOn(block.bounds.center())

    def refresh_overlays_for(self, scan_index: int):
        """Re-render overlays for one page after an edit. Preserves zoom/pan
        and the loaded-window state of every other page."""
        if not (0 <= scan_index < len(self._blocks)):
            return
        block = self._blocks[scan_index]
        if not block.is_loaded:
            return
        if block.scan.unreadable or self._image_cache is None:
            return
        try:
            img = self._image_cache.get(block.page_index)
        except Exception:
            return
        composite = self._draw_overlays(img, block.scan, self._answer_key,
                                        self._low_conf_threshold)
        block.pixmap_item.setPixmap(numpy_rgb_to_qpixmap(composite))

    def active_index(self) -> int:
        return self._active_index

    def set_show_correct_answers(self, show: bool):
        """Toggle the red 'correct answer' outline overlays."""
        show = bool(show)
        if show == self._show_correct_answers:
            return
        self._show_correct_answers = show
        self._redraw_all_loaded()

    def set_low_conf_threshold(self, threshold: float):
        """Update the low-confidence threshold and re-render the amber row
        outlines on every currently-loaded page."""
        self._low_conf_threshold = float(threshold)
        self._redraw_all_loaded()

    def _redraw_all_loaded(self):
        """Re-render overlays for every currently-loaded page in place.
        Placeholders are unaffected and pick up new state when they load."""
        if self._image_cache is None:
            return
        for block in self._blocks:
            if not block.is_loaded or block.scan.unreadable:
                continue
            try:
                img = self._image_cache.get(block.page_index)
            except Exception:
                continue
            composite = self._draw_overlays(img, block.scan, self._answer_key,
                                            self._low_conf_threshold)
            block.pixmap_item.setPixmap(numpy_rgb_to_qpixmap(composite))

    # ----------------------------------------------------- scroll / view fit
    def _fit_block(self, scan_index: int):
        if not (0 <= scan_index < len(self._blocks)):
            return
        block = self._blocks[scan_index]
        self.resetTransform()
        self.fitInView(block.bounds, QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def _page_at_viewport_center(self) -> int:
        if not self._blocks:
            return -1
        center = self.mapToScene(self.viewport().rect().center())
        y = center.y()
        if y < self._blocks[0].bounds.top():
            return 0
        for block in self._blocks:
            if block.bounds.top() <= y <= block.bounds.bottom() + PAGE_GAP / 2:
                return block.scan_index
        return self._blocks[-1].scan_index

    def _on_scroll_settled(self):
        new_idx = self._page_at_viewport_center()
        if new_idx == -1 or new_idx == self._active_index:
            return
        self._active_index = new_idx
        self._update_loaded_window()
        if not self._suppress_active_signal:
            self.active_page_changed.emit(new_idx)

    # --------------------------------------------------------- overlay paint
    def _draw_overlays(self, image, scan, answer_key, low_conf_threshold):
        out = image.copy()
        if scan.bars is None:
            return out
        for q in range(1, scan.num_questions + 1):
            selected = set(scan.answers.get(q, []))
            correct = answer_key.correct_for(q) if answer_key else set()
            low_conf = scan.confidence.get(q, 1.0) < low_conf_threshold
            # No-answer row: the key has a correct answer for this question
            # but the student selected nothing. Highlight separately so it
            # looks different from "ambiguous" (low-confidence) rows.
            no_answer = (answer_key is not None
                         and not selected and bool(correct)
                         and scan.matric_string() != ANSWER_KEY_MATRIC)
            for opt in range(NUM_OPTIONS):
                rect = scan.bubble_rect(q, opt)
                if rect is None:
                    continue
                x1, y1, x2, y2 = rect
                if opt in selected:
                    cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_SELECTED, SELECTED_THICKNESS)
                else:
                    cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_UNSELECTED, 1)
                if self._show_correct_answers and correct and opt in correct:
                    cv2.rectangle(out, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), COLOR_KEY, KEY_THICKNESS)
            if low_conf or no_answer:
                xs = [scan.bubble_rect(q, o) for o in range(NUM_OPTIONS)]
                xs = [r for r in xs if r is not None]
                if xs:
                    rx1 = min(r[0] for r in xs) - 8
                    ry1 = min(r[1] for r in xs) - 8
                    rx2 = max(r[2] for r in xs) + 8
                    ry2 = max(r[3] for r in xs) + 8
                    # Low-confidence wins if both apply (ambiguity beats
                    # missing — the user might still want to flip a bubble).
                    color = COLOR_LOW_CONF if low_conf else COLOR_NO_ANSWER
                    cv2.rectangle(out, (rx1, ry1), (rx2, ry2), color, LOW_CONF_THICKNESS)

        for digit in range(10):
            for pos in range(MATRIC_LENGTH):
                rect = scan.matric_bubble_rect(digit, pos)
                if rect is None:
                    continue
                x1, y1, x2, y2 = rect
                if scan.matric_digits[pos] == digit:
                    cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_SELECTED, SELECTED_THICKNESS)
                else:
                    cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_UNSELECTED, 1)

        if getattr(scan, "skip_from_export", False):
            # Big "SKIPPED" badge top-right so it's obvious in the multi-page
            # scroll which pages won't be in the export.
            text = "SKIPPED FROM EXPORT"
            font_scale = out.shape[0] / 700
            thickness = max(2, int(font_scale * 3))
            (tw, th), _ = cv2.getTextSize(
                text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
            x = out.shape[1] - tw - 60
            y = th + 60
            cv2.rectangle(out, (x - 20, y - th - 20), (x + tw + 20, y + 20),
                          (255, 230, 230), cv2.FILLED)
            cv2.putText(out, text, (x, y),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                        COLOR_SKIP_BADGE, thickness, cv2.LINE_AA)
        return out

    def _hit_test_in_scan(self, scan, local_x, local_y):
        """Find the smallest bubble rect containing the local-coord click,
        if any. Returns ``(kind, i1, i2)`` or ``None``."""
        if scan.bars is None:
            return None
        best = None
        best_area = float("inf")
        for q in range(1, scan.num_questions + 1):
            for opt in range(NUM_OPTIONS):
                rect = scan.bubble_rect(q, opt)
                if rect is None:
                    continue
                x1, y1, x2, y2 = rect
                if x1 <= local_x <= x2 and y1 <= local_y <= y2:
                    area = (x2 - x1) * (y2 - y1)
                    if area < best_area:
                        best_area = area
                        best = ("answer", q, opt)
        for digit in range(10):
            for pos in range(MATRIC_LENGTH):
                rect = scan.matric_bubble_rect(digit, pos)
                if rect is None:
                    continue
                x1, y1, x2, y2 = rect
                if x1 <= local_x <= x2 and y1 <= local_y <= y2:
                    area = (x2 - x1) * (y2 - y1)
                    if area < best_area:
                        best_area = area
                        best = ("matric", digit, pos)
        return best

    def mouseMoveEvent(self, event):
        # Show a confidence tooltip when hovering over an answer bubble.
        # Cheap: only re-emit when the (page, question) under the cursor
        # actually changes.
        if self._blocks:
            sp = self.mapToScene(event.position().toPoint())
            for block in self._blocks:
                if block.bounds.contains(sp):
                    local_x = sp.x() - block.bounds.x()
                    local_y = sp.y() - block.bounds.y()
                    hit = self._hit_test_in_scan(block.scan, local_x, local_y)
                    if hit and hit[0] == "answer":
                        key = (block.scan_index, hit[1])
                        if key != self._last_tooltip_key:
                            conf = block.scan.confidence.get(hit[1], 0.0)
                            QtWidgets.QToolTip.showText(
                                event.globalPosition().toPoint(),
                                f"Q{hit[1]} confidence: {conf:.2f}\n"
                                "(0 = on the boundary, 1 = clearly classified)",
                                self,
                            )
                            self._last_tooltip_key = key
                        return super().mouseMoveEvent(event)
                    break
        if self._last_tooltip_key is not None:
            QtWidgets.QToolTip.hideText()
            self._last_tooltip_key = None
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton or not self._blocks:
            return super().mousePressEvent(event)
        sp = self.mapToScene(event.position().toPoint())
        for block in self._blocks:
            if block.bounds.contains(sp):
                local_x = sp.x() - block.bounds.x()
                local_y = sp.y() - block.bounds.y()
                hit = self._hit_test_in_scan(block.scan, local_x, local_y)
                if hit is not None:
                    kind, i1, i2 = hit
                    self.bubble_clicked.emit(block.scan_index, kind, i1, i2)
                    event.accept()
                    return
                break
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        # On macOS Qt swaps Ctrl and Cmd by default, so ControlModifier here
        # corresponds to the physical Cmd key. Accept Meta as well for setups
        # where the swap is disabled, and on Linux/Windows for the literal
        # Ctrl key. Trackpad smooth scrolling reports pixelDelta; classic
        # wheels report angleDelta.
        mods = event.modifiers()
        if mods & (QtCore.Qt.KeyboardModifier.ControlModifier
                   | QtCore.Qt.KeyboardModifier.MetaModifier):
            pixel_dy = event.pixelDelta().y() if not event.pixelDelta().isNull() else 0
            angle_dy = event.angleDelta().y()
            # 1 angle notch == 120 units. We want a per-notch factor near
            # 1.08 (gentler than the previous 1.15) and pixel-based smooth
            # zoom that scales with actual scroll distance.
            if pixel_dy != 0:
                factor = 1.0 + pixel_dy / 250.0
            elif angle_dy != 0:
                factor = 1.0 + angle_dy / 1500.0
            else:
                event.accept()
                return
            factor = max(0.5, min(2.0, factor))
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    def viewportEvent(self, event):
        if event.type() == QtCore.QEvent.Type.Gesture:
            return self._handle_gesture(event)
        return super().viewportEvent(event)

    def _handle_gesture(self, event):
        pinch = event.gesture(QtCore.Qt.GestureType.PinchGesture)
        if pinch is None:
            return False
        flags = pinch.changeFlags()
        if flags & QtWidgets.QPinchGesture.ChangeFlag.ScaleFactorChanged:
            factor = float(pinch.scaleFactor())
            # Pinch sends frequent small deltas; clamp to keep things gentle.
            factor = max(0.7, min(1.4, factor))
            self.scale(factor, factor)
        event.accept(pinch)
        return True

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_0 and self._blocks:
            self._fit_block(self._active_index if self._active_index >= 0 else 0)
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
        self.combo.setToolTip(
            "How each question's score is computed. The chosen strategy\n"
            "applies to every student in the cohort."
        )
        for s in self._strategies:
            self.combo.addItem(s.NAME, s)
        self.combo.currentIndexChanged.connect(self._on_strategy_changed)
        row.addWidget(self.combo, 1)
        load_btn = QtWidgets.QPushButton("Load custom…")
        load_btn.setToolTip(
            "Load a strategy from a Python file. The module must define a\n"
            "score(selected, correct, weight, num_options, **opts) function\n"
            "and (optionally) NAME, DESCRIPTION, and OPTIONS metadata."
        )
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
    """The Review tab. Holds the page list, the page view, and the side panel.

    Scoring is configured on the Setup tab and shared with this widget via
    the ``scoring_panel`` parameter — both the live per-page total here and
    the CSV export read from the same panel."""

    exported = QtCore.Signal(str)

    def __init__(self, scoring_panel: "ScoringPanel", parent=None):
        super().__init__(parent)
        self.scans = []
        self.answer_key: Optional[AnswerKey] = None
        self.image_cache: Optional[PageImageCache] = None
        self.current_scan_index = -1
        self.low_conf_threshold = 0.15
        self.scoring_panel = scoring_panel
        self._undo = UndoManager()
        # Session save target (set in set_data when a PDF path is supplied).
        self._session_path: Optional["pathlib.Path"] = None
        self._scan_pdf_path: Optional[str] = None
        self._answer_source: Optional[dict] = None
        self._build_ui()
        self._install_shortcuts()
        self.scoring_panel.changed.connect(self._on_scoring_changed)

    def _install_shortcuts(self):
        """Keyboard shortcuts for fast triage. Use ``ApplicationShortcut`` so
        they fire on the Review tab regardless of which sub-widget has focus,
        but don't override text-input keys (the matric field consumes its
        own keystrokes before the shortcut sees them)."""
        bindings = [
            ("J", lambda: self._navigate(1)),
            ("K", lambda: self._navigate(-1)),
            ("N", self._jump_to_next_review),
            ("F", self._toggle_review_filter),
            (QtGui.QKeySequence.StandardKey.Undo, self._do_undo),
            (QtGui.QKeySequence.StandardKey.Redo, self._do_redo),
        ]
        for key, slot in bindings:
            seq = key if isinstance(key, QtGui.QKeySequence.StandardKey) \
                else QtGui.QKeySequence(key)
            sc = QtGui.QShortcut(seq, self)
            sc.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

    def _toggle_review_filter(self):
        self.filter_combo.setCurrentIndex(
            (self.filter_combo.currentIndex() + 1) % self.filter_combo.count()
        )

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)

        # Left pane
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["All pages", "Needs review only"])
        self.filter_combo.setToolTip(
            "All pages: every scanned page in document order.\n"
            "Needs review only: just the pages with issues to check."
        )
        self.filter_combo.currentIndexChanged.connect(self._refresh_list)
        ll.addWidget(self.filter_combo)
        self.page_list = QtWidgets.QListWidget()
        self.page_list.setToolTip(
            "✓ pages have no issues; ⚠ pages have something to check.\n"
            "Click any entry to jump to that page."
        )
        self.page_list.currentRowChanged.connect(self._on_list_row_changed)
        ll.addWidget(self.page_list)
        self.export_button = QtWidgets.QPushButton("Export results CSV…")
        self.export_button.setToolTip(
            "Save a CSV with one row per student. Includes per-question\n"
            "correct/incorrect counts and a Total column when a scoring\n"
            "strategy is chosen on the Setup tab."
        )
        self.export_button.clicked.connect(self._on_export)
        self.export_button.setEnabled(False)
        ll.addWidget(self.export_button)
        left.setMaximumWidth(260)
        root.addWidget(left, 0)

        # Center: page view (gets the lion's share of the width).
        self.page_view = PageImageView()
        self.page_view.bubble_clicked.connect(self._on_bubble_clicked)
        self.page_view.active_page_changed.connect(self._on_active_page_changed)
        root.addWidget(self.page_view, 1)

        # Right pane (kept compact so the page image dominates).
        right = QtWidgets.QWidget()
        right.setMaximumWidth(280)
        rl = QtWidgets.QVBoxLayout(right)
        rl.addWidget(QtWidgets.QLabel("<b>Page</b>"))
        self.page_label = QtWidgets.QLabel("-")
        rl.addWidget(self.page_label)

        rl.addWidget(QtWidgets.QLabel("<b>Matriculation number</b>"))
        self.matric_edit = QtWidgets.QLineEdit()
        self.matric_edit.setMaxLength(8)
        self.matric_edit.setPlaceholderText("8 digits")
        self.matric_edit.setToolTip(
            "8-digit matric for the current page. You can also click the\n"
            "matric bubbles directly on the page image."
        )
        self.matric_edit.editingFinished.connect(self._on_matric_edited)
        rl.addWidget(self.matric_edit)

        rl.addWidget(QtWidgets.QLabel("<b>Issues to check</b>"))
        self.flags_view = QtWidgets.QPlainTextEdit()
        self.flags_view.setReadOnly(True)
        self.flags_view.setMaximumHeight(120)
        self.flags_view.setPlaceholderText("(no issues — looks clean)")
        self.flags_view.setToolTip(
            "Things the scanner thinks need a closer look on this page.\n"
            "Click bubbles in the page image to fix detection mistakes."
        )
        rl.addWidget(self.flags_view)

        rl.addWidget(QtWidgets.QLabel("<b>Score</b>"))
        self.score_label = QtWidgets.QLabel("-")
        self.score_label.setStyleSheet("font-size: 14pt;")
        self.score_label.setToolTip(
            "Live score for the current page using the scoring strategy\n"
            "configured on the Setup tab."
        )
        rl.addWidget(self.score_label)

        self.show_correct_check = QtWidgets.QCheckBox("Show correct answers")
        self.show_correct_check.setChecked(True)
        self.show_correct_check.setToolTip(
            "When ticked, the correct answers are outlined in red on every\n"
            "page (only meaningful once an answer key is loaded)."
        )
        self.show_correct_check.toggled.connect(self.page_view.set_show_correct_answers)
        rl.addWidget(self.show_correct_check)

        self.skip_check = QtWidgets.QCheckBox("Skip this page from export")
        self.skip_check.setToolTip(
            "Tick to exclude the current page from the exported CSV — useful\n"
            "for duplicate scans or blank sheets. The page stays visible in\n"
            "the review list with a SKIPPED FROM EXPORT badge."
        )
        self.skip_check.toggled.connect(self._on_skip_toggled)
        rl.addWidget(self.skip_check)

        self.review_slider_label = QtWidgets.QLabel(
            f"Flag sensitivity: {int(self.low_conf_threshold * 100)}%"
        )
        rl.addWidget(self.review_slider_label)
        self.review_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.review_slider.setMinimum(0)
        self.review_slider.setMaximum(100)
        self.review_slider.setValue(int(self.low_conf_threshold * 100))
        self.review_slider.setToolTip(
            "Higher = more questions get flagged for review.\n"
            "Lower = only the most ambiguous bubbles surface."
        )
        self.review_slider.valueChanged.connect(self._on_review_slider_changed)
        rl.addWidget(self.review_slider)

        nav = QtWidgets.QHBoxLayout()
        prev_btn = QtWidgets.QPushButton("◀ Prev")
        prev_btn.setToolTip("Jump to the previous page in the list.")
        prev_btn.clicked.connect(lambda: self._navigate(-1))
        next_btn = QtWidgets.QPushButton("Next ▶")
        next_btn.setToolTip("Jump to the next page in the list.")
        next_btn.clicked.connect(lambda: self._navigate(1))
        nav.addWidget(prev_btn)
        nav.addWidget(next_btn)
        rl.addLayout(nav)

        next_review = QtWidgets.QPushButton("Next page needing review")
        next_review.setToolTip(
            "Jump to the next page that has any issue to check, wrapping\n"
            "around the cohort if necessary. (shortcut: N)"
        )
        next_review.clicked.connect(self._jump_to_next_review)
        rl.addWidget(next_review)

        hint = QtWidgets.QLabel(
            f"<i>Click bubbles to toggle. "
            f"{ZOOM_KEY_LABEL}+scroll or pinch zooms; '0' fits to window. "
            f"J/K next/previous page · N next-needing-review · F toggle filter.</i>"
        )
        hint.setWordWrap(True)
        hint.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                           QtWidgets.QSizePolicy.Policy.Minimum)
        rl.addWidget(hint)
        rl.addStretch(1)
        root.addWidget(right, 0)

    # ------------------------------------------------------------------ data
    def set_data(self, scans, answer_key, image_cache, low_conf_threshold=0.15,
                 scan_pdf_path: Optional[str] = None,
                 answer_source: Optional[dict] = None):
        self.scans = scans
        self.answer_key = answer_key
        self.image_cache = image_cache
        self.low_conf_threshold = low_conf_threshold
        self._scan_pdf_path = scan_pdf_path
        self._answer_source = answer_source
        self._undo.clear()

        # Resolve where this cohort's mid-review state lives (if any) and
        # offer to resume any saved edits before showing the pages.
        self._session_path = self._resolve_session_path(scan_pdf_path)
        if self._session_path is not None and self._session_path.exists():
            self._maybe_resume_session()

        recompute_duplicate_flags(self.scans)
        for s in self.scans:
            recompute_flags(s, self.low_conf_threshold, self.answer_key)
        self.export_button.setEnabled(answer_key is not None)
        self.page_view.set_pages(scans, answer_key, image_cache, low_conf_threshold)
        self._refresh_list()
        if self.page_list.count():
            self.page_list.setCurrentRow(0)
            self.current_scan_index = 0
            if self.scans:
                self._update_side_panel(self.scans[0])

    # --------------------------------------------------------- session state
    def _session_dir(self) -> pathlib.Path:
        base = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.StandardLocation.AppDataLocation
        )
        path = pathlib.Path(base) / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve_session_path(self, scan_pdf_path) -> Optional[pathlib.Path]:
        if not scan_pdf_path:
            return None
        # Hash the absolute path so different cohorts don't collide and the
        # filename doesn't leak the user's directory layout.
        digest = hashlib.sha1(scan_pdf_path.encode("utf-8")).hexdigest()[:16]
        return self._session_dir() / f"{digest}.json"

    def _maybe_resume_session(self):
        """If a saved session exists for this PDF, ask the user whether to
        restore it. Called from set_data after a fresh scan completes."""
        try:
            payload = json.loads(self._session_path.read_text())
        except (OSError, ValueError) as exc:
            logging.warning(f"Could not read session file: {exc}")
            return
        n_edits = len(payload.get("scans", []))
        if not n_edits:
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            "Resume previous review?",
            f"A previous session for this PDF has {n_edits} edited page(s).\n\n"
            "Restore those edits on top of the fresh scan?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self._apply_saved_state(payload)

    def _apply_saved_state(self, payload):
        by_page = {entry["page_index"]: entry for entry in payload.get("scans", [])}
        applied = 0
        for scan in self.scans:
            entry = by_page.get(scan.page_index)
            if entry is None:
                continue
            try:
                restore_scan_snapshot(scan, entry)
                applied += 1
            except Exception as exc:
                logging.warning(
                    f"Could not restore page {scan.page_index + 1}: {exc}"
                )
        logging.info(f"Restored {applied} edited page(s) from saved session.")

    def _save_session_state(self):
        """Persist user edits to disk after every change. The file is
        deleted on a clean quit and after a successful export."""
        if self._session_path is None:
            return
        # Save only pages with non-default state to keep the file small and
        # to make "no edits → no file" easy to detect.
        edited = []
        for scan in self.scans:
            snap = scan_snapshot(scan)
            edited.append({"page_index": scan.page_index, **snap})
        payload = {
            "scan_pdf": self._scan_pdf_path,
            "answer_source": self._answer_source,
            "scans": edited,
        }
        try:
            self._session_path.write_text(json.dumps(payload))
        except OSError as exc:
            logging.warning(f"Could not save session: {exc}")

    def clear_session(self):
        """Remove the on-disk session file. Called on a clean app quit and
        after a successful export."""
        if self._session_path is not None and self._session_path.exists():
            try:
                self._session_path.unlink()
            except OSError as exc:
                logging.warning(f"Could not delete session file: {exc}")

    # ------------------------------------------------------------- list view
    def _list_label(self, scan):
        flag_icon = "⚠ " if scan.flags else "✓ "
        suffix = "  (KEY)" if scan.matric_string() == ANSWER_KEY_MATRIC else ""
        if getattr(scan, "skip_from_export", False):
            suffix = "  (SKIPPED)" + suffix
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
        if idx == self.current_scan_index:
            return
        # Programmatic scroll. Fitting on list-driven jumps would override
        # the user's working zoom level, so pass fit=False.
        self.page_view._suppress_active_signal = True
        self.page_view.scroll_to_page(idx)
        self.page_view._suppress_active_signal = False
        self.current_scan_index = idx
        self._update_side_panel(self.scans[idx])

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
                for row in range(self.page_list.count()):
                    if self.page_list.item(row).data(QtCore.Qt.ItemDataRole.UserRole) == i:
                        self.page_list.setCurrentRow(row)
                        return

    def _on_review_slider_changed(self, value):
        threshold = value / 100.0
        self.low_conf_threshold = threshold
        self.review_slider_label.setText(f"Flag sensitivity: {value}%")
        for s in self.scans:
            recompute_flags(s, threshold, self.answer_key)
        recompute_duplicate_flags(self.scans)
        self._refresh_list()
        if 0 <= self.current_scan_index < len(self.scans):
            self._update_side_panel(self.scans[self.current_scan_index])
        self.page_view.set_low_conf_threshold(threshold)

    @QtCore.Slot(int)
    def _on_active_page_changed(self, scan_idx):
        """User scrolled the page view; sync the left list and side panel."""
        if scan_idx == self.current_scan_index:
            return
        self.current_scan_index = scan_idx
        for row in range(self.page_list.count()):
            if self.page_list.item(row).data(QtCore.Qt.ItemDataRole.UserRole) == scan_idx:
                self.page_list.blockSignals(True)
                self.page_list.setCurrentRow(row)
                self.page_list.blockSignals(False)
                break
        if 0 <= scan_idx < len(self.scans):
            self._update_side_panel(self.scans[scan_idx])

    def _update_side_panel(self, scan):
        self.page_label.setText(f"{scan.page_index + 1} of {len(self.scans)}")
        self.matric_edit.blockSignals(True)
        self.matric_edit.setText(scan.matric_string())
        self.matric_edit.blockSignals(False)
        issues = friendly_issue_summary(scan)
        self.flags_view.setPlainText("\n".join(f"• {line}" for line in issues))
        self.skip_check.blockSignals(True)
        self.skip_check.setChecked(bool(getattr(scan, "skip_from_export", False)))
        self.skip_check.blockSignals(False)
        self._refresh_score()

    # --------------------------------------------------------------- edits
    def _apply_edit(self, scan_idx, mutator, description):
        """Capture before/after snapshots around ``mutator(scan)`` and push
        the pair to the undo stack, then update views and persist state.

        ``mutator(scan)`` performs the change in-place; this helper does the
        rest — flag recomputation, overlay refresh, list label, side panel,
        session save."""
        if not (0 <= scan_idx < len(self.scans)):
            return
        scan = self.scans[scan_idx]
        before = scan_snapshot(scan)
        mutator(scan)
        after = scan_snapshot(scan)
        if before == after:
            return  # no-op edit, don't pollute the undo stack
        self._undo.push(UndoEntry(scan_idx, description, before, after))
        self._post_edit(scan_idx)

    def _post_edit(self, scan_idx):
        """Refresh everything that depends on a scan's state. Called after
        every edit, every undo, and every redo."""
        scan = self.scans[scan_idx]
        recompute_flags(scan, self.low_conf_threshold, self.answer_key)
        recompute_duplicate_flags(self.scans)
        self.page_view.refresh_overlays_for(scan_idx)
        if scan_idx == self.current_scan_index:
            self._update_side_panel(scan)
        self._refresh_list_item(scan_idx)
        self._save_session_state()

    def _on_bubble_clicked(self, scan_idx, kind, i1, i2):
        if kind == "answer":
            self._apply_edit(scan_idx,
                             lambda s: s.toggle_answer(i1, i2),
                             f"toggle answer for q{i1}")
        else:  # matric
            def mutate(s):
                current = s.matric_digits[i2]
                s.set_matric_digit(i2, None if current == i1 else i1)
            self._apply_edit(scan_idx, mutate, f"matric digit {i2 + 1}")

    def _on_skip_toggled(self, checked: bool):
        if self.current_scan_index < 0:
            return
        scan = self.scans[self.current_scan_index]
        if scan.skip_from_export == checked:
            return
        self._apply_edit(self.current_scan_index,
                         lambda s: setattr(s, "skip_from_export", checked),
                         "skip from export" if checked else "include in export")

    def _do_undo(self):
        e = self._undo.undo()
        if e is None:
            return
        restore_scan_snapshot(self.scans[e.scan_idx], e.before)
        self._post_edit(e.scan_idx)
        self._scroll_to_affected_page(e.scan_idx)
        logging.info(f"Undid: {e.description} (page {e.scan_idx + 1})")

    def _do_redo(self):
        e = self._undo.redo()
        if e is None:
            return
        restore_scan_snapshot(self.scans[e.scan_idx], e.after)
        self._post_edit(e.scan_idx)
        self._scroll_to_affected_page(e.scan_idx)
        logging.info(f"Redid: {e.description} (page {e.scan_idx + 1})")

    def _scroll_to_affected_page(self, scan_idx):
        """If undo/redo touched a page that's not currently visible, scroll
        to it so the user sees what changed."""
        if scan_idx == self.current_scan_index:
            return
        for row in range(self.page_list.count()):
            if self.page_list.item(row).data(QtCore.Qt.ItemDataRole.UserRole) == scan_idx:
                self.page_list.setCurrentRow(row)
                return

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
        new_digits = [int(c) for c in text]
        self._apply_edit(self.current_scan_index,
                         lambda s: s.__setattr__("matric_digits", new_digits),
                         "edit matric")
        # duplicate change can affect siblings — refresh whole list labels
        self._refresh_list()

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
        # Successful export → mid-review state is no longer needed; clear it
        # so a future re-run of this PDF starts from a clean scan.
        self.clear_session()
        self.exported.emit(path)
        QtWidgets.QMessageBox.information(
            self, "Exported", f"Saved {len(df)} rows to:\n{path}"
        )
