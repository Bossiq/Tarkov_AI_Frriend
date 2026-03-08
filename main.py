"""
PMC Overwatch — application entry point.

Initialises the GUI, AI brain, voice I/O, screen capture, sound effects,
web dashboard, and (optionally) the Twitch bot, then runs the Tkinter
main loop on the main thread.

All background work is coordinated through a shared ``threading.Event``
(``gui.shutdown_event``) so the app exits cleanly when the window is closed
or a SIGTERM / SIGINT is received.

Input modes (INPUT_MODE env var):
  • auto   — continuous VAD listening (default)
  • toggle — press PTT_KEY to start/stop recording
  • push   — hold PTT_KEY to record, release to stop

IMPORTANT: No blocking I/O ever touches the Tkinter main thread.
All heavy work (mic calibration, model loading, transcription, TTS)
runs on daemon threads.
"""

import atexit
import asyncio
import logging
import os
import re
import signal
import threading
import time
from typing import Optional

from dotenv import load_dotenv

# Load environment variables BEFORE any module that reads them
load_dotenv()

from logging_config import setup_logging  # noqa: E402

setup_logging()

from brain import Brain  # noqa: E402
from expression_engine import detect_expression, Emotion  # noqa: E402
from gui import OverwatchGUI  # noqa: E402
from twitch_bot import TwitchBot  # noqa: E402
from video_capture import ScreenCapture  # noqa: E402
from voice_input import VoiceInput  # noqa: E402
from voice_output import VoiceOutput  # noqa: E402
from avatar_3d import Avatar3D  # noqa: E402

logger = logging.getLogger(__name__)


