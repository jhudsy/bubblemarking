# bubblemarking

Reads a University of Aberdeen MCQ marksheet, detects which bubbles are filled,
lets you correct any misscans interactively, then exports per-student results.

Set `PYTHONPATH` to the directory containing the `bubblemarking` package, then:

```
python3 -m bubblemarking.gui
```

(equivalently `python3 bubblemarking/gui/main.py`.)

The GUI is the only entry point — there is no longer a CLI.

## Workflow

1. **Setup tab** — pick a scan PDF and (optionally) an answer-key file. Tick
   "Answer key is in the scan" if the key is bubbled in by a tutor on a sheet
   with matriculation `00000000`. Tick "Warn if more than one answer per
   question" for single-answer exams. Below the Scan button, configure the
   **Scoring** strategy that will apply to every student in this batch.
2. Click **Scan and review**. Pages are processed one at a time; progress is
   logged into the text area. After every page is scanned, the system
   calibrates against the cohort and re-classifies all bubbles using an
   absolute filled/blank threshold learned from this batch's pencil and
   scanner combo.
3. **Review tab** opens automatically when scanning finishes:
   - **Left:** list of pages. Use the dropdown to show all pages or only
     those needing review. Click any entry to jump to that page.
   - **Centre:** every page laid out vertically — scroll with the mouse
     wheel or trackpad to move continuously through the cohort. The page
     closest to the viewport centre becomes the "active" page, and the left
     list highlights it automatically. Pages outside a small window around
     the active page render as cheap placeholders to keep memory bounded.
   - **Right:** matric editor, friendly issue list ("Worth a glance:
     question 12, 33."), live score, view options, navigation.
4. **Editing.** Click a bubble (answer or matric digit) to toggle it. Edits
   preserve your zoom and pan — the click no longer snaps the view. Type
   directly into the matric field for fast keyboard entry.
5. **Zoom and pan.** `⌘ + scroll` (or pinch on a trackpad; `Ctrl + scroll`
   on Linux/Windows) zooms in around the cursor. `0` fits the current page
   to the window. Bare scroll/swipe moves vertically through the document.
6. **Keyboard shortcuts** (active anywhere on the Review tab except inside
   the matric edit field):
   - `J` / `K` — jump to next / previous page in the list.
   - `N` — jump to the next page that has issues to check (wraps).
   - `F` — toggle the page list between "All pages" and "Needs review only".
   - `⌘Z` / `⌘⇧Z` (Ctrl on Linux/Windows) — undo / redo any edit
     (bubble toggle, matric change, skip toggle). Edits made on a
     different page from the one currently visible scroll into view
     when you undo so you can see what changed.
7. **View toggles.**
   - **Show correct answers** — turns the red "correct answer" outlines on
     and off.
   - **Skip this page from export** — exclude the current page (e.g. a
     duplicate scan) from the CSV. The page stays visible in the review
     list with a "SKIPPED FROM EXPORT" watermark so you can change your
     mind.
   - **Flag sensitivity** slider — controls how aggressively pages are
     flagged for review. Drag left for a quieter queue, right for more
     coverage. Updates live across every loaded page.
   - **Hover** any answer bubble to see the calibrated confidence for that
     question (0 = on the boundary, 1 = clearly classified).
8. **Mid-review safety net.** Every edit is saved to a per-PDF session
   file under the platform-native data directory. If the app crashes or
   you quit without exporting, re-running on the same PDF offers to
   restore your edits on top of a fresh scan. A clean quit *or* a
   successful CSV export deletes the file.
9. **Export.** Click **Export results CSV…** in the bottom-left. The CSV
   contains one row per student with `QuestionN{NumCorrect, NumIncorrect,
   Answer, Weight}` columns and a `Total` column when a strategy is
   selected; row 0 is the answer key. Pages marked "Skip from export"
   are omitted.

The Setup tab's options (single-answer warning, key-in-scan checkbox,
scoring strategy and its values, flag sensitivity) and the Review tab's
"Show correct answers" toggle are persisted between runs via the
platform-native settings store, so a typical workflow is "click Scan,
verify the few flags, export" without re-configuring anything.

## Answer file format

CSV or XLSX, no header (a header row is tolerated and skipped). Columns:

1. Question number
2. Comma-separated correct letters
3. *(optional)* Per-question weight, default `1.0`

```
1, "A,B,E", 2
2, "A"
3, "C,D", 1.5
```

Letters not in `A`–`E` are ignored. Whitespace is tolerated. Non-numeric
weights are ignored (treated as default).

## Scoring

A scoring strategy turns a student's selections into a numeric score per
question. The **Setup tab** has a Scoring panel below the Scan button
where you pick a strategy and tweak its options. On the Review tab, the
right-hand side panel shows the live score for whichever page is
currently centred in the view — so you can sanity-check the rule on real
students as you scroll.

Three built-ins ship with the package:

| Name | Behaviour |
|------|-----------|
| **All or nothing** | Full weight if and only if the selection matches the key exactly. |
| **Partial credit** | Each correct option selected earns `weight / num_correct`; each wrong one subtracts a configurable fraction of the same. Optional floor at zero. |
| **All-or-nothing with negative marking** | Full weight on exact match; otherwise a flat penalty per wrong selection (configurable). Blank scores zero. Optionally exempt answers that overlap with the key. |

Click **Load custom…** in the scoring panel to load a strategy from any
`.py` file. The module must define:

```python
NAME = "My strategy"
DESCRIPTION = "What it does."
OPTIONS = {
    "my_option": {"type": float, "default": 0.5, "label": "My option"},
    # supported types: float, int, bool, str
}

def score(selected, correct, weight, num_options, **opts):
    """Return the score for a single question.
    selected, correct: sets of option indices (0=A, 1=B, ...)
    weight: per-question weight from the answer file (default 1.0)
    num_options: total options per question (5 for the standard sheet)
    **opts: values for each entry in OPTIONS, type-coerced.
    """
    return ...
```

The exported CSV gets a `Total` column when a strategy is selected; the
answer-key row holds the maximum achievable total (i.e. the score the key
itself would get under the chosen strategy).

## Architecture

```
scanning.py            — image processing + the PageScan dataclass
dataframes.py          — AnswerKey + build_output_df (results CSV builder)
scoring/               — pluggable scoring strategies
  __init__.py          — loader, built-in registry, option coercion
  all_or_nothing.py
  partial_credit.py
  negative_marking.py
gui/main.py            — main window, Setup tab, scan worker thread
gui/review.py          — Review tab, vertical multi-page view, ScoringPanel
```

### `scanning.PageScan`

The result of scanning one page. Attributes:

| Attribute | What it holds |
|-----------|---------------|
| `page_index` | 0-based page number in the PDF |
| `prepared_image` | straightened RGB image (dropped after scan to save memory; the GUI re-renders on demand) |
| `bars`, `right_bar_cache`, `matric_right_cache` | geometry of the black bars and bubble row anchors |
| `matric_brightness`, `matric_digits`, `matric_confidence` | matric block detection state |
| `question_brightness` | dict `q → np.ndarray(5)` per-bubble darkness samples |
| `answers` | dict `q → list[int]` of selected option indices (0=A … 4=E) |
| `confidence` | dict `q → float` (gap between sorted bubble brightnesses) |
| `flags` | list of strings: `unreadable`, `no_matric`, `multi_answer:12`, `low_confidence:33`, `duplicate_matric:51234567`, `no_answer:7`. The `no_answer` flag only appears when an answer key is loaded and that question is in-scope (the key has a correct answer for it) — out-of-scope questions on a 120-row form for a 30-question exam are *not* flagged. |
| `skip_from_export` | bool. When True, ``build_output_df`` omits this page. Set via the "Skip this page from export" checkbox; persisted in the mid-review session file. |
| `one_answer_only`, `num_questions` | the options the scan was run with |

Helpers: `bubble_rect(q, opt)` and `matric_bubble_rect(digit, pos)` return
`(x1, y1, x2, y2)` in image coordinates, used both for drawing overlays and
for click hit-testing. `toggle_answer(q, opt)` and `set_matric_digit(pos, v)`
mutate the scan; the GUI calls these from click handlers.

### Scan pipeline

Two passes:

**Pass 1 — per-page detection (`scanning.scan_page`).**

1. `prepare_image` — rotates the page to portrait, deskews using Hough lines,
   then locates the 44 black registration bars by binarising the image and
   scanning vertically. A short threshold sweep (127, 100, 150, 80, 170) and
   a 180-degree rotation fallback are tried before giving up. On failure the
   page comes back flagged `unreadable` rather than aborting.
2. `scan_matriculation` — samples the 8 × 10 matric grid.
3. For each question 1..N, `scan_question` samples the 5 option bubbles. The
   first pass labels filled vs blank using a per-row relative threshold
   (80 % of the row's max brightness). This produces a working answer set
   even before calibration; its job is to seed the calibration step.

**Pass 2 — cohort calibration (`scanning.calibrate_from_scans` /
`reclassify_with_calibration`).**

After every page has been scanned, the worker pools every bubble's
brightness across the whole batch, partitioned by the first-pass label.
The medians of the two populations bound a single absolute decision
boundary at their midpoint.

The final answer detection is the **union** of:

- the cohort-absolute test (bubbles darker than the boundary), and
- the first-pass per-row test (already on the `PageScan`).

The union preserves the original pipeline's recall on faint marks (which
sit above the absolute threshold but stand out in their row) while
benefiting from absolute detection on rows the relative threshold would
miss (uniform-fill or all-blank rows). On the 20-page sample, the new
pipeline matches the old code's detections exactly except for one
additional (genuine) faint-mark catch.

