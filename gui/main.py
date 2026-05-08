import logging
import sys
from typing import Optional

from PySide6 import QtCore, QtWidgets

from bubblemarking import scanning
from bubblemarking.dataframes import (
    AnswerKey,
    extract_answer_key_from_scans,
    read_answer_key_from_file,
)
from bubblemarking.gui.review import (
    PageImageCache,
    ReviewWidget,
    ScoringPanel,
    recompute_duplicate_flags,
    recompute_flags,
)


SETTINGS_ORG = "bubblemarking"
SETTINGS_APP = "bubblemarking"


class WriteLogToWidgetHandler(logging.Handler):
    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    def emit(self, record):
        QtCore.QMetaObject.invokeMethod(
            self.widget,
            "append",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, self.format(record)),
        )


class ScanWorker(QtCore.QObject):
    """Runs the page-by-page scan in a worker thread, emitting progress."""

    progress = QtCore.Signal(int, int, str)
    finished = QtCore.Signal(list, object, object)  # scans, answer_key, doc
    failed = QtCore.Signal(str)

    def __init__(self, scan_path, answer_path, one_answer_only, answer_in_scan):
        super().__init__()
        self.scan_path = scan_path
        self.answer_path = answer_path
        self.one_answer_only = one_answer_only
        self.answer_in_scan = answer_in_scan

    @QtCore.Slot()
    def run(self):
        try:
            answer_key: Optional[AnswerKey] = None
            num_questions = None
            if not self.answer_in_scan:
                if not self.answer_path:
                    self.failed.emit("No answer file selected.")
                    return
                try:
                    answer_key = read_answer_key_from_file(self.answer_path)
                    num_questions = answer_key.num_questions
                    logging.info(f"Loaded answer key with {num_questions} questions")
                except Exception as exc:
                    self.failed.emit(f"Could not read answer file: {exc}")
                    return

            try:
                doc = scanning.get_file(self.scan_path)
            except Exception as exc:
                self.failed.emit(f"Could not open scan file: {exc}")
                return

            num_pages = scanning.get_number_of_pages(doc)
            logging.info(f"Scanning {num_pages} pages")

            scans = []
            for i in range(num_pages):
                self.progress.emit(i + 1, num_pages, f"Scanning page {i + 1}…")
                try:
                    image = scanning.get_image_from_file(doc, i)
                    scan = scanning.scan_page(
                        image,
                        page_index=i,
                        one_answer_only=self.one_answer_only,
                        num_questions=num_questions,
                    )
                except Exception as exc:
                    logging.error(f"Page {i + 1} failed: {exc}")
                    scan = scanning.PageScan(page_index=i, flags=["unreadable"])

                # Free the prepared image immediately — the GUI will re-render
                # via PageImageCache on demand. Keeping all of them at SCALE=5.0
                # would be many GB for a real-size cohort.
                scan.prepared_image = None

                if scan.matric_string() == scanning.ANSWER_KEY_MATRIC and num_questions is None:
                    last = 0
                    for q in sorted(scan.answers.keys()):
                        if scan.answers[q]:
                            last = q
                    num_questions = max(last, 1)
                    logging.info(f"Answer key found on page {i + 1}, num_questions={num_questions}")

                scans.append(scan)

            # Cohort calibration: pool first-pass labels across the batch to
            # learn an absolute filled/blank threshold, then re-classify every
            # bubble. Confidence becomes margin-from-boundary, so the review
            # queue surfaces only genuinely ambiguous rows.
            calibration = scanning.calibrate_from_scans(scans)
            if calibration.valid:
                logging.info(
                    f"Calibration: filled median {calibration.filled_median:.0f}, "
                    f"blank median {calibration.unfilled_median:.0f}, "
                    f"threshold {calibration.threshold:.0f} "
                    f"(n_filled={calibration.n_filled}, n_blank={calibration.n_unfilled})"
                )
                for s in scans:
                    scanning.reclassify_with_calibration(s, calibration)
            else:
                logging.warning(
                    "Calibration skipped — not enough first-pass labels to learn "
                    f"a filled/blank boundary (n_filled={calibration.n_filled}, "
                    f"n_blank={calibration.n_unfilled}). Falling back to per-row threshold."
                )

            if answer_key is None:
                answer_key = extract_answer_key_from_scans(scans)
                if answer_key is None:
                    logging.warning(
                        "No answer key found in scans. Export will be unavailable until you load one."
                    )

            for s in scans:
                recompute_flags(s, answer_key=answer_key)
            recompute_duplicate_flags(scans)

            self.finished.emit(scans, answer_key, doc)
        except Exception as exc:
            logging.exception("Scan worker crashed")
            self.failed.emit(str(exc))