# ── Core System ──────────────────────────────────────────────────────
class PMCOverwatch:
    """Wires together the AI brain, voice I/O, screen capture, SFX,
    dashboard, Twitch bot, and GUI."""

    def __init__(self, gui: OverwatchGUI) -> None:
        self._gui = gui
        self._shutdown = gui.shutdown_event
        self._gui.log("Initializing PMC Overwatch …")

        # Input mode
        self._input_mode = os.getenv("INPUT_MODE", "auto").lower()
        self._ptt_key = os.getenv("PTT_KEY", "f4").lower()
        self._ptt_active = threading.Event()
        self._ptt_toggle_on = False

        # ── Concurrency guards ────────────────────────────────────────
        self._processing_lock = threading.Lock()   # prevent parallel interactions
        self._listen_active = False                 # prevent duplicate listen threads

        # ── Shared barge-in interrupt event ───────────────────────────
        self._interrupt_event = threading.Event()

        # ── Screen Capture (anti-cheat safe — same API as OBS) ────────
        screen_enabled = os.getenv("SCREEN_CAPTURE", "true").lower() in ("true", "1")
        screen_monitor = int(os.getenv("SCREEN_MONITOR", "1"))
        screen_fps = float(os.getenv("SCREEN_FPS", "1.0"))
        self._screen = ScreenCapture(
            monitor=screen_monitor,
            fps=screen_fps,
            shutdown_event=self._shutdown,
        )
        self._screen_enabled = screen_enabled
        self._screen_commentary = os.getenv("SCREEN_COMMENTARY", "true").lower() in ("true", "1")
        self._screen_commentary_interval = int(os.getenv("SCREEN_COMMENTARY_INTERVAL", "30"))

        # ── Sound Effects ─────────────────────────────────────────────
        try:
            from sound_effects import SoundEffects
            self._sfx = SoundEffects()
        except Exception:
            self._sfx = None
            logger.info("SFX module not available")

        # ── 3D Avatar (optional) ───────────────────────────────────────
        self._avatar_3d: Optional[Avatar3D] = None
        if os.getenv("AVATAR_3D", "false").lower() in ("true", "1"):
            self._avatar_3d = Avatar3D()

        # ── Components ─────────────────────────────────────────────────
        self._vi = VoiceInput(shutdown_event=self._shutdown, gui_log=self._gui.log)
        self._vo = VoiceOutput(
            gui_callback=self._gui.log,
            on_speak_start=lambda: self._on_speak_start(),
            on_speak_end=lambda: self._on_speak_end(),
            on_amplitude=lambda a: self._on_amplitude(a),
            interrupt_event=self._interrupt_event,
        )

        self._brain: Optional[Brain] = None
        self._twitch_bot: Optional[TwitchBot] = None
        self._running = False
        self._toggle_lock = threading.Lock()  # prevents double listen threads
        self._barge_in_occurred = False  # set after barge-in to skip onset detection

        # Hook up GUI callbacks
        self._gui.set_toggle_callback(self._on_toggle)
        self._gui.set_chat_callback(self._on_chat_message)
        self._gui.set_mic_callback(
            lambda idx: self._vi.set_device(idx, gui_log=self._gui.log)
        )

        # Initialize brain in background so GUI appears instantly
        threading.Thread(
            target=self._init_brain_async, name="BrainInit", daemon=True
        ).start()

    # ── Async brain init ──────────────────────────────────────────────
    def _init_brain_async(self) -> None:
        """Load the AI brain on a background thread (avoids GUI freeze)."""
        try:
            self._brain = Brain()
            engine = getattr(self._brain, '_engine', 'unknown')
            model = getattr(self._brain, '_model', 'unknown')
            self._gui.log(f"[Brain] Online ({engine}: {model})")
            self._gui.log("[Brain] Warming up model …")
            self._brain._warmup()
            self._gui.log("[Brain] Model ready (warm)")
        except ConnectionError as exc:
            self._gui.log(f"[!] {exc}")
            logger.error("Brain init failed: %s", exc)
            self._brain = None

        # Start screen capture if enabled
        if self._screen_enabled and self._screen.available:
            if self._screen.start():
                self._gui.log("[Screen] Capture active (safe mode — same as OBS)")
            else:
                self._gui.log("[Screen] Capture unavailable")
        else:
            self._gui.log("[Screen] Capture disabled")

        mode_label = {"auto": "Auto VAD", "toggle": "Toggle (F4)", "push": "PTT (F4)"}
        self._gui.log(f"Input mode: {mode_label.get(self._input_mode, 'Auto')}")

        # SFX status
        if self._sfx and self._sfx.enabled:
            self._gui.log("[SFX] Sound effects active")
            self._sfx.play("startup")

        # Start 3D avatar if configured
        if self._avatar_3d:
            if self._avatar_3d.start():
                self._gui.log("[Avatar] 3D holographic avatar active")
                self._gui.set_avatar_3d(self._avatar_3d)
            else:
                self._gui.log("[Avatar] 3D avatar unavailable — using sprites")
                self._avatar_3d = None

        self._gui.log("System ready. Click Start to begin.")

    # ── Avatar/GUI callbacks (route to both 2D + 3D) ────────────────
    def _on_speak_start(self) -> None:
        self._gui.set_vis_mode("speaking")
        if self._avatar_3d:
            self._avatar_3d.set_mode("speaking")

    def _on_speak_end(self) -> None:
        self._gui.set_vis_mode("thinking")
        if self._avatar_3d:
            self._avatar_3d.set_mode("thinking")

    def _on_amplitude(self, amp: float) -> None:
        self._gui.set_amplitude(amp)
        if self._avatar_3d:
            self._avatar_3d.set_mouth(amp)

    # ── Twitch ────────────────────────────────────────────────────────
    def setup_twitch(self) -> bool:
        """Initialise the Twitch bot.  Returns True if successful."""
        try:
            self._twitch_bot = TwitchBot()
            self._twitch_bot.set_callback(self._on_twitch_message)
            self._twitch_bot.set_system_reference(self)
            return True
        except ValueError as exc:
            self._gui.log(f"[!] Twitch disabled: {exc}")
            logger.warning("Twitch bot not started: %s", exc)
            return False

    def start_twitch_bot(self) -> None:
        """Run the Twitch bot (blocking).  Intended for a daemon thread."""
        if self._twitch_bot is None:
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self._gui.log("[Twitch] Connecting...")
            self._twitch_bot.run()
        except Exception:
            logger.exception("Twitch bot error")
            self._gui.log("[!] Twitch bot disconnected")

    # ── Toggle ────────────────────────────────────────────────────────
    def _on_toggle(self, is_running: bool) -> None:
        """Called by the GUI when the user clicks Start / Stop.

        CRITICAL: This runs on the Tkinter main thread, so it must
        NEVER block.  All heavy work goes to background threads.
        """
        self._running = is_running
        if is_running:
            if self._brain is None:
                self._gui.log(
                    "[!] Cannot start: Brain is not ready. "
                    "Check Ollama or Groq API key."
                )
                self._gui.force_toggle_off()
                self._running = False
                return

            # Guard against duplicate listening threads
            if self._listen_active:
                logger.warning("Listen thread already active — ignoring toggle")
                return
            self._listen_active = True

            # Start the listening thread
            t = threading.Thread(
                target=self._listening_thread, name="ListenThread", daemon=True
            )
            t.start()
            self._gui.register_thread(t)

            # Start screen commentary thread if enabled
            if self._screen_commentary and self._screen_enabled:
                ct = threading.Thread(
                    target=self._screen_commentary_thread,
                    name="ScreenCommentary",
                    daemon=True,
                )
                ct.start()
                self._gui.register_thread(ct)

            # Start keyboard listener for toggle/push modes
            if self._input_mode in ("toggle", "push"):
                self._start_keyboard_listener()
        else:
            self._listen_active = False

    # ── Keyboard listener (push-to-talk) ─────────────────────────────
    def _start_keyboard_listener(self) -> None:
        """Start pynput keyboard listener for PTT modes."""
        try:
            from pynput import keyboard

            key_map = {
                "f1": keyboard.Key.f1, "f2": keyboard.Key.f2,
                "f3": keyboard.Key.f3, "f4": keyboard.Key.f4,
                "f5": keyboard.Key.f5, "f6": keyboard.Key.f6,
                "f7": keyboard.Key.f7, "f8": keyboard.Key.f8,
                "f9": keyboard.Key.f9, "f10": keyboard.Key.f10,
                "f11": keyboard.Key.f11, "f12": keyboard.Key.f12,
            }
            target_key = key_map.get(self._ptt_key, keyboard.Key.f4)

            def on_press(key):
                if key == target_key:
                    if self._input_mode == "push":
                        self._ptt_active.set()
                        self._gui.log("[PTT] Recording...")
                    elif self._input_mode == "toggle":
                        self._ptt_toggle_on = not self._ptt_toggle_on
                        if self._ptt_toggle_on:
                            self._ptt_active.set()
                            self._gui.log("[PTT] Recording started")
                        else:
                            self._ptt_active.clear()
                            self._gui.log("[PTT] Recording stopped")

            def on_release(key):
                if key == target_key and self._input_mode == "push":
                    self._ptt_active.clear()

            listener = keyboard.Listener(
                on_press=on_press, on_release=on_release
            )
            listener.daemon = True
            listener.start()
            self._gui.register_thread(listener)
            logger.info("Keyboard listener started (key=%s, mode=%s)",
                        self._ptt_key, self._input_mode)

        except ImportError:
            logger.warning("pynput not installed -- falling back to auto mode")
            self._input_mode = "auto"
        except Exception:
            logger.exception("Failed to start keyboard listener")
            self._input_mode = "auto"

    # ── Listening thread ──────────────────────────────────────────────
    def _listening_thread(self) -> None:
        """Continuous listening loop.

        Supports three modes:
          auto   — continuous VAD (default)
          toggle — waits for PTT toggle, then records until toggled off
          push   — waits for PTT key held, records while held
        """
        # Calibrate mic
        self._gui.log("[Mic] Calibrating...")
        self._gui.set_status("Calibrating...")
        self._vi.calibrate(gui_log=self._gui.log)

        self._gui.log("[Mic] Listening active -- speak to interact.")
        self._gui.set_status("Listening...")
        logger.info("Listening thread started (mode=%s)", self._input_mode)

        while self._running and not self._shutdown.is_set():
            try:
                if self._input_mode in ("toggle", "push"):
                    # Wait for PTT activation
                    self._gui.set_status(f"Press {self._ptt_key.upper()} to talk")
                    self._gui.set_vis_mode("idle")
                    while not self._ptt_active.is_set():
                        if not self._running or self._shutdown.is_set():
                            break
                        self._ptt_active.wait(timeout=0.2)
                    if not self._running or self._shutdown.is_set():
                        break
                    self._gui.set_status("Listening...")
                    self._gui.set_vis_mode("listening")

                self._process_interaction(use_audio=True)

            except Exception:
                logger.exception("Error in listening loop")
                self._gui.log("[!] Listening error -- retrying...")
                if self._shutdown.wait(timeout=2.0):
                    break

            if self._shutdown.wait(timeout=0.1):
                break

        self._listen_active = False
        self._gui.log("[Mic] Listening stopped.")
        self._gui.set_status("Offline")
        logger.info("Listening thread stopped")

    # ── Screen commentary thread ──────────────────────────────────────
    def _screen_commentary_thread(self) -> None:
        """Periodically analyze the screen and let the AI comment."""
        logger.info("Screen commentary thread started (interval=%ds)",
                     self._screen_commentary_interval)
        self._gui.log("[Screen] AI commentary active")

        while self._running and not self._shutdown.is_set():
            # Wait for the configured interval
            if self._shutdown.wait(timeout=self._screen_commentary_interval):
                break
            if not self._running or self._brain is None:
                continue

            # Get current frame
            frame_path = self._screen.get_latest_frame_path()
            if not frame_path:
                continue

            # Ask the brain to analyze the screenshot
            try:
                reaction = self._brain.analyze_screen(frame_path)
                if reaction:
                    self._gui.log(f"[PMC] {reaction}")
                    # Speak the reaction via TTS
                    self._gui.set_vis_mode("speaking")
                    expression = detect_expression(reaction)
                    self._gui.set_expression(expression)
                    self._vo.speak_streamed(iter([reaction]))
                    self._gui.reset_expression()
                    if self._running:
                        self._gui.set_vis_mode("listening")
            except Exception:
                logger.debug("Screen commentary error", exc_info=True)

        logger.info("Screen commentary thread stopped")

    # ── Twitch message handler ────────────────────────────────────────
    async def _on_twitch_message(self, author: str, content: str) -> None:
        self._gui.log(f"[Twitch] {author}: {content}")
        if self._sfx:
            self._sfx.play("twitch")
        thread = threading.Thread(
            target=self._process_interaction,
            args=(f"Twitch user {author} says: {content}",),
            daemon=True,
        )
        thread.start()

    # ── Chat text message handler ─────────────────────────────────────
    def _on_chat_message(self, text: str) -> None:
        """Called by the GUI when the user types a chat message."""
        if not text.strip():
            return
        self._process_interaction(text_prompt=text)

    # ── Core interaction pipeline ─────────────────────────────────────
    def _process_interaction(
        self,
        text_prompt: Optional[str] = None,
        use_audio: bool = False,
        use_video: bool = False,
    ) -> None:
        if self._brain is None:
            return

        # Prevent concurrent interactions (causes TTS event loop crash)
        if not self._processing_lock.acquire(blocking=False):
            logger.warning("Already processing an interaction — skipping")
            return
        try:
            self._process_interaction_inner(
                text_prompt=text_prompt, use_audio=use_audio, use_video=use_video
            )
        finally:
            self._processing_lock.release()

    def _process_interaction_inner(
        self,
        text_prompt: Optional[str] = None,
        use_audio: bool = False,
        use_video: bool = False,
    ) -> None:
        """Actual interaction logic, called under _processing_lock."""
        if self._brain is None:
            return

        # ── Audio capture + transcription ─────────────────────────────
        if use_audio:
            try:
                self._gui.set_status("Listening...")
                # After barge-in, skip onset detection — user is already speaking
                assume = self._barge_in_occurred
                self._barge_in_occurred = False
                audio_path = self._vi.listen(
                    output_filename="current_request.wav",
                    assume_speaking=assume,
                )
                if not audio_path:
                    return

                self._gui.set_status("Processing...")
                self._gui.set_vis_mode("thinking")
                self._gui.log("[STT] Speech captured, transcribing...")
                self._gui.set_status("Transcribing...")
                if self._sfx:
                    self._sfx.play("thinking")
                result = self._vi.transcribe(audio_path)

                # Clean up recorded audio
                if os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except OSError:
                        logger.warning("Could not remove temp audio: %s", audio_path)

                if not result:
                    self._gui.log("[!] Could not understand audio, try again.")
                    return

                transcription, detected_lang = result
                lang_label = {"en": "EN", "ro": "RO", "ru": "RU"}.get(
                    detected_lang, detected_lang.upper() if detected_lang else "??"
                )
                self._gui.log(f"[You] ({lang_label}) {transcription}")
                text_prompt = transcription

                # Pass detected language to TTS so voice matches user's language
                self._vo.set_language_hint(detected_lang or "en")

            except Exception:
                logger.exception("Audio capture/transcription error")
                self._gui.log("[!] Audio capture failed")
                if self._sfx:
                    self._sfx.play("error")
                return

        if not text_prompt:
            return

        # ── Add screen context if available ───────────────────────────
        if self._screen_enabled and self._screen.available:
            frame_path = self._screen.get_latest_frame_path()
            if frame_path:
                screen_ctx = self._brain.get_screen_context(frame_path)
                if screen_ctx:
                    text_prompt += screen_ctx

        # ── Stream response sentence-by-sentence ──────────────────────
        self._gui.set_status("Thinking...")
        self._gui.set_vis_mode("thinking")
        self._gui.log("[Brain] Generating response...")

        response_start = time.monotonic()

        # Reset and start barge-in monitor
        self._vo.reset_interrupt()
        self._vi.start_bargein_monitor(self._interrupt_event)

        if self._sfx:
            self._sfx.play("respond")

        # Stream sentences with expression detection + gesture parsing
        sentences = self._brain.stream_sentences(text_prompt)
        _GESTURE_RE = re.compile(r'\[gesture:(\w+)\]', re.IGNORECASE)
        gesture_fired = False  # only one gesture per response

        def _sentences_with_expression():
            nonlocal gesture_fired
            for sentence in sentences:
                expression = detect_expression(sentence)
                self._gui.set_expression(expression)
                # Forward expression to 3D avatar
                if self._avatar_3d:
                    self._avatar_3d.set_emotion(expression.value)

                # Parse and trigger gesture tags
                gesture_match = _GESTURE_RE.search(sentence)
                if gesture_match and not gesture_fired and self._avatar_3d:
                    gesture_name = gesture_match.group(1)
                    self._avatar_3d.set_gesture(gesture_name)
                    gesture_fired = True
                    logger.info("Gesture triggered: %s", gesture_name)

                # Strip gesture tags from spoken text
                clean = _GESTURE_RE.sub('', sentence).strip()
                if clean:
                    yield clean

        try:
            self._vo.speak_streamed(_sentences_with_expression())
        except Exception:
            logger.exception("speak_streamed error")

        # Stop barge-in monitor and check for captured audio
        bargein_audio_path = self._vi.stop_bargein_monitor()
        was_interrupted = self._vo.was_interrupted()

        elapsed = time.monotonic() - response_start

        if was_interrupted:
            logger.info("Response interrupted by user after %.1fs", elapsed)
            self._gui.log("[PMC] ...interrupted")
            self._barge_in_occurred = True
            if self._sfx:
                self._sfx.play("bargein")
        else:
            logger.info("Response cycle completed in %.1fs", elapsed)
            # Post-response cooldown: prevent TTS echo from triggering new cycle
            time.sleep(1.0)

        # Reset expression after speaking
        self._gui.reset_expression()
        if self._avatar_3d:
            self._avatar_3d.set_emotion("neutral")

        # ── Handle barge-in: transcribe + process immediately ──────
        if was_interrupted and bargein_audio_path:
            self._gui.log("[Barge-in] Interrupted -- processing your input...")
            self._gui.set_status("Transcribing...")
            self._gui.set_vis_mode("listening")

            bargein_result = self._vi.transcribe(bargein_audio_path)

            # Clean up barge-in audio
            try:
                if os.path.exists(bargein_audio_path):
                    os.remove(bargein_audio_path)
            except OSError:
                pass

            if bargein_result:
                bargein_text, _ = bargein_result
                if bargein_text and bargein_text.strip():
                    self._gui.log(f"[You] {bargein_text}")
                    logger.info("Barge-in transcription: %s", bargein_text)
                    self._process_interaction(text_prompt=bargein_text)
                    return
            self._gui.log("[Barge-in] Could not understand -- resuming listening.")

        # Revert to listening or offline
        if self._running:
            self._gui.set_vis_mode("listening")
            self._gui.set_status("Listening...")
        else:
            self._gui.set_vis_mode("idle")
            self._gui.set_status("Offline")

    # ── Dashboard status provider ─────────────────────────────────────
    def _get_dashboard_status(self) -> dict:
        """Provide status data for the web dashboard."""
        return {
            "running": self._running,
            "engine": self._brain._engine if self._brain else "none",
            "model": self._brain._model if self._brain else "none",
            "screen_capture": self._screen_enabled,
            "screen_frames": self._screen.frame_count if self._screen else 0,
            "responses": 0,
        }

    def _get_dashboard_logs(self) -> list:
        """Provide logs for the web dashboard."""
        return self._gui._chat_log[-100:] if hasattr(self._gui, '_chat_log') else []


