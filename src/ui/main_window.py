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
  4. The page uncovers the words, and colours only the exceptions: red
     wrong, amber not sure. Correct recitation is left in plain ink.
  5. Press again → mic closes, remaining audio flushed.

The transcriptions themselves are not shown and are not lost: they go to the
session log, which is on by default and is where anyone actually reads them.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy,
    QLabel, QMenu, QWidgetAction,
)
from PyQt6.QtGui import QActionGroup
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint, QTimer
from PyQt6.QtMultimedia import QAudioSource

from pathlib import Path

from src.ui.style import (
    INCORRECT_COLOR, MISSED_COLOR, TEXT_PRIMARY,
    ACCENT, TEXT_MUTED, TEXT_SECONDARY, RECORD_ERROR,
)
from src.ui.widgets import (
    TransportButton, SettingsButton, StatusDot, StatusLabel,
)
from src.config import (STRICTNESS, STRICTNESS_LABELS, STRICTNESS_LEVELS,
                        SETTLE_TICK_MS)
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

        # A verdict is held back until no further audio window can revise it,
        # which is a fact about elapsed time — so something has to ask about
        # it when no window is arriving. Without this, pausing mid-page
        # leaves the last words you recited uncoloured for as long as you
        # stay quiet. Only repaints what changed, so it costs nothing while
        # nothing is settling.
        self._settle_timer = QTimer(self)
        self._settle_timer.setInterval(SETTLE_TICK_MS)
        self._settle_timer.timeout.connect(self._tracker.tick)

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
        """Settings at one end, play in the middle, state at the other.

        The middle really is the middle. It used to be a stretch either side
        of the button, which centres it in *what is left over* — so the
        moment the state text grew, which it does every time a recorded
        session is saved and the path is printed, the leftover space stopped
        being symmetrical and the play button slid left. Two side zones of
        equal stretch weight are always the same width whatever is in them,
        and the button between them cannot move.
        """
        bar = QWidget(parent)
        bar.setObjectName("bar")
        bar.setFixedHeight(64)

        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(10)

        # ── Left: settings ────────────────────────────────────────────
        self.settings_btn = SettingsButton()
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._show_settings)
        row.addWidget(self._zone(bar, self.settings_btn), 1)

        # ── Centre: play, and nothing beside it ───────────────────────
        self.listen_btn = TransportButton()
        self.listen_btn.setEnabled(False)
        self.listen_btn.setToolTip("Start listening")
        self.listen_btn.clicked.connect(self._toggle_listening)
        row.addWidget(self.listen_btn, 0, Qt.AlignmentFlag.AlignCenter)

        # ── Right: what the app is doing, only while it matters ───────
        # Whether it is *searching* or *following* is the one thing the
        # reciter cannot otherwise know, and it is what explains the blank
        # page during discovery.
        self.status_dot = StatusDot()
        self.status_label = StatusLabel()
        self.status_label.setObjectName("status")
        # Ignored, not Preferred: the label must never ask the layout for the
        # width of its text. That request is what moved the play button.
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)
        # Label first, dot last. The dot then sits at the bar's right edge
        # opposite the settings button, at a fixed place the eye can find,
        # instead of drifting with the length of the text beside it.
        row.addWidget(self._zone(bar, self.status_label, self.status_dot,
                                 grow=0), 1)
        self._set_status("Loading model\u2026", "transcribing")
        return bar

    @staticmethod
    def _zone(parent, *widgets, grow: int | None = None) -> QWidget:
        """One end of the bar.

        Both ends are given the same stretch weight in the bar's own layout,
        so they are the same width whatever is in them — that is what holds
        the play button between them on the window's centre line. `grow`
        names the widget that absorbs the zone's slack; with none named, a
        stretch takes it and the contents sit at the outer edge.
        """
        zone = QWidget(parent)
        zone.setObjectName("bar_zone")
        row = QHBoxLayout(zone)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for index, widget in enumerate(widgets):
            row.addWidget(widget, 1 if index == grow else 0,
                          Qt.AlignmentFlag.AlignVCenter)
        if grow is None:
            row.addStretch(1)
        return zone

    # ── Settings ───────────────────────────────────────────────────────

    def _show_settings(self):
        """One button, one popup. No preferences window.

        Everything here is a decision that belongs *inside* a session. A
        control that does not need touching mid-session belongs in config.py,
        where it can carry the comment explaining why it is the value it is.
        """
        menu = QMenu(self)

        # ── How much evidence before a word is painted red ────────────
        menu.addAction(self._section(menu, "Flag mistakes"))
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
        menu.addSeparator()
        menu.addAction(self._section(menu, "Speech engine"))
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
        menu.addSeparator()
        menu.addAction(self._section(menu, "Session"))
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
    def _section(menu: QMenu, title: str) -> QWidgetAction:
        """A heading inside the popup.

        QMenu.addSection() draws its text through the native style, and this
        menu does not use the native style — the app's stylesheet replaces
        QMenu wholesale, so every section came out as an unlabelled
        separator and the three groups of settings ran together. A widget
        action is drawn by the widget, so it survives.
        """
        label = QLabel(title, menu)
        label.setStyleSheet(
            f"color:{TEXT_MUTED}; font-size:11px; font-weight:600;"
            f"letter-spacing:0.6px; padding:8px 14px 3px 14px;"
        )
        action = QWidgetAction(menu)
        action.setDefaultWidget(label)
        action.setEnabled(False)
        return action

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
        """What the page is saying, said once.

        Plain ink leads, because it is what most of a page looks like and it
        is the only entry that is not a colour. Correct recitation is no
        longer marked — a word appearing at all is the app saying it heard
        you say it, and saying nothing more is the point: what is coloured is
        what wants looking at.

        That does fold two states into one. A word confirmed correct and a
        word the app heard but could not place both read as plain ink, and
        the legend says so rather than pretending the distinction survived.
        """
        swatches = (
            (TEXT_PRIMARY, "correct \u2014 or heard, but never placed"),
            (INCORRECT_COLOR, "wrong"),
            (MISSED_COLOR, "not sure \u2014 or skipped"),
        )
        holder = QWidget(menu)
        column = QVBoxLayout(holder)
        column.setContentsMargins(14, 6, 14, 8)
        column.setSpacing(5)
        for colour, meaning in swatches:
            line = QLabel(
                f'<span style="color:{colour}; font-size:15px">\u25cf</span>'
                f'&nbsp;&nbsp;<span style="color:{TEXT_SECONDARY}">{meaning}</span>'
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
            return
        surah = self._tracker.last_surah
        ayah = self._tracker.last_ayah
        if not surah or not ayah:
            self._set_status("Following", "recording")
            return
        # The surah's name, not only its number: "An-Nisa 4:12" is a place,
        # "4:12" is a coordinate you have to go and look up.
        name = self._tracker.last_surah_name
        where = f"{name} {surah}:{ayah}" if name else f"{surah}:{ayah}"
        self._set_status(where, "recording")

    # What each state colours the dot. The text carries the words; the dot is
    # the part you take in without reading it.
    _STATE_COLORS = {
        "idle": TEXT_MUTED,
        "recording": ACCENT,
        "transcribing": TEXT_SECONDARY,
        "error": RECORD_ERROR,
    }

    def _set_status(self, text: str, state: str = "idle"):
        self.status_label.set_message(text)
        self.status_label.setProperty("state", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_dot.set_color(self._STATE_COLORS.get(state, TEXT_MUTED))

    def _set_btn_style(self, recording: bool):
        """The button paints itself from isChecked(); this only repaints it."""
        self.listen_btn.setToolTip("Stop listening" if recording
                                   else "Start listening")
        self.listen_btn.update()

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
        self._settle_timer.start()
        self._listening = True
        self._set_btn_style(True)
        self._set_status("Listening…", "recording")

    def _stop_listening(self):
        self._listening = False
        self._settle_timer.stop()
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
                self._set_status(f"Recorded \u2192 {where.name}", "idle")
                return

        if log.enabled:
            log.event("SESSION", "listening stopped")
            log.summary()
            # The name, not the path. The path is the tooltip — the bar is for
            # glancing at, and a full path there is just a wide grey smear.
            self._set_status(f"Ready \u00b7 {Path(log.path).name}", "idle")
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
