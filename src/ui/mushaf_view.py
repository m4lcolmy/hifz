"""Mushaf page viewer — QCF4 glyph rendering with word-level hitboxes.

Renders Mushaf pages using Quran Complex Fonts (QCF4) where each word
is a single pre-drawn glyph.  Transparent QGraphicsRectItem hitboxes
overlay each word for recitation feedback (green/red highlighting).

Words are masked white initially and revealed as recitation progresses.
Bounding boxes are computed dynamically via Qt's QGraphicsTextItem
boundingRect() — no external coordinate data required.
"""

from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsSimpleTextItem
from PyQt6.QtGui import QColor, QPen, QFont, QFontMetricsF, QPainter
from PyQt6.QtCore import Qt, QRectF

from src.core.qcf_data import QCFDataLoader
from src.config import QCF_FONT_SIZE, QCF_WORD_SPACING, QCF_LINE_SPACING
from src.ui.style import CORRECT_COLOR, INCORRECT_COLOR, TEXT_PRIMARY, BG_SURFACE


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
        self.setBackgroundBrush(QColor("#FFFFFF"))  # Pure white background
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Lazy-init shared loader
        if MushafView._qcf_loader is None:
            MushafView._qcf_loader = QCFDataLoader()

        # Hitbox map: (surah_id, ayah_id, word_position) → QGraphicsRectItem
        self._word_hitboxes: dict[tuple[int, int, int], list] = {}

        # Track current page
        self.current_page_num: int | None = None

        # Layout constants (Classic Mushaf Ratio ~1:1.45)
        self._page_width = 800.0   # base scene units
        self._page_height = 1160.0 # 800 * 1.45
        self._margin_x = 60.0      # generous side margins
        self._margin_top = 80.0    # top margin
        self._margin_bottom = 80.0 # bottom margin

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene.sceneRect().isValid():
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def load_page(self, page_num: int):
        """Load and render a Mushaf page using QCF4 glyphs."""
        self.scene.clear()
        self._word_hitboxes = {}
        self.current_page_num = page_num

        page_data = self._qcf_loader.load_page(page_num)
        if page_data is None:
            print(f"Error: Could not load QCF page {page_num}")
            return

        # Measure line height from font metrics
        sample_font = self._qcf_loader.get_font(page_data.font_name)
        line_height = self._measure_line_height(sample_font)

        # Usable space
        content_width = self._page_width - 2 * self._margin_x
        grid_height = self._page_height - self._margin_top - self._margin_bottom
        line_step = grid_height / 14.0 # 15 lines = 14 gaps

        # Render each line
        for line_words in page_data.lines:
            if not line_words:
                continue
                
            # Use the line number from the first word (1-based from QCF)
            line_num = line_words[0].line
            y_pos = self._margin_top + (line_num - 1) * line_step

            # First pass: measure total width of all glyphs on this line
            glyph_items = []
            total_width = 0.0

            for word in line_words:
                font = self._qcf_loader.get_font(word.font_name)
                
                text_item = QGraphicsSimpleTextItem(chr(word.code))
                text_item.setFont(font)
                text_item.setBrush(QColor(TEXT_PRIMARY))

                metrics = QFontMetricsF(font)
                w = metrics.horizontalAdvance(chr(word.code))
                glyph_items.append((text_item, word, w))
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

            for text_item, word, glyph_w in glyph_items:
                x_pos -= glyph_w
                text_item.setPos(x_pos, y_pos)
                self.scene.addItem(text_item)

                # Create hitbox overlay for interactive words
                if word.word_type == "word" and word.verse_key and word.position is not None:
                    try:
                        parts = word.verse_key.split(":")
                        surah_id = int(parts[0])
                        ayah_id = int(parts[1])
                    except (ValueError, IndexError):
                        x_pos -= gap
                        continue

                    # Hitbox from text item's bounding rect in scene coordinates
                    rect = text_item.boundingRect()
                    scene_rect = QRectF(
                        x_pos, y_pos,
                        rect.width(), rect.height()
                    )
                    hitbox = self.scene.addRect(scene_rect)
                    hitbox.setPen(QPen(Qt.GlobalColor.transparent))
                    # Opaque white mask — word hidden until recited
                    hitbox.setBrush(QColor(255, 255, 255, 255))
                    hitbox.setZValue(10)  # above text

                    key = (surah_id, ayah_id, word.position)
                    if key not in self._word_hitboxes:
                        self._word_hitboxes[key] = []
                    self._word_hitboxes[key].append(hitbox)

                # Also mask "end" markers (ayah number circles)
                elif word.word_type == "end" and word.verse_key:
                    rect = text_item.boundingRect()
                    scene_rect = QRectF(x_pos, y_pos, rect.width(), rect.height())
                    hitbox = self.scene.addRect(scene_rect)
                    hitbox.setPen(QPen(Qt.GlobalColor.transparent))
                    hitbox.setBrush(QColor(255, 255, 255, 255))
                    hitbox.setZValue(10)

                    # Store end markers keyed to their verse for auto-reveal
                    try:
                        parts = word.verse_key.split(":")
                        surah_id = int(parts[0])
                        ayah_id = int(parts[1])
                        # Use position=0 convention for end markers
                        end_key = (surah_id, ayah_id, 0)
                        if end_key not in self._word_hitboxes:
                            self._word_hitboxes[end_key] = []
                        self._word_hitboxes[end_key].append(hitbox)
                    except (ValueError, IndexError):
                        pass

                x_pos -= gap

        # Set fixed scene rect for consistent ratio
        self.scene.setSceneRect(QRectF(0, 0, self._page_width, self._page_height))
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def update_recitation(self, surah: int, ayah: int, word_index: int, is_correct: bool):
        """Unmask and highlight a word based on recitation correctness.

        word_index is 0-based from quran.py but QCF positions are 1-based.
        We check both conventions for robustness.
        """
        # QCF positions are 1-based; quran.py word_index is 0-based
        keys_to_try = [
            (surah, ayah, word_index),      # 0-based (from quran.py)
            (surah, ayah, word_index + 1),  # 1-based (QCF convention)
        ]

        for key in keys_to_try:
            hitboxes = self._word_hitboxes.get(key)
            if hitboxes:
                # Use semi-transparent version of theme colors
                base_color = QColor(CORRECT_COLOR if is_correct else INCORRECT_COLOR)
                color = QColor(base_color.red(), base_color.green(), base_color.blue(), 80)
                for hitbox in hitboxes:
                    hitbox.setBrush(color)

        # Also reveal the end-of-ayah marker when any word in that ayah is recited
        end_key = (surah, ayah, 0)
        end_hitboxes = self._word_hitboxes.get(end_key)
        if end_hitboxes:
            for hitbox in end_hitboxes:
                # Make end marker transparent (revealed but not colored)
                if hitbox.brush().color().alpha() == 255:
                    hitbox.setBrush(QColor(255, 255, 255, 0))

    def _measure_line_height(self, font: QFont) -> float:
        """Measure typical glyph height using a sample text item."""
        metrics = QFontMetricsF(font)
        h = metrics.height()
        return h if h > 0 else 60.0  # fallback
