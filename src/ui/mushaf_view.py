"""Mushaf page viewer — QCF4 glyph rendering with word-level hitboxes.

Renders Mushaf pages using Quran Complex Fonts (QCF4) where each word
is a single pre-drawn glyph.  Transparent QGraphicsRectItem hitboxes
overlay each word for recitation feedback: a word recited correctly is
uncovered and left alone, and only a mistake is given a colour.

Words are masked in the page colour initially and revealed as recitation
progresses. Bounding boxes are computed dynamically via Qt's
QGraphicsTextItem boundingRect() — no external coordinate data required.

Three things on a page are *not* masked, because hiding them tells the
reciter nothing and costs them their bearings:

  - the ayah markers, so the shape of the page — how many ayahs, how long
    each one runs — is there from the first glance rather than assembling
    itself as you recite;
  - the surah title, drawn as the banner a printed Mushaf gives it;
  - the bismillah line, which is not a numbered ayah anywhere but 1:1 and
    so has no word to score. There is nothing to uncover and nothing to
    mark: reciting it correctly leaves the line exactly as it was printed.

Measuring is the theme of the rest of the module. A QCF glyph's box says
almost nothing about where the glyph puts ink — it is twice the height of
the letters in it, and a title glyph from the QBSML font overshoots it
entirely — so anything drawn *around* a glyph rather than *as* one asks
_ink() where the letters really are. See _page_band().
"""

from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsSimpleTextItem,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QFontMetricsF, QPainter, QPainterPath
from PyQt6.QtCore import Qt, QRectF

from src.core.debug import log
from src.core.qcf_data import QCFDataLoader
from src.config import QCF_FONT_SIZE, QCF_WORD_SPACING, QCF_LINE_SPACING
from src.ui.style import (
    INCORRECT_COLOR, MISSED_COLOR, TEXT_PRIMARY,
    PAGE_BG, PAGE_BACKDROP, PAGE_BORDER, AYAH_MARK_COLOR, BANNER_BG,
    TINT_ALPHA,
)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


# Fully opaque: the word is hidden. Anything else means it is showing.
HIDDEN = 255
CLEAR = QColor(0, 0, 0, 0)

# A mask sized to the glyph's bounding rect leaves the overhanging parts of
# some QCF glyphs — a tail, a hamza carried above the line — outside it and
# showing, which reads as specks of ink scattered over an empty page. The
# gap between words is QCF_WORD_SPACING, so a mask may grow by half of it
# each side without reaching its neighbour's ink.
MASK_BLEED = 1.0

# The tint is a highlighter, not a fill: it is drawn around the line's ink
# rather than around the glyph's box, because the box is twice the height of
# the letters in it. See _ink() and _page_band().
TINT_PADDING = 3.0       # scene units of air above and below the ink
TINT_RADIUS = 2.5

# Stacking on the page: glyphs at 0, then the tint, then the mask over both.
# The surah banner goes above the mask — see _draw_surah_banner().
MASK_Z = 10
BANNER_Z = 11


def _verdict_tint(status: bool | None) -> QColor:
    """The colour a verdict paints over a word — and nothing for a correct one.

    **Correct recitation is not marked.** A word recited correctly is simply
    uncovered and left in the Mushaf's own ink, because that is what a
    correctly recited page looks like: a page. Tinting it said nothing the
    reciter did not already know, and a page of green is a page you have to
    read *through* a colour — on a long surah almost every word carried one,
    so the two or three that mattered had to be found inside a wash of them.

    Only the exceptions are coloured now, which is the whole point of a
    highlighter. Returning CLEAR rather than skipping the paint matters: a
    word revised from wrong to correct by a later, better window must have
    its red *removed*, not merely be left showing.
    """
    if status is True:
        return CLEAR
    color = QColor(INCORRECT_COLOR if status is False else MISSED_COLOR)
    # Semi-transparent tint so the glyph underneath stays readable
    color.setAlpha(TINT_ALPHA)
    return color


class _WordOverlay:
    """The two rectangles sitting on top of one word's glyph.

    They cannot be one item, which is what they used to be. The mask must
    cover the glyph square and exactly or the letters show around it; the
    tint wants rounded corners and room to breathe or the marked-up page
    turns into a column of blocks. One item can only be one shape, and the
    shape the mask needs is the one the tint must not have.
    """

    __slots__ = ("mask", "tint")

    def __init__(self, mask, tint):
        self.mask = mask
        self.tint = tint

    @property
    def hidden(self) -> bool:
        return self.mask.brush().color().alpha() == HIDDEN

    def show(self):
        self.mask.setBrush(CLEAR)

    def paint(self, color: QColor):
        self.show()
        self.tint.setBrush(color)


