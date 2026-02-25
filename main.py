import os
import threading
import asyncio
import time
from dotenv import load_dotenv

# Load environment variables before importing modules that depend on them
load_dotenv()

from video_capture import VideoCapture
from voice_input import VoiceInput
from voice_output import VoiceOutput
from brain import Brain
from twitch_bot import TwitchBot
from gui import OverwatchGUI


class SCAVESystem:
    """Core system wiring the AI brain, voice I/O, Twitch bot, and GUI together."""

    def __init__(self, gui: OverwatchGUI):
        self.gui = gui
        self.gui.log("Initializing SCAV-E System...")

        self.vc = VideoCapture()
        self.vi = VoiceInput()
        self.vo = VoiceOutput(gui_callback=self.gui.log)
        self.brain = Brain()
        self.twitch_bot = TwitchBot()

        self.latest_frame_path = "latest_frame.jpg"
        self.running = False  # controlled by toggle button

        # Hook up twitch chat callback
        self.twitch_bot.set_callback(self.on_twitch_message)
        self.twitch_bot.set_system_reference(self)

        # Register the toggle callback on the GUI
        self.gui.set_toggle_callback(self._on_toggle)

        self.gui.log("System initialised. Click Start Overwatch to begin.")

    # ── Toggle ────────────────────────────────────────────────────────
    def _on_toggle(self, is_running: bool):
        """Called by the GUI when the user clicks Start / Stop."""
        self.running = is_running
        if is_running:
            # Start the listening loop in a background thread
            t = threading.Thread(target=self._listening_thread, daemon=True)
            t.start()

    # ── Listening thread ──────────────────────────────────────────────
    def _listening_thread(self):
        """Runs the continuous VAD listening loop in a background thread."""
        self.gui.log("🎧 Hands-free listening active.")
        while self.running:
            try:
                self.gui.set_status("Listening...")
                self.process_interaction(text_prompt=None, use_audio=True, use_video=False)
            except Exception as e:
                self.gui.log(f"⚠ Listening error: {e}")
                time.sleep(2)
            time.sleep(0.1)
        self.gui.log("🎧 Listening stopped.")

    # ── Twitch message handler ────────────────────────────────────────
    async def on_twitch_message(self, author, content):
        if "scav" in content.lower() or "blyat" in content.lower():
            prompt = f"User {author} said: '{content}'. Respond to them."
            self.gui.log(f"💬 Twitch [{author}]: {content}")
            await asyncio.to_thread(self.process_interaction, prompt, False, False)

    # ── Core interaction logic ────────────────────────────────────────
    def process_interaction(self, text_prompt=None, use_audio=False, use_video=False):
        audio_path = None
        image_path = None

        if use_audio:
            try:
                self.gui.set_status("Listening...")
                audio_path = self.vi.listen(output_filename="current_request.wav")
                if not audio_path:
                    return
                self.gui.log("📡 Speech captured, processing...")
                if not text_prompt:
                    text_prompt = "Transcribe and respond to this audio request."
            except Exception as e:
                self.gui.log(f"⚠ Audio recording error: {e}")
                time.sleep(2)
                return

        if use_video:
            import cv2
            frame = self.vc.get_frame()
            if frame is not None:
                cv2.imwrite(self.latest_frame_path, frame)
                image_path = self.latest_frame_path

        self.gui.set_status("Thinking...")
        self.gui.log("🧠 Generating response...")
        response = self.brain.generate_response(
            text_prompt=text_prompt,
            image_path=image_path,
            audio_path=audio_path,
        )

        # Speak response (also logs via gui_callback)
        self.gui.set_status("Speaking...")
        self.vo.speak(response)
        self.gui.set_status("Listening..." if self.running else "Offline")

        # Cleanup recorded audio
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

    # ── Video background thread ───────────────────────────────────────
    def start_video_loop(self):
        if self.vc.start():
            self.gui.log("📹 Video capture started.")
            while self.running:
                time.sleep(60)
                if not self.running:
                    break
        else:
            self.gui.log("⚠ Could not start video capture.")

    # ── Twitch bot background thread ──────────────────────────────────
    def start_twitch_bot(self):
        """Runs the Twitch bot in its own asyncio event loop on a daemon thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self.gui.log("📺 Starting Twitch bot...")
            self.twitch_bot.run()
        except Exception as e:
            self.gui.log(f"⚠ Twitch bot error: {e}")


# ── Entry point ───────────────────────────────────────────────────────
def main():
    gui = OverwatchGUI()
    system = SCAVESystem(gui)

    # Start the Twitch bot in a daemon thread so it doesn't block the GUI
    twitch_thread = threading.Thread(target=system.start_twitch_bot, daemon=True)
    twitch_thread.start()

    # Run the GUI on the main thread (required by Tkinter)
    gui.mainloop()


if __name__ == "__main__":
    main()
