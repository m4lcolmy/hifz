"""Main application window — the page, and one bar under it.

Two regions and nothing else: the Mushaf fills everything above a bar, and
the bar holds settings at one end, play at the centre, and the live state at
the other end.

It used to be three. A `QTextEdit` took the right 400px of a 1000px window to
print the same verdicts the page was already showing in colour, and a
floating pill sat *on top of* the page at bottom centre, hiding the last line
of it because nothing reserved the space. Both are gone: the page is the
interface, and a bar in the layout cannot drift over it the way an absolutely
positioned pill could.

Flow:
  1. Press play → mic opens, VAD monitors audio.
  2. When speech ends, the chunk transcribes.
  3. The transcription is matched against the Quran.
  4. The page colours the words: green correct, red wrong, amber not sure.
  5. Press again → mic closes, remaining audio flushed.

The transcriptions themselves are not shown and are not lost: they go to the
session log, which is on by default and is where anyone actually reads them.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QMenu, QWidgetAction,
)
from PyQt6.QtGui import QActionGroup
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt6.QtMultimedia import QAudioSource

from pathlib import Path

from src.ui.style import (
    CORRECT_COLOR, INCORRECT_COLOR, MISSED_COLOR, VERSE_REF_COLOR,
)
from src.config import STRICTNESS, STRICTNESS_LABELS, STRICTNESS_LEVELS
from src.ui.mushaf_view import MushafView
from src.ui.recitation import RecitationTracker
from src.audio.capture import default_audio_format, get_input_device
from src.audio.vad import SlidingWindowBuffer
from src.audio.recorder import SessionRecorder
from src.audio import ModelLoaderThread, TranscriberWorker
from src.core.quran import QuranIndex
from src.core.debug import log
from src.core.page_map import PageMap


class MainWindow(QMainWindow):
    """Real-time Quran recitation + verification window."""

    # Signals
    _chunk_ready = pyqtSignal(object)

    def __init__(self, record: bool = False, engine: str | None = None):
        super().__init__()
        # --record: keep the audio and cut it up into benchmark cases on stop.
        self._record = record
        # --engine: which speech engine to run. None means the configured
        # default, which is Whisper — a second engine is always a choice
        # somebody made, never a silent swap.
        self._engine_name = engine
        self._recorder: SessionRecorder | None = None
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
        self._tracker = RecitationTracker(
            self.mushaf_view, self._page_map, self._quran_index,
            strictness=STRICTNESS,
        )

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

        # ── The page, filling everything above the bar ─────────────────
        self.mushaf_view = MushafView()
        layout.addWidget(self.mushaf_view, 1)
        self.mushaf_view.load_page(1)

        # ── The bar ───────────────────────────────────────────────────
        # A real widget in the layout, not an absolutely positioned child.
        # The pill it replaces was 240x48 placed by resizeEvent() at bottom
        # centre, over a page that was laid out as if it were not there — so
        # the bottom of the Mushaf sat underneath it. A bar *is* the space;
        # there is no geometry to keep in sync and nothing to drift.
        layout.addWidget(self._build_bar(central))

    def _build_bar(self, parent) -> QWidget:
        bar = QWidget(parent)
        bar.setObjectName("bar")
        bar.setFixedHeight(56)

        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(8)

        # ── Left: settings ────────────────────────────────────────────
        self.settings_btn = QPushButton("\u2699")
        self.settings_btn.setObjectName("bar_icon")
        self.settings_btn.setFixedSize(36, 36)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._show_settings)
        row.addWidget(self.settings_btn, 0, Qt.AlignmentFlag.AlignLeft)

        # ── Centre: play, and nothing beside it ───────────────────────
        # Stretches of equal weight either side keep it on the window's
        # centre line rather than the centre of what is left over, so it does
        # not shuffle when the state text changes length.
        row.addStretch(1)
        self.listen_btn = QPushButton("\u25b6")
        self.listen_btn.setObjectName("record")
        self.listen_btn.setEnabled(False)
        self.listen_btn.setCheckable(True)
        self.listen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.listen_btn.clicked.connect(self._toggle_listening)
        self.listen_btn.setFixedSize(40, 40)
        row.addWidget(self.listen_btn, 0, Qt.AlignmentFlag.AlignCenter)
        row.addStretch(1)

        # ── Right: what the app is doing, only while it matters ───────
        # Whether it is *searching* or *following* is the one thing the
        # reciter cannot otherwise know, and it is what explains the blank
        # page during discovery.
        self.status_label = QLabel("Loading model\u2026")
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
        self.status_label.setMinimumWidth(140)
        row.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignRight)
        return bar

    # ── Settings ───────────────────────────────────────────────────────

    def _show_settings(self):
        """One button, one popup. No preferences window.

        Everything here is a decision that belongs *inside* a session. A
        control that does not need touching mid-session belongs in config.py,
        where it can carry the comment explaining why it is the value it is.
        """
        menu = QMenu(self)

        # ── How much evidence before a word is painted red ────────────
        menu.addSection("Flag mistakes")
        group = QActionGroup(menu)
        group.setExclusive(True)
        for level in STRICTNESS_LEVELS:
            action = menu.addAction(STRICTNESS_LABELS.get(level, level))
            action.setCheckable(True)
            action.setChecked(level == self._tracker.strictness)
            action.triggered.connect(
                lambda _checked, name=level: self._set_strictness(name))
            group.addAction(action)

        # ── Which engine turns audio into text ────────────────────────
        from src.audio.engines import DEFAULT_ENGINE, ENGINES, engine_choices
        menu.addSection("Speech engine")
        # While the model is still loading there is no engine object to ask,
        # but the menu must still show which one is coming — otherwise the
        # one moment you are most likely to open it is the one moment it
        # shows nothing selected.
        current = (getattr(self.model, "name", None)
                   or self._engine_name or DEFAULT_ENGINE)
        for name, label, description in engine_choices():
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(name == current)
            why = self._engine_unavailable(ENGINES[name])
            if why:
                # Listed but disabled, with the reason as the tooltip —
                # EngineUnavailable's message already says what to do about
                # it, which is usually "download the model".
                action.setEnabled(False)
                action.setToolTip(why)
            else:
                action.setToolTip(description)
                action.triggered.connect(
                    lambda _checked, e=name: self._switch_engine(e))

        # ── Capture this session as test data ─────────────────────────
        menu.addSection("Session")
        record = menu.addAction("Record this session")
        record.setCheckable(True)
        record.setChecked(self._record)
        record.setToolTip("Keep the audio and cut it into benchmark cases on "
                          "stop. A session that is not recorded is a test "
                          "case thrown away.")
        record.setEnabled(not self._listening)
        record.triggered.connect(self._set_record)

        menu.addSeparator()
        menu.addAction(self._legend_action(menu))

        menu.exec(self.settings_btn.mapToGlobal(
            self.settings_btn.rect().topLeft()) - QPoint(0, menu.sizeHint().height())
        )

    @staticmethod
    def _engine_unavailable(engine_cls) -> str:
        """The reason an engine cannot run here, or "" if it can.

        Asked without loading anything: building the engine is cheap and its
        constructor does not touch the model, so this only costs a path check.
        """
        from src.audio.engines import EngineUnavailable
        try:
            engine = engine_cls()
        except Exception as e:                      # pragma: no cover
            return str(e)
        model_dir = getattr(engine, "model_dir", None)
        if model_dir is not None and not Path(str(model_dir)).exists():
            try:
                engine.load()
            except EngineUnavailable as e:
                return str(e)
            except Exception as e:                  # pragma: no cover
                return str(e)
        return ""

    def _legend_action(self, menu: QMenu) -> QWidgetAction:
        """What the four colours mean, said once.

        Three colours now mean three different things — correct, wrong, and
        *not sure*, the amber from the untrusted-region rule — plus the
        basmala line, which is shown but never scored. Nothing told the
        reciter any of that.
        """
        swatches = (
            (CORRECT_COLOR, "correct"),
            (INCORRECT_COLOR, "wrong"),
            (MISSED_COLOR, "not sure \u2014 or skipped"),
            (VERSE_REF_COLOR, "basmala \u2014 shown, never scored"),
        )
        holder = QWidget(menu)
        column = QVBoxLayout(holder)
        column.setContentsMargins(14, 6, 14, 8)
        column.setSpacing(4)
        for colour, meaning in swatches:
            line = QLabel(
                f'<span style="color:{colour}; font-size:15px">\u25cf</span>'
                f'&nbsp;&nbsp;<span style="color:#6B7280">{meaning}</span>'
            )
            column.addWidget(line)
        action = QWidgetAction(menu)
        action.setDefaultWidget(holder)
        return action

    def _set_strictness(self, level: str):
        self._tracker.set_strictness(level)
        log.event("SETTINGS", f"strictness -> {level}")

    def _set_record(self, checked: bool):
        """A per-session decision, which is why it was never made.

        It used to be `./run.sh --record`, decided before launch — and that is
        why most sessions were not captured.
        """
        self._record = checked
        log.event("SETTINGS", f"record -> {checked}")

    def _switch_engine(self, name: str):
        """Load a different engine without restarting.

        `ModelLoaderThread` already takes an engine name, so this is a
        supported operation rather than a restart. A bake-off you can only
        start from a terminal gets run once.
        """
        if name == getattr(self.model, "name", None):
            return
        if self._listening:
            self._stop_listening()
            self.listen_btn.setChecked(False)
        log.event("SETTINGS", f"engine -> {name}")
        self.listen_btn.setEnabled(False)
        self.model = None
        self._set_status("Loading\u2026", "transcribing")
        self._engine_name = name
        self._teardown_worker()
        self._load_model()

    def _teardown_worker(self):
        """Stop the worker so a new engine can take its place."""
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None

    # ── Status helpers ─────────────────────────────────────────────────

    def _show_mode(self):
        """Say whether the app is searching for the reciter or following one.

        This is the one thing the page cannot show. During discovery the page
        is blank — not because the app is broken, but because it does not yet
        know where the reciter is, and a blank page says neither. One word,
        and only while listening.
        """
        if not self._listening:
            return
        if self._tracker.mode == "discovery":
            self._set_status("Searching\u2026", "transcribing")
        else:
            surah = self._tracker.last_surah
            ayah = self._tracker.last_ayah
            where = f" {surah}:{ayah}" if surah and ayah else ""
            self._set_status(f"Following{where}", "recording")

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
        self._loader = ModelLoaderThread(self._engine_name)
        self._loader.finished.connect(self._on_model_loaded)
        self._loader.error.connect(self._on_model_error)
        self._loader.start()

    def _on_model_loaded(self, processor, model):
        log.event("MODEL", f"{getattr(model, 'label', 'model')} loaded")
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
        log.event("ERROR", f"model load failed: {msg}")
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
            self._recorder = self._new_recorder() if self._record else None
            self._audio_source = QAudioSource(device, fmt)
            self._audio_io = self._audio_source.start()
            self._audio_io.readyRead.connect(self._on_audio_data)
        except Exception as e:
            self.listen_btn.setChecked(False)
            self._set_status(f"Mic error: {e}", "error")
            return

        log.event("SESSION", f"listening started (device={device.description()})")
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
                    "audio": leftover.data,
                    "window": leftover,
                    "mode": self._tracker.mode,
                    "context_surah": self._tracker.last_surah,
                    "context_ayah": self._tracker.last_ayah,
                    "context_word_index": self._tracker.last_word_index,
                })
            self._chunk_detector = None

        # Commit any verdicts that were waiting for a better audio window —
        # otherwise a mistake in the final word is never shown.
        # Commits verdicts that were waiting for a better audio window —
        # otherwise a mistake in the final word is never shown. It paints the
        # page itself; the HTML it returns has nowhere left to go.
        self._tracker.finalize()

        if self._recorder is not None:
            recorder, self._recorder = self._recorder, None
            try:
                where = recorder.finish(self._tracker.verdicts(),
                                        log.path if log.enabled else None)
            except Exception as e:                      # never lose a session
                log.event("ERROR", f"recording failed: {e!r}")
                where = None
            if where is not None:
                print(f"Session recording → {where}")
                self._set_status(f"Recorded → {where.name}", "idle")
                return

        if log.enabled:
            log.event("SESSION", "listening stopped")
            log.summary()
            self._set_status(f"Ready — log: {log.path}", "idle")
            return

        self._set_status("Ready", "idle")

    # ── Audio data handling ────────────────────────────────────────────

    def _on_audio_data(self):
        """Called by QAudioSource when new PCM data is available."""
        if self._audio_io is None or self._chunk_detector is None:
            return

        raw = self._audio_io.readAll().data()
        if not raw:
            return

        if self._recorder is not None:
            self._recorder.feed(raw)

        for window in self._chunk_detector.feed(raw):
            self._chunk_ready.emit({
                "audio": window.data,
                "window": window,
                "mode": self._tracker.mode,
                "context_surah": self._tracker.last_surah,
                "context_ayah": self._tracker.last_ayah,
                "context_word_index": self._tracker.last_word_index,
            })

    # ── Transcription results ──────────────────────────────────────────

    def _new_recorder(self) -> SessionRecorder:
        from datetime import datetime
        from src.config import LOGS_DIR
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return SessionRecorder(LOGS_DIR / f"session-{stamp}")

    def _on_result(self, result: dict):
        """Display transcription result with Quran verification coloring."""
        self._tracker.on_result(result)
        self._show_mode()
        if self._recorder is not None:
            # After on_result, so the clip is labelled with where the tracker
            # believed it was once this window had been absorbed.
            self._recorder.note(
                result.get("window"), self._tracker.last_surah,
                self._tracker.last_ayah, result.get("text", ""),
            )

    def _on_worker_error(self, msg: str):
        log.event("ERROR", f"worker: {msg}")
        self._set_status(f"Error: {msg}", "error")

    # ── Cleanup & Resize ───────────────────────────────────────────────

    def closeEvent(self, event):
        if self._listening:
            self._stop_listening()
        self._teardown_worker()
        log.close()
        super().closeEvent(event)