class MushafView(QGraphicsView):
    """Qt widget that displays a QCF-rendered Mushaf page with recitation feedback."""

    # Shared QCF data loader (fonts registered once, shared across instances)
    _qcf_loader = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QColor(PAGE_BACKDROP))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Lazy-init shared loader
        if MushafView._qcf_loader is None:
            MushafView._qcf_loader = QCFDataLoader()

        # Hitbox map: (surah_id, ayah_id, word_position) → QGraphicsRectItem
        self._word_hitboxes: dict[tuple[int, int, int], list] = {}
        # surah_id → the glyphs of that surah's bismillah line
        self._basmala_hitboxes: dict[int, list] = {}

        # Track current page
        self.current_page_num: int | None = None
        # Bumped on every load. load_page() rebuilds the scene from scratch,
        # so every hitbox a caller is holding — and every colour it believes
        # it has painted — belongs to the page before this one. Anything
        # caching paint state watches this and starts again when it moves.
        self.page_serial: int = 0

        # Layout constants (Classic Mushaf Ratio ~1:1.45)
        self._page_width = 800.0   # base scene units
        self._page_height = 1160.0 # 800 * 1.45
        self._margin_x = 60.0      # generous side margins
        self._margin_top = 80.0    # top margin
        self._margin_bottom = 80.0 # bottom margin
        # Room around the sheet for its shadow. The scene rect has to include
        # it or fitInView crops the blur off at the edges.
        self._bleed = 26.0
        self._shadow = None        # kept alive for the lifetime of the page
        self._clip = None          # everything printed on the page hangs here
        # (font family, glyph code) → where that glyph puts ink. See _ink().
        self._ink_cache: dict[tuple[str, int], QRectF] = {}

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene.sceneRect().isValid():
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # ── Page rendering ─────────────────────────────────────────────────

    def load_page(self, page_num: int):
        """Load and render a Mushaf page using QCF4 glyphs."""
        self.scene.clear()
        self._word_hitboxes = {}
        self._basmala_hitboxes = {}
        self._clip = None
        self.current_page_num = page_num
        self.page_serial += 1

        page_data = self._qcf_loader.load_page(page_num)
        if page_data is None:
            print(f"Error: Could not load QCF page {page_num}")
            return

        self._draw_sheet()

        # Usable space
        content_width = self._page_width - 2 * self._margin_x
        grid_height = self._page_height - self._margin_top - self._margin_bottom
        line_step = grid_height / 14.0 # 15 lines = 14 gaps
        # The page font's box height. Every body glyph measures exactly this,
        # so it is the line's own height, and a glyph from another font — the
        # surah title, set in QBSML at less than half the size — is centred
        # against it instead of hanging from the top of the line.
        body_height = self._measure_line_height(
            self._qcf_loader.get_font(page_data.font_name))
        band = self._page_band(page_data, body_height, line_step)

        # Render each line
        for line_words in page_data.lines:
            if not line_words:
                continue

            # Use the line number from the first word (1-based from QCF)
            line_num = line_words[0].line
            y_pos = self._margin_top + (line_num - 1) * line_step

            # A surah title is alone on its line on all 114 pages that carry
            # one, and is a banner rather than a word, so it is drawn rather
            # than packed.
            if len(line_words) == 1 and line_words[0].word_type == "surah_header":
                self._draw_surah_banner(line_words[0], y_pos, body_height,
                                        content_width, line_step)
                continue

            # First pass: measure every glyph on this line — its advance, for
            # packing, and where its ink actually falls, for highlighting.
            glyph_items = []
            total_width = 0.0

            for word in line_words:
                font = self._qcf_loader.get_font(word.font_name)

                text_item = QGraphicsSimpleTextItem(chr(word.code))
                text_item.setFont(font)
                text_item.setBrush(QColor(self._ink_colour(word)))

                metrics = QFontMetricsF(font)
                w = metrics.horizontalAdvance(chr(word.code))
                glyph_items.append((text_item, word, w, self._ink(font, word.code)))
                total_width += w

            if not glyph_items:
                continue

            # Use fixed spacing (requested: no space between words)
            gap = QCF_WORD_SPACING
            num_gaps = len(glyph_items) - 1

            # Compute actual total width with gaps for centering
            actual_total = total_width + max(0, num_gaps) * gap

            # RTL layout: place glyphs right-to-left, centered on page
            # Start x = right edge of centered content block
            start_x = self._margin_x + (content_width + actual_total) / 2.0
            x_pos = start_x

            for text_item, word, glyph_w, ink in glyph_items:
                x_pos -= glyph_w
                metrics = QFontMetricsF(self._qcf_loader.get_font(word.font_name))
                y_glyph = y_pos + (body_height - metrics.height()) / 2.0
                text_item.setPos(x_pos, y_glyph)
                text_item.setParentItem(self._clip)
                self._register(text_item, word, x_pos, y_glyph, glyph_w,
                               gap, band, ink)
                x_pos -= gap

        # Scene rect includes the bleed, so the sheet's shadow is inside the
        # view rather than clipped at its edge.
        self.scene.setSceneRect(QRectF(
            -self._bleed, -self._bleed,
            self._page_width + 2 * self._bleed,
            self._page_height + 2 * self._bleed,
        ))
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _ink(self, font: QFont, code: int) -> QRectF:
        """Where a glyph actually puts ink, relative to its box's top-left.

        QCF glyph boxes are 85 units tall at the size this renders at, and
        the letters inside them occupy between 41 and 52 — the rest is room
        for the tallest possible ascender and the deepest possible descender
        of a font that has to set the whole Mushaf. A highlight drawn on the
        box is therefore about twice as tall as the word it marks, and sits
        high of it, which is what made a marked-up page look damaged.

        Outlining the glyph gives the real extent. It costs a path per glyph,
        so the answer is cached: a page is ~200 glyphs and most of them
        repeat.
        """
        key = (font.family(), code)
        cached = self._ink_cache.get(key)
        if cached is not None:
            return cached
        path = QPainterPath()
        path.addText(0.0, QFontMetricsF(font).ascent(), font, chr(code))
        rect = path.boundingRect()
        if rect.isEmpty():                       # a font with no outline
            rect = QRectF(0.0, 0.0, 0.0, QFontMetricsF(font).height())
        self._ink_cache[key] = rect
        return rect

    def _page_band(self, page_data, body_height: float,
                   line_step: float) -> tuple[float, float]:
        """Where a highlight sits, as offsets into a glyph's box.

        One band for the whole page. Two weaker ideas were tried first:

        The glyph box. It is 85 units tall and the letters in it occupy
        about 45, so every highlight was twice the height of its word and
        sat high of it.

        The extremes of each line's own ink. That hugs the words, but the
        tallest line on a page measures 71 units against a line step of 71,
        so the bands of two neighbouring lines met — and the height changed
        from line to line, which reads as carelessness.

        So: the median of every word on the page, clamped to leave a clear
        gap between lines. A word with an unusually tall alif or a deep final
        nun overhangs its highlight by a few units, which is what a stroke of
        highlighter does to a word and reads as nothing at all. Ayah markers
        are excluded from the measurement — they are circles hanging lower
        than the letters, and would drag the band down onto the line below.
        """
        tops, bottoms = [], []
        for line_words in page_data.lines:
            for word in line_words:
                if word.word_type != "word":
                    continue
                ink = self._ink(self._qcf_loader.get_font(word.font_name),
                                word.code)
                if ink.isEmpty():
                    continue
                tops.append(ink.top())
                bottoms.append(ink.bottom())
        if not tops:
            return 0.0, body_height

        top, bottom = _median(tops), _median(bottoms)
        # Never taller than the line step allows, or neighbouring lines touch.
        ceiling = line_step - 2 * TINT_PADDING - 2.0
        if bottom - top > ceiling:
            middle = (top + bottom) / 2.0
            top, bottom = middle - ceiling / 2.0, middle + ceiling / 2.0
        return top, bottom

    def _draw_sheet(self):
        """The paper the text sits on.

        The view's own background used to be the page: white to the window's
        edge, with nothing to say where the Mushaf began and the application
        ended. A bordered sheet on a neutral backdrop is what the reciter is
        actually looking at, and it costs one item behind everything else.
        """
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0, 0, self._page_width, self._page_height), 6.0, 6.0)
        sheet = self.scene.addPath(
            path, QPen(QColor(PAGE_BORDER), 1.0), QBrush(QColor(PAGE_BG)))
        sheet.setZValue(-10)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(34.0)
        shadow.setOffset(0.0, 8.0)
        shadow.setColor(QColor(60, 54, 40, 46))
        sheet.setGraphicsEffect(shadow)
        self._shadow = shadow

        # A second copy of the same shape, invisible, that everything on the
        # page is parented to and clipped by. The last line's glyph boxes are
        # taller than the space left under them, so their masks used to hang
        # past the bottom of the sheet — invisible while the page and the
        # window were both white, a pale tab sticking out of the paper once
        # they were not. It cannot be the sheet itself: an effect applies to
        # an item's children too, and the whole page would be inside the
        # shadow's blur.
        self._clip = self.scene.addPath(
            path, QPen(Qt.GlobalColor.transparent), QBrush(Qt.GlobalColor.transparent))
        self._clip.setZValue(0)
        self._clip.setFlag(
            self._clip.GraphicsItemFlag.ItemClipsChildrenToShape, True)

    @staticmethod
    def _ink_colour(word) -> str:
        """What colour this glyph is printed in.

        Ayah markers are set in gold the way a printed Mushaf sets them: they
        are never masked, so they are on screen the whole time, and in body
        ink they compete with the words for attention rather than framing
        them.
        """
        if word.word_type == "end":
            return AYAH_MARK_COLOR
        return TEXT_PRIMARY

    def _draw_surah_banner(self, word, y_pos: float, body_height: float,
                           content_width: float, line_step: float):
        """The surah title, in the frame a printed Mushaf gives it.

        QBSML holds only the name, in an ornamental hand, and at the page's
        own point size it measures 34 units against a body line of 85 — so
        drawn as if it were a word it came out as a small mark adrift at the
        top of an otherwise empty line, which is why the surah names could
        not be read. It is a banner: a ruled frame across the text column,
        with the name set inside it.
        """
        font = self._qcf_loader.get_font(word.font_name)
        title = QGraphicsSimpleTextItem(chr(word.code))
        title.setFont(font)
        title.setBrush(QColor(TEXT_PRIMARY))

        metrics = QFontMetricsF(font)
        # Its ink, not its metrics: this glyph overshoots its em box by six
        # units and stands 48 tall where the font reports 33, so sizing it by
        # the font would leave it pressed against the frame.
        ink = self._ink(font, word.code)
        centre_y = y_pos + body_height / 2.0

        height = min(line_step * 0.88, body_height * 0.82)
        width = content_width * 0.84
        left = self._margin_x + (content_width - width) / 2.0
        frame = QRectF(left, centre_y - height / 2.0, width, height)

        # Above the masks, not below them. Glyph boxes are taller than the
        # line spacing, so the words on the line above always hang a little
        # way into this one — invisible while everything is the colour of the
        # paper, but the banner is not, and their masks cut a pale line
        # across its top. Their *ink* stops well short of the frame, so
        # drawing over them covers nothing but empty box.
        for inset, pen_width in ((0.0, 1.3), (4.0, 0.7)):
            path = QPainterPath()
            path.addRoundedRect(frame.adjusted(inset, inset, -inset, -inset),
                                4.0, 4.0)
            rule = self.scene.addPath(
                path, QPen(QColor(AYAH_MARK_COLOR), pen_width),
                QBrush(QColor(BANNER_BG) if inset == 0.0
                       else Qt.GlobalColor.transparent))
            rule.setParentItem(self._clip)
            rule.setZValue(BANNER_Z)

        # Scaled to sit inside the frame with room to spare, then centred on
        # it. setScale() works from the item's own origin, so the ink's offset
        # within the box is part of the sum.
        scale = (height * 0.66) / ink.height() if ink.height() else 1.0
        title.setScale(scale)
        title.setPos(frame.center().x() - (ink.x() + ink.width() / 2.0) * scale,
                     centre_y - (ink.y() + ink.height() / 2.0) * scale)
        title.setZValue(BANNER_Z + 1)
        title.setParentItem(self._clip)

    def _register(self, text_item, word, x_pos: float, y_pos: float,
                  advance: float, gap: float, band: tuple[float, float],
                  ink: QRectF):
        """Give a glyph its overlay, if it gets one.

        Words get an opaque mask that hides them until they are recited.
        The bismillah gets a transparent one, so it is legible from the
        start but can still be tinted when the reciter is heard to recite
        it. Ayah markers and surah headers get nothing: they are page
        furniture and are always on show.
        """
        if word.word_type == "word" and word.verse_key and word.position is not None:
            key = self._verse_key(word)
            if key is None:
                return
            surah_id, ayah_id = key
            # The highlight runs the word's advance plus half the gap either
            # side, so a run of correct words abuts into one continuous
            # stroke with no overlap — overlapping semi-transparent tints
            # would print a darker seam at every word boundary.
            top, bottom = band
            self._word_hitboxes.setdefault(
                (surah_id, ayah_id, word.position), []
            ).append(self._overlay(
                text_item, x_pos, y_pos,
                QRectF(x_pos - gap / 2.0, y_pos + top - TINT_PADDING,
                       advance + gap, (bottom - top) + 2 * TINT_PADDING),
                hidden=True))

        elif word.word_type == "bismillah" and word.sura:
            # Never hidden: it is legible from the moment the page opens, and
            # only ever gains a tint. Sized to its own ink rather than to the
            # line's band — it is alone on its line, and one glyph rather
            # than a run, so nothing abuts it and the slack either side of it
            # would simply show.
            self._basmala_hitboxes.setdefault(word.sura, []).append(
                self._overlay(
                    text_item, x_pos, y_pos,
                    QRectF(x_pos + ink.x() - TINT_PADDING,
                           y_pos + ink.y() - TINT_PADDING,
                           ink.width() + 2 * TINT_PADDING,
                           ink.height() + 2 * TINT_PADDING),
                    hidden=False))

    def _overlay(self, text_item, x_pos: float, y_pos: float,
                 tint_rect: QRectF, hidden: bool) -> _WordOverlay:
        box = text_item.boundingRect()
        mask_rect = QRectF(x_pos - MASK_BLEED, y_pos,
                           box.width() + 2 * MASK_BLEED, box.height())
        mask = self.scene.addRect(mask_rect)
        mask.setParentItem(self._clip)
        mask.setPen(QPen(Qt.GlobalColor.transparent))
        mask.setBrush(QColor(PAGE_BG) if hidden else CLEAR)
        mask.setZValue(MASK_Z)  # above the glyph

        path = QPainterPath()
        path.addRoundedRect(tint_rect, TINT_RADIUS, TINT_RADIUS)
        tint = self.scene.addPath(path, QPen(Qt.GlobalColor.transparent),
                                  QBrush(CLEAR))
        tint.setParentItem(self._clip)
        tint.setZValue(MASK_Z - 1)   # under the mask, over the glyph
        return _WordOverlay(mask, tint)

    @staticmethod
    def _verse_key(word) -> tuple[int, int] | None:
        try:
            surah, ayah = word.verse_key.split(":")[:2]
            return int(surah), int(ayah)
        except (AttributeError, ValueError, IndexError):
            return None

    # ── Feedback ───────────────────────────────────────────────────────

    def update_recitation(self, surah: int, ayah: int, word_index: int, status: bool | None):
        """Unmask and highlight a word based on recitation correctness.

        `status` is True (correct), False (wrong) or None (skipped by the
        reciter).  `word_index` is 0-based from quran.py; QCF word positions
        are 1-based, so exactly one conversion is applied here.
        """
        key = (surah, ayah, word_index + 1)
        hitboxes = self._word_hitboxes.get(key)

        if log.enabled:
            if hitboxes:
                log.count("mushaf_hits")
            elif any(k[0] == surah and k[1] == ayah for k in self._word_hitboxes):
                # The ayah IS on this page but that word position is not —
                # the index and the Mushaf disagree about word boundaries.
                log.count("mushaf_misses")
                log.mushaf(
                    f"OFF-PAGE {surah}:{ayah} word {word_index} (qcf pos {word_index + 1}) "
                    f"not found on page {self.current_page_num}"
                )
            # else: the ayah simply lives on another page — expected, not a bug.

        if not hitboxes:
            return

        for hitbox in hitboxes:
            hitbox.paint(_verdict_tint(status))

    def reveal(self, surah: int, ayah: int, word_index: int) -> bool:
        """Show a word without saying anything about it.

        For words the app knows were recited but could not place — the run
        before discovery locks on — and for words it has heard but not
        finished judging, while the audio windows that will judge them are
        still arriving.

        Any colour already on the word is taken off, because this says
        "nothing is claimed here" and a leftover red would go on claiming.
        That is the case of a reciter going back over a line they were marked
        wrong on: the mark is withdrawn while the new recitation is heard
        out, rather than sitting there through it.
        """
        hitboxes = self._word_hitboxes.get((surah, ayah, word_index + 1))
        if not hitboxes:
            return False
        for hitbox in hitboxes:
            hitbox.paint(CLEAR)
        return True

    def paint_basmala(self, surah: int, status: bool = True) -> bool:
        """Tint a surah's bismillah line, as the reciter having recited it.

        Returns False when this page carries no bismillah for that surah —
        At-Tawbah, which has none at all, and every page that is not a surah
        opening. Al-Fatiha is not here either: its basmala is ayah 1:1 and is
        scored word by word like any other.
        """
        hitboxes = self._basmala_hitboxes.get(surah)
        if not hitboxes:
            return False
        for hitbox in hitboxes:
            hitbox.paint(_verdict_tint(status))
        return True

    def _measure_line_height(self, font: QFont) -> float:
        """Measure typical glyph height using a sample text item."""
        metrics = QFontMetricsF(font)
        h = metrics.height()
        return h if h > 0 else 60.0  # fallback