# ── Entry point ──────────────────────────────────────────────────────
def main() -> None:
    # Set an event loop for the main thread so TwitchIO can attach to it during init
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    gui = OverwatchGUI()
    system = PMCOverwatch(gui)

    # ── Signal handlers for clean CLI shutdown ────────────────────────
    def _signal_handler(signum, frame):
        logger.info("Received signal %s — shutting down", signum)
        gui.shutdown_event.set()
        gui.after(0, gui._on_close)

    signal.signal(signal.SIGINT, _signal_handler)
    try:
        # SIGTERM is available on macOS/Linux but may not work on all
        # Windows configurations
        signal.signal(signal.SIGTERM, _signal_handler)
    except (OSError, ValueError):
        pass  # SIGTERM not available (Windows)

    # ── Web Dashboard (optional) ──────────────────────────────────────
    dashboard_enabled = os.getenv("DASHBOARD_ENABLED", "true").lower() in ("true", "1")
    if dashboard_enabled:
        try:
            from dashboard import Dashboard
            port = int(os.getenv("DASHBOARD_PORT", "8420"))
            dash = Dashboard(
                port=port,
                get_status=system._get_dashboard_status,
                get_logs=system._get_dashboard_logs,
                clear_memory=lambda: system._brain.clear_memory() if system._brain else None,
            )
            if dash.start():
                gui.log(f"[Dashboard] http://localhost:{port}")
        except Exception:
            logger.info("Dashboard not available")
    else:
        gui.log("[Dashboard] Disabled")

    # ── Twitch bot (optional) ─────────────────────────────────────────
    if os.getenv("TWITCH_TOKEN"):
        if system.setup_twitch():
            t = threading.Thread(
                target=system.start_twitch_bot, name="TwitchThread", daemon=True
            )
            t.start()
            gui.register_thread(t)
    else:
        gui.log("[Info] Twitch disabled (TWITCH_TOKEN not set)")
        logger.info("Twitch bot disabled -- no TWITCH_TOKEN in environment")

    # ── Run ───────────────────────────────────────────────────────────
    logger.info("Starting PMC Overwatch GUI")
    gui.mainloop()

    # Cleanup
    system._screen.stop()
    logger.info("Application exited")


if __name__ == "__main__":
    main()
