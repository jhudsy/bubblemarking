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
from bubblemarking.gui.gui import Ui_MainWindow
from bubblemarking.gui.review import (
    PageImageCache,
    ReviewWidget,
    recompute_duplicate_flags,
    recompute_flags,
)


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
                recompute_flags(s)
            recompute_duplicate_flags(scans)

            self.finished.emit(scans, answer_key, doc)
        except Exception as exc:
            logging.exception("Scan worker crashed")
            self.failed.emit(str(exc))


class AppMainWindow(Ui_MainWindow):
    def __init__(self, window):
        self.setupUi(window)
        self._main_window = window
        window.setWindowTitle("MCQ Scanning")

        # Hide widgets we no longer use. Output is chosen at export time on
        # the Review tab; the marked-PDF output has been replaced by the
        # interactive viewer.
        for w in (
            self.OutputFileLabel,
            self.OutputFileName,
            self.OutputFileSelectButton,
            self.SaveImageFileCheckbox,
            self.ImageFileLabel,
            self.ImageFileName,
            self.ImageFileSelectButton,
        ):
            w.hide()

        # Repurpose the central layout: wrap the existing setup form inside a
        # QTabWidget so we can add a Review tab alongside it.
        old_central = window.centralWidget()
        tabs = QtWidgets.QTabWidget(window)

        # The existing widgets live inside `old_central` already laid out via
        # geometry. We re-parent the form to a Setup tab and re-parent the log
        # text area below it.
        setup_tab = QtWidgets.QWidget()
        setup_layout = QtWidgets.QVBoxLayout(setup_tab)
        self.layoutWidget.setParent(setup_tab)
        setup_layout.addWidget(self.layoutWidget)
        self.OutputTextArea.setParent(setup_tab)
        setup_layout.addWidget(self.OutputTextArea, 1)
        self.ClearOutputButton.setParent(setup_tab)
        setup_layout.addWidget(self.ClearOutputButton)
        # Reset geometry-based positioning that the .ui file inflicted on us.
        for w in (self.layoutWidget, self.OutputTextArea, self.ClearOutputButton):
            w.setGeometry(QtCore.QRect())
        tabs.addTab(setup_tab, "Setup")

        # Review tab
        self.review = ReviewWidget()
        self.review.exported.connect(self._on_exported)
        tabs.addTab(self.review, "Review and export")
        tabs.setTabEnabled(1, False)

        self.tabs = tabs
        window.setCentralWidget(tabs)

        # Hooks
        self.ScanFileSelectButton.clicked.connect(self.select_scan_file)
        self.AnswerFileSelectButton.clicked.connect(self.select_answer_file)
        self.ScanButton.clicked.connect(self.run_scan)
        self.ScanButton.setText("Scan and review")

        self.menubar.setNativeMenuBar(True)

        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        handler = WriteLogToWidgetHandler(widget=self.OutputTextArea)
        handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logger.addHandler(handler)

        self._scan_thread: Optional[QtCore.QThread] = None
        self._worker: Optional[ScanWorker] = None
        self._doc = None

    # --- file pickers
    def select_scan_file(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._main_window, "Select scan", filter="PDF files (*.pdf)"
        )
        if f:
            self.ScanFileName.setText(f)

    def select_answer_file(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._main_window, "Select answer key",
            filter="Answer key (*.csv *.xlsx);;CSV (*.csv);;XLSX (*.xlsx)",
        )
        if f:
            self.AnswerFileName.setText(f)

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
    window = QtWidgets.QMainWindow()
    AppMainWindow(window)
    window.resize(1100, 750)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
