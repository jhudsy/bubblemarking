"""Image processing pipeline for the MCQ marksheet.

Top-level entry point is :func:`scan_page`: feed it a rendered page image
and it returns a :class:`PageScan` containing the detected matriculation
number, per-question answers, brightness samples, geometry caches, and
flags describing anything the scanner is unsure about.

The module is intentionally side-effect free aside from logging. Failure
modes (unreadable page, missing bars, ambiguous bubbles) surface as fields
on the returned :class:`PageScan` rather than exceptions, so the GUI can
queue them for human review."""
import cv2
import pypdfium2 as pdfium
import numpy as np
import logging
from dataclasses import dataclass, field
from typing import Optional


# Bubble grid geometry. The MCQ sheet has 4 columns of 30 questions, 5 options each.
# Offsets are pixel deltas from the right edge of each black bar at SCALE=5.0.
QUESTION_OFFSETS = [
    [-2397, -2325, -2252, -2180, -2108],  # questions 1..30
    [-1819, -1747, -1674, -1602, -1530],  # questions 31..60
    [-1241, -1169, -1096, -1024, -951],   # questions 61..90
    [-663, -590, -519, -446, -374],       # questions 91..120
]
MATRIC_OFFSETS = [-594, -522, -449, -377, -305, -233, -161, -89]
ANSWER_BAR_START = 12  # bars[12..41] are the 30 answer rows
MATRIC_BAR_START = 2   # bars[2..11] are the 10 matric digit rows
NUM_OPTIONS = 5
MATRIC_LENGTH = 8
DEFAULT_NUM_QUESTIONS = 120
UNREAD_MATRIC = "99999999"
ANSWER_KEY_MATRIC = "00000000"


@dataclass
class Calibration:
    """Cohort-wide bubble-brightness calibration.

    Built from first-pass scan results across every page in the batch. ``filled``
    bubbles (per the relative threshold) form one population, ``unfilled`` ones
    the other; the decision boundary sits at the midpoint of the medians and
    confidence is reported as the bubble's distance from that boundary,
    normalised by half the spread (so confidence ≥ 1 means the bubble lands
    on or past one of the medians)."""
    filled_median: float = 0.0
    unfilled_median: float = 0.0
    threshold: float = 0.0
    spread: float = 0.0
    n_filled: int = 0
    n_unfilled: int = 0
    valid: bool = False

    def is_filled(self, brightness: float) -> bool:
        return brightness < self.threshold

    def margin(self, brightness: float) -> float:
        if self.spread <= 0:
            return 0.0
        return float(abs(brightness - self.threshold) / (self.spread / 2.0))


@dataclass
class PageScan:
    """All data extracted from a single scanned page, plus geometry for editing."""
    page_index: int
    prepared_image: Optional[np.ndarray] = None
    bars: Optional[list] = None
    right_bar_cache: dict = field(default_factory=dict)
    matric_right_cache: dict = field(default_factory=dict)
    matric_brightness: Optional[np.ndarray] = None
    matric_digits: list = field(default_factory=lambda: [None] * MATRIC_LENGTH)
    question_brightness: dict = field(default_factory=dict)
    answers: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    matric_confidence: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    num_questions: int = DEFAULT_NUM_QUESTIONS
    one_answer_only: bool = False

    @property
    def unreadable(self) -> bool:
        # ``prepared_image`` is intentionally dropped after scanning to bound
        # memory; the GUI re-renders on demand. ``bars`` is the durable
        # signal that geometry was successfully extracted.
        return self.bars is None

    def matric_string(self) -> str:
        if any(d is None for d in self.matric_digits):
            return UNREAD_MATRIC
        return "".join(str(d) for d in self.matric_digits)

    def set_matric_digit(self, position: int, value):
        if not (0 <= position < MATRIC_LENGTH):
            raise ValueError(f"matric position {position} out of range")
        if value is not None and not (0 <= value <= 9):
            raise ValueError(f"matric digit {value} out of range")
        self.matric_digits[position] = value

    def toggle_answer(self, question: int, option: int):
        ans = self.answers.setdefault(question, [])
        if option in ans:
            ans.remove(option)
        else:
            if self.one_answer_only:
                ans.clear()
            ans.append(option)
            ans.sort()

    def bubble_rect(self, question: int, option: int, **kwargs):
        if self.bars is None:
            return None
        return question_bubble_rect(self.bars, self.right_bar_cache, question, option, **kwargs)

    def matric_bubble_rect(self, digit_value: int, position: int, **kwargs):
        if self.bars is None:
            return None
        return matric_bubble_rect(self.bars, self.matric_right_cache, digit_value, position, **kwargs)


