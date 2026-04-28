"""Main application window — real-time streaming with Quran verification.

Flow:
  1. User presses "Start Listening" → mic opens, VAD monitors audio.
  2. When speech ends (silence detected), the chunk auto-transcribes.
  3. Transcription is matched against the Quran.
  4. Results appear with green (correct) / red (incorrect) coloring.
  5. User presses "Stop" → mic closes, remaining audio flushed.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QSplitter,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt6.QtMultimedia import QAudioSource

from src.ui.style import arabic_font
from src.ui.mushaf_view import MushafView
from src.ui.recitation import RecitationTracker
from src.audio.capture import default_audio_format, get_input_device
from src.audio.vad import SlidingWindowBuffer
from src.audio import ModelLoaderThread, TranscriberWorker
from src.core.quran import QuranIndex
from src.core.page_map import PageMap


class MainWindow(QMainWindow):
    """Real-time Quran recitation + verification window."""

    # Signals
    _chunk_ready = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hifz — Quran Recitation Trainer")
        self.setMinimumSize(800, 480)
        self.resize(1000, 800) 
        self._center_window()

        # ── State ──────────────────────────────────────────────────────
        self.processor = None
        self.model = None
        self._listening = False
        self._audio_source: QAudioSource | None = None
        self._audio_io = None
        self._chunk_detector: SlidingWindowBuffer | None = None

        # Worker thread (created after model loads)
        self._worker: TranscriberWorker | None = None
        self._worker_thread: QThread | None = None

        # Quran index (loaded in background)
        self._quran_index: QuranIndex | None = None

        # Page map for surah/ayah → page lookup
        self._page_map = PageMap()

        self._build_ui()
        self._load_quran_index()
        self._load_model()

        # Recitation tracker (owns position state + result rendering)
        self._tracker = RecitationTracker(self.mushaf_view, self._page_map)

    def _center_window(self):
        """Center the main window on the current screen."""
        frame = self.frameGeometry()
        center = self.screen().availableGeometry().center()
        frame.moveCenter(center - QPoint(0, 35))
        self.move(frame.topLeft())

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Splitter for Mushaf View and Text Output (Side-by-side)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # Mushaf View (Left side)
        self.mushaf_view = MushafView()
        splitter.addWidget(self.mushaf_view)
        
        # Load page 1 initially so it's not completely empty
        self.mushaf_view.load_page(1)

        # Transcription output (Right side, RTL for Arabic)
        self.output_text = QTextEdit()
        self.output_text.setObjectName("output")
        self.output_text.setReadOnly(True)
        self.output_text.setFont(arabic_font())
        self.output_text.setPlaceholderText("Listening...")
        splitter.addWidget(self.output_text)
        
        splitter.setSizes([600, 400])

        # Floating Pill Container (Minimalist)
        self.pill = QWidget(central)
        self.pill.setObjectName("pill")
        
        # Professional Drop Shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(25)
        shadow.setXOffset(0)
        shadow.setYOffset(8)
        shadow.setColor(QColor(0, 0, 0, 20))
        self.pill.setGraphicsEffect(shadow)

        pill_layout = QHBoxLayout(self.pill)
        pill_layout.setContentsMargins(16, 6, 6, 6)
        pill_layout.setSpacing(12)

        # Status
        self.status_label = QLabel("Loading model…")
        self.status_label.setObjectName("status")
        pill_layout.addWidget(self.status_label)

        # Listen button
        self.listen_btn = QPushButton("▶")
        self.listen_btn.setObjectName("record")
        self.listen_btn.setEnabled(False)
        self.listen_btn.setCheckable(True)
        self.listen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.listen_btn.clicked.connect(self._toggle_listening)
        self.listen_btn.setFixedSize(36, 36)
        pill_layout.addWidget(self.listen_btn)
        
        # Ensure pill stays on top
        self.pill.raise_()

    # ── Status helpers ─────────────────────────────────────────────────

    def _set_status(self, text: str, state: str = "idle"):
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _set_btn_style(self, recording: bool):
        self.listen_btn.setProperty("recording", "true" if recording else "false")
        self.listen_btn.style().unpolish(self.listen_btn)
        self.listen_btn.style().polish(self.listen_btn)

    # ── Quran index loading ────────────────────────────────────────────

    def _load_quran_index(self):
        """Load QuranIndex (fast, ~100ms, runs on main thread at startup)."""
        try:
            self._quran_index = QuranIndex()
        except Exception as e:
            print(f"Warning: could not load Quran index: {e}")
            self._quran_index = None

    # ── Model loading ──────────────────────────────────────────────────

    def _load_model(self):
        self._loader = ModelLoaderThread()
        self._loader.finished.connect(self._on_model_loaded)
        self._loader.error.connect(self._on_model_error)
        self._loader.start()

    def _on_model_loaded(self, processor, model):
        self.processor = processor
        self.model = model

        # Start persistent worker thread
        self._worker_thread = QThread(self)
        self._worker = TranscriberWorker(processor, model, self._quran_index)
        self._worker.moveToThread(self._worker_thread)
        self._chunk_ready.connect(self._worker.process_chunk)
        self._worker.result_ready.connect(self._on_result)
        self._worker.error.connect(self._on_worker_error)
        self._worker_thread.start()

        self._set_status("Ready", "idle")
        self.listen_btn.setEnabled(True)

    def _on_model_error(self, msg: str):
        self._set_status(f"Model error: {msg}", "error")

    # ── Listening toggle ───────────────────────────────────────────────

    def _toggle_listening(self, checked: bool):
        if checked:
            self._start_listening()
        else:
            self._stop_listening()

    def _start_listening(self):
        fmt = default_audio_format()
        device = get_input_device()

        if device is None:
            self.listen_btn.setChecked(False)
            self._set_status("No microphone found", "error")
            return

        if not device.isFormatSupported(fmt):
            self.listen_btn.setChecked(False)
            self._set_status("Mic format unsupported", "error")
            return

        try:
            self._tracker.reset()
            self._chunk_detector = SlidingWindowBuffer()
            self._audio_source = QAudioSource(device, fmt)
            self._audio_io = self._audio_source.start()
            self._audio_io.readyRead.connect(self._on_audio_data)
        except Exception as e:
            self.listen_btn.setChecked(False)
            self._set_status(f"Mic error: {e}", "error")
            return

        self._listening = True
        self.listen_btn.setText("■")
        self._set_btn_style(True)
        self._set_status("Listening…", "recording")

    def _stop_listening(self):
        self._listening = False
        self.listen_btn.setText("▶")
        self._set_btn_style(False)

        # Stop mic
        if self._audio_source is not None:
            self._audio_source.stop()
            self._audio_source = None
            self._audio_io = None

        # Flush any remaining speech
        if self._chunk_detector is not None:
            leftover = self._chunk_detector.flush()
            if leftover:
                self._chunk_ready.emit({
                    "audio": leftover,
                    "mode": self._tracker.mode,
                    "context_surah": self._tracker.last_surah,
                    "context_ayah": self._tracker.last_ayah,
                    "context_word_index": self._tracker.last_word_index,
                })
            self._chunk_detector = None

        self._set_status("Ready", "idle")

    # ── Audio data handling ────────────────────────────────────────────

    def _on_audio_data(self):
        """Called by QAudioSource when new PCM data is available."""
        if self._audio_io is None or self._chunk_detector is None:
            return

        raw = self._audio_io.readAll().data()
        if not raw:
            return

        chunks = self._chunk_detector.feed(raw)
        for chunk in chunks:
            self._chunk_ready.emit({
                "audio": chunk,
                "mode": self._tracker.mode,
                "context_surah": self._tracker.last_surah,
                "context_ayah": self._tracker.last_ayah,
                "context_word_index": self._tracker.last_word_index,
            })

    # ── Transcription results ──────────────────────────────────────────

    def _on_result(self, result: dict):
        """Display transcription result with Quran verification coloring."""
        html = self._tracker.on_result(result)
        self.output_text.setHtml(html)
        # Auto-scroll to bottom to show last recited ayah
        self.output_text.verticalScrollBar().setValue(
            self.output_text.verticalScrollBar().maximum()
        )

    def _on_worker_error(self, msg: str):
        self._set_status(f"Error: {msg}", "error")

    # ── Cleanup & Resize ───────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'pill'):
            # Minimalist pill size
            pill_width = 240
            pill_height = 48
            self.pill.setGeometry(
                (self.width() - pill_width) // 2,
                self.height() - pill_height - 40,
                pill_width,
                pill_height
            )

    def closeEvent(self, event):
        if self._listening:
            self._stop_listening()
        if self._worker is not None:
            self._worker.stop()
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait()
        super().closeEvent(event)
