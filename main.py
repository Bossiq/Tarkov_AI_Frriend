"""
PMC Overwatch — application entry point.

Initialises the GUI, AI brain, voice I/O, and (optionally) the Twitch bot,
then runs the Tkinter main loop on the main thread.

All background work is coordinated through a shared ``threading.Event``
(``gui.shutdown_event``) so the app exits cleanly when the window is closed
or a SIGTERM / SIGINT is received.

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
from gui import OverwatchGUI  # noqa: E402
from twitch_bot import TwitchBot  # noqa: E402
from video_capture import VideoCapture  # noqa: E402
from voice_input import VoiceInput  # noqa: E402
from voice_output import VoiceOutput  # noqa: E402

logger = logging.getLogger(__name__)


# ── Core System ──────────────────────────────────────────────────────
class SCAVESystem:
    """Wires together the AI brain, voice I/O, Twitch bot, and GUI."""

    def __init__(self, gui: OverwatchGUI) -> None:
        self._gui = gui
        self._shutdown = gui.shutdown_event
        self._gui.log("Initializing PMC Overwatch …")

        # ── Components (lightweight init only) ─────────────────────────
        self._vc = VideoCapture()
        self._vi = VoiceInput(shutdown_event=self._shutdown, gui_log=self._gui.log)
        self._vo = VoiceOutput(
            gui_callback=self._gui.log,
            on_speak_start=lambda: self._gui.set_vis_mode("speaking"),
            on_speak_end=lambda: self._gui.set_vis_mode("thinking"),
        )

        self._brain: Optional[Brain] = None
        self._twitch_bot: Optional[TwitchBot] = None
        self._running = False

        self._latest_frame_path = "latest_frame.jpg"

        # Hook up GUI toggle
        self._gui.set_toggle_callback(self._on_toggle)

        # Initialize brain in background so GUI appears instantly
        threading.Thread(
            target=self._init_brain_async, name="BrainInit", daemon=True
        ).start()

    # ── Async brain init ──────────────────────────────────────────────
    def _init_brain_async(self) -> None:
        """Load the AI brain on a background thread (avoids GUI freeze)."""
        try:
            self._brain = Brain()
            self._gui.log(f"[Brain] Online (Ollama: {self._brain._model})")
        except ConnectionError as exc:
            self._gui.log(f"[!] {exc}")
            logger.error("Brain init failed: %s", exc)
            self._brain = None

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
                    "[!] Cannot start: Ollama is not running. "
                    "Launch the Ollama app or run: ollama serve"
                )
                # Reset toggle back to stopped state
                self._gui.force_toggle_off()
                self._running = False
                return

            # Start the listening thread — calibration happens INSIDE it,
            # NOT on the main thread (prevents UI freeze)
            t = threading.Thread(
                target=self._listening_thread, name="ListenThread", daemon=True
            )
            t.start()
            self._gui.register_thread(t)

    # ── Listening thread ──────────────────────────────────────────────
    def _listening_thread(self) -> None:
        """Continuous VAD listening loop running on a background thread.

        Calibration happens HERE (background thread), NOT on main thread.
        """
        # Calibrate mic on the background thread — this blocks for ~1s
        # but that's fine because we're NOT on the Tkinter main thread
        self._gui.log("[Mic] Calibrating...")
        self._gui.set_status("Calibrating...")
        self._vi.calibrate(gui_log=self._gui.log)

        self._gui.log("[Mic] Listening active -- speak to interact.")
        self._gui.set_status("Listening...")
        logger.info("Listening thread started")

        while self._running and not self._shutdown.is_set():
            try:
                self._gui.set_status("Listening...")
                self._process_interaction(use_audio=True)
            except Exception:
                logger.exception("Error in listening loop")
                self._gui.log("[!] Listening error -- retrying...")
                if self._shutdown.wait(timeout=2.0):
                    break
            # Small pause between listen cycles
            if self._shutdown.wait(timeout=0.1):
                break

        self._gui.log("[Mic] Listening stopped.")
        self._gui.set_status("Offline")
        logger.info("Listening thread stopped")

    # ── Twitch message handler ────────────────────────────────────────
    async def _on_twitch_message(self, author: str, content: str) -> None:
        if "scav" in content.lower() or "blyat" in content.lower():
            prompt = f"User {author} said: '{content}'. Respond to them."
            self._gui.log(f"[Twitch] {author}: {content}")
            await asyncio.to_thread(
                self._process_interaction, text_prompt=prompt
            )

    # ── Core interaction logic ────────────────────────────────────────
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
                audio_path = self._vi.listen(output_filename="current_request.wav")
                if not audio_path:
                    return

                self._gui.log("[STT] Speech captured, transcribing...")
                self._gui.set_status("Transcribing...")
                transcription = self._vi.transcribe(audio_path)

                # Clean up recorded audio
                if os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except OSError:
                        logger.warning("Could not remove temp audio: %s", audio_path)

                if not transcription:
                    self._gui.log("[!] Could not understand audio, try again.")
                    return

                self._gui.log(f"[You] {transcription}")
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
        self._vo.speak_streamed(self._brain.stream_sentences(text_prompt))
        elapsed = time.monotonic() - response_start
        logger.info("Response cycle completed in %.1fs", elapsed)

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
    system = SCAVESystem(gui)

    # ── Signal handlers for clean CLI shutdown ────────────────────────
    def _signal_handler(signum, frame):
        logger.info("Received signal %s — shutting down", signum)
        gui.shutdown_event.set()
        gui.after(0, gui._on_closing)

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