###############################################################################
# PDF / image plumbing
###############################################################################
def get_file(file_name):
    return pdfium.PdfDocument(file_name)


def get_number_of_pages(doc):
    return len(doc)


def get_image_from_file(doc, page_number, **kwargs):
    SCALE = kwargs.get("SCALE", 5.0)
    page = doc[page_number]
    image = page.render(scale=SCALE, no_smoothimage=True, optimize_mode="print").to_numpy()
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return image


###############################################################################
# Image preparation
###############################################################################
def straighten_image(original_image, **kwargs):
    threshold = kwargs.get("threshold", 40)
    image_percent = kwargs.get("image_percent", 0.05)
    image = cv2.cvtColor(original_image, cv2.COLOR_BGR2GRAY)
    height = image.shape[0]
    _, thresh = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)
    thresh = cv2.bitwise_not(thresh)
    linesTop = cv2.HoughLinesP(
        thresh[0:int(height * image_percent)], 1, np.pi / 180, 100,
        minLineLength=5, maxLineGap=100,
    )
    linesBottom = cv2.HoughLinesP(
        thresh[int(height - height * image_percent):], 1, np.pi / 180, 100,
        minLineLength=20, maxLineGap=100,
    )
    if linesTop is None or linesBottom is None:
        return original_image
    lines = np.concatenate((linesTop, linesBottom))
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi
        angles.append(angle)
    angle = float(np.mean(angles))
    M = cv2.getRotationMatrix2D((image.shape[1] // 2, image.shape[0] // 2), angle, 1)
    return cv2.warpAffine(original_image, M, (image.shape[1], image.shape[0]))


def find_black_bars(orig_image, **kwargs):
    threshold = kwargs.get("threshold", 127)
    right_scan_percent = kwargs.get("right_scan_percent", 0.005)
    num_black_bars = kwargs.get("num_black_bars", kwargs.get("num_black_Bars", 44))
    min_bar_height = kwargs.get("min_bar_height", 20)
    width = orig_image.shape[1]

    image = cv2.cvtColor(orig_image, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)

    # Scan from right edge inward looking for the column of black bars.
    black_bars_found = False
    x = int(width - width * right_scan_percent)
    while not black_bars_found and x > 0:
        if np.sum(thresh[:, x]) < orig_image.shape[0] * 255 - num_black_bars * min_bar_height * 255:
            black_bars_found = True
        x -= 1

    if not black_bars_found:
        return None

    start = x
    while x > 0 and np.sum(thresh[:, x]) < orig_image.shape[0] * 255 - num_black_bars * min_bar_height * 255:
        x -= 1
    end = x
    mid = (start + end) // 2

    blackBars = []
    foundTop = False
    cur_height = 0
    top = 0
    for i in range(0, thresh.shape[0]):
        if thresh[i, mid] == 0 and not foundTop:
            foundTop = True
            top = i
            cur_height = 0
        elif thresh[i, mid] == 0 and foundTop:
            cur_height += 1
        if thresh[i, mid] == 255 and foundTop:
            foundTop = False
            if cur_height > min_bar_height:
                blackBars.append((top - 1, i + 7))

    if len(blackBars) == num_black_bars:
        return blackBars
    # Soft fallback: if a stray dot, fold, or smudge created one or two extra
    # dark runs, drop the shortest until we hit the expected count. Limit how
    # far we'll stretch (don't accept wildly wrong page geometry).
    if num_black_bars < len(blackBars) <= num_black_bars + 4:
        # Sort by run height, longest first; keep the top ``num_black_bars``,
        # then re-sort by y-position to preserve top-to-bottom order.
        ranked = sorted(blackBars, key=lambda b: b[1] - b[0], reverse=True)
        kept = sorted(ranked[:num_black_bars], key=lambda b: b[0])
        logging.info(
            f"find_black_bars: dropped {len(blackBars) - num_black_bars} short "
            f"extras to recover the expected {num_black_bars}-bar grid."
        )
        return kept
    return None


def prepare_image(image, **kwargs):
    """Straighten and locate the black bars. Tries multiple binarisation thresholds
    and a 180-degree rotation before giving up."""
    if image.shape[0] < image.shape[1]:
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    new_image = straighten_image(image, **kwargs)

    # Threshold sweep — scans with mild over/underexposure can confuse a single
    # threshold. Try a small range.
    threshold_candidates = kwargs.pop("threshold_candidates", [127, 100, 150, 80, 170])
    for orientation in (new_image, cv2.rotate(new_image, cv2.ROTATE_180)):
        for thr in threshold_candidates:
            bars = find_black_bars(orientation, threshold=thr, **kwargs)
            if bars is not None:
                return orientation, bars
    return None, None


###############################################################################
# Bubble geometry
###############################################################################
def find_right(line):
    """Locate the right edge of a black bar within a horizontal slice."""
    line = cv2.cvtColor(line, cv2.COLOR_BGR2GRAY)
    line = cv2.threshold(line, 200, 255, cv2.THRESH_BINARY)[1]
    line = cv2.erode(line, np.ones([3, 3]), iterations=2)
    line = cv2.bitwise_not(line)
    count = 0
    for i in range(line.shape[1] - 1, 0, -1):
        if np.sum(line[5:25, i]) > 4000:
            count += 1
        elif count < 60:
            count = 0
        else:
            return i
    logging.warning("Could not find right edge of black bar")
    return None


def question_bubble_rect(bars, right_bar_cache, question, option, **kwargs):
    """Return (x1, y1, x2, y2) for a bubble. `question` is 1-indexed."""
    window_size = kwargs.get("window_size", 58)
    window_height = kwargs.get("window_height", 1)
    q0 = question - 1
    bar_idx = q0 % 30 + ANSWER_BAR_START
    column = q0 // 30
    bar = bars[bar_idx]
    right = right_bar_cache.get(bar_idx)
    if right is None:
        return None
    offset = QUESTION_OFFSETS[column][option]
    line_height = bar[1] - bar[0]
    x1 = right + offset - window_size // 2
    x2 = right + offset + window_size // 2
    y1 = bar[0] + int((1 - window_height) * line_height)
    y2 = bar[0] + int(window_height * line_height)
    return (x1, y1, x2, y2)


def matric_bubble_rect(bars, matric_right_cache, digit_value, position, **kwargs):
    window_size = kwargs.get("window_size", 60)
    window_height = kwargs.get("window_height", 0.8)
    bar_idx = digit_value + MATRIC_BAR_START
    bar = bars[bar_idx]
    right = matric_right_cache.get(bar_idx)
    if right is None:
        return None
    offset = MATRIC_OFFSETS[position]
    line_height = bar[1] - bar[0]
    x1 = right + offset - window_size // 2
    x2 = right + offset + window_size // 2
    y1 = bar[0] + int((1 - window_height) * line_height)
    y2 = bar[0] + int(window_height * line_height)
    return (x1, y1, x2, y2)


###############################################################################
# Bubble brightness sampling
###############################################################################
def _sample_window(image, x1, y1, x2, y2, red_threshold, erode=True):
    window = image[y1:y2, x1:x2].copy()
    if window.size == 0:
        return 0
    window = window[:, :, 0]
    window = cv2.threshold(window, red_threshold, 255, cv2.THRESH_BINARY)[1]
    if erode:
        window = cv2.erode(window, np.ones([3, 3]), iterations=2)
    return int(np.sum(window))


def _sample_darkest_in_roi(image, cx_low, cx_high, cy_low, cy_high,
                            red_threshold, erode=True,
                            search_radius=8, step=4):
    """Sample the canonical bubble window AND a few nearby offsets, returning
    the darkest (smallest) brightness found. Robust to small registration
    drift between cohort and the hardcoded bubble offsets without losing
    the per-window erosion behaviour.

    The search box is intentionally tight (radius < half the inter-bubble
    spacing) to avoid a neighbour's dark pixels leaking into the score."""
    best = _sample_window(image, cx_low, cy_low, cx_high, cy_high,
                          red_threshold, erode=erode)
    for dx in range(-search_radius, search_radius + 1, step):
        for dy in range(-search_radius, search_radius + 1, step):
            if dx == 0 and dy == 0:
                continue
            v = _sample_window(image, cx_low + dx, cy_low + dy,
                               cx_high + dx, cy_high + dy,
                               red_threshold, erode=erode)
            if v < best:
                best = v
    return best


def question_confidence(brightness):
    """Return 0..1 — high when the largest gap between sorted bubble brightnesses
    is wide compared to the maximum brightness. Ambiguous pages (all bubbles
    similar) score near zero."""
    if brightness is None or len(brightness) == 0:
        return 0.0
    m = float(np.max(brightness))
    if m <= 0:
        return 0.0
    sorted_b = np.sort(brightness)
    gaps = np.diff(sorted_b)
    if len(gaps) == 0:
        return 0.0
    return float(np.max(gaps) / m)


def _detect_question_answers(brightness, threshold, one_answer_only):
    """Decide which options are filled given a 5-element brightness array.
    Lower brightness = darker = filled."""
    m = np.max(brightness)
    if m <= 0:
        return []
    answers = [i for i, b in enumerate(brightness) if b < threshold * m]
    if one_answer_only:
        ans = int(np.argmin(brightness))
        if brightness[ans] > 0.9 * m:
            return []
        return [ans]
    return answers


def scan_question(image, bars, right_bar_cache, question, **kwargs):
    """Sample the 5 bubbles for one question. Returns (answers, brightness, updated cache)."""
    red_threshold = kwargs.get("red_threshold", 170)
    one_answer_only = kwargs.get("one_answer_only", False)
    threshold = kwargs.get("threshold", 0.8)

    q0 = question - 1
    bar_idx = q0 % 30 + ANSWER_BAR_START
    if bar_idx not in right_bar_cache:
        line = image[bars[bar_idx][0]:bars[bar_idx][1], :]
        right_bar_cache[bar_idx] = find_right(line)

    brightness = np.zeros(NUM_OPTIONS)
    for opt in range(NUM_OPTIONS):
        rect = question_bubble_rect(bars, right_bar_cache, question, opt, **kwargs)
        if rect is None:
            continue
        x1, y1, x2, y2 = rect
        brightness[opt] = _sample_darkest_in_roi(
            image, x1, x2, y1, y2, red_threshold,
        )

    answers = _detect_question_answers(brightness, threshold, one_answer_only)
    return answers, brightness, right_bar_cache


def scan_matriculation(image, bars, matric_right_cache, **kwargs):
    """Sample the 10-digit-by-8-position matriculation block.

    For each position (column) the darkest bubble across the 10 digit rows
    wins, provided it is at least 10% darker than the brightest. Confidence
    is the normalised gap between the darkest and second-darkest digit.
    The relative-per-column test gracefully rejects pages where the whole
    matric block is uniformly dark (e.g. unfilled but with paper noise) —
    every column fails the ratio test and the matric reads as unset.

    Erosion is intentionally skipped here: the matric uses different sampling
    geometry than the answer block, and erosion makes uniform-dark blocks
    look like clean detections. Without it the brightness matrix reflects
    the raw thresholded mark area, which is what the per-column ratio test
    was tuned for in the original pipeline.

    Returns ``(digits, brightness_matrix, confidence_per_position, cache)``,
    where ``digits`` is a list of 8 ints or ``None`` when no digit was
    confidently detected at that position."""
    red_threshold = kwargs.get("red_threshold", 200)

    brightness = np.zeros((10, MATRIC_LENGTH))
    for digit in range(10):
        bar_idx = digit + MATRIC_BAR_START
        if bar_idx not in matric_right_cache:
            line = image[bars[bar_idx][0]:bars[bar_idx][1], :]
            matric_right_cache[bar_idx] = find_right(line)
        for pos in range(MATRIC_LENGTH):
            rect = matric_bubble_rect(bars, matric_right_cache, digit, pos, **kwargs)
            if rect is None:
                continue
            x1, y1, x2, y2 = rect
            brightness[digit, pos] = _sample_window(image, x1, y1, x2, y2, red_threshold, erode=False)

    digits = []
    confidence = []
    for pos in range(MATRIC_LENGTH):
        col = brightness[:, pos]
        m = np.max(col)
        if m <= 0:
            digits.append(None)
            confidence.append(0.0)
            continue
        idx = int(np.argmin(col))
        if col[idx] > 0.9 * m:
            digits.append(None)
            confidence.append(0.0)
        else:
            digits.append(idx)
            sorted_col = np.sort(col)
            confidence.append(float((sorted_col[1] - sorted_col[0]) / m))
    return digits, brightness, confidence, matric_right_cache


###############################################################################
# Page-level scan entry point
###############################################################################
def scan_page(image, page_index=0, **kwargs):
    """Top-level: take a raw rendered PDF page image, return a populated PageScan."""
    num_questions = kwargs.get("num_questions") or DEFAULT_NUM_QUESTIONS
    num_questions = min(max(num_questions, 1), DEFAULT_NUM_QUESTIONS)
    one_answer_only = kwargs.get("one_answer_only", False)
    low_conf_threshold = kwargs.get("low_conf_threshold", 0.15)

    scan = PageScan(page_index=page_index, num_questions=num_questions, one_answer_only=one_answer_only)

    prepared, bars = prepare_image(image, **kwargs)
    if prepared is None or bars is None:
        scan.flags.append("unreadable")
        return scan

    scan.prepared_image = prepared
    scan.bars = bars

    digits, matric_b, matric_conf, scan.matric_right_cache = scan_matriculation(
        prepared, bars, scan.matric_right_cache, **kwargs
    )
    scan.matric_digits = digits
    scan.matric_brightness = matric_b
    scan.matric_confidence = matric_conf
    if any(d is None for d in digits):
        scan.flags.append("no_matric")

    for q in range(1, num_questions + 1):
        answers, brightness, scan.right_bar_cache = scan_question(
            prepared, bars, scan.right_bar_cache, q, **kwargs
        )
        scan.answers[q] = answers
        scan.question_brightness[q] = brightness
        scan.confidence[q] = question_confidence(brightness)
        if scan.confidence[q] < low_conf_threshold:
            scan.flags.append(f"low_confidence:{q}")
        if one_answer_only and len(answers) > 1:
            scan.flags.append(f"multi_answer:{q}")
        if not answers:
            scan.flags.append(f"no_answer:{q}")

    return scan


def answers_to_string(answers):
    """Convert [0,2,4] -> 'A,C,E'."""
    return ",".join(chr(65 + a) for a in sorted(answers))


###############################################################################
# Cohort calibration
###############################################################################
def calibrate_from_scans(scans, min_filled: int = 5, min_unfilled: int = 50) -> Calibration:
    """Pool first-pass bubble brightnesses across the batch and learn where
    "filled" and "blank" actually sit for this cohort's pencil + scanner combo.

    The decision boundary in :class:`Calibration` is the midpoint between the
    two medians; bubbles darker than that threshold are filled. Returns a
    :class:`Calibration` with ``valid=False`` if either population is too
    small or the medians have not separated."""
    filled = []
    unfilled = []
    for s in scans:
        if s.unreadable:
            continue
        for q, br in s.question_brightness.items():
            if br is None:
                continue
            ans = set(s.answers.get(q, []))
            for opt, b in enumerate(br):
                (filled if opt in ans else unfilled).append(float(b))

    if len(filled) < min_filled or len(unfilled) < min_unfilled:
        return Calibration(n_filled=len(filled), n_unfilled=len(unfilled), valid=False)

    fmed = float(np.median(filled))
    umed = float(np.median(unfilled))
    if umed - fmed < 1.0:
        return Calibration(filled_median=fmed, unfilled_median=umed,
                           n_filled=len(filled), n_unfilled=len(unfilled), valid=False)

    return Calibration(
        filled_median=fmed,
        unfilled_median=umed,
        threshold=(fmed + umed) / 2.0,
        spread=umed - fmed,
        n_filled=len(filled),
        n_unfilled=len(unfilled),
        valid=True,
    )


def reclassify_with_calibration(scan: PageScan, calibration: Calibration):
    """Refine answer detection using the cohort-wide threshold and rewrite
    confidence as margin-from-boundary.

    The detection rule is the **union** of two signals:

    * Cohort calibration: bubbles darker than the cohort threshold are filled.
    * First-pass relative: bubbles already labelled filled by the per-row
      0.8-of-max heuristic that ran during the page scan.

    The union preserves the old pipeline's recall on faint marks (which sit
    above the absolute threshold but stand out within their row) while
    benefiting from absolute detection on rows the relative threshold would
    miss (uniform-fill or all-blank rows).

    Confidence is the minimum bubble margin from the cohort boundary,
    normalised by half the filled/blank spread. Bubbles near the boundary
    drag confidence down even when the union still picks them — the GUI's
    review filter catches them as ``low_confidence``.

    The matric block is intentionally **not** reclassified here. Its
    per-column relative test already handles uniform-dark blocks correctly
    (every column fails the ratio test, matric reads as unset), and the
    cohort threshold from answer bubbles doesn't generalise to the matric
    sampling geometry."""
    if not calibration.valid or scan.unreadable:
        return
    for q, br in scan.question_brightness.items():
        if br is None:
            continue
        cal_ans = {i for i, b in enumerate(br) if calibration.is_filled(float(b))}
        first_pass = set(scan.answers.get(q, []))
        ans = sorted(cal_ans | first_pass)
        if scan.one_answer_only and len(ans) > 1:
            ans = [int(np.argmin(br))]
        scan.answers[q] = ans
        margins = [calibration.margin(float(b)) for b in br]
        scan.confidence[q] = float(min(margins)) if margins else 0.0
