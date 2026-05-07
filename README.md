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
   "Answers in scan file" if the key is bubbled in by a tutor on a sheet with
   matriculation `00000000`. Tick "Warn if there is more than one answer per
   question" for single-answer exams.
2. Click **Scan and review**. Pages are processed one at a time; progress is
   logged into the text area. Pages that the scanner is unsure about get
   flagged for human review.
3. **Review tab** opens automatically when scanning finishes:
   - Left: list of pages. Use the dropdown to show only pages that need review
     (unreadable page, missing matric, duplicate matric, low confidence,
     missing/multi answer).
   - Centre: the page image with overlay rectangles. Click a bubble to toggle
     selection; click a matric digit to set or clear it. Green = selected;
     red outline = correct answer (when the key is loaded); amber row =
     low-confidence detection.
     - `Ctrl + scroll` zoom, `0` fit to window.
   - Right: matric editor, flag list, navigation, "Next page needing review".
4. When the list is clean, click **Export results CSV…**. The CSV contains
   one row per student with `QuestionN{NumCorrect,NumIncorrect,Answer}`
   columns; row 0 is the answer key.

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
question. The Review tab has a **Scoring** panel above the export button
where you pick a strategy and tweak its options; the score for the
currently visible page updates live, so you can sanity-check the rule on a
real student before exporting.

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
gui/main.py            — main window, scan worker thread
gui/review.py          — Review tab + ScoringPanel
gui/gui.py             — generated from gui.ui (do not hand-edit)
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
| `flags` | list of strings such as `unreadable`, `no_matric`, `multi_answer:12`, `low_confidence:33`, `duplicate_matric:51234567`. Genuinely-blank rows are *not* flagged — the calibration step lets them score high-confidence-blank. |
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

- `PageImageView` is a `QGraphicsView` that paints the prepared page plus
  bubble overlays into a single pixmap and keeps a list of bubble rectangles
  for hit-testing. Left-click finds the smallest enclosing bubble rect and
  emits `bubble_clicked(kind, i1, i2)`; the parent updates the `PageScan`
  and asks the view to redraw.
- `PageImageCache` is a 4-page LRU. It re-renders + re-prepares pages on
  demand from the original `PdfDocument`. After scanning, each `PageScan`
  has its `prepared_image` cleared so the cohort fits comfortably in memory.
- `recompute_flags(scan)` and `recompute_duplicate_flags(scans)` rebuild the
  flag set after each edit so resolved problems disappear from the queue.

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

The Setup form lives in `gui.ui`. Regenerate `gui.py` after changes:

```
pyside6-uic -o gui.py gui.ui
```

`gui/main.py` reparents the generated form into a `QTabWidget` at runtime
and hides obsolete widgets, so changes to `gui.ui` don't have to track the
runtime restructure.

