"""
PMC Overwatch — application entry point.

Initialises the GUI, AI brain, voice I/O, and (optionally) the Twitch bot,
then runs the Tkinter main loop on the main thread.

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

import asyncio
import logging
import os
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
from video_capture import VideoCapture  # noqa: E402
from voice_input import VoiceInput  # noqa: E402
from voice_output import VoiceOutput  # noqa: E402

logger = logging.getLogger(__name__)


# ── Core System ──────────────────────────────────────────────────────
class PMCOverwatch:
    """Wires together the AI brain, voice I/O, Twitch bot, and GUI."""

    def __init__(self, gui: OverwatchGUI) -> None:
        self._gui = gui
        self._shutdown = gui.shutdown_event
        self._gui.log("Initializing PMC Overwatch …")

        # Input mode
        self._input_mode = os.getenv("INPUT_MODE", "auto").lower()
        self._ptt_key = os.getenv("PTT_KEY", "f4").lower()
        self._ptt_active = threading.Event()
        self._ptt_toggle_on = False

        # ── Shared barge-in interrupt event ───────────────────────────
        self._interrupt_event = threading.Event()

        # ── Components ─────────────────────────────────────────────────
        self._vc = VideoCapture()
        self._vi = VoiceInput(shutdown_event=self._shutdown, gui_log=self._gui.log)
        self._vo = VoiceOutput(
            gui_callback=self._gui.log,
            on_speak_start=lambda: self._gui.set_vis_mode("speaking"),
            on_speak_end=lambda: self._gui.set_vis_mode("thinking"),
            on_amplitude=lambda a: self._gui.set_amplitude(a),
            interrupt_event=self._interrupt_event,
        )

        self._brain: Optional[Brain] = None
        self._twitch_bot: Optional[TwitchBot] = None
        self._running = False
        self._latest_frame_path = "latest_frame.jpg"
        self._barge_in_occurred = False  # set after barge-in to skip onset detection

        # Hook up GUI callbacks
        self._gui.set_toggle_callback(self._on_toggle)
        self._gui.set_chat_callback(self._on_chat_message)

        # Initialize brain in background so GUI appears instantly
        threading.Thread(
            target=self._init_brain_async, name="BrainInit", daemon=True
        ).start()

    # ── Async brain init ──────────────────────────────────────────────
    def _init_brain_async(self) -> None:
        """Load the AI brain on a background thread (avoids GUI freeze)."""
        try:
            self._brain = Brain()
            engine = self._brain._engine
            model = self._brain._model
            self._gui.log(f"[Brain] Online ({engine}: {model})")
            self._gui.log("[Brain] Warming up model …")
            self._brain._warmup()
            self._gui.log("[Brain] Model ready (warm)")
        except ConnectionError as exc:
            self._gui.log(f"[!] {exc}")
            logger.error("Brain init failed: %s", exc)
            self._brain = None

        mode_label = {"auto": "Auto VAD", "toggle": "Toggle (F4)", "push": "PTT (F4)"}
        self._gui.log(f"Input mode: {mode_label.get(self._input_mode, 'Auto')}")
        self._gui.log("System ready. Click Start to begin.")

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

            # Start the listening thread
            t = threading.Thread(
                target=self._listening_thread, name="ListenThread", daemon=True
            )
            t.start()
            self._gui.register_thread(t)

            # Start keyboard listener for toggle/push modes
            if self._input_mode in ("toggle", "push"):
                self._start_keyboard_listener()

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

        self._gui.log("[Mic] Listening stopped.")
        self._gui.set_status("Offline")
        logger.info("Listening thread stopped")

    # ── Twitch message handler ────────────────────────────────────────
    async def _on_twitch_message(self, author: str, content: str) -> None:
        self._gui.log(f"[Twitch] {author}: {content}")
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

                # Instant feedback — user sees "Processing" the moment they stop talking
                self._gui.set_status("Processing...")
                self._gui.set_vis_mode("thinking")
                self._gui.log("[STT] Speech captured, transcribing...")
                self._gui.set_status("Transcribing...")
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
                # Propagate detected language to TTS for smarter voice selection
                self._vo.set_language_hint(detected_lang)
                lang_label = {"en": "EN", "ro": "RO", "ru": "RU"}.get(detected_lang, detected_lang.upper())
                self._gui.log(f"[You] ({lang_label}) {transcription}")
                text_prompt = transcription

            except Exception:
                logger.exception("Audio capture/transcription error")
                self._gui.log("[!] Audio capture failed")
                return

        if not text_prompt:
            return

        # ── Stream response sentence-by-sentence ──────────────────────
        self._gui.set_status("Thinking...")
        self._gui.set_vis_mode("thinking")
        self._gui.log("[Brain] Generating response...")

        response_start = time.monotonic()

        # Reset and start barge-in monitor
        self._vo.reset_interrupt()
        self._vi.start_bargein_monitor(self._interrupt_event)

        # Collect sentences and detect emotion
        sentences = self._brain.stream_sentences(text_prompt)
        first_emotion_set = False

        # Collect sentences and detect expression per sentence
        sentences = self._brain.stream_sentences(text_prompt)

        def _sentences_with_expression():
            for sentence in sentences:
                expression = detect_expression(sentence)
                self._gui.set_expression(expression)
                logger.debug("Expression: %s for '%s'", expression.value, sentence[:50])
                yield sentence

        self._vo.speak_streamed(_sentences_with_emotion())

        # Stop barge-in monitor and check for captured audio
        bargein_audio_path = self._vi.stop_bargein_monitor()
        was_interrupted = self._vo.was_interrupted()

        elapsed = time.monotonic() - response_start
        logger.info("Response cycle completed in %.1fs (interrupted=%s)",
                    elapsed, was_interrupted)

        def _barge_in_monitor():
            """Background thread: watch mic for user speech during TTS.

            The monitor keeps its InputStream open and records audio after
            detection, bridging the gap until ``listen()`` opens its own.
            """
            # Wait until audio playback actually starts before monitoring.
            if not self._vo._speaking_started.wait(timeout=60):
                return
            if monitor_stop.is_set():
                return
            self._vi.monitor_for_speech(monitor_stop)

        def _barge_in_trigger():
            """Wait for the detection event, then interrupt output."""
            self._vi._bargein_detected.wait()
            if not monitor_stop.is_set():
                logger.info("Barge-in detected — interrupting playback")
                self._vo.request_interrupt()
                self._brain._interrupt.set()

        monitor_thread = threading.Thread(
            target=_barge_in_monitor, name="BargeInMonitor", daemon=True
        )
        trigger_thread = threading.Thread(
            target=_barge_in_trigger, name="BargeInTrigger", daemon=True
        )
        monitor_thread.start()
        trigger_thread.start()

        try:
            self._vo.speak_streamed(_sentences_with_expression())
        finally:
            # Stop the monitor — it will finish capturing and exit
            monitor_stop.set()
            self._vi._bargein_detected.set()  # unblock trigger thread
            monitor_thread.join(timeout=2.0)
            trigger_thread.join(timeout=1.0)

        elapsed = time.monotonic() - response_start

        if self._vo.was_interrupted:
            logger.info("Response interrupted by user after %.1fs", elapsed)
            self._gui.log("[PMC] …interrupted")
            self._barge_in_occurred = True  # next listen() skips onset detection
        else:
            logger.info("Response cycle completed in %.1fs", elapsed)
            # Post-response cooldown: let environment settle before re-listening
            # Prevents TTS echo / chair movement from triggering a new cycle
            time.sleep(0.5)

        # Reset expression after speaking
        self._gui.reset_expression()

        # ── Handle barge-in: transcribe + process immediately ──────
        if was_interrupted and bargein_audio_path:
            self._gui.log("[Barge-in] Interrupted -- processing your input...")
            self._gui.set_status("Transcribing...")
            self._gui.set_vis_mode("listening")

            transcription = self._vi.transcribe(bargein_audio_path)

            # Clean up barge-in audio
            try:
                import os as _os
                if _os.path.exists(bargein_audio_path):
                    _os.remove(bargein_audio_path)
            except OSError:
                pass

            if transcription and transcription.strip():
                self._gui.log(f"[You] {transcription}")
                logger.info("Barge-in transcription: %s", transcription)
                # Process the barge-in input as a new interaction
                self._process_interaction(text_prompt=transcription)
                return
            else:
                self._gui.log("[Barge-in] Could not understand -- resuming listening.")

        # Revert to listening or offline
        if self._running:
            self._gui.set_vis_mode("listening")
            self._gui.set_status("Listening...")
        else:
            self._gui.set_vis_mode("idle")
            self._gui.set_status("Offline")


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

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

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
    logger.info("Application exited")


if __name__ == "__main__":
    main()