class AppMainWindow(QtCore.QObject):
    """The main window: a QTabWidget with a Setup tab (built from scratch
    here) and the interactive Review tab.

    Inherits ``QObject`` so the slots wired up to the cross-thread
    ``ScanWorker`` signals run on the main (GUI) thread — without that,
    macOS aborts when a ``QMessageBox`` is constructed from the worker."""

    def __init__(self, window):
        super().__init__(window)
        self._main_window = window
        window.setWindowTitle("MCQ Scanning")

        # ---- Setup tab (fresh widgets, not from gui.ui) ----
        setup_tab = QtWidgets.QWidget()
        setup_v = QtWidgets.QVBoxLayout(setup_tab)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        # Scan file
        scan_row = QtWidgets.QHBoxLayout()
        self.ScanFileName = QtWidgets.QLineEdit()
        self.ScanFileName.setToolTip("Path to the scanned PDF.")
        self.ScanFileSelectButton = QtWidgets.QPushButton("Select…")
        self.ScanFileSelectButton.setToolTip(
            "Pick the PDF of scanned answer sheets. One page per student."
        )
        scan_row.addWidget(self.ScanFileName, 1)
        scan_row.addWidget(self.ScanFileSelectButton)
        form.addRow("Scan PDF:", scan_row)

        # One-answer-only checkbox
        self.OneAnswerCheckbox = QtWidgets.QCheckBox(
            "Warn if more than one answer per question"
        )
        self.OneAnswerCheckbox.setToolTip(
            "Tick for single-answer exams. Rows where the scanner detects more\n"
            "than one filled bubble will be flagged for review."
        )
        form.addRow("", self.OneAnswerCheckbox)

        # Answer-in-scan checkbox
        self.AnswerInFileCheckbox = QtWidgets.QCheckBox(
            "Answer key is in the scan (matric 00000000)"
        )
        self.AnswerInFileCheckbox.setChecked(True)
        self.AnswerInFileCheckbox.setToolTip(
            "Leave ticked if a tutor has bubbled the key onto a sheet using\n"
            "matriculation 00000000. Untick to load a separate CSV/XLSX key."
        )
        form.addRow("", self.AnswerInFileCheckbox)

        # Answer file
        ans_row = QtWidgets.QHBoxLayout()
        self.AnswerFileName = QtWidgets.QLineEdit()
        self.AnswerFileName.setToolTip("Path to the answer key file.")
        self.AnswerFileSelectButton = QtWidgets.QPushButton("Select…")
        self.AnswerFileSelectButton.setToolTip(
            "Pick a CSV or XLSX answer key.\n"
            "Columns: question number, comma-separated correct letters,\n"
            "optional question weight."
        )
        ans_row.addWidget(self.AnswerFileName, 1)
        ans_row.addWidget(self.AnswerFileSelectButton)
        self.AnswerFileLabel = QtWidgets.QLabel("Answer file:")
        form.addRow(self.AnswerFileLabel, ans_row)

        # The answer file row is meaningful only when the key is NOT in the scan.
        def _toggle_answer_row(checked: bool):
            self.AnswerFileName.setEnabled(not checked)
            self.AnswerFileSelectButton.setEnabled(not checked)
            self.AnswerFileLabel.setEnabled(not checked)
        _toggle_answer_row(self.AnswerInFileCheckbox.isChecked())
        self.AnswerInFileCheckbox.toggled.connect(_toggle_answer_row)

        # Wrap the form so it stays at sane width and aligned left.
        form_holder = QtWidgets.QWidget()
        form_holder.setLayout(form)
        form_holder.setMaximumWidth(700)
        form_row = QtWidgets.QHBoxLayout()
        form_row.addWidget(form_holder)
        form_row.addStretch(1)
        setup_v.addLayout(form_row)

        self.ScanButton = QtWidgets.QPushButton("Scan and review")
        self.ScanButton.setToolTip(
            "Begin scanning every page in the PDF. The Review tab opens\n"
            "automatically when scanning finishes."
        )
        setup_v.addWidget(self.ScanButton)

        # Scoring strategy lives on the Setup tab — it applies to every
        # student in the cohort, so it makes sense to configure it once
        # before scanning. The Review tab reads from this same panel.
        self.scoring_panel = ScoringPanel()
        scoring_row = QtWidgets.QHBoxLayout()
        scoring_holder = QtWidgets.QWidget()
        scoring_holder.setLayout(QtWidgets.QVBoxLayout())
        scoring_holder.layout().setContentsMargins(0, 0, 0, 0)
        scoring_holder.layout().addWidget(self.scoring_panel)
        scoring_holder.setMaximumWidth(700)
        scoring_row.addWidget(scoring_holder)
        scoring_row.addStretch(1)
        setup_v.addLayout(scoring_row)

        # Log text area
        self.OutputTextArea = QtWidgets.QTextBrowser()
        setup_v.addWidget(self.OutputTextArea, 1)

        self.ClearOutputButton = QtWidgets.QPushButton("Clear output")
        self.ClearOutputButton.setToolTip("Clear the log above.")
        self.ClearOutputButton.clicked.connect(self.OutputTextArea.clear)
        setup_v.addWidget(self.ClearOutputButton)

        # ---- Review tab ----
        self.review = ReviewWidget(scoring_panel=self.scoring_panel)
        self.review.exported.connect(self._on_exported)

        # ---- Tabs ----
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(setup_tab, "Setup")
        self.tabs.addTab(self.review, "Review and export")
        self.tabs.setTabEnabled(1, False)
        window.setCentralWidget(self.tabs)

        # Menubar (native on macOS so it appears at the top of the screen).
        window.menuBar().setNativeMenuBar(True)

        # ---- Signals ----
        self.ScanFileSelectButton.clicked.connect(self.select_scan_file)
        self.AnswerFileSelectButton.clicked.connect(self.select_answer_file)
        self.ScanButton.clicked.connect(self.run_scan)

        # ---- Logging ----
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        widget_handler = WriteLogToWidgetHandler(widget=self.OutputTextArea)
        widget_handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logger.addHandler(widget_handler)
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logger.addHandler(stream)

        self._scan_thread: Optional[QtCore.QThread] = None
        self._worker: Optional[ScanWorker] = None
        self._doc = None

        # ---- Persisted settings ----
        # Settings get loaded after every input is connected so handlers see
        # the restored state. Saves happen on every user-driven change so
        # there's no need to hook a save on quit.
        self._settings = QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._load_settings()
        self._connect_settings_writes()

    # ----------------------------------------------------------- settings
    def _load_settings(self):
        s = self._settings
        # Bool keys.
        self.OneAnswerCheckbox.setChecked(
            s.value("one_answer_only", False, type=bool)
        )
        self.AnswerInFileCheckbox.setChecked(
            s.value("answer_in_scan", True, type=bool)
        )
        self.review.show_correct_check.setChecked(
            s.value("show_correct_answers", True, type=bool)
        )
        # Float keys.
        thr = float(s.value("low_conf_threshold", 0.15))
        thr = max(0.0, min(1.0, thr))
        self.review.review_slider.setValue(int(round(thr * 100)))
        # Scoring strategy + its options.
        wanted_name = s.value("scoring_strategy", type=str)
        if wanted_name:
            for i in range(self.scoring_panel.combo.count()):
                if self.scoring_panel.combo.itemText(i) == wanted_name:
                    self.scoring_panel.combo.setCurrentIndex(i)
                    break
        # Per-strategy option values stored under "scoring_options/<name>/<key>".
        active = self.scoring_panel.strategy()
        if active is not None:
            self._restore_strategy_options(active)
        # Last scan/answer file paths — restored but not auto-loaded.
        self.ScanFileName.setText(s.value("last_scan_path", "", type=str))
        self.AnswerFileName.setText(s.value("last_answer_path", "", type=str))

    def _restore_strategy_options(self, strategy):
        s = self._settings
        prefix = f"scoring_options/{strategy.NAME}/"
        for name, widget in self.scoring_panel._option_widgets.items():
            key = prefix + name
            if not s.contains(key):
                continue
            value = s.value(key)
            if isinstance(widget, QtWidgets.QCheckBox):
                widget.setChecked(
                    value if isinstance(value, bool)
                    else str(value).lower() in ("1", "true", "yes", "on")
                )
            elif isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                try:
                    widget.setValue(type(widget.value())(float(value)))
                except (ValueError, TypeError):
                    pass
            elif isinstance(widget, QtWidgets.QLineEdit):
                widget.setText(str(value))

    def _connect_settings_writes(self):
        s = self._settings
        self.OneAnswerCheckbox.toggled.connect(
            lambda v: s.setValue("one_answer_only", bool(v))
        )
        self.AnswerInFileCheckbox.toggled.connect(
            lambda v: s.setValue("answer_in_scan", bool(v))
        )
        self.review.show_correct_check.toggled.connect(
            lambda v: s.setValue("show_correct_answers", bool(v))
        )
        self.review.review_slider.valueChanged.connect(
            lambda v: s.setValue("low_conf_threshold", v / 100.0)
        )
        self.scoring_panel.changed.connect(self._save_scoring_state)
        self.ScanFileName.editingFinished.connect(
            lambda: s.setValue("last_scan_path", self.ScanFileName.text())
        )
        self.AnswerFileName.editingFinished.connect(
            lambda: s.setValue("last_answer_path", self.AnswerFileName.text())
        )

    def _save_scoring_state(self):
        s = self._settings
        active = self.scoring_panel.strategy()
        if active is None:
            return
        s.setValue("scoring_strategy", active.NAME)
        prefix = f"scoring_options/{active.NAME}/"
        for name, widget in self.scoring_panel._option_widgets.items():
            if isinstance(widget, QtWidgets.QCheckBox):
                s.setValue(prefix + name, widget.isChecked())
            elif isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                s.setValue(prefix + name, widget.value())
            elif isinstance(widget, QtWidgets.QLineEdit):
                s.setValue(prefix + name, widget.text())

    # --- file pickers
    def select_scan_file(self):
        logging.info("Opening scan file picker…")
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select scan", "", "PDF files (*.pdf)"
        )
        if f:
            self.ScanFileName.setText(f)
            self._settings.setValue("last_scan_path", f)

    def select_answer_file(self):
        logging.info("Opening answer file picker…")
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select answer key", "",
            "Answer key (*.csv *.xlsx);;CSV (*.csv);;XLSX (*.xlsx)",
        )
        if f:
            self.AnswerFileName.setText(f)
            self._settings.setValue("last_answer_path", f)

    # --- scan flow
    def run_scan(self):
        if self._scan_thread is not None and self._scan_thread.isRunning():
            QtWidgets.QMessageBox.information(
                self._main_window, "Busy", "A scan is already running."
            )
            return

        scan_file = self.ScanFileName.text().strip()
        if not scan_file:
            QtWidgets.QMessageBox.warning(self._main_window, "No scan", "Pick a scan PDF first.")
            return
        answer_in_scan = self.AnswerInFileCheckbox.isChecked()
        answer_file = self.AnswerFileName.text().strip()
        one_answer_only = self.OneAnswerCheckbox.isChecked()

        self.ScanButton.setEnabled(False)
        self.OutputTextArea.append("Starting scan…")

        worker = ScanWorker(scan_file, answer_file, one_answer_only, answer_in_scan)
        thread = QtCore.QThread(self._main_window)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._scan_thread = thread
        self._worker = worker
        thread.start()

    @QtCore.Slot(int, int, str)
    def _on_progress(self, current, total, msg):
        logging.info(f"[{current}/{total}] {msg}")

    @QtCore.Slot(list, object, object)
    def _on_scan_finished(self, scans, answer_key, doc):
        self._doc = doc
        cache = PageImageCache(doc, scale=5.0, maxsize=4)
        self.review.set_data(scans, answer_key, cache)
        self.tabs.setTabEnabled(1, True)
        self.tabs.setCurrentIndex(1)
        flagged = sum(1 for s in scans if s.flags)
        logging.info(f"Scan complete: {len(scans)} pages, {flagged} flagged for review")

    @QtCore.Slot(str)
    def _on_scan_failed(self, msg):
        logging.error(msg)
        QtWidgets.QMessageBox.warning(self._main_window, "Scan failed", msg)

    @QtCore.Slot()
    def _on_thread_finished(self):
        self.ScanButton.setEnabled(True)
        self._scan_thread = None
        self._worker = None

    @QtCore.Slot(str)
    def _on_exported(self, path):
        logging.info(f"Wrote {path}")


def main():
    app = QtWidgets.QApplication(sys.argv)
    # Set the organisation / application identifiers so QSettings stores
    # preferences in the platform-native location without the noisy
    # "QSettings: name not specified" warnings.
    app.setOrganizationName(SETTINGS_ORG)
    app.setApplicationName(SETTINGS_APP)
    window = QtWidgets.QMainWindow()
    AppMainWindow(window)
    window.resize(1100, 750)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
