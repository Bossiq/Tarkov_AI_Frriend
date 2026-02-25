import os
import threading
import time
from dotenv import load_dotenv

# Load environment variables. Do this before importing Brain and TwitchBot
load_dotenv()

from video_capture import VideoCapture
from voice_input import VoiceInput
from voice_output import VoiceOutput
from brain import Brain
from twitch_bot import TwitchBot

class SCAVESystem:
    def __init__(self):
        print("Initializing SCAV-E System...")
        self.vc = VideoCapture()
        self.vi = VoiceInput()
        self.vo = VoiceOutput()
        self.brain = Brain()
        self.twitch_bot = TwitchBot()
        
        # Keep track of the latest frame
        self.latest_frame_path = "latest_frame.jpg"
        self.running = True

        # Hook up twitch chat callback
        self.twitch_bot.set_callback(self.on_twitch_message)
        # Pass system reference for VAD async loop inside event_ready
        self.twitch_bot.set_system_reference(self)

    async def on_twitch_message(self, author, content):
        """Handles a message from Twitch chat."""
        # We can implement a wake word or simple mention detection
        if "scav" in content.lower() or "blyat" in content.lower():
            # Quick async dispatch - generate response and speak it
            prompt = f"User {author} said: '{content}'. Respond to them."
            import asyncio
            # Offload processing to a separate thread safely via asyncio event loop
            asyncio.create_task(asyncio.to_thread(self.process_interaction, prompt, True, False))

    def process_interaction(self, text_prompt=None, use_audio=False, use_video=False):
        """Core interaction loop capturing necessary inputs and getting AI response."""
        audio_path = None
        image_path = None

        if use_audio:
            try:
                # Record via continuous voice activity detection
                audio_path = self.vi.listen(output_filename="current_request.wav")
                if not audio_path:
                    # Return immediately to avoid empty GenAI spam
                    return
                if not text_prompt:
                    text_prompt = "Transcribe and respond to this audio request."
            except Exception as e:
                print(f"Audio recording error: {e}")
                time.sleep(2)
                return

        if use_video:
            import cv2
            frame = self.vc.get_frame()
            if frame is not None:
                cv2.imwrite(self.latest_frame_path, frame)
                image_path = self.latest_frame_path
            
        print("Thinking...")
        response = self.brain.generate_response(
            text_prompt=text_prompt,
            image_path=image_path,
            audio_path=audio_path
        )
        
        # Output verbal response
        self.vo.speak(response)
        
        # Cleanup
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)
            
    def start_video_loop(self):
        """Starts video capture continuously."""
        if self.vc.start():
            print("Video capture started.")
            while self.running:
                # Example: analyze a frame every 60 seconds autonomously
                time.sleep(60) 
                if not self.running:
                    break
                # Only analyze if we aren't already speaking
                # self.process_interaction(text_prompt="Analyze what is happening on screen.", use_video=True)
        else:
            print("Could not start video capture.")

    async def continuous_listening_loop(self):
        import asyncio
        print("[SCAV-E/PMC] Hands-free continuous listening active.")
        while self.running:
            try:
                await asyncio.to_thread(self.process_interaction, None, True, False)
            except Exception as e:
                print(f"Listening loop crashed: {e}")
                await asyncio.sleep(2)
            await asyncio.sleep(0.1)

    def run(self):
        # Start the video background thread
        threading.Thread(target=self.start_video_loop, daemon=True).start()
        
        print("PMC Overwatch is alive. Ready for voice interaction or Twitch chat. Press Ctrl+C to stop.")
        
        try:
            # Start the twitch bot (this blocks the main thread)
            self.twitch_bot.run()
        except KeyboardInterrupt:
            print("\nShutting down Overwatch...")
            self.running = False
            self.vc.stop()
            print("Done. Goodbye.")

if __name__ == "__main__":
    import asyncio
    # Create and set an event loop for the main thread so TwitchIO can attach to it during init
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    system = SCAVESystem()
    system.run()
