"""
PMC Overwatch — Headless Voice Engine + Mascot Server.

Runs the AI voice pipeline (mic → STT → LLM → TTS) headlessly
and broadcasts state to the mascot overlay via WebSocket.

No desktop GUI — the mascot lives in OBS as a Browser Source,
and the web dashboard at localhost:8420 is the control panel.

Input modes (INPUT_MODE env var):
  • auto   — continuous VAD listening (default)
  • toggle — press PTT_KEY to start/stop recording
  • push   — hold PTT_KEY to record, release to stop
"""

import asyncio
import atexit
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
from mascot_server import MascotServer  # noqa: E402
from video_capture import ScreenCapture  # noqa: E402
from voice_input import VoiceInput  # noqa: E402
from voice_output import VoiceOutput  # noqa: E402

logger = logging.getLogger(__name__)


class PMCOverwatch:
    """Headless voice AI engine with mascot WebSocket broadcasting."""

    def __init__(self) -> None:
        self._shutdown = threading.Event()

        # ── Logging (in-memory for dashboard) ─────────────────────────
        self._log_lines: list[str] = []
        self._log_lock = threading.Lock()

        self.log("Initializing PMC Overwatch (headless) …")

        # Input mode
        self._input_mode = os.getenv("INPUT_MODE", "auto").lower()
        self._ptt_key = os.getenv("PTT_KEY", "f4").lower()
        self._ptt_active = threading.Event()
        self._ptt_toggle_on = False

        # ── Concurrency guards ────────────────────────────────────────
        self._processing_lock = threading.Lock()
        self._listen_active = False
        self._interrupt_event = threading.Event()

        # ── Screen Capture ────────────────────────────────────────────
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

        # ── Mascot Server (WebSocket bridge) ──────────────────────────
        port = int(os.getenv("DASHBOARD_PORT", "8420"))
        self._mascot = MascotServer(
            port=port,
            get_status=self._get_status,
            clear_memory=lambda: self._brain.clear_memory() if self._brain else None,
        )

        # ── Voice I/O ─────────────────────────────────────────────────
        self._vi = VoiceInput(shutdown_event=self._shutdown, gui_log=self.log)
        self._vo = VoiceOutput(
            gui_callback=self.log,
            on_speak_start=lambda: self._on_speak_start(),
            on_speak_end=lambda: self._on_speak_end(),
            on_amplitude=lambda a: self._on_amplitude(a),
            interrupt_event=self._interrupt_event,
        )

        self._brain: Optional[Brain] = None
        self._twitch_bot = None
        self._running = False
        self._barge_in_occurred = False

    # ── Logging ───────────────────────────────────────────────────────
    def log(self, message: str) -> None:
        """Log a message to console + in-memory buffer (for dashboard)."""
        logger.info(message)
        with self._log_lock:
            self._log_lines.append(message)
            if len(self._log_lines) > 500:
                self._log_lines = self._log_lines[-250:]

    # ── Mascot state callbacks ────────────────────────────────────────
    def _on_speak_start(self) -> None:
        self._mascot.set_mode("speaking")

    def _on_speak_end(self) -> None:
        self._mascot.set_mode("thinking")

    def _on_amplitude(self, amp: float) -> None:
        self._mascot.set_amplitude(amp)

    def _set_mode(self, mode: str) -> None:
        self._mascot.set_mode(mode)

    def _set_emotion(self, emotion: str) -> None:
        self._mascot.set_emotion(emotion)

    # ── Initialization ────────────────────────────────────────────────
    def start(self) -> None:
        """Start all subsystems."""
        # Start mascot server
        if self._mascot.available:
            self._mascot.start()
            self.log(f"[Mascot] Server at http://127.0.0.1:{int(os.getenv('DASHBOARD_PORT', '8420'))}")
            self.log(f"[Mascot] OBS URL: http://127.0.0.1:{int(os.getenv('DASHBOARD_PORT', '8420'))}/mascot")
        else:
            self.log("[!] Mascot server unavailable — install fastapi + uvicorn")

        # Initialize brain
        self.log("[Brain] Loading AI models...")
        try:
            self._brain = Brain()
            engine = getattr(self._brain, '_engine', 'unknown')
            model = getattr(self._brain, '_model', 'unknown')
            self.log(f"[Brain] Online ({engine}: {model})")
            self._brain._warmup()
            self.log("[Brain] Model ready (warm)")
        except ConnectionError as exc:
            self.log(f"[!] Brain init failed: {exc}")
            logger.error("Brain init failed: %s", exc)
            self._brain = None

        # Start screen capture
        if self._screen_enabled and self._screen.available:
            if self._screen.start():
                self.log("[Screen] Capture active (safe mode — same as OBS)")
            else:
                self.log("[Screen] Capture unavailable")
        else:
            self.log("[Screen] Capture disabled")

        # SFX
        if self._sfx and self._sfx.enabled:
            self.log("[SFX] Sound effects active")
            self._sfx.play("startup")

        # Input mode
        mode_label = {"auto": "Auto VAD", "toggle": "Toggle (F4)", "push": "PTT (F4)"}
        self.log(f"Input mode: {mode_label.get(self._input_mode, 'Auto')}")

        # Twitch bot
        if os.getenv("TWITCH_TOKEN"):
            self._setup_twitch()
        else:
            self.log("[Twitch] Disabled (no TWITCH_TOKEN)")

        self.log("System ready. Starting voice pipeline...")
        self._running = True
        self._listen_active = True

        # Start listening thread
        threading.Thread(
            target=self._listening_thread, name="ListenThread", daemon=True
        ).start()

        # Start screen commentary if enabled
        if self._screen_commentary and self._screen_enabled:
            threading.Thread(
                target=self._screen_commentary_thread,
                name="ScreenCommentary", daemon=True,
            ).start()

        # Start keyboard listener for PTT modes
        if self._input_mode in ("toggle", "push"):
            self._start_keyboard_listener()

    def stop(self) -> None:
        """Stop all subsystems."""
        self._running = False
        self._shutdown.set()
        self._screen.stop()
        self._mascot.stop()
        self.log("System stopped.")
        logger.info("Application exited")

    # ── Twitch ────────────────────────────────────────────────────────
    def _setup_twitch(self) -> None:
        """Initialize and start the Twitch bot."""
        try:
            from twitch_bot import TwitchBot
            self._twitch_bot = TwitchBot()
            self._twitch_bot.set_callback(self._on_twitch_message)
            self._twitch_bot.set_system_reference(self)

            thread = threading.Thread(
                target=self._run_twitch, name="TwitchThread", daemon=True
            )
            thread.start()
            self.log("[Twitch] Connecting...")
        except Exception as exc:
            self.log(f"[Twitch] Disabled: {exc}")
            logger.warning("Twitch bot not started: %s", exc)

    def _run_twitch(self) -> None:
        """Run the Twitch bot (blocking, on its own event loop)."""
        if self._twitch_bot is None:
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self._twitch_bot.run()
        except Exception:
            logger.exception("Twitch bot error")
            self.log("[!] Twitch bot disconnected")

    async def _on_twitch_message(self, author: str, content: str) -> None:
        """Handle incoming Twitch chat messages."""
        self.log(f"[Twitch] {author}: {content}")
        self._mascot.send_chat_event(author, content)
        if self._sfx:
            self._sfx.play("twitch")
        thread = threading.Thread(
            target=self._process_interaction,
            args=(f"Twitch user {author} says: {content}",),
            daemon=True,
        )
        thread.start()

    # ── Keyboard listener (PTT modes) ─────────────────────────────────
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
                        self.log("[PTT] Recording...")
                    elif self._input_mode == "toggle":
                        self._ptt_toggle_on = not self._ptt_toggle_on
                        if self._ptt_toggle_on:
                            self._ptt_active.set()
                            self.log("[PTT] Recording started")
                        else:
                            self._ptt_active.clear()
                            self.log("[PTT] Recording stopped")

            def on_release(key):
                if key == target_key and self._input_mode == "push":
                    self._ptt_active.clear()

            listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            listener.daemon = True
            listener.start()
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
        """Continuous voice listening loop."""
        self.log("[Mic] Calibrating...")
        self._mascot.set_mode("listening")
        self._vi.calibrate(gui_log=self.log)

        self.log("[Mic] Listening active — speak to interact.")
        logger.info("Listening thread started (mode=%s)", self._input_mode)

        while self._running and not self._shutdown.is_set():
            try:
                if self._input_mode in ("toggle", "push"):
                    self._mascot.set_mode("idle")
                    while not self._ptt_active.is_set():
                        if not self._running or self._shutdown.is_set():
                            break
                        self._ptt_active.wait(timeout=0.2)
                    if not self._running or self._shutdown.is_set():
                        break
                    self._mascot.set_mode("listening")

                self._process_interaction(use_audio=True)

            except Exception:
                logger.exception("Error in listening loop")
                self.log("[!] Listening error — retrying...")
                if self._shutdown.wait(timeout=2.0):
                    break

            if self._shutdown.wait(timeout=0.1):
                break

        self._listen_active = False
        self._mascot.set_mode("idle")
        self.log("[Mic] Listening stopped.")
        logger.info("Listening thread stopped")

    # ── Screen commentary thread ──────────────────────────────────────
    def _screen_commentary_thread(self) -> None:
        """Periodically analyze screen and let AI comment."""
        logger.info("Screen commentary thread started (interval=%ds)",
                     self._screen_commentary_interval)
        self.log("[Screen] AI commentary active")

        while self._running and not self._shutdown.is_set():
            if self._shutdown.wait(timeout=self._screen_commentary_interval):
                break
            if not self._running or self._brain is None:
                continue

            frame_path = self._screen.get_latest_frame_path()
            if not frame_path:
                continue

            try:
                reaction = self._brain.analyze_screen(frame_path)
                if reaction:
                    self.log(f"[PMC] {reaction}")
                    self._mascot.set_mode("speaking")
                    expression = detect_expression(reaction)
                    self._mascot.set_emotion(expression.value)
                    self._mascot.set_subtitle(reaction)
                    self._vo.speak_streamed(iter([reaction]))
                    self._mascot.set_emotion("neutral")
                    self._mascot.set_subtitle("")
                    if self._running:
                        self._mascot.set_mode("listening")
            except Exception:
                logger.debug("Screen commentary error", exc_info=True)

        logger.info("Screen commentary thread stopped")

    # ── Core interaction pipeline ─────────────────────────────────────
    def _process_interaction(
        self,
        text_prompt: Optional[str] = None,
        use_audio: bool = False,
    ) -> None:
        if self._brain is None:
            return

        if not self._processing_lock.acquire(blocking=False):
            logger.warning("Already processing an interaction — skipping")
            return
        try:
            self._process_interaction_inner(
                text_prompt=text_prompt, use_audio=use_audio
            )
        finally:
            self._processing_lock.release()

    def _process_interaction_inner(
        self,
        text_prompt: Optional[str] = None,
        use_audio: bool = False,
    ) -> None:
        """Core pipeline: audio → STT → LLM → TTS with mascot broadcasting."""
        if self._brain is None:
            return

        # ── Audio capture + transcription ─────────────────────────────
        if use_audio:
            try:
                self._mascot.set_mode("listening")
                assume = self._barge_in_occurred
                self._barge_in_occurred = False
                audio_path = self._vi.listen(
                    output_filename="current_request.wav",
                    assume_speaking=assume,
                )
                if not audio_path:
                    return

                self._mascot.set_mode("thinking")
                self.log("[STT] Speech captured, transcribing...")
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
                    self.log("[!] Could not understand audio, try again.")
                    return

                transcription, detected_lang = result
                lang_label = {"en": "EN", "ro": "RO", "ru": "RU"}.get(
                    detected_lang, detected_lang.upper() if detected_lang else "??"
                )
                self.log(f"[You] ({lang_label}) {transcription}")
                text_prompt = transcription
                self._vo.set_language_hint(detected_lang or "en")

            except Exception:
                logger.exception("Audio capture/transcription error")
                self.log("[!] Audio capture failed")
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

        # ── Stream response ───────────────────────────────────────────
        self._mascot.set_mode("thinking")
        self.log("[Brain] Generating response...")

        response_start = time.monotonic()
        self._vo.reset_interrupt()
        self._vi.start_bargein_monitor(self._interrupt_event)

        if self._sfx:
            self._sfx.play("respond")

        sentences = self._brain.stream_sentences(text_prompt)
        _GESTURE_RE = re.compile(r'\[gesture:(\w+)\]', re.IGNORECASE)
        gesture_fired = False

        def _sentences_with_expression():
            nonlocal gesture_fired
            for sentence in sentences:
                expression = detect_expression(sentence)
                self._mascot.set_emotion(expression.value)

                # Parse gesture tags
                gesture_match = _GESTURE_RE.search(sentence)
                if gesture_match and not gesture_fired:
                    gesture_name = gesture_match.group(1)
                    self._mascot.send_animation(gesture_name)
                    gesture_fired = True
                    logger.info("Gesture triggered: %s", gesture_name)

                # Strip gesture tags from spoken text
                clean = _GESTURE_RE.sub('', sentence).strip()
                if clean:
                    self._mascot.set_subtitle(clean)
                    yield clean

        try:
            self._vo.speak_streamed(_sentences_with_expression())
        except Exception:
            logger.exception("speak_streamed error")

        # Stop barge-in monitor
        bargein_audio_path = self._vi.stop_bargein_monitor()
        was_interrupted = self._vo.was_interrupted()

        elapsed = time.monotonic() - response_start

        if was_interrupted:
            logger.info("Response interrupted by user after %.1fs", elapsed)
            self.log("[PMC] ...interrupted")
            self._barge_in_occurred = True
            if self._sfx:
                self._sfx.play("bargein")
        else:
            logger.info("Response cycle completed in %.1fs", elapsed)
            time.sleep(1.0)  # Post-response cooldown

        # Reset state
        self._mascot.set_emotion("neutral")
        self._mascot.set_subtitle("")

        # ── Handle barge-in ───────────────────────────────────────────
        if was_interrupted and bargein_audio_path:
            self.log("[Barge-in] Interrupted — processing your input...")
            self._mascot.set_mode("listening")

            bargein_result = self._vi.transcribe(bargein_audio_path)
            try:
                if os.path.exists(bargein_audio_path):
                    os.remove(bargein_audio_path)
            except OSError:
                pass

            if bargein_result:
                bargein_text, _ = bargein_result
                if bargein_text and bargein_text.strip():
                    self.log(f"[You] {bargein_text}")
                    logger.info("Barge-in transcription: %s", bargein_text)
                    self._process_interaction(text_prompt=bargein_text)
                    return
            self.log("[Barge-in] Could not understand — resuming listening.")

        # Revert to listening
        if self._running:
            self._mascot.set_mode("listening")
        else:
            self._mascot.set_mode("idle")

    # ── Status provider (for dashboard) ───────────────────────────────
    def _get_status(self) -> dict:
        return {
            "running": self._running,
            "engine": self._brain._engine if self._brain else "none",
            "model": self._brain._model if self._brain else "none",
            "screen_capture": self._screen_enabled,
            "screen_frames": self._screen.frame_count if self._screen else 0,
        }


# ── Entry point ──────────────────────────────────────────────────────
def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    system = PMCOverwatch()

    # Signal handlers for clean shutdown
    def _signal_handler(signum, frame):
        logger.info("Received signal %s — shutting down", signum)
        system.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (OSError, ValueError):
        pass

    # Start
    system.start()

    # Keep main thread alive
    try:
        while not system._shutdown.is_set():
            system._shutdown.wait(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        system.stop()


if __name__ == "__main__":
    main()