The matric block is intentionally **not** reclassified with the cohort
threshold. The matric uses different sampling geometry, and the old
per-column relative test (the darkest digit per column wins, but only if
it's at least 10 % darker than the brightest) handles uniformly-dark
matric blocks correctly — every column fails the ratio test and the
matric reads as unset, which is what we want for pages where the student
didn't fill in their ID.

Per-question confidence becomes the **margin from the boundary** (in units
of half the filled/blank spread), taken as the minimum across the row's
bubbles. With this metric:

- A clearly blank row scores ~1 (every bubble far above the threshold).
- A clearly answered row scores ~0.5–1 (the filled bubble is far below,
  the blanks far above; the closest-to-boundary bubble drives the score).
- Only genuinely ambiguous rows — bubbles near the threshold — score low.

The "needs review" filter keys off `confidence < 0.3`. On the 20-page
sample bundled in `examples/`, calibration cuts the flag count from
~3700 (mostly false alarms on legitimately-blank rows) to ~80, while
matching the old pipeline on every matric read and every answer
detection.

If the cohort is too small or too uniform for calibration to fit
(`Calibration.valid == False`), the GUI falls back to first-pass labels
and logs a warning.

### Results pipeline (`dataframes`)

- `read_answer_key_from_file(path) -> AnswerKey` — reads CSV/XLSX, with an
  optional third column for per-question weight.
- `extract_answer_key_from_scans(scans) -> AnswerKey | None` — pulls the key
  off the first page whose matric is `00000000`. Weights default to 1.0.
- `score_scan(scan, key, strategy, options) -> float` — applies a strategy
  to every question; same function the live total uses in the Review tab.
- `max_total(key, strategy, options) -> float` — score that the key itself
  would get; the upper bound shown alongside the live total.
- `build_output_df(scans, key, strategy=None, options=None) -> pd.DataFrame`
  — row 0 is the key, subsequent rows one per student. Duplicate or unread
  matric numbers are renumbered to a falling sequence beginning at
  `99999999`; the GUI surfaces the same condition as a flag so the user
  can fix it before export. Adds a `Total` column when ``strategy`` is set.

### Review GUI (`gui/review.py`)

- `PageImageView` is a `QGraphicsView` showing every page in the cohort
  laid out vertically in one scene. Each page is a `PageBlock` slot whose
  pixmap is either a cheap "Page N" placeholder or a fully-rendered page
  with overlays. The active page (whichever one is closest to the viewport
  centre) plus ±`LOAD_WINDOW` neighbours are kept rendered; pages outside
  that window revert to placeholders so memory stays bounded regardless
  of cohort size. Scrolling the view updates the active page (debounced,
  drives the left-list selection); editing a page (bubble click or matric
  type) re-renders only that page's overlays so the user's zoom and pan
  are preserved.
- Left-click finds the smallest enclosing bubble rect and emits
  `bubble_clicked(scan_index, kind, i1, i2)`; the parent updates the
  `PageScan` and asks the view to redraw that page only.
- ⌘ + scroll, pinch gestures, and the `0` key all work as expected; bare
  scroll moves through the document; clicks never reset zoom.
- `set_show_correct_answers(bool)` and `set_low_conf_threshold(float)`
  are how the right-pane "Show correct answers" checkbox and the
  "Flag sensitivity" slider drive overlay re-renders across every loaded
  page.
- `PageImageCache` is a 4-page LRU. It re-renders + re-prepares pages on
  demand from the original `PdfDocument`. After scanning, each `PageScan`
  has its `prepared_image` cleared so the cohort fits comfortably in memory.
- `friendly_issue_summary(scan)` translates raw `flag` strings into the
  plain-English bullets shown in the side panel's "Issues to check" box.
- `recompute_flags(scan)` and `recompute_duplicate_flags(scans)` rebuild
  the flag set after each edit (or every slider movement) so resolved
  problems disappear from the queue.

### Scoring strategies (`scoring/`)

Each strategy is a Python module in `scoring/` (or any standalone `.py`
loaded via the GUI). The loader in `scoring/__init__.py` exposes:

- `list_builtins()` — the three built-in strategies as imported modules.
- `load_strategy_from_file(path)` — loads, validates that `score` is
  callable, and supplies sensible defaults for missing `NAME` /
  `DESCRIPTION` / `OPTIONS` attributes.
- `default_options(strategy)` — `{name: default}` for the strategy.
- `coerce_options(strategy, raw)` — converts a raw `{name: value}` dict
  (e.g. from form widgets) to the declared types, falling back to
  defaults on bad input.

The `score(selected, correct, weight, num_options, **opts)` signature is
intentionally pure: no I/O, no global state, easy to unit-test.

### Editing the Setup form

The Setup form is built programmatically in `gui/main.py` (the legacy
`gui.ui` / `gui.py` pair has been retired) — change widget layout, labels,
and tooltips directly in code there.

## Tests

The package ships with a pytest suite covering the answer-key and results
layer, scoring strategies, calibration math, and the friendly-summary
helper. Run them from the repo root with:

```
pip install pytest
PYTHONPATH=.. pytest tests/
```

A separate `tests/test_e2e.py` exercises the full scan → calibrate →
export pipeline against the bundled `examples/scan_*.pdf` if it's
present (and skips automatically when it isn't, since the bundled PDFs
contain real student data and aren't committed).

